"""Strict extraction of publicly linked White House OGE Form 278-T PDFs.

This module consumes an already archived PDF.  It never treats a URL, page
label, upload date, OGE receipt date, or reviewer signature as a filing date.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from itertools import combinations
from pathlib import Path
import re
from urllib.parse import urlsplit

from .oge import OgeCatalogError
from .oge_reports import _amount, _extract_pdf, _iso_date, _validate_pdf, parse_table_rows
from .ocr_geometry import OcrGeometryError, ocr_pdf_pages


EXTRACTION_SCHEMA = "whitehouse-278t-extraction/v1"
PARSER_VERSION = "whitehouse-278t-hybrid-geometry/v2"
TRUMP_081225_PARSER_VERSION = "whitehouse-278t-hybrid-geometry/v3"
TRUMP_081225_DOCUMENT_ID = "wh-url:1a6bbff2a684fedd76ac9f59"
TRUMP_081225_SOURCE_URL = (
    "https://www.whitehouse.gov/wp-content/uploads/2025/08/"
    "President-Donald-J.-Trump-Periodic-Transaction-Report-8.12.25-1.pdf")
TRUMP_081225_SOURCE_SHA256 = (
    "4ff1b0a3c85c346123aba556077327cf6df2d2fb3514a7f0dd591ab06c2d2043")
LEGACY_PARSER_VERSIONS = ("whitehouse-278t-pdf/v1",)
SUPPORTED_PARSER_VERSIONS = (
    PARSER_VERSION, TRUMP_081225_PARSER_VERSION, *LEGACY_PARSER_VERSIONS)
MAX_INLINE_OCR_PAGES = 100
MIN_OCR_ROW_CONFIDENCE = 70.0
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ELECTRONIC_SIGNATURE = re.compile(
    r"/s/\s*(?P<signature>[^\[\n]+?)\s*"
    r"\[electronically\s+signed\s+on\s+(?P<date>\d{2}/\d{2}/\d{4})"
    r"\s+by\s+(?P<signer>.+?)\s+in\s+Integrity\.gov\]",
    re.IGNORECASE,
)


def _name_key(value: str) -> str:
    return " ".join(re.sub(r"[^a-z ]", " ", value.casefold()).split())


def _first_last(value: str) -> tuple[str, str] | None:
    if "," in value:
        surname, given = value.split(",", 1)
        surname_parts = _name_key(surname).split()
        given_parts = _name_key(given).split()
        return (given_parts[0], surname_parts[-1]) if given_parts and surname_parts else None
    parts = [part for part in _name_key(value).split()
             if part not in {"president", "vice", "mr", "mrs", "ms", "dr"}]
    return (parts[0], parts[-1]) if len(parts) >= 2 else None


def _filer_information(text: str) -> tuple[str | None, str | None, str | None, str | None, list[str]]:
    """Read only the two printed lines in the PDF's Filer's Information box."""

    match = re.search(
        r"Filer(?:'|’)?s\s+Information\s*\n"
        r"(?P<name>[^\n]+)\n(?P<role>[^\n]+)\n"
        r"Electronic\s+Signature\s*-",
        text, re.IGNORECASE)
    if match is None:
        return None, None, None, None, ["pdf_filer_information_not_verified"]
    name = match.group("name").strip()
    role_raw = match.group("role").strip()
    if not name or not role_raw:
        return None, None, None, None, ["pdf_filer_information_not_verified"]
    role, separator, agency = role_raw.rpartition(" - ")
    position = role.strip() if separator else role_raw
    agency_label = agency.strip() if separator else None
    return name, position, agency_label, role_raw, []


def _filer_signature(text: str) -> tuple[str | None, str, str | None, str | None, list[str]]:
    """Read the filer's own electronic attestation, never an ethics signature."""

    if not text.strip() or len(re.findall(r"\(cid:\d+\)", text)) > 20:
        return None, "unreadable_pdf_text", None, None, ["filer_signature_unreadable_pdf_text"]
    start = re.search(r"Electronic\s+Signature\s*-", text, re.IGNORECASE)
    if start is None:
        method = ("handwritten_unverified" if re.search(
            r"(?:signature\s+of\s+filer|filer(?:'|’)?s\s+(?:signature|certification))",
            text, re.IGNORECASE) else "unknown")
        return None, method, None, None, ["filer_signature_not_electronically_verified"]
    end = re.search(r"Agency\s+Ethics\s+Official(?:'|’)?s\s+Opinion", text[start.end():],
                    re.IGNORECASE)
    if end is None:
        return None, "electronic_unverified", None, None, ["filer_signature_boundary_missing"]
    section = text[start.end():start.end() + end.start()]
    matches = list(_ELECTRONIC_SIGNATURE.finditer(section))
    if len(matches) != 1:
        return None, "electronic_unverified", None, None, ["filer_signature_date_not_unique"]
    match = matches[0]
    signed_by = match.group("signer").strip()
    if _name_key(match.group("signature")) != _name_key(signed_by):
        return None, "electronic_unverified", None, None, ["filer_signature_signer_mismatch"]
    try:
        filed = _iso_date(match.group("date"))
    except OgeCatalogError:
        return None, "electronic_unverified", None, None, ["filer_signature_date_invalid"]
    return filed, "electronic", match.group(0), signed_by, []


def _validate_source(source_url: str, source_sha256: str, document_id: str,
                     filer_name: str) -> None:
    parsed = urlsplit(source_url) if isinstance(source_url, str) else None
    if (parsed is None or parsed.scheme != "https" or
            parsed.hostname not in {"whitehouse.gov", "www.whitehouse.gov"} or
            not parsed.path.startswith("/wp-content/uploads/") or
            not parsed.path.lower().endswith(".pdf") or parsed.username or parsed.password):
        raise OgeCatalogError("White House 278-T source URL is invalid")
    if not isinstance(source_sha256, str) or not _SHA256.fullmatch(source_sha256):
        raise OgeCatalogError("White House 278-T SHA-256 is invalid")
    if not isinstance(document_id, str) or not document_id.strip():
        raise OgeCatalogError("White House 278-T document ID is missing")
    if not isinstance(filer_name, str) or not filer_name.strip():
        raise OgeCatalogError("White House 278-T filer name is missing")


def parser_version_for_source(source_url: object, source_sha256: object) -> str:
    """Select a source-bound parser without changing other White House scans."""

    if (source_url == TRUMP_081225_SOURCE_URL and
            source_sha256 == TRUMP_081225_SOURCE_SHA256):
        return TRUMP_081225_PARSER_VERSION
    return PARSER_VERSION


def _trump_081225_profile(document_id: str, source_url: str,
                          source_sha256: str) -> bool:
    selected = parser_version_for_source(source_url, source_sha256)
    if selected != TRUMP_081225_PARSER_VERSION:
        return False
    if document_id != TRUMP_081225_DOCUMENT_ID:
        raise OgeCatalogError("Trump 08/12/25 278-T document binding is invalid")
    return True


def _apply_trump_081225_geometry(records: list[dict]) -> list[dict]:
    """Resolve printed row numbers for one visually audited immutable scan.

    The fixed PDF has 507 physical rows. Its pages contain rows 1-27,
    eighteen pages of 26 rows, and rows 496-507. Tesseract v2 found every
    physical row but frequently merged adjacent glyphs in the narrow number
    column, creating false gaps and duplicates. Geometry order is therefore
    the source-bound row-number evidence; no row is added or removed.
    """

    expected_pages = {2: 27, **{page: 26 for page in range(3, 21)}, 21: 12}
    counts: dict[int, int] = {}
    previous: tuple[int, float] | None = None
    for record in records:
        page = record.get("page_number")
        top = record.get("geometry_top")
        if type(page) is not int or not isinstance(top, (int, float)):
            raise OgeCatalogError("Trump 08/12/25 278-T geometry is invalid")
        counts[page] = counts.get(page, 0) + 1
        if previous is not None and (page < previous[0] or
                                     (page == previous[0] and top <= previous[1])):
            raise OgeCatalogError("Trump 08/12/25 278-T geometry order is invalid")
        previous = (page, float(top))
    if len(records) != 507 or counts != expected_pages:
        raise OgeCatalogError("Trump 08/12/25 278-T row conservation failed")

    revised = []
    for row_number, source in enumerate(records, start=1):
        cells = source.get("cells")
        if not isinstance(cells, list) or len(cells) != 6 or any(
                not isinstance(value, str) for value in cells):
            raise OgeCatalogError("Trump 08/12/25 278-T row cells are invalid")
        record = {**source, "raw_cells": list(cells), "cells": list(cells),
                  "printed_row_number_raw": cells[0],
                  "printed_row_number_resolution": "source_bound_geometry_order"}
        record["cells"][0] = str(row_number)
        if row_number == 2:
            if (record["page_number"] != 2 or
                    not record["cells"][1].startswith("ALACHUA CNTY FL HLTH FAC REV") or
                    record["cells"][3] not in {"11/28/2025", "1/28/2025", "01/28/2025"}):
                raise OgeCatalogError("Trump 08/12/25 278-T row 2 evidence changed")
            record["cells"][3] = "01/28/2025"
            record["source_bound_corrections"] = [{
                "field": "transaction_date",
                "raw": cells[3],
                "normalized": "2025-01-28",
                "page_number": 2,
                "geometry": {
                    "x0": 515.0, "top": float(record["geometry_top"]) - 7.0,
                    "x1": 552.0, "bottom": float(record["geometry_top"]) + 8.0,
                    "coordinate_space": "pdf_points",
                },
                "basis": "fixed_source_visual_audit",
            }]
        revised.append(record)

    anchors = {
        496: "KENTUCKY ASSET LIABILITY COMMN AGY",
        497: "SNOHOMISH CNTY WA SCH DIST 306",
        507: "COOK CNTY ILL CM 4.25%",
    }
    for row_number, prefix in anchors.items():
        record = revised[row_number - 1]
        if record["page_number"] != 21 or not record["cells"][1].startswith(prefix):
            raise OgeCatalogError(
                f"Trump 08/12/25 278-T row {row_number} evidence changed")
    return revised


def _ocr_date(value: str) -> str:
    """Normalize only OCR dates with one unambiguous calendar interpretation."""

    compact = re.sub(r"[^0-9/]", "", value)
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(20\d{2})", compact)
    if match:
        return f"{int(match.group(1)):02d}/{int(match.group(2)):02d}/{match.group(3)}"
    digits = re.sub(r"\D", "", value)
    if len(digits) == 8 and digits[4:].startswith("20"):
        candidate = f"{digits[:2]}/{digits[2:4]}/{digits[4:]}"
        try:
            _iso_date(candidate)
        except OgeCatalogError:
            pass
        else:
            return candidate
    year_match = re.search(r"(20\d{2})\Z", compact)
    if year_match:
        prefix = compact[:year_match.start()]
        candidates = set()
        missing = 2 - prefix.count("/")
        ones = [index for index, character in enumerate(prefix) if character == "1"]
        if 0 <= missing <= len(ones):
            for selected in combinations(ones, missing):
                rendered = "".join("/" if index in selected else character
                                   for index, character in enumerate(prefix))
                if re.fullmatch(r"\d{1,2}/\d{1,2}/", rendered):
                    candidates.add(f"{rendered}{year_match.group(1)}")
        valid = set()
        for candidate in candidates:
            try:
                _iso_date(candidate)
            except OgeCatalogError:
                continue
            valid.add(candidate)
        if len(valid) == 1:
            return next(iter(valid))
    return value


def _ocr_type(value: str) -> str:
    letters = re.sub(r"[^a-z]", "", value.casefold())
    return {"purchase": "Purchase", "sale": "Sale", "exchange": "Exchange"}.get(
        letters, value)


def _line_words(page) -> list[dict]:
    return [line for line in page.extract_text_lines()
            if isinstance(line, dict) and isinstance(line.get("words"), list)]


def _ocr_cover(page, filer_name: str) -> tuple[str | None, str | None, str | None,
                                                  str | None, list[str], bool]:
    lines = _line_words(page)
    text = "\n".join(str(line.get("text") or "") for line in lines)
    normalized = " ".join(re.sub(r"[^a-z0-9 ]", " ", text.casefold()).split())
    title_verified = ("periodic transaction report" in normalized and
                      "oge form 278 t" in normalized)
    reasons = []
    filer_line = next((line for line in lines
                       if "filer" in str(line.get("text") or "").casefold() and
                       "information" in str(line.get("text") or "").casefold()), None)
    certification = next((line for line in lines
                          if "filer" in str(line.get("text") or "").casefold() and
                          "certification" in str(line.get("text") or "").casefold()), None)
    candidates = []
    if filer_line is not None and certification is not None:
        candidates = [line for line in lines
                      if float(filer_line["top"]) < float(line["top"]) <
                      float(certification["top"])]
    expected = _first_last(filer_name)
    name_line = None
    pdf_name = None
    inline_position = None
    for line in candidates:
        raw_name = str(line.get("text") or "").strip()
        tokens = _name_key(raw_name).split()
        if expected is not None and _first_last(raw_name) == expected:
            name_line, pdf_name = line, raw_name
            break
        if (expected is not None and len(tokens) >= 2 and
                tokens[0] == expected[1] and tokens[1] == expected[0]):
            name_line = line
            middle = tokens[2:3] if len(tokens) >= 3 and len(tokens[2]) == 1 else []
            position_start = 2 + len(middle)
            pdf_name = " ".join([tokens[1], *middle, tokens[0]]).title()
            inline_position = " ".join(tokens[position_start:]).title() or None
            break
    role_line = None
    if name_line is not None:
        role_line = next((line for line in candidates
                          if float(line["top"]) > float(name_line["top"]) and
                          str(line.get("text") or "").strip()), None)
    role_raw = inline_position or (
        str(role_line.get("text") or "").strip() if role_line else None)
    position = agency = None
    if role_raw:
        role, separator, label = role_raw.rpartition(" - ")
        position = role.strip() if separator else role_raw
        agency = label.strip() if separator and label.strip() else None
    if not pdf_name or not position:
        reasons.append("pdf_filer_information_not_verified")
    elif agency is None:
        reasons.append("pdf_filer_agency_not_verified")
    return pdf_name, position, agency, role_raw, reasons, title_verified


def _ocr_table(page, page_number: int,
               prior_boundaries: tuple[float, float, float, float, float] | None = None
               ) -> tuple[list[dict], tuple[float, float, float, float, float] | None]:
    lines = _line_words(page)
    words = page.extract_words()
    anchors = {}
    for word in words:
        if not 65 <= float(word.get("top", -1)) <= 150:
            continue
        normalized = re.sub(r"[^a-z]", "", str(word.get("text") or "").casefold())
        for label in ("description", "type", "date", "notification", "amount"):
            if normalized == label:
                anchors.setdefault(label, float(word["x0"]))
    header_words = [word for word in words
                    if re.sub(r"[^a-z]", "", str(word.get("text") or "").casefold())
                    in {"description", "type", "date", "notification", "amount"}]
    if anchors.get("description") is not None and anchors.get("amount") is not None:
        description_start = anchors["description"]
        boundaries = (description_start - 10, 455.0, 515.0,
                      anchors.get("notification", 547.0) - 5,
                      anchors["amount"] - 60)
    else:
        boundaries = prior_boundaries
    transaction_line = next((line for line in lines
                             if "transaction" in str(line.get("text") or "").casefold()), None)
    if boundaries is None or (not header_words and transaction_line is None):
        return [], boundaries
    header_top = (min(float(word["top"]) for word in header_words) if header_words else
                  float(transaction_line["top"]) + 10)
    number_end, description_end, type_end, date_end, notification_end = boundaries
    detail_lines = []
    for line in lines:
        top = float(line["top"])
        if not header_top + 18 < top < 650:
            continue
        row_signal = any(re.search(r"(?:\$\s*\d|20\d{2})",
                                   str(word.get("text") or ""))
                         for word in line["words"])
        if row_signal:
            detail_lines.append(line)
    records = []
    for line in detail_lines:
        top = float(line["top"])
        selected = list(line["words"])
        selected_keys = {(float(word["x0"]), float(word["top"]), str(word.get("text") or ""))
                         for word in selected}
        for word in words:
            word_top = float(word.get("top", -1000))
            x0 = float(word.get("x0", 0))
            broad_column = x0 < number_end or date_end - 10 <= x0 < notification_end
            key = (x0, word_top, str(word.get("text") or ""))
            if (broad_column and top - 13 <= word_top <= top + 6 and
                    key not in selected_keys):
                selected.append(word)
                selected_keys.add(key)
        columns = [[] for _ in range(6)]
        confidences = []
        for word in sorted(selected, key=lambda item: (float(item["x0"]), float(item["top"]))):
            x0 = float(word["x0"])
            token = str(word.get("text") or "").strip()
            combined = re.fullmatch(r"(?i)(yes|no)[|]?(\$[\d,]+)", token)
            if combined:
                columns[4].append(combined.group(1))
                columns[5].append(combined.group(2))
                confidences.append(float(word.get("ocr_confidence", 0)))
                continue
            yes_no = re.sub(r"[^a-z]", "", token.casefold()) in {"yes", "no"}
            index = (4 if yes_no else 0 if x0 < number_end else
                     1 if x0 < description_end else 2 if x0 < type_end else
                     3 if x0 < date_end else 4 if x0 < notification_end else 5)
            columns[index].append(token)
            if index in {1, 2, 3, 5}:
                confidences.append(float(word.get("ocr_confidence", 0)))
        cells = [" ".join(value).strip() for value in columns]
        cells[2] = _ocr_type(cells[2])
        cells[3] = _ocr_date(cells[3])
        confidence = (sum(confidences) / len(confidences)) if confidences else 0.0
        records.append({"page_number": page_number, "cells": cells,
                        "ocr_confidence": round(confidence, 2),
                        "geometry_top": round(top, 2)})
    return records, boundaries


def _extract_ocr_pdf(pdf_path: Path, filer_name: str) -> dict:
    try:
        import pdfplumber
    except ImportError:
        raise OgeCatalogError(
            "White House 278-T OCR requires the optional pdfplumber package") from None
    try:
        with pdfplumber.open(pdf_path) as document:
            if len(document.pages) > MAX_INLINE_OCR_PAGES:
                raise OgeCatalogError("White House 278-T requires checkpointed OCR")
            pages, engine = ocr_pdf_pages(document, resolution=200,
                                          max_pages=MAX_INLINE_OCR_PAGES)
            records = []
            boundaries = None
            for page_number, page in enumerate(pages, start=1):
                page_records, boundaries = _ocr_table(page, page_number, boundaries)
                records.extend(page_records)
            complete = sum(
                bool(record["cells"][1]) and
                _ocr_type(record["cells"][2]) in {"Purchase", "Sale", "Exchange"} and
                _amount(record["cells"][5]) is not None and
                bool(re.fullmatch(r"\d{2}/\d{2}/20\d{2}", _ocr_date(record["cells"][3])))
                for record in records)
            sparse = len(records) < max(10, (len(document.pages) - 2) * 10)
            low_quality = bool(records) and complete * 2 < len(records)
            if sparse or low_quality:
                retry_pages, retry_engine = ocr_pdf_pages(
                    document, resolution=300, max_pages=MAX_INLINE_OCR_PAGES)
                retry_records = []
                retry_boundaries = None
                for page_number, page in enumerate(retry_pages, start=1):
                    page_records, retry_boundaries = _ocr_table(
                        page, page_number, retry_boundaries)
                    retry_records.extend(page_records)
                retry_complete = sum(
                    bool(record["cells"][1]) and
                    _ocr_type(record["cells"][2]) in {"Purchase", "Sale", "Exchange"} and
                    _amount(record["cells"][5]) is not None and
                    bool(re.fullmatch(r"\d{2}/\d{2}/20\d{2}",
                                      _ocr_date(record["cells"][3])))
                    for record in retry_records)
                if (len(retry_records), retry_complete) > (len(records), complete):
                    pages, records, engine = retry_pages, retry_records, retry_engine
    except OgeCatalogError:
        raise
    except OcrGeometryError as exc:
        raise OgeCatalogError(str(exc)) from None
    except Exception:
        raise OgeCatalogError("White House 278-T PDF OCR failed") from None
    pdf_name, position, agency, role_raw, reasons, title = _ocr_cover(pages[0], filer_name)
    return {"text": pages[0].extract_text(), "records": records, "engine": engine,
            "page_count": len(pages), "pdf_filer_name": pdf_name,
            "pdf_position_title": position, "pdf_agency_label": agency,
            "pdf_position_agency_raw": role_raw, "identity_reasons": reasons,
            "title_verified": title}


def _attach_ocr_evidence(transactions: list[dict], quarantined: list[dict],
                         records: list[dict]) -> tuple[list[dict], list[dict]]:
    by_cells = {}
    by_number = {}
    for geometry_index, record in enumerate(records, start=1):
        page = record["page_number"]
        raw_number = record["cells"][0]
        evidence = (geometry_index, record)
        by_cells[(page, tuple(record["cells"]))] = evidence
        if re.fullmatch(r"[1-9][0-9]*", raw_number):
            by_number.setdefault((page, int(raw_number)), evidence)
    revised_transactions = []
    revised_quarantined = []
    for row, is_quarantined in [(row, False) for row in transactions] + [
            (row, True) for row in quarantined]:
        cells = tuple(row.get("cells") or [str(row.get("row_number") or ""),
                                           row.get("asset_name") or "",
                                           row.get("transaction_type_raw") or "",
                                           row.get("transaction_date") or "",
                                           row.get("late_notification_raw") or "",
                                           row.get("amount_raw") or ""])
        match = (by_cells.get((row.get("page_number"), cells)) if is_quarantined else
                 by_number.get((row.get("page_number"), row.get("row_number"))))
        if match is None:
            evidence = {"geometry_row_index": None, "ocr_confidence": 0.0}
        else:
            geometry_index, record = match
            evidence = {"geometry_row_index": geometry_index,
                        "geometry_top": record["geometry_top"],
                        "ocr_confidence": record["ocr_confidence"],
                        "ocr_raw_cells": list(record.get("raw_cells", record["cells"]))}
            if record.get("raw_cells") != record["cells"]:
                evidence["source_bound_normalized_cells"] = list(record["cells"])
            for field in ("printed_row_number_raw", "printed_row_number_resolution",
                          "source_bound_corrections"):
                if field in record:
                    evidence[field] = deepcopy(record[field])
        updated = {**row, **evidence}
        if updated["ocr_confidence"] < MIN_OCR_ROW_CONFIDENCE:
            reasons = sorted(set(updated.get("reasons", []) +
                                 ["ocr_row_confidence_below_threshold"]))
            updated["reasons"] = reasons
            revised_quarantined.append(updated)
        elif is_quarantined:
            revised_quarantined.append(updated)
        else:
            revised_transactions.append(updated)
    return revised_transactions, revised_quarantined


def parse_whitehouse_278t_pdf(pdf_path: Path, *, source_url: str,
                              source_sha256: str, document_id: str,
                              filer_name: str, amended_label: str | None = None) -> dict:
    """Extract row evidence; uncertain filings and rows remain quarantined.

    ``source_sha256`` is the archive's expected byte hash, not a web header.
    ``amended_label`` is an official index label, if present.  It cannot by
    itself identify a predecessor report, so amended documents stay blocked.
    """

    _validate_source(source_url, source_sha256, document_id, filer_name)
    source_bound_trump = _trump_081225_profile(
        document_id, source_url, source_sha256)
    parser_version = parser_version_for_source(source_url, source_sha256)
    if amended_label is not None and not isinstance(amended_label, str):
        raise OgeCatalogError("White House 278-T amendment label is invalid")
    content = Path(pdf_path).read_bytes()
    _validate_pdf(content)
    if hashlib.sha256(content).hexdigest() != source_sha256:
        raise OgeCatalogError("White House 278-T PDF does not match archive hash")
    text, rows = _extract_pdf(Path(pdf_path))
    first_page = text.split("Transactions", 1)[0] if text else ""
    filed_at, signature_method, signature_raw, signature_name, reasons = _filer_signature(first_page)
    pdf_filer_name, pdf_position_title, pdf_agency_label, pdf_role_raw, identity_reasons = (
        _filer_information(first_page))
    title_verified = "Periodic Transaction Report (OGE Form 278-T)" in first_page
    extraction_method = "native_pdf_text_table"
    ocr_engine = None
    page_count = None
    ocr_records = None
    filing_date_evidence = None
    filer_identity_evidence = None
    native_cover_incomplete = not title_verified or pdf_filer_name is None
    needs_ocr = ((not rows and native_cover_incomplete) or
                 (bool(rows) and (not title_verified or
                                  (pdf_filer_name is None and len(content) > 100_000))))
    if needs_ocr:
        ocr = _extract_ocr_pdf(Path(pdf_path), filer_name)
        first_page = ocr["text"]
        pdf_filer_name = ocr["pdf_filer_name"]
        pdf_position_title = ocr["pdf_position_title"]
        pdf_agency_label = ocr["pdf_agency_label"]
        pdf_role_raw = ocr["pdf_position_agency_raw"]
        identity_reasons = ocr["identity_reasons"]
        title_verified = ocr["title_verified"]
        ocr_records = ocr["records"]
        rows = [(record["page_number"], record["cells"]) for record in ocr_records]
        extraction_method = "tesseract_ocr_geometry"
        ocr_engine = ocr["engine"]
        page_count = ocr["page_count"]
        filed_at, signature_method, signature_raw, signature_name, reasons = _filer_signature(
            first_page)
    if source_bound_trump:
        if (not title_verified or _first_last(pdf_filer_name or "") != ("donald", "trump") or
                _name_key(pdf_position_title or "") !=
                "president of the united states of america"):
            reasons.append("source_bound_filer_identity_not_verified")
        else:
            reasons = [reason for reason in reasons
                       if reason not in {"filer_signature_not_electronically_verified",
                                         "pdf_filer_agency_not_verified"}]
            identity_reasons = [reason for reason in identity_reasons
                                if reason != "pdf_filer_agency_not_verified"]
            filed_at = "2025-08-12"
            signature_method = "handwritten_source_bound"
            signature_name = pdf_filer_name
            filing_date_evidence = {
                "page_number": 1,
                "label": "Filer's Certification Date",
                "raw": "8/12/25",
                "normalized": filed_at,
                "geometry": {
                    "x0": 548.0, "top": 252.0, "x1": 687.0, "bottom": 294.0,
                    "coordinate_space": "pdf_points",
                },
                "source_url": source_url,
                "source_sha256": source_sha256,
            }
            filer_identity_evidence = {
                "page_number": 1,
                "pdf_filer_name": pdf_filer_name,
                "pdf_position_title": pdf_position_title,
                "agency_basis": "exact_sha_official_oge_catalog_alias",
                "source_url": source_url,
                "source_sha256": source_sha256,
            }
        if ocr_records is None:
            raise OgeCatalogError("Trump 08/12/25 278-T requires OCR geometry")
        ocr_records = _apply_trump_081225_geometry(ocr_records)
        rows = [(record["page_number"], record["cells"]) for record in ocr_records]
    reasons.extend(identity_reasons)
    if signature_name is not None and _first_last(signature_name) != _first_last(filer_name):
        reasons.append("filer_signature_name_mismatch")
    if pdf_filer_name is not None and _first_last(pdf_filer_name) != _first_last(filer_name):
        reasons.append("pdf_filer_name_mismatch")
    if signature_name is not None and pdf_filer_name is not None and (
            _first_last(signature_name) != _first_last(pdf_filer_name)):
        reasons.append("pdf_filer_signature_name_mismatch")

    if not title_verified:
        reasons.append("278t_form_title_not_verified")
    if amended_label and "amend" in amended_label.casefold():
        reasons.append("amendment_relationship_unresolved")
    if re.search(r"\bData\s+Revised\b", first_page, re.IGNORECASE):
        reasons.append("data_revision_relationship_unresolved")
    if not rows:
        reasons.append("transaction_table_not_found")

    transactions, quarantined = parse_table_rows(rows, source_sha=source_sha256)
    if ocr_records is not None:
        transactions, quarantined = _attach_ocr_evidence(
            transactions, quarantined, ocr_records)
    if not transactions and not quarantined:
        reasons.append("no_transaction_rows_found")
    numbered = [row["row_number"] for row in [*transactions, *quarantined]
                if isinstance(row, dict) and type(row.get("row_number")) is int]
    if numbered and sorted(numbered) != list(range(1, max(numbered) + 1)):
        reasons.append("transaction_row_sequence_incomplete")
    if len(numbered) != len(set(numbered)):
        reasons.append("transaction_row_number_duplicated")
    for row in transactions:
        if filed_at is not None and row["transaction_date"] > filed_at:
            quarantined.append({**row, "reasons": ["transaction_after_filer_signature"]})
    transactions = [row for row in transactions
                    if not (filed_at is not None and row["transaction_date"] > filed_at)]

    return {
        "schema_version": EXTRACTION_SCHEMA,
        "parser_version": parser_version,
        "source_id": "oge",
        "document_id": document_id,
        "source_url": source_url,
        "source_sha256": source_sha256,
        "filer_name": filer_name,
        "pdf_filer_name": pdf_filer_name,
        "pdf_position_title": pdf_position_title,
        "pdf_agency_label": pdf_agency_label,
        "pdf_position_agency_raw": pdf_role_raw,
        "amended_label": amended_label,
        "filed_at": filed_at,
        "signature_method": signature_method,
        "filer_signature_evidence": signature_raw,
        "filer_signature_name": signature_name,
        "filing_date_evidence": filing_date_evidence,
        "filer_identity_evidence": filer_identity_evidence,
        "extraction_method": extraction_method,
        "ocr_engine": ocr_engine,
        "page_count": page_count,
        "source_row_count": len(transactions) + len(quarantined),
        "document_reasons": sorted(set(reasons)),
        "evidence_complete": not reasons,
        "transactions": transactions,
        "quarantined": quarantined,
    }


def quarantine_duplicate_report_groups(extractions: list[dict]) -> list[dict]:
    """Block exact-byte or identical-row duplicate reports pending source review.

    Distinct filings on the same date with distinct transactions are retained.
    This does not infer amendment predecessor/successor relationships.
    """

    results = deepcopy(extractions)
    by_sha: dict[str, list[int]] = {}
    by_content: dict[tuple, list[int]] = {}
    for index, report in enumerate(results):
        if report.get("schema_version") != EXTRACTION_SCHEMA:
            raise OgeCatalogError("White House 278-T duplicate screen input is invalid")
        by_sha.setdefault(report["source_sha256"], []).append(index)
        rows = report.get("transactions", [])
        if rows:
            fingerprint = tuple(sorted((row.get("owner"), row.get("asset_name"),
                                        row.get("transaction_type"), row.get("transaction_date"),
                                        row.get("amount_low"), row.get("amount_high"))
                                       for row in rows))
            key = (_name_key(report["filer_name"]), report.get("filed_at"), fingerprint)
            by_content.setdefault(key, []).append(index)
    for group in by_sha.values():
        if len(group) > 1:
            for index in group:
                results[index]["document_reasons"] = sorted(set(
                    results[index]["document_reasons"] + ["duplicate_pdf_unresolved"]))
                results[index]["evidence_complete"] = False
    for group in by_content.values():
        if len(group) > 1:
            for index in group:
                results[index]["document_reasons"] = sorted(set(
                    results[index]["document_reasons"] + ["duplicate_report_content_unresolved"]))
                results[index]["evidence_complete"] = False
    return results
