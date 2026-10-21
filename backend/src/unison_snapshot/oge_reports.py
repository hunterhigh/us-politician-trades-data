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
GENERIC_PARSER_VERSION = "oge-278t-pdf/v7"
GENERIC_ROW_INTERPRETATION_MODE = "source_ordered_owner_account_scope/v1"
TRUMP_SEPT_2026_PARSER_VERSION = "oge-278t-pdf/v3"
TRUMP_SEPT_2026_SECOND_PASS_VERSION = "oge-278t-pdf/v4"
TRUMP_SEPT_2026_STRUCTURAL_PASS_VERSION = "oge-278t-pdf/v5"
TRUMP_SEPT_2026_PAGE7_PASS_VERSION = "oge-278t-pdf/v6"
TRUMP_SEPT_2026_DOCUMENT_ID = "e590116fc9631e9885258e7a002de209"
TRUMP_SEPT_2026_SOURCE_SHA256 = (
    "833c3b4810eaf2e83a3b27867af149634e65145dc4577c753ef20f6b6d515dcf"
)
# Keep the row-layout repair bound to the immutable production bytes even in
# tests that patch the public source constant to exercise the generic replay.
_TRUMP_SEPT_2026_STRUCTURAL_SOURCE_SHA256 = TRUMP_SEPT_2026_SOURCE_SHA256
_TRUMP_SEPT_2026_PAGE7_SOURCE_SHA256 = TRUMP_SEPT_2026_SOURCE_SHA256
TRUMP_SEPT_2026_SOURCE_URL = (
    "https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/"
    "E590116FC9631E9885258E7A002DE209/$FILE/Donald-J-Trump-09.8.2026-278T.pdf"
)
TRUMP_SEPT_2026_FILING_DATE_EVIDENCE = {
    "basis": "fixed_source_visual_filer_certification",
    "page_number": 1,
    "field": "Filer's Certification / Date",
    "raw": "9/8/26",
    "filed_at": "2026-09-08",
    "bbox_pdf_points": [525, 260, 650, 290],
}
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


def _recover_fixed_trump_september_salo(
        transactions: list[dict], quarantined: list[dict]) -> tuple[list[dict], list[dict]]:
    """Resolve one visually checked OCR token without changing original row IDs.

    Only the exact archived Trump September source calls this function. The
    original six cells and raw type remain attached to every corrected row so
    later review can reconstruct the v3 decision without reading a new PDF.
    """

    promoted = list(transactions)
    remaining = []
    for original in quarantined:
        cells = original.get("cells")
        reasons = original.get("reasons")
        raw_type = original.get("transaction_type_raw")
        if (not isinstance(cells, list) or len(cells) != 6 or
                not isinstance(raw_type, str) or raw_type.casefold() != "salo" or
                cells[2] != raw_type or not isinstance(reasons, list) or
                "transaction_type_unsupported" not in reasons):
            remaining.append(original)
            continue
        corrected = dict(original)
        corrected["transaction_type"] = "sale"
        corrected["type_ocr_correction"] = {
            "basis": "fixed_source_salo_ocr_normalization",
            "raw_type": raw_type,
            "resolved_type": "sale",
            "original_reasons": list(reasons),
        }
        residual = [reason for reason in reasons
                    if reason != "transaction_type_unsupported"]
        if residual:
            corrected["reasons"] = residual
            remaining.append(corrected)
        else:
            corrected.pop("reasons")
            promoted.append(corrected)
    return promoted, remaining


def _recover_fixed_trump_september_structural_rows(
        transactions: list[dict], quarantined: list[dict], *,
        source_sha: str) -> tuple[list[dict], list[dict]]:
    """Recover six visually closed rows without rewriting v4 transactions.

    The archived scan omits four leading rows from the OCR table geometry,
    treats the page-34 header as a transaction, and shifts the page-12 fields
    for printed rows 337 and 338.  This repair is deliberately keyed to the
    fixed source and the v4 extraction IDs.  All 352 v4 transaction objects
    remain byte-equivalent; only two quarantined objects change disposition.
    """

    if len(transactions) != 352 or len(quarantined) != 801:
        raise OgeCatalogError("Trump September v5 requires the fixed v4 row counts")
    transaction_ids = [row.get("extraction_id") for row in transactions]
    quarantine_ids = [row.get("extraction_id") for row in quarantined]
    if (any(not isinstance(value, str) for value in transaction_ids + quarantine_ids) or
            len(set(transaction_ids + quarantine_ids)) != 1153):
        raise OgeCatalogError("Trump September v5 requires unique v4 extraction IDs")

    targets = {
        "oge-278t:fac97da8e5025227ad0b9139": {
            "page_number": 12,
            "row_number": 337,
            "asset_name": "",
            "transaction_type_raw": "sale sale",
            "transaction_type": None,
            "transaction_date": "2026-07-29",
            "late_notification_raw": "no",
            "amount_raw": "$1 001 -$15 000",
            "amount_low": 1001,
            "amount_high": 15000,
            "cells": ["337", "", "sale sale", "7/29/2026", "no",
                      "$1 001 -$15 000"],
            "reasons": ["description_missing", "transaction_type_unsupported"],
        },
        "oge-278t:8b3bb35e34734a1102b4e41e": {
            "page_number": 12,
            "row_number": 336,
            "asset_name": "WENDYS CO CLASS A",
            "transaction_type_raw": "",
            "transaction_type": None,
            "transaction_date": "2026-07-29",
            "late_notification_raw": "no",
            "amount_raw": "$1 001 -$15 000",
            "amount_low": 1001,
            "amount_high": 15000,
            "cells": ["336", "WENDYS CO CLASS A", "", "7/29/2026", "no",
                      "$1 001 -$15 000"],
            "reasons": ["row_number_duplicated", "transaction_type_unsupported"],
        },
    }
    fake_header_id = "oge-278t:97438cd2efe2ba17cc80ccb4"
    fake_header = {
        "page_number": 34,
        "row_number": 11,
        "asset_name": "Deacrlpllon",
        "transaction_type_raw": "",
        "transaction_type": None,
        "transaction_date": None,
        "late_notification_raw": "Notlflcatlon Re. .l vedOver 30 0.V.Aao no",
        "amount_raw": "Amount",
        "amount_low": None,
        "amount_high": None,
        "cells": ["11", "Deacrlpllon", "", "Date",
                  "Notlflcatlon Re. .l vedOver 30 0.V.Aao no", "Amount"],
        "reasons": ["amount_range_unsupported", "row_number_duplicated",
                    "transaction_date_invalid", "transaction_type_unsupported"],
    }
    by_id = {row["extraction_id"]: row for row in quarantined}
    for identifier, expected in {**targets, fake_header_id: fake_header}.items():
        row = by_id.get(identifier)
        if row is None or any(row.get(key) != value for key, value in expected.items()):
            raise OgeCatalogError("Trump September v5 source row evidence changed")

    remaining = [row for row in quarantined
                 if row["extraction_id"] not in {*targets, fake_header_id}]
    recovered_transactions = list(transactions)
    recovered_cells = [
        (4, ["67", "NATERA INC", "purchase", "7/17/2026", "No",
             "$1,001 - $15,000"]),
        (4, ["68", "DEXCOM INC", "purchase", "7/17/2026", "No",
             "$1,001 - $15,000"]),
        (9, ["232", "MORGAN STANLEY", "sale", "7/31/2026", "no",
             "$50,001 - $100,000"]),
        (9, ["233", "BOOKING HLDGS INC", "sale", "7/31/2026", "no",
             "$50,001 - $100,000"]),
    ]
    added, rejected = parse_table_rows(recovered_cells, source_sha=source_sha)
    if len(added) != 4 or rejected:
        raise OgeCatalogError("Trump September v5 recovered rows are not qualified")
    for row in added:
        row["source_bound_row_recovery"] = {
            "basis": "fixed_source_visual_table_row_recovery",
            "page_number": row["page_number"],
            "printed_row_number": row["row_number"],
        }
    recovered_transactions.extend(added)

    for identifier, resolved in (
        ("oge-278t:fac97da8e5025227ad0b9139",
         {"row_number": 337, "asset_name": "ARTIVION INC",
          "transaction_type": "sale"}),
        ("oge-278t:8b3bb35e34734a1102b4e41e",
         {"row_number": 338, "asset_name": "WENDYS CO CLASS A",
          "transaction_type": "sale"}),
    ):
        original = by_id[identifier]
        corrected = {key: value for key, value in original.items() if key != "reasons"}
        corrected.update(resolved)
        corrected["source_bound_row_correction"] = {
            "basis": "fixed_source_visual_table_alignment",
            "original_row_number": original["row_number"],
            "original_asset_name": original["asset_name"],
            "original_transaction_type_raw": original["transaction_type_raw"],
            "resolved_row_number": resolved["row_number"],
            "resolved_asset_name": resolved["asset_name"],
            "resolved_transaction_type": resolved["transaction_type"],
        }
        recovered_transactions.append(corrected)

    if len(recovered_transactions) != 358 or len(remaining) != 798:
        raise OgeCatalogError("Trump September v5 row conservation failed")
    final_ids = [row["extraction_id"] for row in recovered_transactions + remaining]
    if len(final_ids) != 1156 or len(set(final_ids)) != 1156:
        raise OgeCatalogError("Trump September v5 extraction IDs are not conserved")
    return recovered_transactions, remaining


_TRUMP_SEPT_2026_PAGE7_TYPE_IDS = {
    172: "oge-278t:4092d776ea20223d8fd66db2",
    173: "oge-278t:48c9e48142ef368c5e39257f",
    178: "oge-278t:608461166207f9b9f333241d",
    179: "oge-278t:d85eb1033dcb46f6eae29dac",
    180: "oge-278t:7c8ffd7b6242a58c7d1a5032",
    181: "oge-278t:956775223c4e229d063b5e3b",
    182: "oge-278t:8aac3a79323a7895873e39e8",
    183: "oge-278t:b943958da9dc84e6a68f9be8",
    184: "oge-278t:0d28d77025535d2a2e153796",
    185: "oge-278t:2fb6cb5849c64fb3638c3281",
    186: "oge-278t:c4baa50416748b28c6a9a4f4",
    187: "oge-278t:4cedc372692338cbdc10c413",
    188: "oge-278t:3a91de09bb8ecf07895a2bff",
    189: "oge-278t:f36c5e73803e09c2e922f9b2",
    190: "oge-278t:405a6b7a46fadfe90305bc1a",
    192: "oge-278t:29692e04a5589bb6a7e5a9cf",
    193: "oge-278t:d2fb936c42e3ac4f9131fd80",
    194: "oge-278t:a7b9036a2d9aff70f4e806c4",
    195: "oge-278t:aa271fa48fca255468def560",
    196: "oge-278t:73b3a93109bb2e3f9e54255d",
    197: "oge-278t:3cd37932c7dfcbc3af1f64e9",
}

_TRUMP_SEPT_2026_PAGE7_SPECIAL = {
    168: {
        "id": "oge-278t:2d10674fb4edd6281d71c093",
        "cells": ["168", "Amazon Com Inc", "sale", "7/17/2026", "No",
                  "$250.001 • $500,000"],
        "resolved": {"amount_low": 250001, "amount_high": 500000},
        "original": {"row_number": 168, "transaction_type": "sale",
                     "transaction_date": "2026-07-17", "amount_low": None,
                     "amount_high": None,
                     "reasons": ["amount_range_unsupported"]},
    },
    171: {
        "id": "oge-278t:064972d2b35aa0ecb7afcac7",
        "cells": ["171", "STATE STREET SPDR BLOOMBERG INTERNATIONAL TREASURY BONO ETF",
                  "purchase", "7/8/2026 Yes", "", "$1,000,001·$5,000000"],
        "resolved": {"transaction_date": "2026-07-08",
                     "late_notification_raw": "Yes"},
        "original": {"row_number": 171, "transaction_type": "purchase",
                     "transaction_date": None, "late_notification_raw": None,
                     "amount_low": 1000001, "amount_high": 5000000,
                     "reasons": ["transaction_date_invalid"]},
    },
    177: {
        "id": "oge-278t:6ecc20e5caf44564da48f847",
        "cells": ["1n", "VANGUARD DIVIDEND APPRECIATION INDEX FUND ETF SHARES",
                  "Durchaso", "7/8/2026", "-", "$1 000.001 • $5,000,000"],
        "resolved": {"row_number": 177, "transaction_type": "purchase",
                     "late_notification_raw": "Yes", "amount_low": 1000001,
                     "amount_high": 5000000},
        "original": {"row_number": None, "transaction_type": None,
                     "transaction_date": "2026-07-08",
                     "late_notification_raw": "-", "amount_low": None,
                     "amount_high": None,
                     "reasons": ["amount_range_unsupported", "row_number_invalid",
                                 "transaction_type_unsupported"]},
    },
    191: {
        "id": "oge-278t:6c61768be2be46745375f278",
        "cells": ["191 WATSCO INC CLASS A", "", "lourchaso", "7/22/2026",
                  "no", "$100,001 -$250 000"],
        "resolved": {"row_number": 191, "asset_name": "WATSCO INC CLASS A",
                     "transaction_type": "purchase"},
        "original": {"row_number": None, "asset_name": "",
                     "transaction_type": None,
                     "transaction_date": "2026-07-22",
                     "amount_low": 100001, "amount_high": 250000,
                     "reasons": ["description_missing", "row_number_invalid",
                                 "transaction_type_unsupported"]},
    },
}


def _recover_fixed_trump_september_page7(
        transactions: list[dict], quarantined: list[dict], *,
        source_sha: str) -> tuple[list[dict], list[dict]]:
    """Resolve only the 25 visually closed page-7 positions, #166-198.

    No OCR cell or extraction ID is rewritten.  In particular, the two
    misread row labels and the date/amount column shifts remain recoverable
    from the original cells attached to each promoted transaction.
    """

    if source_sha != _TRUMP_SEPT_2026_PAGE7_SOURCE_SHA256 or \
            len(transactions) != 358 or len(quarantined) != 798:
        raise OgeCatalogError("Trump September v6 requires the fixed v5 source and counts")
    targets = {**{identifier: number for number, identifier
                   in _TRUMP_SEPT_2026_PAGE7_TYPE_IDS.items()},
               **{entry["id"]: number for number, entry
                  in _TRUMP_SEPT_2026_PAGE7_SPECIAL.items()}}
    if len(targets) != 25:
        raise OgeCatalogError("Trump September v6 target IDs are not unique")
    old_ids = [row.get("extraction_id") for row in transactions + quarantined]
    if len(old_ids) != 1156 or len(set(old_ids)) != 1156:
        raise OgeCatalogError("Trump September v6 requires unique v5 extraction IDs")
    by_id = {row["extraction_id"]: row for row in quarantined}
    if {row["extraction_id"] for row in quarantined
            if row.get("page_number") == 7} != set(targets):
        raise OgeCatalogError("Trump September v6 page-7 disposition changed")

    promoted = list(transactions)
    for identifier, number in sorted(targets.items(), key=lambda item: item[1]):
        original = by_id[identifier]
        cells = original.get("cells")
        if not isinstance(cells, list) or len(cells) != 6 or \
                original.get("page_number") != 7 or original.get("owner") != "Self":
            raise OgeCatalogError("Trump September v6 page-7 row shape changed")
        raw_stable = "|".join((source_sha, "7", *cells, original["owner"]))
        raw_identifier = "oge-278t:" + hashlib.sha256(raw_stable.encode()).hexdigest()[:24]
        if raw_identifier != identifier:
            raise OgeCatalogError("Trump September v6 original OCR cells changed")
        if (original.get("transaction_type_raw") != cells[2] or
                original.get("amount_raw") != cells[5] or
                original.get("late_notification_raw") != (cells[4] or None) or
                original.get("ticker") is not None):
            raise OgeCatalogError("Trump September v6 parsed OCR columns changed")
        if number in _TRUMP_SEPT_2026_PAGE7_TYPE_IDS:
            if (original.get("row_number") != number or
                    cells[0] != str(number) or
                    original.get("asset_name") != cells[1] or
                    original.get("transaction_type_raw") != cells[2] or
                    not original.get("asset_name") or
                    original.get("transaction_date") is None or
                    original.get("amount_low") is None or
                    original.get("amount_high") is None or
                    original.get("reasons") != ["transaction_type_unsupported"]):
                raise OgeCatalogError("Trump September v6 type-only row changed")
            resolved = {"transaction_type": "purchase"}
        else:
            special = _TRUMP_SEPT_2026_PAGE7_SPECIAL[number]
            if (cells != special["cells"] or
                    original.get("asset_name") != (cells[1] or "") or
                    any(original.get(key) != value for key, value
                        in special["original"].items())):
                raise OgeCatalogError("Trump September v6 special row OCR changed")
            resolved = special["resolved"]
        corrected = {key: value for key, value in original.items() if key != "reasons"}
        corrected.update(resolved)
        corrected["source_bound_page7_correction"] = {
            "basis": "fixed_source_visual_page7_table",
            "page_number": 7,
            "printed_row_number": number,
            "original_reasons": list(original["reasons"]),
            "resolved_fields": dict(resolved),
        }
        promoted.append(corrected)

    remaining = [row for row in quarantined if row["extraction_id"] not in targets]
    if len(promoted) != 383 or len(remaining) != 773:
        raise OgeCatalogError("Trump September v6 disposition conservation failed")
    numbered = [row.get("row_number") for row in promoted + remaining]
    if any(sum(value == number for value in numbered) != 1
           for number in range(166, 199)):
        raise OgeCatalogError("Trump September v6 page-7 row labels are not unique")
    if {row["row_number"] for row in promoted + remaining
            if row["page_number"] == 7} != set(range(166, 199)):
        raise OgeCatalogError("Trump September v6 page-7 printed rows are incomplete")
    if [row["extraction_id"] for row in promoted + remaining] != \
            [row["extraction_id"] for row in transactions] + \
            [row["extraction_id"] for row in sorted(
                (by_id[identifier] for identifier in targets),
                key=lambda row: targets[row["extraction_id"]])] + \
            [row["extraction_id"] for row in remaining]:
        raise OgeCatalogError("Trump September v6 extraction IDs changed")
    return promoted, remaining


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


def _is_fixed_trump_september_source(metadata: dict, source_sha: str) -> bool:
    return (
        metadata.get("document_id") == TRUMP_SEPT_2026_DOCUMENT_ID
        and metadata.get("document_url") == TRUMP_SEPT_2026_SOURCE_URL
        and source_sha == TRUMP_SEPT_2026_SOURCE_SHA256
        and metadata.get("filer_name") == "Trump, Donald J"
        and metadata.get("agency") == "White House Office"
        and metadata.get("position_title") == "President"
    )


def load_reusable_extraction(root: Path, metadata_path: Path,
                             reuse_root: Path, *, generic: bool = False) -> dict | None:
    """Load a source- and parser-bound review result after re-hashing its PDF.

    Review data is a durable checkpoint, not a substitute for evidence checks.
    Every lookup first validates the immutable archive metadata, PDF envelope,
    byte length and SHA-256. A missing or malformed checkpoint is a cache miss
    so the caller can run the parser again.
    """

    metadata, _, source_sha = _read_archived_source(root, metadata_path)
    fixed_source = _is_fixed_trump_september_source(metadata, source_sha)
    parser_version = (
        GENERIC_PARSER_VERSION if generic else TRUMP_SEPT_2026_PAGE7_PASS_VERSION
        if fixed_source and source_sha == _TRUMP_SEPT_2026_PAGE7_SOURCE_SHA256
        else TRUMP_SEPT_2026_STRUCTURAL_PASS_VERSION
        if fixed_source and source_sha == _TRUMP_SEPT_2026_STRUCTURAL_SOURCE_SHA256
        else TRUMP_SEPT_2026_SECOND_PASS_VERSION if fixed_source
        else PARSER_VERSION
    )
    checkpoint = (reuse_root / metadata["document_id"] / source_sha /
                  f"{parser_version.replace('/', '-')}.json")
    if not checkpoint.is_file():
        return None
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
    if not generic and fixed_source and value.get("filing_date_evidence") != \
            TRUMP_SEPT_2026_FILING_DATE_EVIDENCE:
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
    fixed_source = not generic and _is_fixed_trump_september_source(metadata, source_sha)
    date_evidence = None
    if generic:
        date_evidence = read_278t_cover_dates(cover["text"] if cover else "", source_sha256=source_sha)
        filed_at = date_evidence["filed_at"]
        filing_reasons = date_evidence["document_reasons"][:]
        if cover is None:
            filing_reasons.append("cover_text_not_separately_observed")
    elif fixed_source:
        filed_at = TRUMP_SEPT_2026_FILING_DATE_EVIDENCE["filed_at"]
        filing_reasons = []
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
    if fixed_source:
        transactions, quarantined = _recover_fixed_trump_september_salo(
            transactions, quarantined)
        if source_sha == _TRUMP_SEPT_2026_STRUCTURAL_SOURCE_SHA256:
            transactions, quarantined = _recover_fixed_trump_september_structural_rows(
                transactions, quarantined, source_sha=source_sha)
        if source_sha == _TRUMP_SEPT_2026_PAGE7_SOURCE_SHA256:
            transactions, quarantined = _recover_fixed_trump_september_page7(
                transactions, quarantined, source_sha=source_sha)
    if not rows:
        filing_reasons.append("transaction_table_not_found")
    if not transactions and not quarantined:
        filing_reasons.append("no_transaction_rows_found")
    return {
        "schema_version": EXTRACTION_SCHEMA,
        "parser_version": (
            GENERIC_PARSER_VERSION if generic else TRUMP_SEPT_2026_PAGE7_PASS_VERSION
            if fixed_source and source_sha == _TRUMP_SEPT_2026_PAGE7_SOURCE_SHA256
            else TRUMP_SEPT_2026_STRUCTURAL_PASS_VERSION
            if fixed_source and source_sha == _TRUMP_SEPT_2026_STRUCTURAL_SOURCE_SHA256
            else TRUMP_SEPT_2026_SECOND_PASS_VERSION if fixed_source
            else PARSER_VERSION
        ),
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
        **({"filing_date_evidence": TRUMP_SEPT_2026_FILING_DATE_EVIDENCE}
           if fixed_source else {}),
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



def _native_amendment_context(observation: dict, source_url: str) -> dict:
    """Interpret a report reference, never a filer signature or an effective date."""
    from .oge_278t_page_shadow import interpret_native_exhibit_page
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
        component = re.search(r"\(([1-9][0-9]*)\)\s*$", title)
        url_reference = re.search(r"([0-9]{1,2})[.]([0-9]{1,2})[.]([0-9]{2}|[0-9]{4})-([1-9][0-9]*)[.]pdf$",
                                  unquote(url.path), re.I)
        label_filer = re.match(r"(.*?)\s+Periodic\s+Transaction\s+Report\b", title, re.I)
        if (not url_reference or not label_filer or
                metadata.get("document_id") != "wh-url:" + hashlib.sha256(metadata["document_url"].encode()).hexdigest()[:24] or
                _exhibit_identity_tokens(label_filer[1]) != _exhibit_identity_tokens(name)):
            continue
        dates = re.findall(r"(?<![0-9])([0-9]{1,2})[.]([0-9]{1,2})[.]([0-9]{2}|[0-9]{4})(?![0-9])", title)
        if (not component or int(component[1]) != context["original_report_component"] or len(dates) != 1 or
                int(url_reference[4]) != int(component[1]) or tuple(map(int, url_reference.groups()[:3])) != tuple(map(int, dates[0])) or
                _exhibit_identity_tokens(name) != _exhibit_identity_tokens(context["filer_name_raw"])):
            continue
        month, day, year = dates[0]
        try:
            report_date = datetime(int(year) + (2000 if len(year) == 2 else 0), int(month), int(day)).date()
        except ValueError:
            continue
        if report_date.isoformat() != context["original_report_date"]:
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


def _native_exhibit_target(label: dict, targets: list[dict], filer_name: str) -> dict:
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
        if not isinstance(row.get("cells"),list) or len(row["cells"])!=6:
            continue
        good,bad=parse_table_rows([(row["page_number"],row["cells"])],source_sha=sha,
            default_owner=row.get("owner","Undisclosed"),allow_open_amounts=True,allow_exchange=True,
            account_title=row.get("account_title"))
        if len(good+bad)!=1 or any((good+bad)[0].get(field)!=row.get(field) for field in
                ("extraction_id","owner","asset_name","ticker","transaction_type","transaction_date","amount_low","amount_high","amount_low_exclusive")):
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
        matches.append({"target_document_id":metadata["document_id"],"target_source_sha256":sha,
            "target_source_url":metadata["document_url"],"target_extraction_id":row["extraction_id"],
            "target_page_number":row["page_number"],"target_printed_number":label["target_printed_number"],
            "target_raw_row_basis":"unique_same_page_native_table_cells_reobserved_from_archive",
            "archive_metadata_sha256":hashlib.sha256(meta_path.read_bytes()).hexdigest(),
            "source_label_word_indices":label["word_indices"],"source_label_raw":label["text"]})
    if len(matches)!=1:
        return {"status":"pending","reasons":["replacement_original_source_or_unique_row_unverified"],
            "target_printed_number":label["target_printed_number"],"source_urls_observed":urls}
    return {"status":"bound_replacement_target","target":matches[0],
        "reasons":["replacement_application_requires_source_reconciliation"]}


def materialize_saved_oge_278t_exhibits(extraction: dict, catalog_record: dict, *, observations: list[dict],
        archived_pdf_path: str, replacement_targets: list[dict] | None = None,
        formal_reference_ids: list[str] | None = None,
        amendment_context_page_number: int | None = None) -> dict:
    """Consume fixed native observations in the existing observed-extraction lane.

    No cover is manufactured. Original unknown pages and unresolved amendment
    targets stay unknown; replacement rows never become additive new trades.
    """
    from copy import deepcopy
    from .oge_278t_page_shadow import observe_native_exhibit_page,interpret_native_exhibit_page
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
    rebuilt=[];seen_pages=set();page_count=None
    for item in observations:
        if not isinstance(item,dict) or item.get("source_sha256")!=extraction["source_sha256"] or type(item.get("page_number")) is not int or item.get("page_number") in seen_pages:
            raise OgeCatalogError("native exhibit observations cross sources or repeat a page")
        seen_pages.add(item["page_number"])
        # This is a bounded native read of the selected archived page only.
        actual=observe_native_exhibit_page(Path(archived_pdf_path),source_sha256=extraction["source_sha256"],page_number=item["page_number"])
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
            if page["page_number"] in overlapping_pages:
                receipts.append({**ref,"disposition":"unresolved","reasons":["native_exhibit_prior_inventory_relation_unresolved"]});continue
            if block["status"]!="replacement_row_candidate":
                receipts.append({**ref,"disposition":"unresolved","reasons":block["reasons"]});continue
            relation = (_native_exhibit_context_target(block["label"], targets, context, numbers, context_sources)
                        if context is not None and not block["label"].get("predecessor_urls") else
                        _native_exhibit_target(block["label"], targets, extraction["catalog_filer_name"]))
            ref["replacement_relation"]=relation
            cells=[block["raw_columns"][name] for name in names]
            good,bad=parse_table_rows([(page["page_number"],cells)],source_sha=extraction["source_sha256"],
                default_owner="Undisclosed",allow_open_amounts=True,allow_exchange=True)
            if len(good+bad)!=1:
                receipts.append({**ref,"disposition":"unresolved","reasons":["replacement_body_not_one_parser_row"]});continue
            row=(good+bad)[0]
            reasons=set(row.get("reasons",[])+relation["reasons"])
            if re.fullmatch(r"\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{2}",cells[3]):
                reasons.add("transaction_date_century_unbound")
            if block.get("partition_crossing_word_indices") or block.get("unassigned_body_word_indices"):
                reasons.add("native_header_field_partition_ambiguous")
            physical={"page_number":page["page_number"],"cells":cells,"native_exhibit_reference":ref}
            key=(physical["page_number"],tuple(cells))
            if key in prior_keys:
                raise OgeCatalogError("native exhibit row already exists in original source inventory")
            prior_keys.add(key)
            result["source_rows"].append(physical)
            result["quarantined"].append({**row,"reasons":sorted(reasons),"native_exhibit_reference":ref,
                "observation_reference":ref,"owner_disclosure_basis":"not_disclosed_in_bound_six_column_table"})
            receipts.append({**ref,"disposition":"quarantined","extraction_id":row["extraction_id"],"reasons":sorted(reasons)})
    if any(row["disposition"]=="quarantined" for row in receipts):
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
    return result
