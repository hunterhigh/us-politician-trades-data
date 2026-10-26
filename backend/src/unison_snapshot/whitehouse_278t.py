"""Strict extraction of publicly linked White House OGE Form 278-T PDFs.

This module consumes an already archived PDF.  It never treats a URL, page
label, upload date, OGE receipt date, or reviewer signature as a filing date.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
from itertools import combinations
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from .oge import OgeCatalogError
from .oge_reports import _amount, _extract_pdf, _iso_date, _validate_pdf, parse_table_rows
from .ocr_geometry import OcrGeometryError, OcrPage, ocr_pdf_pages


EXTRACTION_SCHEMA = "whitehouse-278t-extraction/v1"
PARSER_VERSION = "whitehouse-278t-hybrid-geometry/v2"
GENERIC_PARSER_VERSION = "whitehouse-278t-native-roles/v5"
NATIVE_VISIBLE_DEFAULT_VERSION = "whitehouse-278t-native-visible-default/v1"
NATIVE_VISIBLE_GENERIC_VERSION = "whitehouse-278t-native-visible-generic/v1"
NATIVE_VISIBLE_PARSER_VERSIONS = {NATIVE_VISIBLE_DEFAULT_VERSION, NATIVE_VISIBLE_GENERIC_VERSION}
SAVED_OCR_PARSER_VERSION = "whitehouse-278t-saved-ocr-roles/v6"
DUAL_AXIS_PARSER_VERSION = "whitehouse-278t-saved-ocr-dual-axis/v7"
DUAL_AXIS_MODE = "source_pdf_dual_axis/v1"
SAVED_OCR_PARSER_VERSIONS = {SAVED_OCR_PARSER_VERSION,DUAL_AXIS_PARSER_VERSION}
GENERIC_PARSER_VERSIONS = {GENERIC_PARSER_VERSION,*SAVED_OCR_PARSER_VERSIONS,*NATIVE_VISIBLE_PARSER_VERSIONS}
_SAVED_OCR_ROOTS = ContextVar("whitehouse_saved_ocr_roots", default=None)


@contextmanager
def saved_ocr_source_roots(evidence_root: Path | None, review_root: Path | None):
    """Bind controlled roots for one run; roots are never serialized receipts."""
    token = _SAVED_OCR_ROOTS.set((Path(evidence_root).resolve() if evidence_root else None,
                                Path(review_root).resolve() if review_root else None))
    try:
        yield
    finally:
        _SAVED_OCR_ROOTS.reset(token)


def _saved_source_path(value: str, *, evidence: bool = False) -> Path:
    if not isinstance(value, str) or not value:
        raise OgeCatalogError("White House saved source path is invalid")
    path = Path(value)
    if path.is_absolute():
        return path  # Local development compatibility, explicitly marked in receipt.
    roots = _SAVED_OCR_ROOTS.get()
    root = roots[0 if evidence else 1] if roots else None
    if root is None:
        raise OgeCatalogError("White House relative saved source requires explicit evidence/review roots")
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise OgeCatalogError("White House saved source escapes controlled root")
    return target
TRUMP_081225_PARSER_VERSION = "whitehouse-278t-hybrid-geometry/v3"
TRUMP_081225_DOCUMENT_ID = "wh-url:1a6bbff2a684fedd76ac9f59"
TRUMP_081225_SOURCE_URL = (
    "https://www.whitehouse.gov/wp-content/uploads/2025/08/"
    "President-Donald-J.-Trump-Periodic-Transaction-Report-8.12.25-1.pdf")
TRUMP_081225_SOURCE_SHA256 = (
    "4ff1b0a3c85c346123aba556077327cf6df2d2fb3514a7f0dd591ab06c2d2043")
TRUMP_2026_PARSER_VERSION = "whitehouse-278t-hybrid-geometry/v4"
TRUMP_2026_PROFILES = (
    {"document_id": "wh-url:62340683e32b0263e8f0eb20",
     "source_sha256": "3d2f74d9cc5f1a2a7819e90aea17357b40ddbca9839bf89c8b3f047c6f2e33ce",
     "source_url": "https://www.whitehouse.gov/wp-content/uploads/2026/01/President-Donald-J.-Trump-Periodic-Transaction-Report-1.14.2026-.pdf",
     "report_date": "2026-01-14", "report_date_raw": "01.14.26", "page_count": 8,
     "v2_source_row_count": 191},
    {"document_id": "wh-url:67a0a1742a0d66c5b2ac23c1",
     "source_sha256": "ef2d7f9874d17dee879eae4c2b53d2100d78af42435048ab67f783242daae249",
     "source_url": "https://www.whitehouse.gov/wp-content/uploads/2026/01/President-Donald-J.-Trump-Periodic-Transaction-Report-Amendment-1.14.26.pdf",
     "report_date": "2026-01-14", "report_date_raw": "01.14.26", "page_count": 10,
     "v2_source_row_count": 2},
    {"document_id": "wh-url:8a81a4571e085ccd51a0e4e6",
     "source_sha256": "3eab1c525446546dc921ef4bdc0a60867e7a7b184fc1a8a76cb7be8d4acbdab8",
     "source_url": "https://www.whitehouse.gov/wp-content/uploads/2026/03/President-Donald-J.-Trump-Periodic-Transaction-Report-2.26.26-1.pdf",
     "report_date": "2026-02-26", "report_date_raw": "02.26.26", "page_count": 8,
     "v2_source_row_count": 38},
    {"document_id": "wh-url:2e543c867ad080888580fa12",
     "source_sha256": "795a8dd77ccbf70df3b907cb2b3130ee68a7b2a4d78ccd7b4b59ad0db1569db2",
     "source_url": "https://www.whitehouse.gov/wp-content/uploads/2026/03/President-Donald-J.-Trump-Periodic-Transaction-Report-2.26.26-2.pdf",
     "report_date": "2026-02-26", "report_date_raw": "02.26.26", "page_count": 5,
     "v2_source_row_count": 83},
    {"document_id": "wh-url:82a263659dcbd44a6522ecbc",
     "source_sha256": "ecadf5c04b463f4f96dbf01371282e4c0e24d57aee94a4e2cc723a6c87988c31",
     "source_url": "https://www.whitehouse.gov/wp-content/uploads/2026/04/President-Donald-J.-Trump-Periodic-Transaction-Report-4.20.26.pdf",
     "report_date": "2026-04-20", "report_date_raw": "04.20.26", "page_count": 8,
     "v2_source_row_count": 174},
    {"document_id": "wh-url:11b77506f8e46ae1a83b0531",
     "source_sha256": "b801eaa58cca980c9c9437ea712880c4c4ec612df1461013662c1f2bc58d3e85",
     "source_url": "https://www.whitehouse.gov/wp-content/uploads/2026/05/President-Donald-J.-Trump-Periodic-Transaction-Report-05.08.26-1.pdf",
     "report_date": "2026-05-08", "report_date_raw": "05.08.26", "page_count": 113,
     "v2_source_row_count": None},
    {"document_id": "wh-url:6b1be988199005fdd79af8de",
     "source_sha256": "6985cb0f5c3139fc955dc4fe0b4b084b47fb6e97d3a82bc57903f9ce8a229fc9",
     "source_url": "https://www.whitehouse.gov/wp-content/uploads/2026/05/President-Donald-J.-Trump-Periodic-Transaction-Report-05.08.26-2.pdf",
     "report_date": "2026-05-08", "report_date_raw": "05.08.26", "page_count": 5,
     "v2_source_row_count": 34},
    {"document_id": "wh-url:7e17c4be2b42f563d37df173",
     "source_sha256": "a058a3bfc12a1d4e0ab9cfd8b16ac98c1b92ec713ec9d09882319cb9f722668b",
     "source_url": "https://www.whitehouse.gov/wp-content/uploads/2026/06/President-Donald-J.-Trump-Periodic-Transaction-Report-0.6.25.26-1.pdf",
     "report_date": "2026-06-25", "report_date_raw": "06.25.26", "page_count": 44,
     "v2_source_row_count": 1375},
    {"document_id": "wh-url:01475dee0bcffa4e79f7f58c",
     "source_sha256": "fca15b5424b1d4591373739aef12955e1375eff7bcac3443ee1d57d0fb610b0a",
     "source_url": "https://www.whitehouse.gov/wp-content/uploads/2026/06/President-Donald-J.-Trump-Periodic-Transaction-Report-0.6.25.26-2.pdf",
     "report_date": "2026-06-25", "report_date_raw": "06.25.26", "page_count": 37,
     "v2_source_row_count": 1149},
    {"document_id": "wh-url:5e95b7609861d78aa29e3d13",
     "source_sha256": "e1ec58ef09d3046f6e2741533df80fe88a1210b3b5c2eaa7a7c74c850bda58ed",
     "source_url": "https://www.whitehouse.gov/wp-content/uploads/2026/08/President-Donald-J.-Trump-Periodic-Transaction-Report-08.12.26.pdf",
     "report_date": "2026-08-12", "report_date_raw": "08.12.26", "page_count": 34,
     "v2_source_row_count": 1047},
)
_TRUMP_2026_BY_SOURCE = {
    (profile["source_url"], profile["source_sha256"]): profile
    for profile in TRUMP_2026_PROFILES
}
LEGACY_PARSER_VERSIONS = ("whitehouse-278t-pdf/v1",)
HISTORICAL_PARSER_VERSIONS = (
    PARSER_VERSION, TRUMP_081225_PARSER_VERSION, TRUMP_2026_PARSER_VERSION,
    *LEGACY_PARSER_VERSIONS, GENERIC_PARSER_VERSION, SAVED_OCR_PARSER_VERSION, DUAL_AXIS_PARSER_VERSION)
SUPPORTED_PARSER_VERSIONS = (*HISTORICAL_PARSER_VERSIONS, *sorted(NATIVE_VISIBLE_PARSER_VERSIONS))
MAX_INLINE_OCR_PAGES = 100
MAX_CHECKPOINT_OCR_PAGES = 200
OCR_CHECKPOINT_SCHEMA = "whitehouse-278t-ocr-checkpoint/v1"
OCR_SHARD_SCHEMA = "whitehouse-278t-ocr-geometry-shard/v1"
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
    if (source_url, source_sha256) in _TRUMP_2026_BY_SOURCE:
        return TRUMP_2026_PARSER_VERSION
    return PARSER_VERSION


def trump_2026_profile(document_id: object, source_url: object,
                       source_sha256: object) -> dict | None:
    """Return one immutable 2026 Trump source profile or fail a bad ID binding."""

    profile = _TRUMP_2026_BY_SOURCE.get((source_url, source_sha256))
    if profile is None:
        return None
    if document_id != profile["document_id"]:
        raise OgeCatalogError("Trump 2026 278-T document binding is invalid")
    return profile


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


class TradeOcrCheckpointPending(OgeCatalogError):
    """A bounded 278-T OCR run created immutable shards but is incomplete."""

    def __init__(self, status: dict):
        super().__init__("White House 278-T checkpointed OCR is pending")
        self.status = status


def _apply_trump_2026_geometry(records: list[dict], profile: dict) -> list[dict]:
    """Resolve the narrow printed-number column from conserved visual order.

    The source rows themselves are never added or removed.  The v2 OCR cell
    values remain in ``raw_cells`` and only the row-number cell is replaced.
    """

    expected_count = profile.get("v2_source_row_count")
    if expected_count is not None and len(records) != expected_count:
        raise OgeCatalogError("Trump 2026 278-T v2 row conservation failed")
    previous: tuple[int, float] | None = None
    revised = []
    for row_number, source in enumerate(records, start=1):
        page = source.get("page_number")
        top = source.get("geometry_top")
        cells = source.get("cells")
        if (type(page) is not int or not 1 <= page <= profile["page_count"] or
                not isinstance(top, (int, float)) or
                not isinstance(cells, list) or len(cells) != 6 or
                any(not isinstance(value, str) for value in cells)):
            raise OgeCatalogError("Trump 2026 278-T OCR geometry is invalid")
        position = (page, float(top))
        if previous is not None and position <= previous:
            raise OgeCatalogError("Trump 2026 278-T OCR geometry order is invalid")
        previous = position
        record = {**source, "raw_cells": list(source.get("raw_cells", cells)),
                  "cells": list(cells), "printed_row_number_raw": cells[0],
                  "printed_row_number_resolution": "source_bound_geometry_order"}
        record["cells"][0] = str(row_number)
        revised.append(record)
    if not revised:
        raise OgeCatalogError("Trump 2026 278-T contains no OCR rows")
    return revised


def _trump_2026_filing_evidence(profile: dict) -> dict:
    return {
        "page_number": None,
        "label": "Official White House disclosure listing date",
        "raw": profile["report_date_raw"],
        "normalized": profile["report_date"],
        "basis": "exact_sha_official_disclosure_link_label",
        "source_url": profile["source_url"],
        "source_sha256": profile["source_sha256"],
    }


def _trump_2026_identity_evidence(profile: dict) -> dict:
    return {"page_number":1,"pdf_filer_name":"Donald J Trump",
        "pdf_position_title":"President of the United States of America",
        "agency_basis":"exact_sha_official_disclosure_catalog_alias",
        "source_url":profile["source_url"],"source_sha256":profile["source_sha256"]}


def _build_trump_2026_extraction(
        profile: dict, records: list[dict], *, filer_name: str,
        amended_label: str | None, ocr_engine: str, extraction_method: str,
        ocr_checkpoint: dict | None = None) -> dict:
    records = _apply_trump_2026_geometry(records, profile)
    rows = [(record["page_number"], record["cells"]) for record in records]
    transactions, quarantined = parse_table_rows(rows, source_sha=profile["source_sha256"])
    transactions, quarantined = _attach_ocr_evidence(
        transactions, quarantined, records)
    filed_at = profile["report_date"]
    future = []
    retained = []
    for row in transactions:
        if row["transaction_date"] > filed_at:
            future.append({**row, "reasons": ["transaction_after_official_report_date"]})
        else:
            retained.append(row)
    transactions = retained
    quarantined.extend(future)
    result = {
        "schema_version": EXTRACTION_SCHEMA,
        "parser_version": TRUMP_2026_PARSER_VERSION,
        "source_id": "oge",
        "document_id": profile["document_id"],
        "source_url": profile["source_url"],
        "source_sha256": profile["source_sha256"],
        "filer_name": filer_name,
        "pdf_filer_name": "Donald J Trump",
        "pdf_position_title": "President of the United States of America",
        "pdf_agency_label": None,
        "pdf_position_agency_raw": "President of the United States of America",
        "amended_label": amended_label,
        "filed_at": filed_at,
        "signature_method": "official_disclosure_date_source_bound",
        "filer_signature_evidence": None,
        "filer_signature_name": "Donald J Trump",
        "filing_date_evidence": _trump_2026_filing_evidence(profile),
        "filer_identity_evidence": _trump_2026_identity_evidence(profile),
        "extraction_method": extraction_method,
        "ocr_engine": ocr_engine,
        "page_count": profile["page_count"],
        "source_row_count": len(transactions) + len(quarantined),
        "document_reasons": [],
        "evidence_complete": True,
        "transactions": transactions,
        "quarantined": quarantined,
    }
    if ocr_checkpoint is not None:
        result["ocr_checkpoint"] = ocr_checkpoint
    return result


def replay_trump_2026_v2_extraction(prior: dict) -> dict:
    """Create v4 deterministically from a complete, read-only v2 OCR result."""

    if (not isinstance(prior, dict) or prior.get("parser_version") != PARSER_VERSION or
            prior.get("extraction_method") != "tesseract_ocr_geometry" or
            not isinstance(prior.get("ocr_engine"), str)):
        raise OgeCatalogError("Trump 2026 278-T v2 replay input is invalid")
    profile = trump_2026_profile(
        prior.get("document_id"), prior.get("source_url"), prior.get("source_sha256"))
    if profile is None or prior.get("page_count") != profile["page_count"]:
        raise OgeCatalogError("Trump 2026 278-T v2 replay source is invalid")
    source_rows = [*prior.get("transactions", []), *prior.get("quarantined", [])]
    if (not all(isinstance(row, dict) for row in source_rows) or
            prior.get("source_row_count") != len(source_rows)):
        raise OgeCatalogError("Trump 2026 278-T v2 replay rows are invalid")
    ordered = sorted(source_rows, key=lambda row: row.get("geometry_row_index", -1))
    if [row.get("geometry_row_index") for row in ordered] != list(
            range(1, len(ordered) + 1)):
        raise OgeCatalogError("Trump 2026 278-T v2 geometry is not conserved")
    records = []
    for row in ordered:
        raw = row.get("ocr_raw_cells")
        if not isinstance(raw, list) or len(raw) != 6 or any(
                not isinstance(value, str) for value in raw):
            raise OgeCatalogError("Trump 2026 278-T v2 raw cells are invalid")
        records.append({"page_number": row.get("page_number"), "cells": list(raw),
                        "raw_cells": list(raw),
                        "ocr_confidence": row.get("ocr_confidence"),
                        "geometry_top": row.get("geometry_top")})
    return _build_trump_2026_extraction(
        profile, records, filer_name=prior["filer_name"],
        amended_label=prior.get("amended_label"), ocr_engine=prior["ocr_engine"],
        extraction_method="tesseract_ocr_geometry_v2_replay")


def _write_trade_checkpoint_shard(path: Path, value: dict) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise OgeCatalogError("White House 278-T OCR checkpoint shard conflicts")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                     suffix=".tmp", delete=False) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(path)


def extract_whitehouse_278t_pdf_checkpointed(
        pdf_path: Path, *, source_url: str, source_sha256: str, document_id: str,
        filer_name: str, amended_label: str | None, checkpoint_root: Path,
        page_limit: int = 100, shard_size: int = 25,
        ocr_executable: str | None = None) -> dict:
    """OCR the one 113-page 2026 source in immutable, resumable page shards."""

    profile = trump_2026_profile(document_id, source_url, source_sha256)
    if profile is None or profile["page_count"] <= MAX_INLINE_OCR_PAGES:
        raise OgeCatalogError("White House 278-T checkpointed OCR is not required")
    if (type(page_limit) is not int or type(shard_size) is not int or
            not 1 <= shard_size <= 50 or not shard_size <= page_limit <= 100):
        raise OgeCatalogError("White House 278-T OCR checkpoint bounds are invalid")
    content = Path(pdf_path).read_bytes()
    _validate_pdf(content)
    if hashlib.sha256(content).hexdigest() != source_sha256:
        raise OgeCatalogError("White House 278-T checkpoint PDF is invalid")
    try:
        import pdfplumber
    except ImportError:
        raise OgeCatalogError("White House 278-T OCR requires pdfplumber") from None
    checkpoint_root = Path(checkpoint_root)
    with pdfplumber.open(pdf_path) as document:
        page_count = len(document.pages)
        if page_count != profile["page_count"] or page_count > MAX_CHECKPOINT_OCR_PAGES:
            raise OgeCatalogError("White House 278-T checkpoint page count changed")
        ranges = [(start, min(start + shard_size - 1, page_count))
                  for start in range(1, page_count + 1, shard_size)]
        expected = {f"pages-{start:04d}-{end:04d}.json": (start, end)
                    for start, end in ranges}
        if any(path.name not in expected for path in checkpoint_root.glob("pages-*.json")):
            raise OgeCatalogError("White House 278-T OCR checkpoint has unexpected shards")
        shards = {}
        engines = set()
        for name, (start, end) in expected.items():
            path = checkpoint_root / name
            if not path.is_file():
                continue
            try:
                shard = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise OgeCatalogError("White House 278-T OCR checkpoint is invalid") from None
            pages = shard.get("pages")
            if (shard.get("schema_version") != OCR_SHARD_SCHEMA or
                    shard.get("parser_version") != TRUMP_2026_PARSER_VERSION or
                    shard.get("source_url") != source_url or
                    shard.get("source_sha256") != source_sha256 or
                    shard.get("page_start") != start or shard.get("page_end") != end or
                    not isinstance(shard.get("ocr_engine"), str) or
                    not isinstance(pages, list) or len(pages) != end - start + 1 or
                    [page.get("page_number") for page in pages] != list(range(start, end + 1))):
                raise OgeCatalogError("White House 278-T OCR checkpoint is unbound")
            shards[(start, end)] = shard
            engines.add(shard["ocr_engine"])
        if len(engines) > 1:
            raise OgeCatalogError("White House 278-T OCR checkpoint engine changed")
        processed = 0
        created = 0
        for start, end in ranges:
            if (start, end) in shards:
                continue
            size = end - start + 1
            if processed and processed + size > page_limit:
                break
            try:
                pages, engine = ocr_pdf_pages(
                    document, executable=ocr_executable, resolution=200,
                    page_numbers=list(range(start, end + 1)), max_pages=shard_size)
            except OcrGeometryError as exc:
                raise OgeCatalogError(f"White House 278-T OCR failed: {exc}") from None
            if engines and engine not in engines:
                raise OgeCatalogError("White House 278-T OCR checkpoint engine changed")
            engines.add(engine)
            shard = {"schema_version": OCR_SHARD_SCHEMA,
                     "parser_version": TRUMP_2026_PARSER_VERSION,
                     "source_url": source_url, "source_sha256": source_sha256,
                     "ocr_engine": engine, "page_start": start, "page_end": end,
                     "pages": [{"page_number": number, "width": page.width,
                                "height": page.height, "words": page.extract_words()}
                               for number, page in zip(
                                   range(start, end + 1), pages, strict=True)]}
            _write_trade_checkpoint_shard(
                checkpoint_root / f"pages-{start:04d}-{end:04d}.json", shard)
            shards[(start, end)] = shard
            processed += size
            created += 1
    completed = sum(end - start + 1 for start, end in shards)
    status = {"schema_version": OCR_CHECKPOINT_SCHEMA,
              "parser_version": TRUMP_2026_PARSER_VERSION,
              "source_url": source_url, "source_sha256": source_sha256,
              "page_count": profile["page_count"], "shard_size": shard_size,
              "shard_count": len(ranges), "completed_shard_count": len(shards),
              "completed_page_count": completed,
              "pending_page_count": profile["page_count"] - completed,
              "created_shard_count": created}
    if len(shards) != len(ranges):
        raise TradeOcrCheckpointPending(status)
    pages = []
    for start, end in ranges:
        for page in shards[(start, end)]["pages"]:
            pages.append(OcrPage(width=float(page["width"]), height=float(page["height"]),
                                 words=page["words"]))
    records = []
    boundaries = None
    for page_number, page in enumerate(pages, start=1):
        page_records, boundaries = _ocr_table(page, page_number, boundaries)
        records.extend(page_records)
    return _build_trump_2026_extraction(
        profile, records, filer_name=filer_name, amended_label=amended_label,
        ocr_engine=next(iter(engines)),
        extraction_method="tesseract_ocr_geometry_checkpointed",
        ocr_checkpoint=status)



def _generic_table_rows(inventory: list, *, source_sha256: str,
                        scope_barriers_before: dict | None = None) -> tuple[list, list, list]:
    """Compatibility entry point for the shared source-row scope interpreter."""
    from .oge_reports import parse_generic_table_rows
    return parse_generic_table_rows(inventory, source_sha256=source_sha256,
                                    scope_barriers_before=scope_barriers_before)


def _inherit_whitehouse_generic_rows(result: dict, legacy: dict | None) -> dict:
    """Bind old identities by exact source cells; retain all old parsed facts."""
    if legacy is None:
        return result
    if (not isinstance(legacy, dict) or legacy.get("schema_version") != EXTRACTION_SCHEMA or
            legacy.get("parser_version") not in HISTORICAL_PARSER_VERSIONS or
            any(legacy.get(key) != result.get(key) for key in
                ("document_id", "source_sha256", "source_url", "filer_name"))):
        raise OgeCatalogError("White House generic legacy source binding differs")
    def key(row):
        return (row.get("page_number"), tuple(row.get("cells", [])))
    inventory = [(r["page_number"], r["cells"]) for r in result["source_rows"]]
    replay_good, replay_bad = parse_table_rows(inventory, source_sha=result["source_sha256"], default_owner="Self")
    replay_by_id = {}
    for replay in replay_good+replay_bad:
        replay_by_id.setdefault(replay["extraction_id"], []).append(replay)
    old_by_key, new_counts = {}, {}
    for original in legacy.get("transactions", []):
        row = deepcopy(original)
        if not isinstance(row.get("cells"), list):
            # Early WH caches omit cells. The original stable ID hashes every
            # raw cell and owner; exact replay of that ID plus the full fact
            # projection proves the input, rather than matching by page/number.
            matches = [r for r in replay_by_id.get(row.get("extraction_id"), []) if
                all(r.get(field) == row.get(field) for field in ("page_number", "row_number", "owner", "asset_name", "ticker",
                    "transaction_type", "transaction_date", "amount_low", "amount_high"))]
            if len(matches) == 1:
                row["cells"] = deepcopy(matches[0]["cells"])
                row["legacy_cell_binding"] = "all_raw_cells_stable_id_and_projection_replayed"
        old_by_key.setdefault(key(row), []).append(row)
    for row in result["transactions"] + result["quarantined"]:
        new_counts[key(row)] = new_counts.get(key(row), 0) + 1
    inherited = set()
    for collection in ("transactions", "quarantined"):
        rows = []
        for row in result[collection]:
            matches = old_by_key.get(key(row), [])
            if len(matches) == 1 and len(key(row)[1]) == 6 and new_counts[key(row)] == 1:
                old = matches[0]
                retained = deepcopy(old)
                retained["source_rows_index"] = row["source_rows_index"]
                retained["account_context"] = deepcopy(row["account_context"])
                retained["legacy_inheritance"] = {"basis": "same_pdf_exact_page_cells", "source_sha256": result["source_sha256"],
                    "legacy_parser_version": legacy["parser_version"], "legacy_row": deepcopy(old),
                    "new_observation_reasons": row.get("reasons", [])}
                # Identity preservation is separate from a factual default-owner correction.
                proof = [r for r in replay_by_id.get(old.get("extraction_id"),[]) if key(r) == key(old)]
                if (old.get("owner") == "Self" and row.get("owner") == "Undisclosed" and not row.get("reasons") and len(proof) == 1 and
                        proof[0].get("owner") == "Self" and proof[0].get("extraction_id") == old.get("extraction_id")):
                    retained["owner"] = "Undisclosed"
                    retained["owner_disclosure_basis"] = row["owner_disclosure_basis"]
                    retained["legacy_inheritance"]["owner_correction"] = {"field": "owner", "old_value": "Self", "new_value": "Undisclosed",
                        "rule": "approved_default_self_to_undisclosed/v1", "source_rows_index": row["source_rows_index"]}
                approved_owner = retained.get("legacy_inheritance", {}).get("owner_correction")
                differences = {field: {"old_value": old.get(field), "new_value": row.get(field)} for field in
                    ("owner", "asset_name", "ticker", "transaction_type", "transaction_date", "amount_low", "amount_high", "amount_low_exclusive")
                    if old.get(field) != row.get(field) and not (field == "owner" and approved_owner)}
                if differences:
                    retained["legacy_inheritance"]["unresolved_field_differences"] = differences
                if row.get("account_title") and not old.get("account_title"):
                    retained["account_title"] = row["account_title"]
                    retained["legacy_inheritance"]["account_display_correction"] = {
                        "field": "asset_name", "rule": "approved_source_account_title_display/v1",
                        "old_value": old["asset_name"], "new_value": f"{old['asset_name']} — {row['account_title']}"}
                rows.append(retained)
                inherited.add(old["extraction_id"])
            else:
                rows.append(row)
        result[collection] = rows
    # Legacy facts not found by exact cells remain visible, never silently disappear.
    for old in legacy.get("transactions", []):
        if old.get("extraction_id") not in inherited:
            result["transactions"].append({**deepcopy(old), "legacy_unmatched": True})
    legacy_hash = hashlib.sha256(json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    result["legacy_source_binding"] = {"source_sha256": result["source_sha256"], "legacy_extraction_sha256": legacy_hash,
        "inherited_ids": sorted(inherited), "unmatched_ids": sorted(r["extraction_id"] for r in legacy.get("transactions", []) if r["extraction_id"] not in inherited)}
    return result




def _validate_generic_legacy_owner(report: dict, row: dict, expected: dict, replay_by_id: dict) -> None:
    """Reprove the old default using all original raw cells at consumption time."""
    inheritance = row["legacy_inheritance"]
    old = inheritance.get("legacy_row", {})
    correction = inheritance.get("owner_correction")
    if correction is None:
        if row.get("owner") != old.get("owner"):
            raise OgeCatalogError("White House generic inherited owner changed without a rule")
        return
    matches = replay_by_id.get(old.get("extraction_id"), [])
    fields = ("page_number", "row_number", "owner", "asset_name", "ticker", "transaction_type", "transaction_date",
              "amount_low", "amount_high", "amount_low_exclusive", "cells")
    required = {"field":"owner","old_value":"Self","new_value":"Undisclosed",
        "rule":"approved_default_self_to_undisclosed/v1","source_rows_index":expected["source_rows_index"]}
    if (correction != required or expected.get("owner") != "Undisclosed" or expected.get("reasons") or
            row.get("owner") != "Undisclosed" or old.get("owner") != "Self" or len(matches) != 1 or
            any(matches[0].get(field) != old.get(field) for field in fields) or
            old.get("extraction_id") != row.get("extraction_id")):
        raise OgeCatalogError("White House generic default-owner correction failed raw input replay")


def generic_existing_fact(report: dict, row: dict, existing: list[dict], *, expected_person_id: str | None = None) -> dict | None:
    """Only exact-source inherited rows can reference an existing formal fact."""
    if report.get("parser_version") in {DUAL_AXIS_PARSER_VERSION, *NATIVE_VISIBLE_PARSER_VERSIONS}:
        # The caller has rematerialized this report from its bound inputs.
        # A current-source repeat is not a legacy owner correction or a match by
        # similar fields: the complete publication object must be identical.
        from .whitehouse_278t_candidate import _transaction
        matches = [fact for fact in existing if fact.get("id") == row.get("extraction_id")]
        if (len(matches) != 1 or not expected_person_id or row.get("reasons") or report.get("document_reasons") or
                row not in report.get("transactions", [])):
            return None
        fact = matches[0]
        return fact if fact == _transaction(report, row, expected_person_id) else None
    inheritance = row.get("legacy_inheritance", {})
    old = inheritance.get("legacy_row", {})
    matches = [fact for fact in existing if fact.get("id") == row.get("extraction_id")]
    if (report.get("parser_version") != GENERIC_PARSER_VERSION or len(matches) != 1 or
            inheritance.get("source_sha256") != report.get("source_sha256") or
            inheritance.get("basis") != "same_pdf_exact_page_cells" or
            old.get("extraction_id") != row.get("extraction_id") or
            old.get("page_number") != row.get("page_number") or old.get("cells") != row.get("cells") or
            len(row.get("cells", [])) != 6):
        return None
    fact = matches[0]
    fields = ("owner", "asset_name", "ticker", "transaction_type", "transaction_date", "amount_low", "amount_high", "amount_low_exclusive")
    if (fact.get("source_url") != report.get("source_url") or fact.get("filing_id") != report.get("document_id") or
            fact.get("filed_at") != f"{report.get('filed_at')}T00:00:00Z" or
            any(fact.get(field) != (f"{old['asset_name']} — {old['account_title']}"
                if field == "asset_name" and old.get("account_title") else old.get(field)) and not
                (field == "owner" and inheritance.get("owner_correction") and fact.get("owner") == row.get("owner")) and not
                (field == "asset_name" and inheritance.get("account_display_correction") and
                 fact.get("asset_name") == inheritance["account_display_correction"].get("new_value")) for field in fields)):
        return None
    if any(row.get(field) != old.get(field) for field in fields if field != "owner"):
        return None
    if row.get("owner") != old.get("owner"):
        correction = inheritance.get("owner_correction", {})
        if (old.get("owner") != "Self" or row.get("owner") != "Undisclosed" or
                correction.get("rule") != "approved_default_self_to_undisclosed/v1"):
            return None
    return fact



_FILER_IDENTITY_LABEL = r"Filer(?:(?:'|’)?s)?"


def _generic_cover_identity(text: str) -> tuple:
    name, position, agency, role_raw, reasons = _filer_information(text)
    if name is None:
        info = re.search(_FILER_IDENTITY_LABEL + r"\s+Information\s*\n(.*?)"
                         r"(?=^\s*(?:" + _FILER_IDENTITY_LABEL + r"\s+Certification\b|Electronic\s+Signature\b|"
                         r"Agency\s+Ethics\s+Official\b))", text, re.I|re.S|re.M)
        lines=[line.strip() for line in info[1].splitlines() if line.strip()] if info else []
        # Paper covers use a labelled multi-column identity table. Its header
        # order is not a two-line name/position block. Never turn Position /
        # Agency, Last Name or First Name labels into a conflicting person.
        header = r"(?:position|agency|last\s+name|first\s+name|mi)(?:[\s|]+(?:position|agency|last\s+name|first\s+name|mi))*"
        if len(lines)==2 and not any(re.fullmatch(header,line,re.I) for line in lines):
            name, role_raw = lines
            position, sep, agency = role_raw.rpartition(" - ")
            position, agency = (position.strip(), agency.strip()) if sep else (role_raw, None)
            reasons = []
    return name, position, agency, role_raw, reasons


def _generic_cover_dates(text: str, sha: str, ocr: bool = False) -> dict:
    from .oge_reports import read_278t_cover_dates
    evidence = read_278t_cover_dates(text, source_sha256=sha)
    if ocr:
        evidence["schema_version"] = "whitehouse-278t-saved-ocr-cover-roles/v1"
        for item in evidence["observations"]:
            item["method"] = "saved_ocr_cover_words"
    return evidence


def _saved_source_cover_resolution(document, url, sha, count, dates):
    """Reuse already-verified immutable source profiles, retaining OCR conflicts."""
    profile=trump_2026_profile(document,url,sha)
    if profile is None:return None
    if count != profile["page_count"]:
        raise OgeCatalogError("White House saved original page count differs from verified profile")
    name,position,agency,raw,identity_reasons=_generic_cover_identity(dates["cover_text"])
    reasons=[r for r in dates["document_reasons"] if r != "filer_signature_date_missing"]
    if dates["filed_at"] is not None and dates["filed_at"] != profile["report_date"]:
        reasons.append("ocr_source_filing_date_conflict")
    if name is not None and _first_last(name) != _first_last("Donald J Trump"):
        reasons.append("ocr_source_filer_identity_conflict")
    if position is not None and re.sub(r"[^a-z]","",position.casefold()) not in {
            "president","presidentoftheunitedstates","presidentoftheunitedstatesofamerica"}:
        reasons.append("ocr_source_filer_role_conflict")
    if agency is not None and re.sub(r"[^a-z]","",agency.casefold()) not in {
            "whitehouse","whitehouseoffice","executiveofficeofthepresident"}:
        reasons.append("ocr_source_filer_agency_conflict")
    return {"filed_at":profile["report_date"],"filing_date_evidence":_trump_2026_filing_evidence(profile),
        "filer_identity_evidence":_trump_2026_identity_evidence(profile),"document_reasons":sorted(set(reasons)),
        "ocr_cover_identity_observation":{"name":name,"position":position,"agency":agency,"raw":raw,"reasons":identity_reasons},
        "ocr_filed_at":dates["filed_at"],"resolution_basis":"existing_exact_sha_official_profile"}


def _saved_supported_fields(row: dict, slot: dict, *, source_sha: str) -> dict:
    """Interpret unique field observations with the existing table parser.

    Raw cells remain the stable identity input. Interpreted strings are a
    separate source-bound projection, never a new ID namespace or edited OCR.
    Geometry and conflict blockers are still applied by the relation consumer.
    """
    from .oge_transaction_adapter import _amount_readings
    from datetime import date
    values, references, reasons = {}, {}, []
    amount_reads = _amount_readings(slot["field_readings"]["amount"]["observations"])
    keys = {(tuple(value), exclusive) for _, value, exclusive in amount_reads}
    open_amount = len(keys) == 1 and next(iter(keys))[1] is True
    for field in ("printed_number", "description", "type", "transaction_date", "amount"):
        reads = slot["field_readings"][field]["observations"]
        supported = [(read, read["shape"].get("values", read["shape"].get("value")))
                     for read in reads if read.get("geometry_status") != "ambiguous" and read.get("comparison_eligible") is not False and
                     read["shape"].get("status") in {"ocr_shape_only", "ocr_text_only", "source_glyph_shape_only"}]
        if field == "amount":
            supported = [(read, value) for read, value, _ in amount_reads]
        if len({json.dumps(value, sort_keys=True) for _, value in supported}) != 1:
            reasons.append(field + "_reading_unresolved")
        else:
            values[field] = supported[0][1]
            references[field] = [read["source_ref"] for read, _ in supported]
    result = deepcopy(row)
    if reasons:
        result["reasons"] = sorted(set([*row.get("reasons", []), *reasons]))
        return result
    amount = values["amount"]
    normalized = [str(values["printed_number"]), values["description"], values["type"],
                  date.fromisoformat(values["transaction_date"]).strftime("%m/%d/%Y"), row["cells"][4],
                  f"Over ${amount[0]:,}" if open_amount else f"${amount[0]:,} - ${amount[1]:,}"]
    kwargs = dict(source_sha=source_sha, default_owner=row["owner"], account_title=row.get("account_title"),
                  allow_open_amounts=True, allow_exchange=True)
    good, bad = parse_table_rows([(row["page_number"], normalized)], **kwargs)
    raw_good, raw_bad = parse_table_rows([(row["page_number"], row["cells"])], **kwargs)
    if len(good + bad) != 1 or len(raw_good + raw_bad) != 1:
        result["reasons"] = sorted(set([*row.get("reasons", []), "supported_field_projection_unresolved"]))
        return result
    interpreted, raw = (good + bad)[0], (raw_good + raw_bad)[0]
    projection = ("row_number", "asset_name", "ticker", "transaction_type", "transaction_date",
                  "amount_low", "amount_high", "amount_low_exclusive")
    for field in projection:
        if field in interpreted:
            result[field] = interpreted[field]
        else:
            result.pop(field, None)
    reasons = sorted((set(row.get("reasons", [])) - set(raw.get("reasons", []))) | set(interpreted.get("reasons", [])))
    result.pop("reasons", None)
    if reasons:
        result["reasons"] = reasons
    result["field_interpretation_evidence"] = {"schema_version": "whitehouse-saved-supported-fields/v1",
        "identity_basis": "original_raw_cells_existing_id_algorithm", "raw_extraction_id": row["extraction_id"],
        "raw_cells": deepcopy(row["cells"]), "interpreted_cells": normalized,
        "field_observation_references": references}
    return result


def _saved_directory_identity(inputs: dict, metadata: dict, cover_text: str) -> dict | None:
    """Reprove optional v7 official attribution without editing PDF observations."""
    names = ("official_directory_index", "oge_identity_coverage", "oge_identity_catalog_metadata")
    allowed = {name + suffix for name in names for suffix in ("_path", "_sha256")}
    if any(key.startswith(("official_directory_", "oge_identity_")) and key not in allowed for key in inputs):
        raise OgeCatalogError("White House directory identity input parameter is unknown")
    if not any(name + suffix in inputs for name in names for suffix in ("_path", "_sha256")):
        return None
    from .whitehouse_disclosures import parse_disclosure_index, _label_kind, PAGE_URL
    from .oge_whitehouse import coverage_from_archive
    from .whitehouse_directory_identity_shadow import complete_name_tokens
    from .oge_candidate import _identity_key
    from .whitehouse_278t_audit import _role, _words, _OFFICE_LABELS
    roots = _SAVED_OCR_ROOTS.get()
    if not roots or roots[0] is None:
        raise OgeCatalogError("White House directory identity requires a controlled evidence root")
    def read(name, evidence=False):
        path, sha = inputs.get(name + "_path"), inputs.get(name + "_sha256")
        if not isinstance(path, str) or not isinstance(sha, str) or not _SHA256.fullmatch(sha):
            raise OgeCatalogError("White House directory identity input reference is incomplete")
        raw = _saved_source_path(path, evidence=evidence).read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha:
            raise OgeCatalogError("White House directory identity file hash differs: " + name)
        return json.loads(raw)
    index = read(names[0]); coverage = read(names[1]); catalog = read(names[2], True)
    result = {"schema_version":"whitehouse-saved-directory-identity/v1", "status":"unresolved",
        "source_sha256":metadata["sha256"],"document_id":metadata["document_id"],"source_url":metadata["document_url"],
        "archive_page_sha256":metadata.get("page_sha256"),"resolved_missing_fields":[],"reasons":[]}
    def pending(reason):
        result["reasons"] = [reason]
        return result
    old_label = metadata.get("link_label")
    if (metadata.get("source_id") != "whitehouse_public_disclosures" or
            not isinstance(metadata.get("page_sha256"),str) or not _SHA256.fullmatch(metadata["page_sha256"]) or
            not isinstance(old_label,str) or not old_label.strip() or
            metadata.get("page_url",PAGE_URL) != PAGE_URL):
        return pending("official_directory_archive_label_chain_unresolved")
    old_kind,_,old_name = _label_kind("Transaction Reports",old_label)
    if (old_kind != "278t" or old_name == "label_ambiguous" or
            complete_name_tokens(old_name) != complete_name_tokens(metadata.get("filer_name_from_label") or "")):
        return pending("official_directory_archive_label_conflict")
    if not isinstance(index.get("page_sha256"),str) or not _SHA256.fullmatch(index["page_sha256"]):
        return pending("official_directory_current_page_reference_unresolved")
    page = index.get("page_archive", {})
    expected = f"whitehouse/disclosures/pages/{index['page_sha256']}.html"
    if page.get("archive_path") != expected or page.get("sha256") != index["page_sha256"]:
        return pending("official_directory_original_page_reference_unresolved")
    page_path = _saved_source_path(expected, evidence=True)
    if not page_path.is_file():
        return pending("official_directory_original_page_unavailable")
    raw = page_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != page["sha256"] or len(raw) != page.get("byte_length"):
        raise OgeCatalogError("White House official directory original bytes differ")
    replay = parse_disclosure_index(raw)
    if {key:value for key,value in index.items() if key != "page_archive"} != replay:
        raise OgeCatalogError("White House official directory index differs from original page")
    catalog_path = _saved_source_path(inputs["oge_identity_catalog_metadata_path"], evidence=True)
    if coverage_from_archive(roots[0], catalog_path) != coverage or coverage.get("catalog_sha256") != catalog.get("sha256"):
        raise OgeCatalogError("White House identity coverage differs from archived catalog")
    entries = [entry for entry in index["reports"] if entry.get("source_document_id") == metadata["document_id"]]
    if len(entries) != 1:
        return pending("official_directory_document_not_unique")
    entry = entries[0]
    if (entry.get("document_url") != metadata["document_url"] or entry.get("document_type_from_label") != "278t" or
            entry.get("source_section") != "Transaction Reports" or not entry.get("filer_name_from_label") or
            complete_name_tokens(entry["filer_name_from_label"]) != complete_name_tokens(inputs["filer_name"]) or
            complete_name_tokens(metadata.get("filer_name_from_label") or "") != complete_name_tokens(entry["filer_name_from_label"])):
        return pending("official_directory_archive_identity_conflict")
    if len(complete_name_tokens(entry["filer_name_from_label"])) < 2:
        return pending("official_directory_full_name_incomplete")
    named = [row for row in coverage["reports"] if row.get("document_type") == "278t" and
             complete_name_tokens(row.get("filer_name") or "") == complete_name_tokens(entry["filer_name_from_label"])]
    keys = {_identity_key(row) for row in named}
    if len(keys) != 1:
        return pending("official_directory_catalog_identity_not_unique")
    identity = named[0]
    # A bounded, visibly empty identity block is missing information. An absent
    # or unclosed block is unknown, not permission to erase a possible conflict.
    blocks = list(re.finditer(
        r"^" + _FILER_IDENTITY_LABEL + r"[ \t]+Information[ \t]*\r?\n(.*?)"
        r"^" + _FILER_IDENTITY_LABEL + r"[ \t]+Certification[ \t]*\r?\n(?=(?:Date\s*:|I\s+certify\b))",
        cover_text, re.I | re.S | re.M))
    if len(blocks) != 1:
        return pending("official_directory_pdf_identity_block_ambiguous")
    lines = [line.strip() for line in blocks[0][1].splitlines() if line.strip()]
    if len(lines) > 2:
        return pending("official_directory_pdf_identity_block_ambiguous")
    observed = {"name":lines[0] if lines else None,"position":None,"agency":None,
                "raw_block":blocks[0][1]}
    if len(lines) == 2:
        position, sep, agency = lines[1].rpartition(" - ")
        observed.update(position=position.strip() if sep else lines[1], agency=agency.strip() if sep else None)
    result["pdf_identity_observation"] = observed
    if (observed["name"] and complete_name_tokens(observed["name"]) != complete_name_tokens(identity["filer_name"]) or
            observed["position"] and _role(observed["position"]) != _role(identity["position_title"]) or
            observed["agency"] and _OFFICE_LABELS.get(_words(observed["agency"])) != identity["agency"].casefold()):
        return pending("official_directory_pdf_identity_conflict")
    result.update(status="resolved",reasons=[],
        resolved_missing_fields=[field for field in ("name","position","agency") if not observed[field]],
        directory_entry=deepcopy(entry),catalog_identity={key:identity[key] for key in ("filer_name","position_title","agency")},
        matched_catalog_entry_ids=sorted({row["catalog_entry_id"] for row in named}),
        directory_page_sha256=index["page_sha256"],catalog_sha256=coverage["catalog_sha256"],
        archive_attribution={"source":"controlled_archive_metadata","page_url":PAGE_URL,
            "page_url_basis":"official_archive_schema_source" if "page_url" not in metadata else "archive_metadata",
            "page_sha256":metadata["page_sha256"],"link_label":old_label,
            "filer_name_from_label":metadata["filer_name_from_label"]},
        directory_corroboration={"source":"current_official_html_replayed","page_url":index["page_url"],
            "page_sha256":index["page_sha256"]},
        basis="archive_pdf_attribution_current_directory_full_name_unique_catalog_identity")
    return result


def materialize_saved_whitehouse_278t(inputs: dict) -> dict:
    """Consume complete selected OCR pages without making another OCR call.

    Cache bytes, source identity, frames and original page geometry are separate
    proofs.  Missing original bytes retain complete raw pages but never create
    physical slots or claim a source transaction count of zero.
    """
    import math
    if not isinstance(inputs, dict):
        raise OgeCatalogError("White House saved OCR input is invalid")
    inputs = deepcopy(inputs)
    allow_partial_rows=inputs.get("allow_partial_rows",False)
    if type(allow_partial_rows) is not bool:
        raise OgeCatalogError("White House partial row geometry opt-in must be boolean")
    geometry_mode=inputs.get("geometry_mode")
    if geometry_mode not in (None,DUAL_AXIS_MODE) or geometry_mode and allow_partial_rows:
        raise OgeCatalogError("White House dual-axis mode cannot mix legacy partial geometry")
    dual=geometry_mode==DUAL_AXIS_MODE
    from .oge_ocr_line_order import MODE as OCR_LINE_MODE
    description_line_mode=inputs.get("description_line_mode")
    if description_line_mode not in (None,OCR_LINE_MODE) or description_line_mode and not dual:
        raise OgeCatalogError("White House description line mode requires explicit saved dual-axis input")
    word_mode=inputs.get("word_observation_mode")
    if word_mode not in (None,"source_objects_with_ocr_header/v1") or word_mode and not dual:
        raise OgeCatalogError("source word observations require explicit dual-axis mode")
    observation_dpi=inputs.get("observation_resolution_dpi",200)
    if type(observation_dpi) is not int or not 150 <= observation_dpi <= 400:
        raise OgeCatalogError("saved observation resolution is invalid")
    from .oge_278t_source_glyph_fields import SOURCE_GLYPH_MODE
    source_glyph_mode = inputs.get("source_glyph_mode")
    if source_glyph_mode not in (None, SOURCE_GLYPH_MODE) or source_glyph_mode and not dual:
        raise OgeCatalogError("White House source glyph mode requires explicit saved dual-axis input")
    from .oge_278t_source_date_sequence_fields import MODE as SEQUENCE_MODE
    source_date_sequence_mode=inputs.get("source_date_sequence_mode")
    if source_date_sequence_mode not in (None,SEQUENCE_MODE) or source_date_sequence_mode and not dual:
        raise OgeCatalogError("White House source date sequence mode requires saved dual-axis input")
    if not dual and any(key.startswith(("official_directory_", "oge_identity_")) for key in inputs):
        raise OgeCatalogError("White House directory identity inputs require explicit saved v7")
    def read(name):
        path, digest = inputs.get(name + "_path"), inputs.get(name + "_sha256")
        if not isinstance(path, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise OgeCatalogError("White House saved input requires a fixed file hash: " + name)
        raw = _saved_source_path(path,evidence=name == "archive_metadata").read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise OgeCatalogError("White House saved file bytes differ: " + name)
        return json.loads(raw), raw
    metadata, _ = read("archive_metadata")
    checkpoint, checkpoint_bytes = read("checkpoint")
    document, sha, url = metadata.get("document_id"), metadata.get("sha256"), metadata.get("document_url")
    filer = inputs.get("filer_name")
    _validate_source(url, sha, document, filer)
    if (metadata.get("schema_version") != "whitehouse-public-disclosures-pdf/v1" or
            document != "wh-url:" + hashlib.sha256(url.encode()).hexdigest()[:24] or
            metadata.get("document_type_from_label") != "278t" or
            metadata.get("archive_path") != f"whitehouse/disclosures/reports/{document.removeprefix('wh-url:')}/{sha}.pdf" or
            checkpoint.get("schema_version") != OCR_SHARD_SCHEMA or checkpoint.get("source_sha256") != sha or
            checkpoint.get("source_url") != url or checkpoint.get("parser_version") not in GENERIC_PARSER_VERSIONS | {TRUMP_2026_PARSER_VERSION} or
            not isinstance(checkpoint.get("ocr_engine"), str) or not checkpoint["ocr_engine"]):
        raise OgeCatalogError("White House OCR checkpoint differs from official archive identity")
    blob = inputs.get("checkpoint_git_blob")
    if blob is not None and hashlib.sha1(b"blob " + str(len(checkpoint_bytes)).encode() + b"\0" + checkpoint_bytes).hexdigest() != blob:
        raise OgeCatalogError("White House OCR checkpoint Git object differs")
    pages = checkpoint.get("pages")
    first, last = checkpoint.get("page_start"), checkpoint.get("page_end")
    if (type(first) is not int or type(last) is not int or first < 1 or last < first or last-first > 49 or
            not isinstance(pages, list) or [page.get("page_number") for page in pages] != list(range(first, last+1))):
        raise OgeCatalogError("White House checkpoint page inventory differs")
    numbers = inputs.get("selected_page_numbers")
    if (not isinstance(numbers, list) or not numbers or len(numbers) > 8 or
            any(type(n) is not int or not first <= n <= last for n in numbers) or len(set(numbers)) != len(numbers) or 1 not in numbers):
        raise OgeCatalogError("White House observed input needs an explicit cover and selected page range")
    selected = [deepcopy(page) for page in pages if page["page_number"] in numbers]
    ocr = {}
    source_word_provenance=[]
    for page in selected:
        width, height, words = page.get("width"), page.get("height"), page.get("words")
        if (any(type(v) not in (int,float) or not math.isfinite(v) or v <= 0 for v in (width,height)) or not isinstance(words,list)):
            raise OgeCatalogError("White House saved page dimensions or words are invalid")
        for word in words:
            if (not isinstance(word,dict) or not isinstance(word.get("text"),str) or
                    any(type(word.get(k)) not in (int,float) or not math.isfinite(word[k]) for k in ("x0","x1","top","bottom","ocr_confidence")) or
                    not 0 <= word["x0"] < word["x1"] <= width or not 0 <= word["top"] < word["bottom"] <= height or
                    (not (word_mode and page["page_number"] != 1 and "source_object_index" in word) and
                     any(type(word.get(k)) is not int for k in ("block_num","par_num","line_num")))):
                raise OgeCatalogError("White House saved word geometry or line reference is invalid")
        if word_mode and any("source_object_index" in word for word in words):
            if page["page_number"] == 1 or not inputs.get("archived_pdf_path"):
                raise OgeCatalogError("native saved words require an original table page")
            from .pdf_text_object_words import validate_saved_native_words
            try:
                source_word_provenance.append(validate_saved_native_words(
                    _saved_source_path(inputs["archived_pdf_path"],evidence=True),
                    expected_sha256=sha,page_number=page["page_number"],words=words))
            except ValueError as exc:
                raise OgeCatalogError(str(exc)) from exc
            if page.get("render_frame",{}).get("resolution") != observation_dpi:
                raise OgeCatalogError("native saved word render resolution differs")
        ocr[page["page_number"]] = OcrPage(width=width,height=height,words=words,render_frame=page.get("render_frame"))
    secondary_observations = None
    if any(key in inputs for key in ("secondary_observations_path","secondary_observations_sha256")):
        secondary_observations, _ = read("secondary_observations")
        if not isinstance(secondary_observations,list):
            raise OgeCatalogError("White House secondary observations must be a saved list")
    legacy = None
    if inputs.get("legacy_extraction_path") is not None:
        legacy, _ = read("legacy_extraction")
        if (legacy.get("schema_version") != EXTRACTION_SCHEMA or legacy.get("parser_version") not in HISTORICAL_PARSER_VERSIONS or
                any(legacy.get(k) != value for k,value in (("document_id",document),("source_sha256",sha),("source_url",url)))):
            raise OgeCatalogError("White House saved OCR legacy source differs")
    count = legacy.get("page_count") if legacy else inputs.get("document_page_count")
    if type(count) is not int or not max(numbers) <= count <= 1200:
        raise OgeCatalogError("White House observed document page count is missing")
    dates = _generic_cover_dates(ocr[1].extract_text(),sha,True)
    name,position,agency,role_raw,identity_reasons = _generic_cover_identity(dates["cover_text"])
    reasons = [*dates["document_reasons"],*identity_reasons]
    source_cover=_saved_source_cover_resolution(document,url,sha,count,dates)
    filed_at,filing_evidence,identity_evidence=dates["filed_at"],None,None
    if source_cover:
        name=source_cover["filer_identity_evidence"]["pdf_filer_name"]
        position=source_cover["filer_identity_evidence"]["pdf_position_title"]
        agency,role_raw=None,position
        filed_at=source_cover["filed_at"]
        filing_evidence,identity_evidence=source_cover["filing_date_evidence"],source_cover["filer_identity_evidence"]
        reasons=source_cover["document_reasons"][:]
    directory_identity = _saved_directory_identity(inputs, metadata, dates["cover_text"]) if dual else None
    if directory_identity:
        if directory_identity["status"] == "resolved":
            reasons = [reason for reason in reasons if reason != "pdf_filer_information_not_verified"]
            if source_cover is None:
                observed = directory_identity["pdf_identity_observation"]
                # The legacy two-line fallback can consume Certification/Date
                # labels after an empty block. Scope those parser fields to the
                # bounded original identity block; do not put directory values
                # into fields claiming they were read inside the PDF.
                directory_identity["prior_unscoped_identity_parse"] = {
                    "name":name,"position":position,"agency":agency,"raw":role_raw}
                name,position,agency = (observed[field] for field in ("name","position","agency"))
                role_raw = observed["raw_block"] or None
        else:
            reasons.extend(directory_identity["reasons"])
    if name is not None and _first_last(name) != _first_last(filer):
        reasons.append("pdf_filer_name_mismatch")
    if not re.search(r"OGE\s+Form\s+278[- ]?T\b",dates["cover_text"],re.I):
        reasons.append("278t_form_title_not_verified")
    observations, content, physical, constraints = None, None, [], {}
    supported_slots = {}
    pdf_reference = inputs.get("archived_pdf_path")
    pdf = _saved_source_path(pdf_reference,evidence=True) if pdf_reference is not None else None
    original_verified = pdf is not None and pdf.is_file()
    if (source_glyph_mode or source_date_sequence_mode) and not original_verified:
        raise OgeCatalogError("White House source glyph mode requires the controlled original PDF")
    if original_verified:
        raw = Path(pdf).read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha or len(raw) != metadata.get("byte_length"):
            raise OgeCatalogError("White House observed original differs from archive")
        import pdfplumber
        with pdfplumber.open(pdf) as original:
            if len(original.pages) != count:
                raise OgeCatalogError("White House observed original page count differs")
        table_numbers = [n for n in numbers if n != 1]
        if table_numbers:
            from .oge_278t_raster_slots import observe_raster_slots
            from .oge_278t_field_shadow import observe_content_rows
            try:
                observations = observe_raster_slots(Path(pdf),expected_sha256=sha,page_numbers=table_numbers,
                      saved_ocr_pages={n:ocr[n] for n in table_numbers},saved_ocr_engine=checkpoint["ocr_engine"],
                      resolution=observation_dpi,
                      **({"allow_partial_rows":True} if allow_partial_rows else {}),
                      **({"geometry_mode":DUAL_AXIS_MODE} if dual else {}),
                      **({"description_line_mode":description_line_mode} if description_line_mode else {}),
                      **({"native_baseline_mode":inputs["native_baseline_mode"]} if "native_baseline_mode" in inputs else {}))
                content = observe_content_rows([observations],document_page_count=count,source_pdf_path=Path(pdf),
                    **({"secondary_observations":secondary_observations} if secondary_observations is not None else {}),
                    **({"source_glyph_mode":source_glyph_mode} if source_glyph_mode else {}),
                    **({"source_date_sequence_mode":source_date_sequence_mode} if source_date_sequence_mode else {}))
            except ValueError as exc:
                reasons.append("saved_ocr_geometry_unresolved")
                observations = {"geometry_failure":str(exc)}
            if content:
                relation = {(item["page_number"],item["slot_key"]):item for item in content["slot_content"]}
                fields = ("printed_number","description","type","transaction_date","notification","amount")
                for page in content["field_observations"]["pages"]:
                    for index, slot in enumerate(page.get("slots",[]),1):
                        row = relation[(page["page_number"],str(index))]
                        cells = [slot["raw_columns"].get(field,"") for field in fields]
                        description = row.get("structural_evidence",{}).get("description_text")
                        if description is not None:
                            cells[1] = description
                        if row["disposition"] in {"heading","note"}:
                            if row["disposition"] == "note":
                                continue
                        else:
                            evidence = row.get("relation_evidence",{})
                            if evidence.get("status") != "supported_observation_relation":
                                constraints[len(physical)] = evidence.get("blocking_reasons") or ["observed_row_relation_unresolved"]
                            if dual:
                                supported_slots[len(physical)] = slot
                        physical.append({"page_number":page["page_number"],"cells":cells,
                              "observation_reference":{"source_sha256":sha,"page_number":page["page_number"],"slot_key":str(index),
                                  **({"unresolved_region_before":row["unresolved_region_before"],
                                      "unresolved_region_scope_signal":row["unresolved_region_scope_signal"]}
                                     if "unresolved_region_before" in row else {})}})
    else:
        reasons.append("observed_original_pdf_unavailable_geometry_unknown")
    barriers={i:row["observation_reference"].get("unresolved_region_scope_signal",False)
              for i,row in enumerate(physical) if row["observation_reference"].get("unresolved_region_before")}
    good,bad,structural = _generic_table_rows([(row["page_number"],row["cells"]) for row in physical],source_sha256=sha,
        **({"scope_barriers_before":barriers} if allow_partial_rows or dual else {}))
    kept, rejected = [], []
    for row in good+bad:
        if dual and row["source_rows_index"] in supported_slots:
            row = _saved_supported_fields(row, supported_slots[row["source_rows_index"]], source_sha=sha)
        extra = constraints.get(row["source_rows_index"],[])
        if dual and row.get("amount_low_exclusive") is True:
            extra = [reason for reason in extra if reason != "amount_observation_missing"]
        if extra:
            row["reasons"] = sorted(set([*row.get("reasons",[]),*extra]))
        (rejected if row.get("reasons") else kept).append(row)
    good, bad = kept, rejected
    legacy_counts = ({"parsed_reference_count":len(legacy.get("transactions",[])),
                      "quarantined_reference_count":len(legacy.get("quarantined",[]))} if legacy else None)
    if legacy is not None:
        # Nonobserved old parser rows are references, not thousands of newly
        # quarantined physical slots. Existing formal base facts stay protected.
        legacy = deepcopy(legacy)
        keys = {(row["page_number"],tuple(row["cells"])) for row in physical}
        old_good,old_bad = parse_table_rows([(row["page_number"],row["cells"]) for row in physical],source_sha=sha,default_owner="Self")
        source_ids = {row["extraction_id"] for row in old_good+old_bad}
        legacy["transactions"] = [row for row in legacy.get("transactions",[]) if
            (isinstance(row.get("cells"),list) and (row.get("page_number"),tuple(row["cells"])) in keys) or
            row.get("extraction_id") in source_ids]
    unknown_pages = [n for n in numbers if n != 1 and (not content or
        any(p["page_number"] == n and p.get("status") != "field_shapes_observed"
            for p in content["field_observations"]["pages"]))]
    receipt = {"schema_version":"whitehouse-278t-saved-ocr-receipt/v1","mode":"saved_ocr_words",
        "source_sha256":sha,"selected_page_numbers":sorted(numbers),"document_page_count":count,
        "page_count_basis":"source_sha256_verified_pdf" if original_verified else "declared_prior_input_not_verified_original",
        "source_original_verified":original_verified,"checkpoint_sha256":inputs["checkpoint_sha256"],
        "input_reference_mode":"developer_absolute_paths" if any(isinstance(value,str) and Path(value).is_absolute()
            for key,value in inputs.items() if key.endswith("_path")) else "controlled_relative_roots",
        "observed_word_page_count":len(selected),"observed_physical_slot_count":sum(p.get("known_physical_slot_count",p.get("physical_slot_count")) or 0 for p in (observations or {}).get("pages",[])),
        "unique_source_row_count":None,"coverage_complete":False,
        "legacy_reference_counts":legacy_counts,
        "unobserved_pages":[n for n in range(1,count+1) if n not in numbers],"geometry_unknown_pages":unknown_pages}
    if allow_partial_rows or dual:
        partial_pages=(content or {}).get("partial_geometry_pages",[])
        partial_by_page={p["page_number"]:p for p in partial_pages}
        observed_by_page={p["page_number"]:p for p in (observations or {}).get("pages",[])}
        unbound_pages=[]
        for raw_page in selected:
            number=raw_page["page_number"]
            if number == 1:continue  # Cover words are not transaction-table words.
            observed=observed_by_page.get(number,{})
            if number in partial_by_page:
                word_count=len(partial_by_page[number]["unbound_page_ocr_words"])
                basis="partial_grid_unbound_saved_words"
            elif observed.get("coverage") == "physical_slots_located":
                word_count=observed["words_outside_slots"]
                basis="fully_located_grid_words_outside_slots"
            else:
                word_count=len(raw_page["words"])
                basis="geometry_unknown_all_saved_words"
            unbound_pages.append({"page_number":number,"word_count":word_count,"basis":basis})
        receipt.update(local_row_geometry_mode=DUAL_AXIS_MODE if dual else "strict_joint_boundaries/v1",
            geometry_partial_pages=[p["page_number"] for p in partial_pages],
            unresolved_regions=[{"page_number":p["page_number"],"regions":p["unresolved_regions"]} for p in partial_pages],
            whole_selected_table_slot_count=None if partial_pages or unknown_pages else receipt["observed_physical_slot_count"],
            unbound_observed_word_count=sum(p["word_count"] for p in unbound_pages),
            unbound_observed_word_unit="selected_table_page_words_outside_known_slots",
            unbound_observed_word_pages=unbound_pages)
        if dual:receipt["geometry_mode"]=DUAL_AXIS_MODE
    if not physical:
        reasons.append("observed_table_geometry_unknown")
    if source_glyph_mode:
        receipt["source_glyph_mode"] = source_glyph_mode
    if word_mode:
        receipt.update(word_observation_mode=word_mode,source_word_page_provenance=source_word_provenance,
                       observation_resolution_dpi=observation_dpi)
    if source_date_sequence_mode:
        receipt["source_date_sequence_mode"] = source_date_sequence_mode
    if description_line_mode:
        receipt["description_line_mode"] = description_line_mode
    if metadata.get("amended_label") or dates["amendment_cover"]:
        reasons.append("amendment_relationship_unresolved")
    result = {"schema_version":EXTRACTION_SCHEMA,"parser_version":DUAL_AXIS_PARSER_VERSION if dual else SAVED_OCR_PARSER_VERSION,"source_id":"oge",
        **({"geometry_mode":DUAL_AXIS_MODE} if dual else {}),
        "document_id":document,"source_url":url,"source_sha256":sha,"filer_name":filer,
        "pdf_filer_name":name,"pdf_position_title":position,"pdf_agency_label":agency,"pdf_position_agency_raw":role_raw,
        "filed_at":filed_at,"signature_method":"official_disclosure_date_source_bound" if source_cover else "source_cover_roles","cover_date_evidence":dates,
        "filing_date_evidence":filing_evidence,"filer_identity_evidence":identity_evidence,"amended_label":metadata.get("amended_label"),
        **({"source_cover_resolution":source_cover} if source_cover else {}),
        **({"source_identity_resolution":directory_identity} if directory_identity else {}),
        "extraction_method":"saved_ocr_words_same_source_partial","ocr_engine":checkpoint["ocr_engine"],"page_count":count,
        "transactions":good,"quarantined":bad,"source_rows":physical,"structural_rows":structural,
        "document_reasons":sorted(set(reasons)),"evidence_complete":not reasons,
        "raw_page_observations":selected,"geometry_observations":observations,"content_observations":content,
        "observation_receipt":receipt,"saved_ocr_input":inputs}
    result = _inherit_whitehouse_generic_rows(result,legacy)
    result["source_row_count"] = len(result["transactions"])+len(result["quarantined"])
    return result


def generic_whitehouse_filing_date(report: dict) -> str | None:
    """Validate same-source first-page role evidence without borrowing another date."""
    from .oge_reports import read_278t_cover_dates
    if report.get('parser_version') == NATIVE_VISIBLE_DEFAULT_VERSION:
        pages = report.get('native_text_partition', {}).get('pages', [])
        if not pages:
            raise OgeCatalogError('White House native visible cover evidence is unavailable')
        filed, method, raw, name, _ = _filer_signature(pages[0]['visible_text'])
        if any(report.get(key) != value for key, value in
               (('filed_at', filed), ('signature_method', method),
                ('filer_signature_evidence', raw), ('filer_signature_name', name))):
            raise OgeCatalogError('White House native visible default signature differs')
        return filed
    evidence = report.get("cover_date_evidence", {})
    if (not isinstance(evidence.get("cover_text"), str) or
            _generic_cover_dates(evidence["cover_text"], report["source_sha256"],
                                   report.get("parser_version") in SAVED_OCR_PARSER_VERSIONS) != evidence or
            type(report.get("page_count")) is not int or report["page_count"] < 1):
        raise OgeCatalogError("White House generic cover role evidence differs")
    if report.get("parser_version") in SAVED_OCR_PARSER_VERSIONS:
        resolved=_saved_source_cover_resolution(report.get("document_id"),report.get("source_url"),
            report.get("source_sha256"),report["page_count"],evidence)
        if resolved:
            if (resolved != report.get("source_cover_resolution") or any(report.get(k) != resolved[k]
                    for k in ("filed_at","filing_date_evidence","filer_identity_evidence"))):
                raise OgeCatalogError("White House saved official date resolution differs from source")
            return resolved["filed_at"]
    if evidence.get("filed_at") != report.get("filed_at"):
        raise OgeCatalogError("White House generic cover role evidence differs")
    return evidence["filed_at"]


def validate_generic_whitehouse_report(report: dict) -> dict:
    """Recompute roles and exact row interpretations before qualification."""
    if report.get('parser_version') in NATIVE_VISIBLE_PARSER_VERSIONS:
        return validate_native_visible_whitehouse_report(report)
    if report.get("parser_version") in SAVED_OCR_PARSER_VERSIONS:
        replay = materialize_saved_whitehouse_278t(report.get("saved_ocr_input"))
        if replay != report:
            raise OgeCatalogError("White House saved OCR report differs from source-input replay")
        by_key = {}
        for row in replay["transactions"] + replay["quarantined"]:
            by_key.setdefault((row["page_number"], tuple(row["cells"])), []).append(row)
        return by_key
    generic_whitehouse_filing_date(report)
    name, position, agency, role_raw, identity_reasons = _generic_cover_identity(report["cover_date_evidence"]["cover_text"])
    if any(report.get(field) != value for field,value in (("pdf_filer_name",name),("pdf_position_title",position),
            ("pdf_agency_label",agency),("pdf_position_agency_raw",role_raw))):
        raise OgeCatalogError("White House generic cover identity differs from raw text")
    required = list(report["cover_date_evidence"]["document_reasons"]) + identity_reasons
    if not re.search(r"OGE\s+Form\s+278[- ]?T\b", report["cover_date_evidence"]["cover_text"], re.I):
        required.append("278t_form_title_not_verified")
    if name is not None and _first_last(name) != _first_last(report.get("filer_name") or ""):
        required.append("pdf_filer_name_mismatch")
    if not set(required) <= set(report.get("document_reasons",[])):
        raise OgeCatalogError("White House generic document lost source role reasons")
    if report.get("source_row_count") != len(report["transactions"])+len(report["quarantined"]):
        raise OgeCatalogError("White House generic reported row count differs")
    physical = report.get("source_rows")
    if not isinstance(physical, list) or any(not isinstance(r,dict) or type(r.get("page_number")) is not int or
            not 1 <= r["page_number"] <= report["page_count"] or not isinstance(r.get("cells"),list) or
            any(not isinstance(c,str) for c in r["cells"]) for r in physical):
        raise OgeCatalogError("White House generic source inventory is invalid")
    good, bad, structural = _generic_table_rows([(r["page_number"], r["cells"]) for r in physical], source_sha256=report["source_sha256"])
    if structural != report.get("structural_rows"):
        raise OgeCatalogError("White House generic structural evidence differs")
    by_key = {}
    for row in good+bad:
        by_key.setdefault((row["page_number"],tuple(row["cells"])),[]).append(row)
    actual_count = sum(not r.get("legacy_unmatched") for r in report["transactions"]+report["quarantined"])
    if actual_count != len(good)+len(bad):
        raise OgeCatalogError("White House generic row inventory is not closed")
    fields = ("owner", "asset_name", "ticker", "transaction_type", "transaction_date", "amount_low", "amount_high", "amount_low_exclusive", "extraction_id")
    replay_by_id = {}
    if any(r.get("legacy_inheritance",{}).get("owner_correction") for r in report["transactions"]+report["quarantined"]):
        replay_good,replay_bad = parse_table_rows([(r["page_number"],r["cells"]) for r in physical],
            source_sha=report["source_sha256"],default_owner="Self")
        for replay in replay_good+replay_bad:
            replay_by_id.setdefault(replay["extraction_id"],[]).append(replay)
    seen = set()
    for row in report["transactions"]+report["quarantined"]:
        if row.get("legacy_unmatched"):
            continue
        key = (row.get("page_number"),tuple(row.get("cells",[])))
        matches = by_key.get(key,[])
        matches = [match for match in matches if match["source_rows_index"] == row.get("source_rows_index")]
        locator = row.get("source_rows_index")
        if locator in seen or len(matches) != 1:
            raise OgeCatalogError("White House generic physical row is ambiguous")
        seen.add(locator)
        expected = matches[0]
        inherited = row.get("legacy_inheritance")
        if inherited:
            if inherited.get("new_observation_reasons") != expected.get("reasons",[]):
                raise OgeCatalogError("White House generic inherited row lost new conflicts")
            _validate_generic_legacy_owner(report, row, expected, replay_by_id)
            old = inherited.get("legacy_row", {})
            if (inherited.get("basis") != "same_pdf_exact_page_cells" or inherited.get("source_sha256") != report["source_sha256"] or
                    old.get("page_number") != row.get("page_number") or old.get("cells") != row.get("cells") or
                    old.get("extraction_id") != row.get("extraction_id")):
                raise OgeCatalogError("White House generic inherited source binding differs")
            differences = {field:{"old_value":old.get(field),"new_value":expected.get(field)} for field in fields
                if field != "extraction_id" and old.get(field) != expected.get(field) and
                   not (field == "owner" and inherited.get("owner_correction"))}
            if differences != inherited.get("unresolved_field_differences",{}):
                raise OgeCatalogError("White House generic inherited field differences were altered")
            display = inherited.get("account_display_correction")
            if display is not None and (not expected.get("account_title") or old.get("account_title") or
                    row.get("account_title") != expected["account_title"] or display != {
                        "field":"asset_name","rule":"approved_source_account_title_display/v1", "old_value":old["asset_name"],
                        "new_value":f"{old['asset_name']} — {expected['account_title']}"}):
                raise OgeCatalogError("White House generic account display correction differs from source")
        elif any(row.get(field) != expected.get(field) for field in fields) or row.get("reasons",[]) != expected.get("reasons",[]):
            raise OgeCatalogError("White House generic row differs from source interpretation")
    return by_key


def plan_whitehouse_278t_source(pdf_path: Path, *, source_url: str, source_sha256: str,
                                generic: bool = False) -> dict:
    """Select from original source modes before consulting a saved result."""
    baseline = GENERIC_PARSER_VERSION if generic else parser_version_for_source(source_url, source_sha256)
    if not generic and baseline != PARSER_VERSION:
        return {'parser_version': baseline, 'partition_plan': None}
    from .native_text_partition import plan_native_text_partition
    plan = plan_native_text_partition(Path(pdf_path), expected_sha256=source_sha256)
    unchanged = plan.get('activated') is False and plan.get('inventory_complete') is True
    return {'parser_version': baseline if unchanged else
            NATIVE_VISIBLE_GENERIC_VERSION if generic else NATIVE_VISIBLE_DEFAULT_VERSION,
            'partition_plan': plan}


def _native_partition_inputs(proof: dict) -> tuple:
    pages = proof.get('pages', [])
    cover = pages[0] if pages else {}
    return ('\n'.join(page.get('visible_text', '') for page in pages),
        [(page, list(cells)) for page, cells in proof.get('inventory', [])],
        {'text': cover.get('visible_text', ''), 'document_page_count': len(pages),
         'source_visible': cover.get('status') == 'observed'})


def _native_sequence_gap_explained(proof: dict, rows: list[dict]) -> bool:
    """Account for missing numbers by independent physical unknown slots only."""
    located = {row['row_number']: row['source_rows_index'] for row in rows
        if type(row.get('row_number')) is int and type(row.get('source_rows_index')) is int}
    if not located:
        return False
    unknown_indices = {row['source_rows_index'] for row in rows if row.get('native_unknown_source_row')}
    financial = {row['source_rows_index'] for row in proof.get('rows', [])
        if row.get('financial_slot') is True and row.get('reasons') and
        row.get('source_rows_index') in unknown_indices}
    if max(located) - len(located) > len(financial):
        return False
    gaps = {}
    for missing in set(range(1, max(located) + 1)) - set(located):
        lower = max((number for number in located if number < missing), default=0)
        upper = min(number for number in located if number > missing)
        bounds = (located[lower] if lower else -1, located[upper])
        gaps[bounds] = gaps.get(bounds, 0) + 1
    return bool(gaps) and all(sum(left < index < right for index in financial) >= count
        for (left, right), count in gaps.items())


def plan_whitehouse_278t_archive(metadata: dict, evidence_root: Path, *, generic: bool = False) -> dict:
    baseline = GENERIC_PARSER_VERSION if generic else parser_version_for_source(
        metadata.get('document_url'), metadata.get('sha256'))
    if not generic and baseline != PARSER_VERSION:
        return {'parser_version': baseline, 'partition_plan': None}
    from .whitehouse_disclosures import read_archived_pdf
    content = read_archived_pdf(evidence_root, metadata)
    with tempfile.TemporaryDirectory(prefix='whitehouse-native-plan-') as temporary:
        path = Path(temporary) / 'source.pdf'
        path.write_bytes(content)
        return plan_whitehouse_278t_source(path, source_url=metadata['document_url'],
            source_sha256=metadata['sha256'], generic=generic)


def parse_native_visible_whitehouse_278t_pdf(pdf_path: Path, *, source_url: str,
        source_sha256: str, document_id: str, filer_name: str,
        amended_label: str | None = None, generic: bool = False) -> dict:
    """Interpret a source-selected projection without OCR or legacy migration."""
    from .native_text_partition import observe_native_text_partition
    _validate_source(source_url, source_sha256, document_id, filer_name)
    if not generic and parser_version_for_source(source_url, source_sha256) != PARSER_VERSION:
        raise OgeCatalogError('White House fixed source keeps its historical default parser')
    proof = observe_native_text_partition(Path(pdf_path), expected_sha256=source_sha256)
    if proof.get('activated') is False:
        raise OgeCatalogError('White House source does not select native visible parsing')
    parser = parse_generic_whitehouse_278t_pdf if generic else parse_whitehouse_278t_pdf
    return parser(Path(pdf_path), source_url=source_url, source_sha256=source_sha256,
        document_id=document_id, filer_name=filer_name, amended_label=amended_label,
        _native_partition=proof)


def replay_native_visible_whitehouse_report(report: dict) -> dict:
    """Reopen the controlled original; saved projection labels grant nothing."""
    version = report.get('parser_version')
    roots = _SAVED_OCR_ROOTS.get()
    if version not in NATIVE_VISIBLE_PARSER_VERSIONS or roots is None or roots[0] is None:
        raise OgeCatalogError('White House native visible replay requires controlled evidence roots')
    evidence = roots[0]
    document, sha = report.get('document_id'), report.get('source_sha256')
    _validate_source(report.get('source_url'), sha, document, report.get('filer_name'))
    path = (evidence / 'whitehouse/disclosures/reports' / document.removeprefix('wh-url:') /
            f'{sha}.json').resolve(strict=True)
    if not path.is_relative_to(evidence):
        raise OgeCatalogError('White House native visible metadata escaped controlled evidence')
    metadata = json.loads(path.read_bytes())
    if any(metadata.get(key) != expected for key, expected in
           (('document_id', document), ('document_url', report['source_url']), ('sha256', sha),
            ('filer_name_from_label', report['filer_name']), ('link_label', report.get('amended_label')))):
        raise OgeCatalogError('White House native visible metadata differs from selected source')
    from .whitehouse_disclosures import read_archived_pdf
    content = read_archived_pdf(evidence, metadata)
    with tempfile.TemporaryDirectory(prefix='whitehouse-native-visible-') as temporary:
        pdf_path = Path(temporary) / 'source.pdf'
        pdf_path.write_bytes(content)
        return parse_native_visible_whitehouse_278t_pdf(pdf_path, source_url=report['source_url'],
            source_sha256=sha, document_id=document, filer_name=report['filer_name'],
            amended_label=report.get('amended_label'), generic=version == NATIVE_VISIBLE_GENERIC_VERSION)


def validate_native_visible_whitehouse_report(report: dict) -> dict:
    replay = replay_native_visible_whitehouse_report(report)
    if replay != report:
        raise OgeCatalogError('White House native visible report differs from complete original-source replay')
    by_key = {}
    for row in replay['transactions'] + replay['quarantined']:
        by_key.setdefault((row['page_number'], tuple(row['cells'])), []).append(row)
    return by_key


def parse_generic_whitehouse_278t_pdf(pdf_path: Path, *, source_url: str,
        source_sha256: str, document_id: str, filer_name: str,
        amended_label: str | None = None, legacy_extraction: dict | None = None,
        _native_partition: dict | None = None) -> dict:
    """Opt-in native parsing; scans remain unknown without new OCR side effects."""
    from .oge_reports import read_278t_cover_dates, _compact
    _validate_source(source_url, source_sha256, document_id, filer_name)
    content = Path(pdf_path).read_bytes()
    _validate_pdf(content)
    if hashlib.sha256(content).hexdigest() != source_sha256:
        raise OgeCatalogError("White House generic source PDF hash differs")
    if _native_partition is None:
        text, _, inventory, cover = _extract_pdf(Path(pdf_path), include_inventory=True, include_cover=True)
    else:
        text, inventory, cover = _native_partition_inputs(_native_partition)
    dates = read_278t_cover_dates(cover["text"], source_sha256=source_sha256)
    name, position, agency, role_raw, identity_reasons = _generic_cover_identity(cover["text"])
    reasons = list(dates["document_reasons"]) + identity_reasons
    if not re.search(r"OGE\s+Form\s+278[- ]?T\b", cover["text"], re.I):
        reasons.append("278t_form_title_not_verified")
    if name is not None and _first_last(name) != _first_last(filer_name):
        reasons.append("pdf_filer_name_mismatch")
    if amended_label and "amend" in amended_label.casefold() or dates["amendment_cover"]:
        reasons.append("amendment_relationship_unresolved")
    if re.search(r"\bEXHIBIT\s+[A-Z]\b|\bData\s+Revised\b", text, re.I):
        reasons.append("replacement_block_relationship_unresolved")
    if _native_partition is None:
        good, bad, structural = _generic_table_rows(inventory, source_sha256=source_sha256)
    else:
        from .native_partition_rows import interpret_partition_rows
        good, bad, structural = interpret_partition_rows(_native_partition, source_sha256=source_sha256,
            generic=True)
        reasons.extend(_native_partition.get('document_reasons', []))
        if not cover.get('source_visible'):
            reasons.append('native_visible_cover_unproved')
    if not good and not bad:
        reasons.append("transaction_table_not_found_native_text_only")
    result = {"schema_version": EXTRACTION_SCHEMA, "parser_version": GENERIC_PARSER_VERSION,
        "source_id": "oge", "document_id": document_id, "source_url": source_url, "source_sha256": source_sha256,
        "filer_name": filer_name, "pdf_filer_name": name, "pdf_position_title": position,
        "pdf_agency_label": agency, "pdf_position_agency_raw": role_raw, "filed_at": dates["filed_at"],
        "signature_method": "source_cover_roles", "cover_date_evidence": dates,
        "filing_date_evidence": None, "filer_identity_evidence": None,
        "amended_label": amended_label, "extraction_method": "same_pass_native_text_table", "ocr_engine": None,
        "page_count": cover["document_page_count"], "transactions": good, "quarantined": bad,
        "source_rows": [{"page_number": p, "cells": [_compact(c) for c in row]} for p,row in inventory],
        "structural_rows": structural, "document_reasons": sorted(set(reasons)), "evidence_complete": not reasons}
    if _native_partition is None:
        result = _inherit_whitehouse_generic_rows(result, legacy_extraction)
    else:
        result['parser_version'] = NATIVE_VISIBLE_GENERIC_VERSION
        result['native_text_partition'] = deepcopy(_native_partition)
        result['extraction_method'] = 'source_bound_native_visible_partition'
    result["source_row_count"] = len(result["transactions"]) + len(result["quarantined"])
    return result


def parse_whitehouse_278t_pdf(pdf_path: Path, *, source_url: str,
                              source_sha256: str, document_id: str,
                              filer_name: str, amended_label: str | None = None,
                              _native_partition: dict | None = None) -> dict:
    """Extract row evidence; uncertain filings and rows remain quarantined.

    ``source_sha256`` is the archive's expected byte hash, not a web header.
    ``amended_label`` is an official index label, if present.  It cannot by
    itself identify a predecessor report, so amended documents stay blocked.
    """

    _validate_source(source_url, source_sha256, document_id, filer_name)
    source_bound_trump = _trump_081225_profile(
        document_id, source_url, source_sha256)
    source_bound_2026 = trump_2026_profile(
        document_id, source_url, source_sha256)
    parser_version = parser_version_for_source(source_url, source_sha256)
    if amended_label is not None and not isinstance(amended_label, str):
        raise OgeCatalogError("White House 278-T amendment label is invalid")
    content = Path(pdf_path).read_bytes()
    _validate_pdf(content)
    if hashlib.sha256(content).hexdigest() != source_sha256:
        raise OgeCatalogError("White House 278-T PDF does not match archive hash")
    if _native_partition is None:
        text, rows = _extract_pdf(Path(pdf_path))
        first_page = text.split("Transactions", 1)[0] if text else ""
    else:
        text, rows, cover = _native_partition_inputs(_native_partition)
        first_page = cover['text']
        parser_version = NATIVE_VISIBLE_DEFAULT_VERSION
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
    if _native_partition is not None:
        needs_ocr = False
        page_count = len(_native_partition.get('pages', []))
        extraction_method = 'source_bound_native_visible_partition'
        reasons.extend(_native_partition.get('document_reasons', []))
        if not cover.get('source_visible'):
            reasons.append('native_visible_cover_unproved')
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
    if source_bound_2026 is not None:
        if ocr_records is None:
            raise OgeCatalogError("Trump 2026 278-T requires OCR geometry")
        return _build_trump_2026_extraction(
            source_bound_2026, ocr_records, filer_name=filer_name,
            amended_label=amended_label, ocr_engine=ocr_engine or "",
            extraction_method="tesseract_ocr_geometry")
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

    if _native_partition is None:
        transactions, quarantined = parse_table_rows(rows, source_sha=source_sha256)
    else:
        from .native_partition_rows import interpret_partition_rows
        transactions, quarantined, structural = interpret_partition_rows(_native_partition,
            source_sha256=source_sha256, generic=False, default_owner='Self')
    if ocr_records is not None:
        transactions, quarantined = _attach_ocr_evidence(
            transactions, quarantined, ocr_records)
    if not transactions and not quarantined:
        reasons.append("no_transaction_rows_found")
    numbered = [row["row_number"] for row in [*transactions, *quarantined]
                if isinstance(row, dict) and type(row.get("row_number")) is int]
    sequence_unknown = False
    if any(number != expected for expected, number in enumerate(sorted(numbered), 1)):
        sequence_unknown = (_native_partition is not None and len(set(numbered)) == len(numbered) and
            _native_sequence_gap_explained(_native_partition, transactions + quarantined))
        if not sequence_unknown:
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
        **({'native_text_partition': deepcopy(_native_partition),
            'source_rows': [{'page_number': page, 'cells': list(cells)} for page, cells in rows],
            'structural_rows': structural,
            **({'native_visible_sequence_status': 'partial_physical_unknown'} if sequence_unknown else {})}
           if _native_partition is not None else {}),
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
        # The 2026 emergency publication lane deliberately defers duplicate
        # resolution. Exact-source row evidence remains review-visible now;
        # reconciliation can happen without withholding the current reports.
        if report.get("parser_version") == TRUMP_2026_PARSER_VERSION:
            continue
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
