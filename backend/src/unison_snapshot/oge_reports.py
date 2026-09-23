"""Archive and parse public OGE 278-T PDFs without automating Form 201."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
import urllib.error
from urllib.parse import unquote, urlsplit
import urllib.request

from .oge import OgeCatalogError, OgeSourceConfig, require_collection_enabled


REPORT_ARCHIVE_SCHEMA = "oge-278t-archive/v1"
EXTRACTION_SCHEMA = "oge-278t-extraction/v1"
PARSER_VERSION = "oge-278t-pdf/v2"
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 500

_DIRECT_ID = re.compile(r"[0-9a-f]{32}")
_ROW_NUMBER = re.compile(r"[1-9][0-9]*")
_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-^/]{0,31}")
_DATE = re.compile(r"(0[1-9]|1[0-2])/([0-2][0-9]|3[01])/[0-9]{4}")
_SIGNATURE_DATE = re.compile(
    r"(?:Electronic\s+Signature|Filer(?:'|’)?s\s+Signature|Signature\s+of\s+Filer).*?"
    r"electronically\s+signed\s+on\s+(\d{2}/\d{2}/\d{4})",
    re.IGNORECASE | re.DOTALL,
)
_ACCOUNT_HEADING = re.compile(r"(?:investment|retirement)\s+account\s*#[0-9]+", re.IGNORECASE)
_AMOUNTS = {
    "$1,001 - $15,000": (1001, 15000),
    "$15,001 - $50,000": (15001, 50000),
    "$50,001 - $100,000": (50001, 100000),
    "$100,001 - $250,000": (100001, 250000),
    "$250,001 - $500,000": (250001, 500000),
    "$500,001 - $1,000,000": (500001, 1000000),
    "$1,000,001 - $5,000,000": (1000001, 5000000),
    "$5,000,001 - $25,000,000": (5000001, 25000000),
    "$25,000,001 - $50,000,000": (25000001, 50000000),
}
_TYPES = {"Purchase": "purchase", "Sale": "sale", "Exchange": "exchange"}
_TYPES_CASEFOLD = {key.casefold(): value for key, value in _TYPES.items()}
_TABLE_HEADERS = ("#", "DESCRIPTION", "TYPE", "DATE", "NOTIFICATION", "AMOUNT")


def _compact(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _iso_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
    except (TypeError, ValueError):
        raise OgeCatalogError("OGE 278-T contains an invalid date") from None


def _validate_direct_record(record: object) -> dict:
    if not isinstance(record, dict) or record.get("source_id") != "oge":
        raise OgeCatalogError("OGE direct report record is invalid")
    if record.get("access_method") != "direct_pdf":
        raise OgeCatalogError("OGE request-required reports cannot be automated")
    document_id = record.get("source_document_id")
    if not isinstance(document_id, str) or not _DIRECT_ID.fullmatch(document_id):
        raise OgeCatalogError("OGE direct report has no stable document ID")
    url = record.get("document_url")
    expected = f"/PAS+Index/{document_id}/$FILE/"
    path = unquote(urlsplit(url).path) if isinstance(url, str) else ""
    if expected.lower() not in path.lower() or not path.lower().endswith(".pdf"):
        raise OgeCatalogError("OGE direct report URL does not match its document ID")
    for field in ("filer_name", "agency", "position_title", "catalog_added_date"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise OgeCatalogError(f"OGE direct report has invalid {field}")
    return record


def collapse_direct_catalog_records(records: list[dict]) -> tuple[list[dict], int]:
    """Collapse exact catalog occurrences while rejecting document conflicts."""

    unique: dict[str, dict] = {}
    duplicate_occurrences = 0
    for value in records:
        record = _validate_direct_record(value)
        document_id = record["source_document_id"]
        prior = unique.get(document_id)
        if prior is None:
            unique[document_id] = record
            continue
        comparable = {key: item for key, item in record.items() if key != "catalog_index"}
        prior_comparable = {key: item for key, item in prior.items() if key != "catalog_index"}
        if comparable != prior_comparable:
            raise OgeCatalogError(
                "OGE direct report document ID has conflicting catalog occurrences")
        duplicate_occurrences += 1
    return [unique[key] for key in sorted(unique)], duplicate_occurrences


class OgePdfClient:
    def __init__(self, timeout: float = 45.0, opener=None):
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener()

    def download(self, url: str) -> tuple[bytes, dict[str, str]]:
        request = urllib.request.Request(url, headers={
            "Accept": "application/pdf",
            "Referer": "https://www.oge.gov/web/OGE.nsf/Officials%20Individual%20Disclosures%20Search%20Collection?OpenForm",
            "User-Agent": "unison-oge-evidence/0.1 (+https://github.com/hunterhigh/us-politician-trades-data)",
        }, method="GET")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status != 200 or response.geturl() != url:
                    raise OgeCatalogError("OGE 278-T returned an unexpected response")
                content = response.read(MAX_PDF_BYTES + 1)
                headers = {name.lower(): value for name, value in response.headers.items()
                           if name.lower() in {"etag", "last-modified", "content-type"}}
        except urllib.error.HTTPError as exc:
            raise OgeCatalogError(f"OGE 278-T returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise OgeCatalogError("OGE 278-T is unavailable") from None
        if len(content) > MAX_PDF_BYTES:
            raise OgeCatalogError("OGE 278-T exceeds its size limit")
        if "pdf" not in headers.get("content-type", "").lower():
            raise OgeCatalogError("OGE 278-T returned non-PDF content")
        _validate_pdf(content)
        return content, headers


def _validate_pdf(content: bytes) -> None:
    if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-4096:]:
        raise OgeCatalogError("OGE 278-T PDF envelope is incomplete")


def _write_once(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise OgeCatalogError("Archived OGE report conflicts with its hash")
        return
    with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def archive_direct_pdf(root: Path, record: dict, config: OgeSourceConfig, *,
                       client: OgePdfClient | None = None,
                       retrieved_at: str | None = None) -> dict:
    """Download one catalog-authorized direct PDF and archive it immutably."""

    require_collection_enabled(config)
    record = _validate_direct_record(record)
    content, headers = (client or OgePdfClient()).download(record["document_url"])
    sha = hashlib.sha256(content).hexdigest()
    base = root.resolve()
    folder = base / "oge" / "reports" / record["source_document_id"]
    pdf_path = folder / f"{sha}.pdf"
    metadata_path = folder / f"{sha}.json"
    _write_once(pdf_path, content)
    metadata = {
        "schema_version": REPORT_ARCHIVE_SCHEMA,
        "source_id": "oge",
        "document_id": record["source_document_id"],
        "document_url": record["document_url"],
        "filer_name": record["filer_name"],
        "agency": record["agency"],
        "position_title": record["position_title"],
        "catalog_added_date": record["catalog_added_date"],
        "amended_label": record.get("amended_label"),
        "pending_final_oge_disposition": record.get("pending_final_oge_disposition") is True,
        "retrieved_at": retrieved_at or datetime.now(timezone.utc).isoformat(),
        "sha256": sha,
        "byte_length": len(content),
        "headers": headers,
        "archive_path": pdf_path.relative_to(base).as_posix(),
    }
    encoded = json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    if metadata_path.exists():
        existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        stable = set(metadata) - {"retrieved_at"}
        if any(existing.get(field) != metadata[field] for field in stable):
            raise OgeCatalogError("Archived OGE report metadata conflicts with its PDF")
        return existing
    _write_once(metadata_path, encoded)
    return metadata


def archive_direct_batch(root: Path, catalog: dict, config: OgeSourceConfig, *, limit: int,
                         client: OgePdfClient | None = None) -> dict:
    require_collection_enabled(config)
    rows = catalog.get("transactions") if isinstance(catalog, dict) else None
    if not isinstance(rows, list) or type(limit) is not int or limit < 0:
        raise OgeCatalogError("OGE direct report batch input is invalid")
    direct_occurrences = [row for row in rows
                          if isinstance(row, dict) and row.get("access_method") == "direct_pdf"]
    direct, duplicate_occurrences = collapse_direct_catalog_records(direct_occurrences)
    request_count = sum(isinstance(row, dict) and row.get("access_method") == "request_required"
                        for row in rows)
    attempted = archived = 0
    failures = []
    reports = []
    for record in direct:
        report_dir = root.resolve() / "oge" / "reports" / record["source_document_id"]
        existing = sorted(report_dir.glob("*.json")) if report_dir.is_dir() else []
        if existing:
            reports.append(json.loads(existing[-1].read_text(encoding="utf-8")))
            continue
        if attempted >= limit:
            continue
        attempted += 1
        try:
            reports.append(archive_direct_pdf(root, record, config, client=client))
            archived += 1
        except OgeCatalogError as exc:
            failures.append({"document_id": record["source_document_id"], "error": str(exc)})
    return {
        "schema_version": "oge-278t-archive-batch/v1",
        "source_id": "oge",
        "catalog_direct_occurrence_count": len(direct_occurrences),
        "catalog_direct_count": len(direct),
        "duplicate_catalog_occurrence_count": duplicate_occurrences,
        "request_required_count": request_count,
        "attempted_count": attempted,
        "archived_count": archived,
        "failure_count": len(failures),
        "pending_count": len(direct) - len(reports),
        "reports": sorted(reports, key=lambda row: row["document_id"]),
        "failures": failures,
    }


def _amount(value: str) -> tuple[int, int] | None:
    normalized = _compact(value).replace("–", "-").replace("—", "-")
    normalized = re.sub(r"\s*-\s*", " - ", normalized)
    exact = _AMOUNTS.get(normalized)
    if exact is not None:
        return exact
    # Image-based OGE forms frequently OCR thousands separators as spaces and
    # the dollar sign as "S".  Accept only pairs that still resolve to one of
    # the enumerated statutory ranges; arbitrary numeric guesses stay barred.
    ocr = re.sub(r"(?i)S(?=\s*\d)", "$", normalized)
    ocr = ocr.replace("•", "-").replace("·", "-")
    groups = re.findall(r"\d[\d, ]*", ocr)
    numbers = []
    for group in groups:
        digits = re.sub(r"[ ,]", "", group)
        if digits:
            numbers.append(int(digits))
    pair = tuple(numbers) if len(numbers) == 2 else None
    return pair if pair in set(_AMOUNTS.values()) else None


def _owner_heading(value: str) -> str | None:
    lowered = value.lower()
    if "spouse" in lowered and ("account" in lowered or "assets" in lowered):
        return "Spouse"
    if ("dependent" in lowered or "child" in lowered) and ("account" in lowered or "assets" in lowered):
        return "Dependent Child"
    if ("filer" in lowered or "self" in lowered) and ("account" in lowered or "assets" in lowered):
        return "Self"
    return None


def parse_table_rows(rows: list[tuple[int, list[object]]], *, source_sha: str) -> tuple[list[dict], list[dict]]:
    """Parse extracted six-column table rows and preserve all rejected rows."""

    if not isinstance(rows, list) or not re.fullmatch(r"[0-9a-f]{64}", source_sha):
        raise OgeCatalogError("OGE 278-T table parser input is invalid")
    transactions = []
    quarantined = []
    owner = "Self"
    seen_numbers: set[int] = set()
    for page_number, raw_cells in rows:
        if type(page_number) is not int or page_number <= 0 or not isinstance(raw_cells, list):
            raise OgeCatalogError("OGE 278-T table row is invalid")
        cells = [_compact(cell) for cell in raw_cells]
        if not any(cells):
            continue
        joined = " ".join(cells).lower()
        if (cells[0] == "#" or
                ("description" in joined and "type" in joined and "date" in joined) or
                ("amount" in joined and "received" in joined and "days ago" in joined)):
            continue
        if len(cells) != 6:
            quarantined.append({"page_number": page_number, "cells": cells,
                                "reasons": ["unexpected_column_count"]})
            continue
        raw_number, description, raw_type, raw_date, late_notice, raw_amount = cells
        if (not _ROW_NUMBER.fullmatch(raw_number) and
                raw_number.casefold().startswith(("endnot", "transacti"))):
            continue
        non_transaction = not any((raw_type, raw_date, raw_amount))
        heading = _owner_heading(description) if non_transaction else None
        if heading:
            owner = heading
            continue
        if non_transaction and _ACCOUNT_HEADING.fullmatch(description):
            continue
        if non_transaction and ("intentionally left blank" in description.lower() or
                                "left intentionally blank" in description.lower()):
            continue
        reasons = []
        if not _ROW_NUMBER.fullmatch(raw_number):
            reasons.append("row_number_invalid")
            number = None
        else:
            number = int(raw_number)
            if number in seen_numbers:
                reasons.append("row_number_duplicated")
            seen_numbers.add(number)
        if not description:
            reasons.append("description_missing")
        transaction_type = _TYPES_CASEFOLD.get(raw_type.casefold())
        if transaction_type is None:
            reasons.append("transaction_type_unsupported")
        elif transaction_type == "exchange":
            reasons.append("exchange_requires_contract_resolution")
        try:
            transaction_date = _iso_date(raw_date)
        except OgeCatalogError:
            transaction_date = None
            reasons.append("transaction_date_invalid")
        amount = _amount(raw_amount)
        if amount is None:
            reasons.append("amount_range_unsupported")
        ticker_match = re.search(r"\(([A-Z0-9][A-Z0-9.\-^/]{0,31})\)\s*$", description)
        ticker = ticker_match.group(1) if ticker_match and _TICKER.fullmatch(ticker_match.group(1)) else None
        asset_name = (description[:ticker_match.start()].rstrip(" ,") if ticker_match else description)
        stable = "|".join((source_sha, str(page_number), raw_number, description,
                           raw_type, raw_date, late_notice, raw_amount, owner))
        row = {
            "extraction_id": "oge-278t:" + hashlib.sha256(stable.encode()).hexdigest()[:24],
            "page_number": page_number,
            "row_number": number,
            "owner": owner,
            "asset_name": asset_name,
            "ticker": ticker,
            "transaction_type_raw": raw_type,
            "transaction_type": transaction_type,
            "transaction_date": transaction_date,
            "late_notification_raw": late_notice or None,
            "amount_raw": raw_amount,
            "amount_low": amount[0] if amount else None,
            "amount_high": amount[1] if amount else None,
        }
        if reasons:
            quarantined.append({**row, "cells": cells, "reasons": sorted(set(reasons))})
        else:
            transactions.append(row)
    return transactions, quarantined


def _extract_borderless_transaction_tables(page) -> list[list[list[object]]]:
    """Recover current Integrity.gov tables that only draw horizontal rules."""

    words = page.extract_words(use_text_flow=False, keep_blank_chars=False) or []
    recognized = []
    for word in words:
        text = _compact(word.get("text")).upper() if isinstance(word, dict) else ""
        if text in _TABLE_HEADERS:
            recognized.append((text, word))
    anchors = None
    for text, word in recognized:
        if text != "#":
            continue
        top = float(word["top"])
        candidate = {"#": word}
        for label in _TABLE_HEADERS[1:]:
            matches = [item for item_text, item in recognized
                       if item_text == label and abs(float(item["top"]) - top) <= 3]
            if matches:
                candidate[label] = min(matches, key=lambda item: abs(float(item["top"]) - top))
        if set(candidate) == set(_TABLE_HEADERS):
            anchors = candidate
            break
    if anchors is None:
        return []
    header_tops = [float(anchors[label]["top"]) for label in _TABLE_HEADERS]
    if max(header_tops) - min(header_tops) > 3:
        return []

    # Each table rule is represented as adjacent horizontal segments.  Their
    # shared endpoints reveal the six column boundaries even though the PDF
    # has no vertical strokes for pdfplumber's default table detector.
    horizontal_by_top: dict[float, list[dict]] = {}
    for line in getattr(page, "lines", ()):
        if not isinstance(line, dict):
            continue
        try:
            top = float(line["top"])
            bottom = float(line["bottom"])
            x0 = float(line["x0"])
            x1 = float(line["x1"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs(top - bottom) <= 1 and top > max(header_tops):
            horizontal_by_top.setdefault(round(top, 2), []).append(
                {"x0": min(x0, x1), "x1": max(x0, x1)})

    anchor_x = [float(anchors[label]["x0"]) for label in _TABLE_HEADERS]
    boundaries = None
    for top in sorted(horizontal_by_top):
        endpoints = sorted({edge for line in horizontal_by_top[top]
                            for edge in (line["x0"], line["x1"])})
        starts = []
        for x0 in anchor_x:
            candidates = [edge for edge in endpoints if 0 <= x0 - edge <= 20]
            if not candidates:
                break
            starts.append(max(candidates))
        if len(starts) != len(_TABLE_HEADERS) or starts != sorted(set(starts)):
            continue
        right_edges = [edge for edge in endpoints if edge > anchor_x[-1] + 20]
        if right_edges:
            boundaries = starts + [max(right_edges)]
            break
    if boundaries is None:
        return []

    settings = {
        "vertical_strategy": "explicit",
        "explicit_vertical_lines": boundaries,
        "horizontal_strategy": "lines",
        "snap_tolerance": 3,
        "join_tolerance": 3,
    }
    transaction_page = page
    endnote_tops = [float(word["top"]) for word in words
                    if isinstance(word, dict) and
                    _compact(word.get("text")).casefold() == "endnotes" and
                    float(word["top"]) > max(header_tops)]
    if hasattr(page, "crop") and hasattr(page, "width") and hasattr(page, "height"):
        bottom = min(endnote_tops) if endnote_tops else float(page.height)
        transaction_page = page.crop((0, min(header_tops) - 1, float(page.width), bottom))
    return transaction_page.extract_tables(settings) or []


def _looks_like_transaction_table(table_rows: list[list[object]]) -> bool:
    row_like = 0
    for cells in table_rows:
        compact = [_compact(cell) for cell in cells]
        if (len(compact) == 6 and _ROW_NUMBER.fullmatch(compact[0]) and
                ("/" in compact[3] or "$" in compact[5] or compact[5].startswith("S"))):
            row_like += 1
    return row_like >= 2


def _extract_pdf(content_path: Path) -> tuple[str, list[tuple[int, list[object]]]]:
    try:
        import pdfplumber
    except ImportError:
        raise OgeCatalogError("OGE 278-T extraction requires the optional pdfplumber package") from None
    texts = []
    rows: list[tuple[int, list[object]]] = []
    try:
        with pdfplumber.open(content_path) as document:
            if not 1 <= len(document.pages) <= MAX_PDF_PAGES:
                raise OgeCatalogError("OGE 278-T page count is outside the safety limit")
            for page_number, page in enumerate(document.pages, start=1):
                texts.append(page.extract_text() or "")
                borderless_tables = _extract_borderless_transaction_tables(page)
                if borderless_tables:
                    for table in borderless_tables:
                        rows.extend((page_number, cells) for cells in (table or [])
                                    if isinstance(cells, list))
                    continue
                tables = page.extract_tables() or []
                for table in tables:
                    table_rows = [cells for cells in (table or []) if isinstance(cells, list)]
                    signature = " ".join(_compact(cell).lower()
                                         for cells in table_rows for cell in cells)
                    has_headers = ("description" in signature and "type" in signature and
                                   "date" in signature and "amount" in signature and
                                   "notification" in signature)
                    if not has_headers and not _looks_like_transaction_table(table_rows):
                        continue
                    first_transaction = next(
                        (index for index, cells in enumerate(table_rows)
                         if cells and _ROW_NUMBER.fullmatch(_compact(cells[0]))), None)
                    selected_rows = (table_rows[first_transaction:]
                                     if first_transaction is not None else table_rows)
                    rows.extend((page_number, cells) for cells in selected_rows)
    except OgeCatalogError:
        raise
    except Exception:
        raise OgeCatalogError("OGE 278-T PDF could not be extracted") from None
    return "\n".join(texts), rows


def parse_archived_pdf(root: Path, metadata_path: Path) -> dict:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("schema_version") != REPORT_ARCHIVE_SCHEMA:
        raise OgeCatalogError("OGE 278-T archive metadata is invalid")
    archive_path = metadata.get("archive_path")
    if not isinstance(archive_path, str):
        raise OgeCatalogError("OGE 278-T archive path is invalid")
    base = root.resolve()
    pdf_path = (base / archive_path).resolve()
    if base not in pdf_path.parents:
        raise OgeCatalogError("OGE 278-T archive path escapes its root")
    content = pdf_path.read_bytes()
    _validate_pdf(content)
    source_sha = hashlib.sha256(content).hexdigest()
    if metadata.get("sha256") != source_sha or metadata.get("byte_length") != len(content):
        raise OgeCatalogError("OGE 278-T bytes do not match archive metadata")
    text, rows = _extract_pdf(pdf_path)
    signatures = _SIGNATURE_DATE.findall(text)
    if len(signatures) != 1:
        filed_at = None
        filing_reasons = ["filer_signature_date_not_unique"]
    else:
        filed_at = _iso_date(signatures[0])
        filing_reasons = []
    transactions, quarantined = parse_table_rows(rows, source_sha=source_sha)
    if not rows:
        filing_reasons.append("transaction_table_not_found")
    if not transactions and not quarantined:
        filing_reasons.append("no_transaction_rows_found")
    return {
        "schema_version": EXTRACTION_SCHEMA,
        "parser_version": PARSER_VERSION,
        "source_id": "oge",
        "document_id": metadata["document_id"],
        "source_url": metadata["document_url"],
        "source_sha256": source_sha,
        "catalog_filer_name": metadata["filer_name"],
        "agency": metadata["agency"],
        "position_title": metadata["position_title"],
        "catalog_added_date": metadata["catalog_added_date"],
        "filed_at": filed_at,
        "document_reasons": filing_reasons,
        "evidence_complete": not filing_reasons,
        "transactions": transactions,
        "quarantined": quarantined,
    }
