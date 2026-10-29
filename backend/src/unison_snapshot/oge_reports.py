"""Archive and parse public OGE 278-T PDFs without automating Form 201."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from .source_transport import read_with_retry, TRANSPORT_ERRORS
from .content_integrity import content_boundary
import re
import tempfile
import urllib.error
from urllib.parse import unquote, urlsplit
import urllib.request

from .oge import OgeCatalogError, OgeSourceConfig, require_collection_enabled
from .reviewed_oge_extraction import (source_correction,correct_extraction,
    approved_extraction,replay_base_checkpoint)


REPORT_ARCHIVE_SCHEMA = "oge-278t-archive/v1"
EXTRACTION_SCHEMA = "oge-278t-extraction/v1"
PARSER_VERSION = "oge-278t-pdf/v2"
GENERIC_PARSER_VERSION = "oge-278t-pdf/v7"
GENERIC_ROW_INTERPRETATION_MODE = "source_ordered_owner_account_scope/v1"
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
_OPEN_AMOUNT = re.compile(r"over\s*\$\s*(1,000,000|50,000,000)", re.I)


def read_278t_cover_dates(text: str, *, source_sha256: str) -> dict:
    """Read dates in explicit page-one roles, never from the transaction body.

    Offsets refer to the unmodified embedded cover text. A section's label is
    not evidence that an arbitrary date in its narrative is its signature date.
    """
    if not isinstance(text, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise OgeCatalogError("cover date input is not source-bound text")
    labels = (
        ("filer_signature", r"Filer(?:['’]s)?\s+(?:Certification|Signature)|Signature\s+of\s+(?:Filer|Reporting\s+Individual)"),
        ("electronic_signature_label", r"Electronic\s+Signature\b"),
        ("agency_review", r"Agency\s+Ethics\s+Official(?:['’]s)?\s+Opinion|Agency\s+(?:Review|Certification)|DAEO\s+Signature"),
        ("other_review", r"Other\s+review\s+conducted\s+by|Comments\s+of\s+Reviewing\s+Official"),
        ("oge_certification", r"(?:U\.?S\.?\s+Office\s+of\s+Government\s+Ethics|OGE)\s+Certification"),
        ("receipt", r"(?:Date\s+(?:of\s+)?(?:Receipt|Received)|Received\s+Date)\b"),
        ("report_date", r"Date\s+of\s+Report\b"),
        ("revision_date", r"Data\s+Revised\b"),
        ("transaction_table", r"(?m)^\s*Transactions\b"),
    )
    markers = sorted((match.start(), match.end(), role, match.group())
                     for role, pattern in labels
                     for match in re.finditer(pattern, text, re.I))
    dates = re.compile(
        r"(?<![\w/])(?:\d{1,2}/\d{1,2}/(?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}-\d{2}|"
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+\d{1,2},?\s+(?:19|20)\d{2})(?![\d/])", re.I)
    observations, role = [], None
    amendment = bool(re.search(r"(?im)^\s*(?:AMENDMENT\s+TO|AMENDED\s+(?:REPORT|PERIODIC))", text))
    for index, (start, end, next_role, label) in enumerate(markers):
        # The unqualified electronic-signature label belongs to the first
        # filer block only; it cannot relabel a later reviewing official.
        if next_role == "electronic_signature_label":
            role = role or "filer_signature"
        else:
            role = next_role
        stop = markers[index + 1][0] if index + 1 < len(markers) else len(text)
        if role == "transaction_table":
            continue
        for match in dates.finditer(text, end, stop):
            line_start = text.rfind("\n", start, match.start()) + 1
            prefix = text[line_start:match.start()]
            before_line = text[:line_start].rstrip().split("\n")[-1] if line_start else ""
            explicit_date = bool(re.search(
                r"\bdate(?:\s+signed|\s+of\s+signature)?(?:\s*\([^)]*\))?\s*[:\-]?\s*$", prefix, re.I))
            electronic = bool(re.search(r"electronically\s+signed\s+on\s*$", prefix, re.I))
            signature_label = role == "filer_signature" and bool(re.search(
                r"(?:Filer(?:['’]s)?\s+Signature|Signature\s+of\s+(?:Filer|Reporting\s+Individual))", prefix, re.I))
            following_date_label = bool(re.search(
                r"\bdate(?:\s*\([^)]*\))?\s*:?\s*$", before_line, re.I))
            if not (explicit_date or electronic or signature_label or following_date_label or
                    role in {"receipt", "report_date", "revision_date"}):
                continue
            raw = match.group()
            normalized = None
            for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y", "%B %d %Y"):
                try:
                    normalized = datetime.strptime(raw, fmt).date().isoformat()
                    break
                except ValueError:
                    pass
            observations.append({"role": "amendment_filer_signature" if amendment and role == "filer_signature" else role,
                "raw_date": raw, "date": normalized,
                "status": "readable" if normalized else "invalid_calendar_date",
                "text_span": [match.start(), match.end()], "role_label": label,
                "role_label_span": [start, end], "method": "embedded_cover_text"})
    filer_dates = [item for item in observations if item["role"] == "filer_signature"]
    known = {item["date"] for item in filer_dates if item["date"] is not None}
    if any(item["date"] is None for item in filer_dates):
        reasons = ["filer_signature_date_invalid"]
    elif len(known) > 1:
        reasons = ["filer_signature_date_ambiguous"]
    elif not known:
        reasons = ["filer_signature_date_missing"]
    else:
        reasons = []
    return {"schema_version": "oge-278t-cover-date-roles/v1", "source_sha256": source_sha256,
            "page_number": 1, "cover_text": text,
            "cover_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "observations": observations, "filed_at": next(iter(known)) if not reasons else None,
            "document_reasons": reasons, "amendment_cover": amendment}


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
        def receive():
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status != 200 or response.geturl() != url:
                    raise OgeCatalogError("OGE 278-T returned an unexpected response")
                content = response.read(MAX_PDF_BYTES + 1)
                headers = {name.lower(): value for name, value in response.headers.items()
                           if name.lower() in {"etag", "last-modified", "content-type"}}
            return content, headers
        try:
            content, headers = read_with_retry(receive)
        except urllib.error.HTTPError as exc:
            raise OgeCatalogError(f"OGE 278-T returned HTTP {exc.code}") from None
        except TRANSPORT_ERRORS:
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


_OWNER_HEADING = re.compile(
    r"(?P<owner>spouse|filer|self|dependent child(?:ren)?|child(?:ren)?)(?:['’]s)?\s+"
    r"(?:(?P<account>(?:investment|retirement)\s+account\s*#[0-9]+)|assets|accounts?)",
    re.IGNORECASE)
_BLANK_DECLARATION = re.compile(
    r"(?:(?:this\s+)?line(?:\s+is)?\s+)?(?:intentionally\s+left\s+blank|left\s+intentionally\s+blank)\.?",
    re.IGNORECASE)


def _owner_heading(value: str) -> str | None:
    match = _OWNER_HEADING.fullmatch(_compact(value))
    if match is None:
        return None
    owner = match["owner"].casefold()
    return "Spouse" if owner == "spouse" else "Self" if owner in {"filer", "self"} else "Dependent Child"


def classify_table_structure(raw_cells: list) -> dict | None:
    """Recognize positive structure evidence without using row numbers as type.

    Unknown marker-only cells remain physical unknowns. An unknown numbered or
    unnumbered asset with any financial cells must reach field qualification.
    """
    cells = [_compact(cell) for cell in raw_cells]
    if not any(cells):
        return {"kind": "blank"}
    if cells[0] == "#" and not any(cells[1:]):
        return {"kind": "unknown_marker"}
    if len(cells) != 6:
        return None
    number, description, direction, event, notice, amount = cells
    roles = [r"(?:asset\s+)?description", r"(?:transaction\s+)?type", r"(?:transaction\s+)?date",
             r"notification(?:\s+received\s+over\s+30\s+days\s+ago)?\??", r"amount"]
    role_matches = [bool(re.fullmatch(role, cell, re.I)) for role, cell in zip(
        roles, [description, direction, event, notice, amount], strict=True)]
    # Existing native tables also save the notification/amount header band
    # separately. Its two nonempty cells must each be their exact column role.
    partial_header = not any((description, direction, event)) and all(role_matches[3:])
    if all(role_matches) or partial_header:
        return {"kind": "header"}
    if any((direction, event, amount)):
        return None
    owner = _owner_heading(description)
    if owner:
        match = _OWNER_HEADING.fullmatch(description)
        return {"kind": "owner_account_heading" if match["account"] else "owner_heading",
                "owner": owner, "account_title": description if match["account"] else None}
    if _ACCOUNT_HEADING.fullmatch(description):
        return {"kind": "account_heading", "account_title": description}
    if _BLANK_DECLARATION.fullmatch(description):
        return {"kind": "blank_notice"}
    if re.fullmatch(r"(?:endnotes?|transactions?)(?:\s+.*)?", number, re.I):
        return {"kind": "footer"}
    return None


def parse_table_rows(rows: list[tuple[int, list[object]]], *, source_sha: str,
                     default_owner: str = "Self", allow_open_amounts: bool = False,
                     account_title: str | None = None, allow_exchange: bool = False) -> tuple[list[dict], list[dict]]:
    """Parse extracted six-column table rows and preserve all rejected rows."""

    if not isinstance(rows, list) or not re.fullmatch(r"[0-9a-f]{64}", source_sha):
        raise OgeCatalogError("OGE 278-T table parser input is invalid")
    if account_title is not None and (not isinstance(account_title, str) or not account_title.strip()):
        raise OgeCatalogError("OGE account title must be explicit nonempty source text")
    transactions = []
    quarantined = []
    if default_owner not in {"Self", "Spouse", "Dependent Child", "Undisclosed"}:
        raise OgeCatalogError("OGE 278-T default owner is invalid")
    owner = default_owner
    seen_numbers: set[int] = set()
    for page_number, raw_cells in rows:
        if type(page_number) is not int or page_number <= 0 or not isinstance(raw_cells, list):
            raise OgeCatalogError("OGE 278-T table row is invalid")
        cells = [_compact(cell) for cell in raw_cells]
        if not any(cells):
            continue
        structure = classify_table_structure(cells)
        if structure:
            if structure["kind"] in {"owner_heading", "owner_account_heading"}:
                owner = structure["owner"]
            continue
        if len(cells) != 6:
            quarantined.append({"page_number": page_number, "cells": cells,
                                "reasons": ["unexpected_column_count"]})
            continue
        raw_number, description, raw_type, raw_date, late_notice, raw_amount = cells
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
        elif transaction_type == "exchange" and not allow_exchange:
            reasons.append("exchange_requires_contract_resolution")
        try:
            transaction_date = _iso_date(raw_date)
        except OgeCatalogError:
            transaction_date = None
            reasons.append("transaction_date_invalid")
        amount = _amount(raw_amount)
        open_match = _OPEN_AMOUNT.fullmatch(raw_amount) if allow_open_amounts else None
        if amount is None and open_match is None:
            reasons.append("amount_range_unsupported")
        ticker_match = re.search(r"\(([A-Z0-9][A-Z0-9.\-^/]{0,31})\)\s*$", description)
        ticker = ticker_match.group(1) if ticker_match and _TICKER.fullmatch(ticker_match.group(1)) else None
        asset_name = (description[:ticker_match.start()].rstrip(" ,") if ticker_match else description)
        stable = "|".join((source_sha, str(page_number), raw_number, description,
                           raw_type, raw_date, late_notice, raw_amount, owner))
        if account_title is not None:
            stable += "|account:" + account_title
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
            "amount_low": amount[0] if amount else int(open_match[1].replace(",", "")) if open_match else None,
            "amount_high": amount[1] if amount else None,
            # Keep the exact parser input beside normalized fields so a later
            # shadow adapter can account for every extracted physical row.
            "cells": cells,
        }
        if open_match is not None:
            row["amount_low_exclusive"] = True
        if account_title is not None:
            row["account_title"] = account_title
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


def _extract_pdf(content_path: Path, *, include_inventory: bool = False,
                 include_cover: bool = False):
    try:
        import pdfplumber
    except ImportError:
        raise OgeCatalogError("OGE 278-T extraction requires the optional pdfplumber package") from None
    texts = []
    rows: list[tuple[int, list[object]]] = []
    inventory: list[tuple[int, list[object]]] = []
    try:
        with pdfplumber.open(content_path) as document:
            if not 1 <= len(document.pages) <= MAX_PDF_PAGES:
                raise OgeCatalogError("OGE 278-T page count is outside the safety limit")
            for page_number, page in enumerate(document.pages, start=1):
                texts.append(page.extract_text() or "")
                borderless_tables = _extract_borderless_transaction_tables(page)
                if borderless_tables:
                    for table in borderless_tables:
                        table_rows = [cells for cells in (table or [])
                                      if isinstance(cells, list)]
                        rows.extend((page_number, cells) for cells in table_rows)
                        inventory.extend((page_number, cells) for cells in table_rows)
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
                    inventory.extend((page_number, cells) for cells in table_rows)
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
    if include_inventory:
        result = ("\n".join(texts), rows, inventory)
    else:
        result = ("\n".join(texts), rows)
    if include_cover:
        return (*result, {"page_number": 1, "text": texts[0], "document_page_count": len(texts)})
    return result


@content_boundary('official_hash')
def _read_archived_source(root: Path, metadata_path: Path) -> tuple[dict, Path, str]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if (metadata.get("schema_version") != REPORT_ARCHIVE_SCHEMA or
            metadata.get("source_id") != "oge" or
            not isinstance(metadata.get("document_id"), str) or
            not _DIRECT_ID.fullmatch(metadata["document_id"])):
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
    return metadata, pdf_path, source_sha




def selected_parser_version(metadata: dict, pdf_path: Path, source_sha: str, *, generic=False):
    """Select from the actual source before consulting an old saved result."""
    correction = None if generic else source_correction(metadata,source_sha)
    if correction is None:
        from .oge_native_partition import source_plan
        _, visible_version = source_plan(pdf_path, source_sha256=source_sha, generic=generic)
        if visible_version:
            return visible_version
    return GENERIC_PARSER_VERSION if generic else correction['parser_version'] if correction else PARSER_VERSION


def load_reusable_extraction(root: Path, metadata_path: Path,
                             reuse_root: Path, *, generic: bool = False) -> dict | None:
    """Load a source- and parser-bound review result after re-hashing its PDF.

    Review data is a durable checkpoint, not a substitute for evidence checks.
    Every lookup first validates the immutable archive metadata, PDF envelope,
    byte length and SHA-256. A missing or malformed checkpoint is a cache miss
    so the caller can run the parser again.
    """

    metadata, pdf_path, source_sha = _read_archived_source(root, metadata_path)
    correction = None if generic else source_correction(metadata,source_sha)
    parser_version = selected_parser_version(metadata, pdf_path, source_sha, generic=generic)
    checkpoint = (reuse_root / metadata["document_id"] / source_sha /
                  f"{parser_version.replace('/', '-')}.json")
    if not checkpoint.is_file():
        return replay_base_checkpoint(reuse_root,correction) if correction else None
    try:
        value = json.loads(checkpoint.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    expected = {
        "schema_version": EXTRACTION_SCHEMA,
        "parser_version": parser_version,
        "source_id": "oge",
        "document_id": metadata["document_id"],
        "source_url": metadata.get("document_url"),
        "source_sha256": source_sha,
        "catalog_filer_name": metadata.get("filer_name"),
        "agency": metadata.get("agency"),
        "position_title": metadata.get("position_title"),
        "catalog_added_date": metadata.get("catalog_added_date"),
    }
    if not isinstance(value, dict) or any(value.get(key) != expected_value
                                          for key, expected_value in expected.items()):
        return None
    if (not isinstance(value.get("transactions"), list) or
            not isinstance(value.get("quarantined"), list) or
            not isinstance(value.get("document_reasons"), list) or
            type(value.get("evidence_complete")) is not bool):
        return None
    if correction:
        if not approved_extraction(value):return None
        # Reapply the current data assertions to the saved raw parser result.
        # The approved final artifact remains byte-identical, including its
        # legacy/additive source-proof shape. No OCR is repeated.
        return value if replay_base_checkpoint(reuse_root,correction) is not None else None
    from .oge_native_partition import VERSIONS, replay_partitioned_oge
    if parser_version in VERSIONS:
        try:
            return replay_partitioned_oge(value, root)
        except (OgeCatalogError, OSError, ValueError, KeyError, TypeError):
            return None
    if generic:
        try:
            validate_generic_table_interpretation(value, require_mode=True)
        except (OgeCatalogError, KeyError, TypeError, ValueError):
            return None
        # Integrity is independent of eligibility: a correctly bound negative
        # result remains reusable when a filing date is genuinely absent.
        evidence = value.get("cover_date_evidence")
        count = value.get("document_page_count")
        try:
            replay = read_278t_cover_dates(evidence.get("cover_text"), source_sha256=source_sha)
        except (AttributeError, OgeCatalogError, TypeError):
            return None
        if (any(not isinstance(reason, str) for reason in value["document_reasons"]) or
                replay != evidence or value.get("filed_at") != replay["filed_at"] or
                not set(replay["document_reasons"]) <= set(value["document_reasons"]) or
                type(count) is not int or count < 1 or
                not isinstance(value.get("source_rows"), list) or any(
                    not isinstance(row, dict) or type(row.get("page_number")) is not int or
                    not 1 <= row["page_number"] <= count for row in value["source_rows"])):
            return None
    return value


def parse_generic_table_rows(inventory: list, *, source_sha256: str,
                        scope_barriers_before: dict | None = None) -> tuple[list, list, list]:
    """Interpret exact six-cell rows; optional labels establish source scope."""
    transactions, quarantined, structural = [], [], []
    owner, title, lost_scope, previous_page = "Undisclosed", None, False, None
    owner_scope_lost = False
    seen = set()
    for index, (page, raw) in enumerate(inventory):
        if scope_barriers_before is not None and index in scope_barriers_before and (
                title or owner != "Undisclosed" or scope_barriers_before[index]):
            lost_scope=True
            owner_scope_lost = owner_scope_lost or owner != "Undisclosed"
        cells = [_compact(cell) for cell in raw]
        if previous_page is not None and page > previous_page + 1 and (title or owner != "Undisclosed"):
            lost_scope = True
            owner_scope_lost = owner_scope_lost or owner != "Undisclosed"
        previous_page = page
        structure = classify_table_structure(cells)
        if structure and structure["kind"] in {"owner_heading", "owner_account_heading"}:
            owner, title, lost_scope = structure["owner"], structure["account_title"], False
            owner_scope_lost = False
            structural.append({"source_rows_index": index, "kind": structure["kind"], "text": cells[1]})
            continue
        if structure and structure["kind"] == "account_heading":
            title, lost_scope = structure["account_title"], False
            structural.append({"source_rows_index": index, "kind": "account_heading", "text": cells[1]})
            continue
        if len(cells) == 6:
            description = cells[1]
            label = not any((cells[0], cells[2], cells[3], cells[5]))
            if label and re.search(r"\b(?:spouse|dependent|filer|self)\b.*\b(?:assets?|accounts?)\b", description, re.I):
                lost_scope = True
                owner_scope_lost = True
                structural.append({"source_rows_index": index, "kind": "ambiguous_owner_heading", "text": description})
            if label and re.search(r"\b(?:EXHIBIT|AMENDMENT|REPLAC(?:E|EMENT)|CONTINUED)\b", description, re.I):
                lost_scope = True
                structural.append({"source_rows_index": index, "kind": "unresolved_report_relation", "text": description})
                continue
        if structure and structure["kind"] == "unknown_marker":
            structural.append({"source_rows_index": index, "kind": "unknown_table_content", "text": "#"})
        good, bad = parse_table_rows([(page, cells)], source_sha=source_sha256,
            default_owner=owner, account_title=title, allow_open_amounts=True, allow_exchange=True)
        for row in good + bad:
            row["source_rows_index"] = index
            row["account_context"] = {"status": "unknown" if lost_scope or owner_scope_lost else "observed" if title else "not_observed", "title": title}
            if row["owner"] == "Undisclosed":
                row["owner_disclosure_basis"] = "not_disclosed_in_bound_six_column_table"
            key = (page, row.get("row_number"), title, owner)
            reasons = list(row.get("reasons", []))
            if row.get("row_number") is not None and key in seen:
                reasons.append("same_page_account_row_number_ambiguous")
            seen.add(key)
            if lost_scope or owner_scope_lost:
                reasons.append("account_context_unresolved")
            if owner_scope_lost:
                reasons.append("owner_context_unresolved")
            if reasons:
                quarantined.append({**row, "reasons": sorted(set(reasons))})
            else:
                transactions.append(row)
    # Both occurrences remain unresolved; never pick the first duplicate.
    duplicates = {(r["page_number"], r.get("row_number"), r.get("account_title"), r.get("owner"))
                  for r in quarantined if "same_page_account_row_number_ambiguous" in r["reasons"]}
    kept = []
    for row in transactions:
        if (row["page_number"], row.get("row_number"), row.get("account_title"), row.get("owner")) in duplicates:
            quarantined.append({**row, "reasons": ["same_page_account_row_number_ambiguous"]})
        else:
            kept.append(row)
    return kept, quarantined, structural


def validate_generic_table_interpretation(extraction: dict, *, require_mode: bool = False) -> None:
    """Rebuild new scope-bearing facts from the entire saved physical inventory.

    Older generic extracts remain readable, but cannot masquerade as the new
    interpretation merely by editing their normalized account/owner fields.
    """
    from .oge_native_partition import GENERIC_VERSIONS, validate_partitioned_interpretation
    if extraction.get('parser_version') in GENERIC_VERSIONS:
        validate_partitioned_interpretation(extraction)
        return
    mode = extraction.get("row_interpretation_mode")
    if mode is None and not require_mode:
        if "structural_rows" in extraction or any(
                any(field in row for field in ("account_title", "account_context", "source_rows_index"))
                for row in extraction.get("transactions", []) + extraction.get("quarantined", [])):
            raise OgeCatalogError("OGE scoped fields require source-row interpretation mode")
        return
    if extraction.get("parser_version") != GENERIC_PARSER_VERSION or mode != GENERIC_ROW_INTERPRETATION_MODE:
        raise OgeCatalogError("OGE generic row interpretation mode is invalid")
    inventory=extraction.get("source_rows")
    count=extraction.get("document_page_count")
    if (not isinstance(inventory,list) or type(count) is not int or count < 1 or any(
            not isinstance(row,dict) or type(row.get("page_number")) is not int or
            not 1 <= row["page_number"] <= count or not isinstance(row.get("cells"),list)
            for row in inventory)):
        raise OgeCatalogError("OGE generic scope inventory is invalid")
    physical=[(r["page_number"],r["cells"]) for r in inventory]
    if any(b[0] < a[0] for a,b in zip(physical,physical[1:])):
        raise OgeCatalogError("OGE generic scope inventory page order is invalid")
    good,bad,structural=parse_generic_table_rows(physical,source_sha256=extraction["source_sha256"])
    if (extraction.get("transactions") != good or extraction.get("quarantined") != bad or
            extraction.get("structural_rows") != structural):
        raise OgeCatalogError("OGE generic scope interpretation differs from saved source rows")


def parse_archived_pdf(root: Path, metadata_path: Path, *, generic: bool = False) -> dict:
    metadata, pdf_path, source_sha = _read_archived_source(root, metadata_path)
    from .oge_native_partition import VERSIONS, parse_partitioned_oge
    selected = selected_parser_version(metadata, pdf_path, source_sha, generic=generic)
    if selected in VERSIONS:
        return parse_partitioned_oge(metadata, pdf_path, source_sha256=source_sha,
            generic=generic, parser_version=selected)
    extracted = (_extract_pdf(pdf_path, include_inventory=True, include_cover=True) if generic else
                 _extract_pdf(pdf_path, include_inventory=True))
    cover = None
    if len(extracted) == 4:
        text, rows, inventory, cover = extracted
    elif len(extracted) == 2:  # Keep patched/offline callers using the old return shape compatible.
        text, rows = extracted
        inventory = rows
    else:
        text, rows, inventory = extracted
    signatures = _SIGNATURE_DATE.findall(text)
    correction = None if generic else source_correction(metadata,source_sha)
    date_evidence = None
    if generic:
        date_evidence = read_278t_cover_dates(cover["text"] if cover else "", source_sha256=source_sha)
        filed_at = date_evidence["filed_at"]
        filing_reasons = date_evidence["document_reasons"][:]
        if cover is None:
            filing_reasons.append("cover_text_not_separately_observed")
    elif len(signatures) != 1:
        filed_at = None
        filing_reasons = ["filer_signature_date_not_unique"]
    else:
        filed_at = _iso_date(signatures[0])
        filing_reasons = []
    if generic:
        transactions, quarantined, structural = parse_generic_table_rows(inventory, source_sha256=source_sha)
    else:
        transactions, quarantined = parse_table_rows(rows, source_sha=source_sha)
    if not rows:
        filing_reasons.append("transaction_table_not_found")
    if not transactions and not quarantined:
        filing_reasons.append("no_transaction_rows_found")
    result = {
        "schema_version": EXTRACTION_SCHEMA,
        "parser_version": GENERIC_PARSER_VERSION if generic else PARSER_VERSION,
        "source_id": "oge",
        "document_id": metadata["document_id"],
        "source_url": metadata["document_url"],
        "source_sha256": source_sha,
        "catalog_filer_name": metadata["filer_name"],
        "agency": metadata["agency"],
        "position_title": metadata["position_title"],
        "catalog_added_date": metadata["catalog_added_date"],
        "filed_at": filed_at,
        **({"cover_date_evidence": date_evidence,
            "row_interpretation_mode": GENERIC_ROW_INTERPRETATION_MODE,
            "structural_rows": structural,
            "document_page_count": cover["document_page_count"] if cover else None}
           if generic else {}),
        "document_reasons": filing_reasons,
        "evidence_complete": not filing_reasons,
        "transactions": transactions,
        "quarantined": quarantined,
        # The adapter ledger uses this bounded table extraction to account for
        # headers, section labels, and rows the business parser cannot classify.
        "source_rows": [
            {"page_number": page, "cells": [_compact(cell) for cell in cells]}
            for page, cells in inventory
        ] + [
            {"page_number": row["page_number"], "cells": row["cells"],
             "source_bound_row_recovery": row["source_bound_row_recovery"]}
            for row in transactions if row.get("source_bound_row_recovery")
        ],
    }
    return correct_extraction(result,correction) if correction else result



def _native_amendment_context(observation: dict, source_url: str) -> dict:
    """Interpret a report reference, never a filer signature or an effective date."""
    from .oge_278t_page_coverage import interpret_native_exhibit_page
    interpret_native_exhibit_page(observation)  # Same source-word/page validation.
    text = observation["page_text"]
    month_date = r"[A-Za-z]+\s+[0-9]{1,2},\s+[0-9]{4}"
    filer = re.search(r"(?m)\bFiler:\s*([^\n]+)", text)
    period = re.search(r"(?:Reporting|Repotting)\s+Period:\s*(" + month_date +
                       r")\s*[-–]\s*(" + month_date + r")", text)
    original = re.search(r"Date\s+of\s*(?:Report|Repmt):\s*(" + month_date + r")", text)
    signed = re.search(r"originally\s+signed\s+on\s+(" + month_date + r")", text, re.I)
    component = re.search(r"278t\(([1-9][0-9]*)\)", unquote(source_url), re.I)
    result = {"schema_version": "oge-278t-amendment-reference-context/v1",
              "source_sha256": observation["source_sha256"], "page_number": observation["page_number"],
              "page_text": text, "page_text_sha256": observation["page_text_sha256"],
              "source_observation_canonical_sha256": hashlib.sha256(json.dumps(observation,
                  ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
              "context_role": "original_report_reference_not_filer_signature_or_amendment_effective_date"}
    amount_only = all(re.search(pattern, text, re.I) for pattern in (
        r"AMENDMENT\s+TO", r"PERIODIC\s+TRANSACTION\s+REPORT",
        r"modify[^.]*transaction\s+amounts", r"Exhibit\s+A",
        r"attached\s+to\s+and\s+become\s+a\s+(?:part|patt)\s+of"))
    if not (filer and period and original and signed and component and amount_only):
        return {**result, "status": "unresolved", "reasons": ["amendment_report_reference_context_incomplete"]}
    try:
        dates = [datetime.strptime(value, "%B %d, %Y").date() for value in
                 (period[1], period[2], original[1], signed[1])]
    except ValueError:
        return {**result, "status": "unresolved", "reasons": ["amendment_report_reference_date_unreadable"]}
    if dates[0] > dates[1] or dates[1] > dates[2] or dates[2] != dates[3]:
        return {**result, "status": "unresolved", "reasons": ["amendment_report_reference_dates_conflict"]}
    return {**result, "status": "source_context_available", "filer_name_raw": filer[1].strip(),
            "original_report_date": dates[2].isoformat(),
            "original_reporting_period": [dates[0].isoformat(), dates[1].isoformat()],
            "original_report_component": int(component[1]), "amount_only_statement": True,
            "source_text_spans": {"filer": list(filer.span()), "period": list(period.span()),
                                  "report_date": list(original.span()), "original_signed": list(signed.span())},
            "reasons": []}


def _exhibit_identity_tokens(value: str) -> list[str]:
    tokens = re.findall(r"[a-z]+", (value or "").casefold())
    return sorted(token for token in tokens if token not in {"president", "mr", "mrs", "ms"})


def _native_original_metadata_context(metadata: dict, context: dict) -> bool:
    """Same official reference fields for document hints and full row binding."""
    url = urlsplit(metadata.get("document_url") or "")
    if url.scheme != "https" or url.hostname not in {"www.whitehouse.gov", "whitehouse.gov"}:
        return False
    name = metadata.get("filer_name_from_label") or metadata.get("filer_name")
    title = metadata.get("link_label") or ""
    component = re.search(r"\(([1-9][0-9]*)\)\s*$", title)
    reference = re.search(r"([0-9]{1,2})[.]([0-9]{1,2})[.]([0-9]{2}|[0-9]{4})-([1-9][0-9]*)[.]pdf$",
        unquote(url.path), re.I)
    filer = re.match(r"(.*?)\s+Periodic\s+Transaction\s+Report\b", title, re.I)
    dates = re.findall(r"(?<![0-9])([0-9]{1,2})[.]([0-9]{1,2})[.]([0-9]{2}|[0-9]{4})(?![0-9])", title)
    if (not reference or not filer or not component or len(dates)!=1 or
            metadata.get("document_id")!="wh-url:"+hashlib.sha256(metadata["document_url"].encode()).hexdigest()[:24] or
            _exhibit_identity_tokens(filer[1])!=_exhibit_identity_tokens(name) or
            _exhibit_identity_tokens(name)!=_exhibit_identity_tokens(context.get("filer_name_raw")) or
            int(component[1])!=context.get("original_report_component") or
            int(reference[4])!=int(component[1]) or
            tuple(map(int,reference.groups()[:3]))!=tuple(map(int,dates[0]))):return False
    month,day,year=dates[0]
    try:report_day=datetime(int(year)+(2000 if len(year)==2 else 0),int(month),int(day)).date()
    except ValueError:return False
    return report_day.isoformat()==context.get("original_report_date")


def _exhibit_original_archive(bundle: dict) -> tuple[dict, Path]:
    root = Path(bundle["evidence_root"]).resolve()
    metadata_path = Path(bundle["archive_metadata_path"]).resolve()
    if not metadata_path.is_relative_to(root):
        raise OgeCatalogError("replacement archive metadata escapes the pinned evidence root")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("schema_version") == REPORT_ARCHIVE_SCHEMA:
        metadata, pdf, _ = _read_archived_source(root, metadata_path)
        return metadata, pdf
    if (metadata.get("schema_version") != "whitehouse-public-disclosures-pdf/v1" or
            metadata.get("source_id") != "whitehouse_public_disclosures" or
            not re.fullmatch(r"wh-url:[0-9a-f]{24}", metadata.get("document_id") or "") or
            not isinstance(metadata.get("archive_path"), str)):
        raise OgeCatalogError("replacement original archive metadata is unsupported")
    pdf = (root / metadata["archive_path"]).resolve()
    if not pdf.is_relative_to(root):
        raise OgeCatalogError("replacement original archive escapes evidence root")
    raw = pdf.read_bytes()
    _validate_pdf(raw)
    if hashlib.sha256(raw).hexdigest() != metadata.get("sha256") or len(raw) != metadata.get("byte_length"):
        raise OgeCatalogError("replacement original archive bytes differ from metadata")
    return metadata, pdf


def _native_original_printed_anchors(pdf: Path, sha: str, numbers: list[int]) -> dict:
    """Read only requested literal markers under a native # header; not row ordinals."""
    import pdfplumber
    found = {number: [] for number in numbers}
    with pdfplumber.open(pdf) as document:
        for page_index, page in enumerate(document.pages):
            words = page.extract_words()
            headers = [(index, word) for index, word in enumerate(words) if word["text"] == "#"]
            for header_index, header in headers:
                right = [word for word in words if word["x0"] > header["x1"] and
                         word["top"] < header["bottom"] and word["bottom"] > header["top"]]
                if not right:
                    continue
                edge = min(word["x0"] for word in right)
                for index, word in enumerate(words):
                    if (word["text"] not in {str(n) for n in numbers} or word["top"] <= header["bottom"] or
                            word["x0"] < header["x0"] or word["x1"] >= edge):
                        continue
                    number = int(word["text"])
                    found[number].append({"source_sha256": sha, "page_number": page_index + 1,
                        "printed_number_raw": word["text"], "word_index": index,
                        "word_box": [word[k] for k in ("x0", "top", "x1", "bottom")],
                        "number_header_word_index": header_index,
                        "number_header_box": [header[k] for k in ("x0", "top", "x1", "bottom")],
                        "source_words_canonical_sha256": hashlib.sha256(json.dumps(words, ensure_ascii=False,
                            sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                        "basis": "literal_native_printed_marker_under_same_page_number_header_not_ordinal"})
    return found


def _native_exhibit_context_target(label: dict, targets: list[dict], context: dict, numbers: list[int],
                                  verified_sources: dict | None = None) -> dict:
    if context.get("status") != "source_context_available":
        return {"status": "pending", "target_printed_number": label["target_printed_number"],
                "reasons": ["amendment_report_reference_context_incomplete"]}
    matches = []
    verified_sources = {} if verified_sources is None else verified_sources
    for bundle in targets:
        source_key = (str(Path(bundle["evidence_root"]).resolve()),
                      str(Path(bundle["archive_metadata_path"]).resolve()), tuple(numbers))
        if source_key not in verified_sources:
            metadata, pdf = _exhibit_original_archive(bundle)
            verified_sources[source_key] = (metadata, pdf, None)
        metadata, pdf, anchors = verified_sources[source_key]
        url = urlsplit(metadata.get("document_url") or "")
        if url.scheme != "https" or url.hostname not in {"www.whitehouse.gov", "whitehouse.gov"}:
            continue
        name = metadata.get("filer_name_from_label") or metadata.get("filer_name")
        title = metadata.get("link_label") or ""
        if not _native_original_metadata_context(metadata,context):
            continue
        if anchors is None:
            anchors = _native_original_printed_anchors(pdf, metadata["sha256"], numbers)
            verified_sources[source_key] = (metadata, pdf, anchors)
        if any(len(anchors[number]) != 1 for number in numbers):
            continue
        matches.append({"target_document_id": metadata["document_id"], "target_source_sha256": metadata["sha256"],
            "target_source_url": metadata["document_url"], "archive_metadata_sha256": hashlib.sha256(
                Path(bundle["archive_metadata_path"]).read_bytes()).hexdigest(),
            "official_filer_label": name, "official_report_label": title,
            "original_report_date": context["original_report_date"],
            "original_reporting_period_reference": context["original_reporting_period"],
            "original_reporting_period_basis": "amendment_cover_reference_not_independently_reconstructed_original_period",
            "original_report_component": context["original_report_component"],
            "source_context_sha256": context["source_observation_canonical_sha256"],
            "target_printed_anchor": anchors[label["target_printed_number"]][0],
            "all_referenced_printed_numbers": numbers,
            "basis": "official_archive_identity_date_component_and_source_printed_anchors",
            "row_binding_status": "unverified_not_derived_from_cache_ordinal"})
    if len(matches) != 1:
        return {"status": "pending", "target_printed_number": label["target_printed_number"],
                "candidate_count": len(matches), "reasons": ["replacement_context_candidate_not_unique_or_unverified"]}
    return {"status": "candidate_context_match", "candidate": matches[0],
            "reasons": ["replacement_target_extraction_row_unverified"],
            "application_status": "not_applied_context_is_not_row_or_field_qualification"}


def _native_exhibit_target(label: dict, targets: list[dict], filer_name: str, *, verify_owner_scope: bool = False) -> dict:
    """Bind a printed target only through an explicit original-source URL."""
    urls = label.get("predecessor_urls", [])
    if not urls:
        return {"status":"pending","reasons":["replacement_target_document_unresolved"],
            "target_printed_number":label["target_printed_number"]}
    matches=[]
    for bundle in targets:
        root=Path(bundle["evidence_root"]).resolve()
        meta_path=Path(bundle["archive_metadata_path"]).resolve()
        if not meta_path.is_relative_to(root):
            raise OgeCatalogError("replacement archive metadata escapes the pinned evidence root")
        metadata,pdf,sha = _read_archived_source(root,meta_path)
        if metadata["document_url"] not in urls:
            continue
        target_url=urlsplit(metadata["document_url"])
        if target_url.scheme!="https" or target_url.hostname not in {"extapps2.oge.gov","www2.oge.gov","www.oge.gov","oge.gov"}:
            raise OgeCatalogError("replacement target URL is not an official OGE source")
        original=bundle.get("extraction")
        if (not isinstance(original,dict) or original.get("schema_version") != EXTRACTION_SCHEMA or
                any(original.get(a)!=metadata.get(b) for a,b in (("document_id","document_id"),("source_sha256","sha256"),
                    ("source_url","document_url"),("catalog_filer_name","filer_name"))) or
                original.get("catalog_filer_name")!=filer_name):
            raise OgeCatalogError("replacement target cache differs from its original archive")
        candidates=[row for row in original.get("transactions",[])+original.get("quarantined",[])
                    if row.get("row_number")==label["target_printed_number"]]
        if len(candidates)!=1:
            continue
        row=candidates[0]
        if type(row.get("row_number")) is not int or not isinstance(row.get("cells"),list) or len(row["cells"])!=6:
            continue
        good,bad=parse_table_rows([(row["page_number"],row["cells"])],source_sha=sha,
            default_owner=row.get("owner","Undisclosed"),allow_open_amounts=True,allow_exchange=True,
            account_title=row.get("account_title"))
        if len(good+bad)!=1 or any((good+bad)[0].get(field)!=row.get(field) for field in
                ("extraction_id","row_number","owner","asset_name","ticker","transaction_type","transaction_date","amount_low","amount_high","amount_low_exclusive")):
            raise OgeCatalogError("replacement original source row ID differs from exact raw cells")
        import pdfplumber
        with pdfplumber.open(pdf) as target_pdf:
            page_number=row.get("page_number")
            if type(page_number) is not int or not 1<=page_number<=len(target_pdf.pages):
                continue
            original_page=target_pdf.pages[page_number-1]
            original_tables=_extract_borderless_transaction_tables(original_page) or original_page.extract_tables() or []
            same_raw=[cells for table in original_tables for cells in table if isinstance(cells,list) and
                [_compact(cell) for cell in cells]==row["cells"]]
            if len(same_raw)!=1:
                continue
            if verify_owner_scope:
                native_rows=[(page_number,cells) for table in original_tables for cells in table if isinstance(cells,list)]
                if original.get("parser_version") == GENERIC_PARSER_VERSION:
                    scope_good,scope_bad,_=parse_generic_table_rows(native_rows,source_sha256=sha)
                else:
                    scope_good,scope_bad=parse_table_rows(native_rows,source_sha=sha,default_owner="Self",
                        allow_open_amounts=True,allow_exchange=True)
                scope_rows=[r for r in scope_good+scope_bad if r.get("cells")==row["cells"]]
                if len(scope_rows)!=1 or any(scope_rows[0].get(field)!=row.get(field) for field in
                        ("extraction_id","owner","account_title")):
                    continue
                explicit_owner=False
                for _, source_cells in native_rows:
                    if [_compact(c) for c in source_cells]==row["cells"]:break
                    structure=classify_table_structure([_compact(c) for c in source_cells])
                    if structure and structure["kind"] in {"owner_heading","owner_account_heading"}:
                        explicit_owner=True
        matches.append({"target_document_id":metadata["document_id"],"target_source_sha256":sha,
            "target_source_url":metadata["document_url"],"target_extraction_id":row["extraction_id"],
            "target_page_number":row["page_number"],"target_printed_number":label["target_printed_number"],
            "target_raw_row_basis":"unique_same_page_native_table_cells_reobserved_from_archive",
            "archive_metadata_sha256":hashlib.sha256(meta_path.read_bytes()).hexdigest(),
            **({"owner_scope_basis":"source_page_parser_role_replay_not_cache_supplied_default",
                "owner_scope_explicit":explicit_owner,
                "owner_scope_page_rows_sha256":hashlib.sha256(json.dumps(native_rows,ensure_ascii=False,
                    sort_keys=True,separators=(",",":")).encode()).hexdigest()} if verify_owner_scope else {}),
            "source_label_word_indices":label["word_indices"],"source_label_raw":label["text"]})
    if len(matches)!=1:
        return {"status":"pending","reasons":["replacement_original_source_or_unique_row_unverified"],
            "target_printed_number":label["target_printed_number"],"source_urls_observed":urls}
    return {"status":"bound_replacement_target","target":matches[0],
        "reasons":["replacement_application_requires_source_reconciliation"]}


def _native_exhibit_period_date(raw: str, context: dict | None, relation: dict) -> dict | None:
    """Interpret a short numeric event date only inside the source's explicit period."""
    if (not context or context.get("status") != "source_context_available" or
            relation.get("status") != "candidate_context_match" or
            relation.get("candidate", {}).get("source_context_sha256") !=
            context.get("source_observation_canonical_sha256")):
        return None
    match = re.fullmatch(r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{2})", raw)
    if not match:
        return None
    try:
        start, end = [datetime.strptime(value, "%Y-%m-%d").date()
                      for value in context["original_reporting_period"]]
    except (KeyError, TypeError, ValueError):
        return None
    if start > end or end.year - start.year > 100:
        return None
    first, second, suffix = map(int, match.groups())
    candidates = set()
    for year in range(start.year, end.year + 1):
        if year % 100 != suffix:
            continue
        try:
            value = datetime(year, first, second).date()
        except ValueError:
            continue
        if start <= value <= end:
            candidates.add(value.isoformat())
    if len(candidates) != 1:
        return None
    return {"schema_version": "oge-278t-source-period-date/v1", "raw_value": raw,
            "normalized_value": next(iter(candidates)),
            "reporting_period": [start.isoformat(), end.isoformat()],
            "source_context_sha256": context["source_observation_canonical_sha256"],
            "source_cover_page_number": context["page_number"],
            "basis": "unique_numeric_interpretation_within_explicit_same_source_reporting_period",
            "role": "transaction_date_not_filing_date"}


def materialize_saved_oge_278t_exhibits(extraction: dict, catalog_record: dict, *, observations: list[dict],
        archived_pdf_path: str, replacement_targets: list[dict] | None = None,
        formal_reference_ids: list[str] | None = None,
        amendment_context_page_number: int | None = None,
        replacement_application_mode: str | None = None,
        prior_formal_transactions: list[dict] | None = None) -> dict:
    """Consume fixed native observations in the existing observed-extraction lane.

    No cover is manufactured. Original unknown pages and unresolved amendment
    targets stay unknown; replacement rows never become additive new trades.
    """
    from copy import deepcopy
    from .oge_278t_page_coverage import observe_native_exhibit_page,interpret_native_exhibit_page
    from .oge_transaction_adapter import (OBSERVED_PARSER_VERSION,_observation_input_extraction,
        legacy_disposition_reference,adapt_oge_278t_extraction)
    if not isinstance(extraction,dict) or extraction.get("parser_version") not in {PARSER_VERSION,GENERIC_PARSER_VERSION}:
        raise OgeCatalogError("native exhibit interpretation requires an original supported extraction")
    original=deepcopy(extraction)
    prepared=_observation_input_extraction(extraction)
    reference=None
    if "source_rows" not in prepared:
        reference=legacy_disposition_reference(original,catalog_record)
        prepared={**deepcopy(original),"source_rows":[],"transactions":[],"quarantined":[]}
    adapt_oge_278t_extraction(prepared,catalog_record)
    if not isinstance(observations,list) or not observations:
        raise OgeCatalogError("native exhibit observations are missing")
    targets=[] if replacement_targets is None else replacement_targets
    if not isinstance(targets,list):
        raise OgeCatalogError("replacement target references must be a list")
    if replacement_application_mode not in {None, "verified_amount_only/v1"}:
        raise OgeCatalogError("unsupported native replacement application mode")
    if prior_formal_transactions is not None and (not isinstance(prior_formal_transactions, list) or
            any(not isinstance(row, dict) for row in prior_formal_transactions)):
        raise OgeCatalogError("native replacement prior formal facts must be an array")
    rebuilt=[];seen_pages=set();page_count=None
    for item in observations:
        if not isinstance(item,dict) or item.get("source_sha256")!=extraction["source_sha256"] or type(item.get("page_number")) is not int or item.get("page_number") in seen_pages:
            raise OgeCatalogError("native exhibit observations cross sources or repeat a page")
        seen_pages.add(item["page_number"])
        # This is a bounded native read of the selected archived page only.
        actual=observe_native_exhibit_page(Path(archived_pdf_path),source_sha256=extraction["source_sha256"],page_number=item["page_number"],
            **({"embedded_column_structure":True} if "embedded_column_structure" in item else {}))
        if actual!=item:
            raise OgeCatalogError("saved native exhibit words/tables differ from original archived page")
        if page_count is not None and page_count!=actual["document_page_count"]:
            raise OgeCatalogError("native exhibit document page inventories differ")
        page_count=actual["document_page_count"]
        rebuilt.append(interpret_native_exhibit_page(actual))
    context = None
    if amendment_context_page_number is not None:
        if type(amendment_context_page_number) is not int or amendment_context_page_number in seen_pages:
            raise OgeCatalogError("amendment context page must be a distinct explicit source page")
        cover = observe_native_exhibit_page(Path(archived_pdf_path), source_sha256=extraction["source_sha256"],
                                           page_number=amendment_context_page_number)
        context = _native_amendment_context(cover, extraction["source_url"])
        if (context.get("status") == "source_context_available" and
                _exhibit_identity_tokens(context["filer_name_raw"]) != _exhibit_identity_tokens(extraction["catalog_filer_name"])):
            raise OgeCatalogError("amendment cover filer differs from its official source identity")
    numbers = sorted({block["label"]["target_printed_number"] for page in rebuilt for block in page["blocks"]
                      if block.get("label")})
    result=deepcopy(prepared)
    result.update(parser_version=OBSERVED_PARSER_VERSION,source_parser_version=extraction["parser_version"],
        document_page_count=page_count,native_exhibit_observations=deepcopy(observations))
    if reference is not None:
        result["legacy_disposition_reference"]=reference
    protected=set(formal_reference_ids or [])
    if reference:
        known={row["extraction_id"] for row in original["transactions"]+original["quarantined"]}
        if protected-known:
            raise OgeCatalogError("native exhibit formal references are outside the original cache")
    receipts=[]
    overlapping_pages={r["page_number"] for r in prepared["source_rows"]}&seen_pages
    if overlapping_pages:
        # Without physical locators, old extracted rows cannot be silently
        # counted again as the newly observed replacement blocks.
        result["document_reasons"]=sorted(set(result.get("document_reasons",[])+["native_exhibit_prior_inventory_relation_unresolved"]))
        result["evidence_complete"]=False
    names=("printed_number","description","type","transaction_date","notification","amount")
    context_sources = {}  # Verified only for this materialization; never a persistent cache.
    column_structures = {o["page_number"]:o["embedded_column_structure"] for o in observations
                         if "embedded_column_structure" in o}
    prior_keys={(row["page_number"],tuple(row["cells"])) for row in result["source_rows"]}
    for page in rebuilt:
        for block in page["blocks"]:
            ref={"method":"embedded_native_pdf_words","source_sha256":extraction["source_sha256"],
                "page_number":page["page_number"],"table_index":block["table_index"],
                "input_observation_canonical_sha256":page["input_observation_canonical_sha256"],
                "table_bounds":block["table_bounds"],"field_word_indices":block.get("field_word_indices",{}),
                "label":block.get("label"),"partition_basis":block["partition_basis"],
                "unassigned_body_word_indices":block.get("unassigned_body_word_indices",[]),
                "partition_crossing_word_indices":block.get("partition_crossing_word_indices",[])}
            if page["page_number"] in column_structures:
                ref["column_structure_basis"]="source_sha_verified_embedded_image_reobserved"
                ref["column_boundary_intervals_pdf_points"]=block.get("column_boundary_intervals_pdf_points")
                ref["column_structure_decoded_pixels_sha256"]=column_structures[page["page_number"]].get("decoded_pixels_sha256")
                ref["all_word_indices"]=block.get("all_word_indices",[])
                ref["body_word_indices"]=block.get("body_word_indices",[])
                ref["header_role_anchor_basis"]=block.get("header_role_anchor_basis")
                ref["header_role_anchors_pdf_points"]=block.get("header_role_anchors_pdf_points")
                ref["header_word_indices"]=block.get("header_word_indices",{})
            if page["page_number"] in overlapping_pages:
                receipts.append({**ref,"disposition":"unresolved","reasons":["native_exhibit_prior_inventory_relation_unresolved"]});continue
            if block["status"]!="replacement_row_candidate":
                receipts.append({**ref,"disposition":"unresolved","reasons":block["reasons"]});continue
            relation = (_native_exhibit_context_target(block["label"], targets, context, numbers, context_sources)
                        if context is not None and not block["label"].get("predecessor_urls") else
                        _native_exhibit_target(block["label"], targets, extraction["catalog_filer_name"],
                            verify_owner_scope=replacement_application_mode is not None))
            ref["replacement_relation"]=relation
            cells=[block["raw_columns"][name] for name in names]
            good,bad=parse_table_rows([(page["page_number"],cells)],source_sha=extraction["source_sha256"],
                default_owner="Undisclosed",allow_open_amounts=True,allow_exchange=True)
            if len(good+bad)!=1:
                receipts.append({**ref,"disposition":"unresolved","reasons":["replacement_body_not_one_parser_row"]});continue
            row=(good+bad)[0]
            reasons=set(row.get("reasons",[])+relation["reasons"])
            date_evidence = _native_exhibit_period_date(cells[3], context, relation)
            if date_evidence is not None:
                row["transaction_date"] = date_evidence["normalized_value"]
                ref["transaction_date_interpretation"] = date_evidence
                reasons.discard("transaction_date_invalid")
            if re.fullmatch(r"\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{2}",cells[3]):
                if date_evidence is None:
                    reasons.add("transaction_date_century_unbound")
            if block.get("partition_crossing_word_indices") or block.get("unassigned_body_word_indices"):
                reasons.add("native_header_field_partition_ambiguous")
            application = None
            if replacement_application_mode is not None:
                application, application_reasons = _native_amount_only_application(extraction, catalog_record,
                    row, context, relation, targets, sorted(reasons), prior_formal_transactions or [])
                reasons.update(application_reasons)
                if application is not None:
                    reasons.discard("replacement_application_requires_source_reconciliation")
                    ref["replacement_application"] = application
                    row["owner"] = application["resolved_owner"]
                    if application["prior_row"].get("account_title"):
                        row["account_title"] = application["prior_row"]["account_title"]
            physical={"page_number":page["page_number"],"cells":cells,"native_exhibit_reference":ref}
            key=(physical["page_number"],tuple(cells))
            if key in prior_keys:
                raise OgeCatalogError("native exhibit row already exists in original source inventory")
            prior_keys.add(key)
            result["source_rows"].append(physical)
            disposition = "qualified" if application is not None and not reasons else "quarantined"
            result["transactions" if disposition == "qualified" else "quarantined"].append({**row,
                **({"reasons": sorted(reasons)} if reasons else {}),"native_exhibit_reference":ref,
                "observation_reference":ref,"owner_disclosure_basis":"not_disclosed_in_bound_six_column_table"})
            receipts.append({**ref,"disposition":disposition,"extraction_id":row["extraction_id"],"reasons":sorted(reasons)})
    if replacement_application_mode is not None:
        for row in list(result["transactions"]):
            if row.get("native_exhibit_reference", {}).get("replacement_application"):
                continue
            result["transactions"].remove(row)
            result["quarantined"].append({**row,"reasons":sorted(set(row.get("reasons",[])+["amendment_relationship_unresolved"]))})
    if any(row["disposition"] in {"qualified", "quarantined"} for row in receipts):
        result["legacy_document_reasons"]=deepcopy(extraction.get("document_reasons",[]))
        result["document_reasons"]=[reason for reason in result.get("document_reasons",[]) if reason not in
            {"transaction_table_not_found","no_transaction_rows_found"}]
        result["evidence_complete"]=not result["document_reasons"]
    result["observation_receipt"]={"schema_version":"oge-278t-observed-facts/v1","mode":"native_exhibit_substitution",
        "source_sha256":extraction["source_sha256"],"rows":receipts,"observed_slot_count":len(receipts),
        "observation_unit":"native_replacement_table_block","unresolved_slot_count":sum(r["disposition"]=="unresolved" for r in receipts),
        "unobserved_pages":[p for p in range(1,page_count+1) if p not in seen_pages],"geometry_unknown_pages":[],
        "unknown_regions":[{"page_number":page["page_number"],"reason":"native_exhibit_block_or_prior_inventory_relation_unresolved",
            "input_observation_canonical_sha256":page["input_observation_canonical_sha256"]} for page in rebuilt if
            not page["blocks"] or page["unused_label_indices"] or page["page_number"] in overlapping_pages or
            any(block["status"]!="replacement_row_candidate" for block in page["blocks"])],"coverage_complete":False,"page_count_basis":"source_sha256_verified_pdf",
        "legacy_inventory_basis":prepared.get("legacy_inventory_basis","saved_physical_inventory"),
        "legacy_document_reasons":result.get("legacy_document_reasons",[]),
        "unique_source_row_count":None,"protected_formal_reference_ids":sorted(protected),
        **({"amendment_context": context, "context_observation_scope": "report_reference_only_transaction_inventory_still_unknown"}
           if context is not None else {}),
        **({"legacy_reference_sha256":reference["source_extraction_sha256"],
            "legacy_normalized_transaction_count":reference["normalized_transaction_count"],
            "legacy_quarantined_reference_count":reference["quarantined_reference_count"],
            "legacy_normalized_not_sent_to_qualification":True} if reference else {})}
    if replacement_application_mode is not None:
        result["replacement_application_mode"] = replacement_application_mode
        result["native_exhibit_application_replay"] = {
            "schema_version": "oge-native-exhibit-application-replay/v1", "original_extraction": original,
            "inputs": {"observations": deepcopy(observations), "archived_pdf_path": str(archived_pdf_path),
                "replacement_targets": deepcopy(targets), "formal_reference_ids": sorted(protected),
                "amendment_context_page_number": amendment_context_page_number,
                "replacement_application_mode": replacement_application_mode,
                "prior_formal_transactions": deepcopy(prior_formal_transactions or [])}}
    return result

def _native_amount_only_application(source: dict, record: dict, row: dict, context: dict | None,
        relation: dict, targets: list[dict], reasons: list[str], formal: list[dict]) -> tuple[dict | None, list[str]]:
    """Prove fields and prior identity; never promote a context-only candidate."""
    if relation.get('status') != 'bound_replacement_target':
        return None, []
    pending = [r for r in reasons if r != 'replacement_application_requires_source_reconciliation']
    if pending:
        return None, []
    if not context or context.get('status') != 'source_context_available' or context.get('amount_only_statement') is not True:
        return None, ['replacement_amount_only_source_statement_unverified']
    target = relation['target']
    if (target['target_document_id'] == source['document_id'] or
            target['target_source_sha256'] == source['source_sha256']):
        return None, ['replacement_self_reference']
    originals = [bundle['extraction'] for bundle in targets if isinstance(bundle.get('extraction'), dict)
        and bundle['extraction'].get('document_id') == target['target_document_id']
        and bundle['extraction'].get('source_sha256') == target['target_source_sha256']]
    if len(originals) != 1:
        return None, ['replacement_original_interpretation_not_unique']
    original = originals[0]
    prior = [r for r in original.get('transactions', []) if r.get('extraction_id') == target['target_extraction_id']]
    if len(prior) != 1 or prior[0].get('reasons') or original.get('document_reasons') or original.get('evidence_complete') is not True:
        return None, ['replacement_original_required_fields_not_qualified']
    old = prior[0]
    if any(original.get(field) != source.get(field) for field in ('catalog_filer_name', 'agency', 'position_title')):
        return None, ['replacement_original_source_identity_conflict']
    try:
        original_day = datetime.strptime(original.get('filed_at'), '%Y-%m-%d').date()
        amendment_day = datetime.strptime(source.get('filed_at'), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None, ['replacement_filing_dates_unqualified']
    if amendment_day < original_day:
        return None, ['replacement_precedes_original_filing']
    if (row.get('transaction_date') is None or old.get('transaction_date') is None or
            row['transaction_date'] > source['filed_at'] or old['transaction_date'] > original['filed_at']):
        return None, ['replacement_transaction_date_sequence_invalid']
    if context.get('original_report_date') != original_day.isoformat():
        return None, ['replacement_original_report_date_conflict']
    unresolved = set(source.get('document_reasons', [])) - {'transaction_table_not_found','no_transaction_rows_found'}
    if unresolved:
        return None, ['replacement_source_document_unqualified']
    if any(row.get(field) != old.get(field) for field in ('asset_name','ticker','transaction_type','transaction_date')):
        return None, ['amount_only_replacement_non_amount_financial_conflict']
    from .oge_candidate import _identity_key, _person_id, _transaction_projection
    key = _identity_key(record)
    if key is None:
        return None, ['replacement_source_identity_unqualified']
    person = _person_id(key)
    prior_fact = _transaction_projection(original, old, person)
    retained_owner = any(r.get('id') in {prior_fact['id'],row['extraction_id']} and
                         r.get('owner')==old['owner'] for r in formal)
    resolved_owner = old['owner'] if target.get('owner_scope_explicit') or retained_owner or old['owner']!='Self' else 'Undisclosed'
    expected_row = {**row, 'owner': resolved_owner}
    if old.get('account_title'):
        expected_row['account_title'] = old['account_title']
    expected_fact = _transaction_projection({**source, 'parser_version': 'oge-278t-observed/v1'}, expected_row, person)
    def equal(left, right):
        return all(left.get(field) == right.get(field) for field in set(left) | set(right))
    for expected in (prior_fact, expected_fact):
        candidates = [r for r in formal if r.get('id') == expected['id']]
        if len(candidates) > 1 or any(not equal(r, expected) for r in candidates):
            return None, ['replacement_existing_formal_fact_not_source_bound']
    if any(str(r.get('filing_id')) == str(original['document_id']) for r in formal) and not any(
            r.get('id') == prior_fact['id'] for r in formal) and not any(r.get('id') == expected_fact['id'] for r in formal):
        return None, ['replacement_prior_formal_row_identity_unresolved']
    return {'schema_version':'oge-native-amount-only-application/v1', 'status':'source_verified_amount_only',
        'resolved_owner':resolved_owner,
        'owner_basis':'source_page_scope_or_existing_formal_owner_otherwise_approved_undisclosed',
        'prior_transaction_id': prior_fact['id'], 'transaction_id': row['extraction_id'],
        'prior_fact': prior_fact, 'prior_row': old, 'target': target,
        'amendment_document_id':source['document_id'],'amendment_source_sha256':source['source_sha256'],
        'amendment_source_url':source['source_url'],'original_filed_at':original_day.isoformat(),
        'amendment_filed_at':amendment_day.isoformat(),
        'source_context_sha256':context['source_observation_canonical_sha256'],
        'notification_metadata':{'original_raw':old.get('late_notification_raw'),
                                  'replacement_raw':row.get('late_notification_raw'),
                                  'role':'optional_over30days_metadata_not_a_required_financial_field'}}, []
