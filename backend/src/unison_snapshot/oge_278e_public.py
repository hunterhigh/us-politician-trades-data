"""Strict source-row extraction for public White House OGE Form 278e PDFs.

This is an evidence extraction, not a canonical snapshot producer. In particular,
New Entrant and early-filed Termination assets have no proven point valuation date;
Part 7 transactions must be reconciled with 278-T reports before publication.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
from pathlib import Path
import re
from urllib.parse import urlsplit

from .oge import OgeCatalogError
from .oge_annual import _range


SCHEMA = "whitehouse-public-278e-extraction/v1"
PARSER_VERSION = "whitehouse-278e-positioned-text/v1"
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 1200
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SECTION = re.compile(r"^([1-9])\.\s+(Filer's Employment Assets|Spouse's Employment Assets|Other Assets and Income|Transactions|Liabilities|Gifts and Travel|Filer's Employment Agreements|Filer's Sources|Filer's Positions)", re.I)
_ROW = re.compile(r"^([1-9]\d*(?:\.\d+)*)\s+\S")
_SIGNATURE = re.compile(r"^/s/\s+(.+?)\s+\[electronically signed on\s+(\d{2}/\d{2}/\d{4})\s+by\s+(.+?)\s+in Integrity\.gov\]", re.I | re.M)
_BAND = re.compile(r"\$[\d,]+\s*-\s*\$[\d,]+")
_DAY = re.compile(r"\d{1,2}/\d{1,2}/\d{4}\Z")
_PARTS = {2: ("part2", "Self"), 5: ("part5", "Spouse"),
          6: ("part6", "Unknown"), 7: ("part7", "Unknown")}


def _compact(value: str) -> str:
    return " ".join(value.split())


def _date(value: str) -> str | None:
    if not _DAY.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def _name_key(value: str) -> tuple[str, str] | None:
    """Compare the first and last names; middle names are supplementary only."""
    parts = re.findall(r"[a-z]+", value.casefold())
    if len(parts) < 2:
        return None
    if "," in value:
        return parts[0], parts[1]
    return parts[-1], parts[0]


def _cover(cover: str, expected_filer: str) -> dict:
    if "OGE Form 278e" not in cover or "Public Financial Disclosure Report" not in cover:
        raise OgeCatalogError("White House PDF is not an OGE Form 278e")
    report_match = re.search(r"^Report Type:\s*(Annual|New Entrant|Termination|Annual/Termination|Annual Term)(?: Report)?\s*$", cover, re.I | re.M)
    if report_match is None:
        raise OgeCatalogError("White House 278e report type is unverified")
    report_type = report_match[1].casefold()
    report_type = {"annual": "Annual", "new entrant": "New Entrant",
                   "termination": "Termination", "annual/termination": "Annual Term",
                   "annual term": "Annual Term"}[report_type]
    name_match = re.search(r"^Filer's Information\s*\n([^\n]+)\n([^\n]+)", cover, re.I | re.M)
    if name_match is None or _name_key(name_match[1]) != _name_key(expected_filer):
        raise OgeCatalogError("White House 278e filer does not match the cover")
    signatures = [match for match in _SIGNATURE.finditer(cover)
                  if _name_key(match[1]) == _name_key(expected_filer)
                  and _name_key(match[3]) == _name_key(expected_filer)]
    if len(signatures) != 1:
        raise OgeCatalogError("White House 278e filer signature is missing or ambiguous")
    filed_at = _date(signatures[0][2])
    if filed_at is None:
        raise OgeCatalogError("White House 278e filer signature date is invalid")
    year_match = re.search(r"^Year \(Annual Report only\):\s*(20\d{2})\s*$", cover, re.M)
    cover_year = int(year_match[1]) if year_match else None
    if report_type == "Annual" and (cover_year is None or cover_year < 2025
                                    or cover_year > date.fromisoformat(filed_at).year):
        raise OgeCatalogError("White House annual report year is unverified")
    if report_type != "Annual" and cover_year is not None:
        raise OgeCatalogError("White House 278e report type conflicts with annual year")
    appointment = re.search(r"^Date of Appointment:\s*(\d{1,2}/\d{1,2}/\d{4})\s*$", cover, re.M)
    termination = re.search(r"^Date of Termination:\s*(\d{1,2}/\d{1,2}/\d{4})\s*$", cover, re.M)
    appointment_on = _date(appointment[1]) if appointment else None
    termination_on = _date(termination[1]) if termination else None
    if report_type == "New Entrant" and appointment_on is None:
        raise OgeCatalogError("White House New Entrant appointment date is unverified")
    if report_type in {"Termination", "Annual Term"} and termination_on is None:
        raise OgeCatalogError("White House Termination date is unverified")
    # OGE annual Parts 2/5/6/7 cover the preceding calendar year. New Entrant
    # valuation may use any day <31 days before filing; never invent that day.
    period_end = f"{cover_year - 1}-12-31" if report_type == "Annual" else (
        termination_on if report_type in {"Termination", "Annual Term"} else None)
    valuation_date = period_end if report_type == "Annual" else None
    position_line = _compact(name_match[2])
    position_title, separator, agency_office = position_line.rpartition(" - ")
    if not separator:
        position_title, agency_office = position_line, None
    return {"filer_name": _compact(name_match[1]),
            "position_line_raw": position_line,
            "position_title_raw": position_title or None,
            "agency_office_raw": agency_office,
            "report_type": report_type,
            "cover_report_year": cover_year, "report_period_end": period_end,
            "holding_valuation_date": valuation_date,
            "appointment_date": appointment_on, "termination_date": termination_on,
            "filing_date": filed_at, "signature_text": _compact(signatures[0][0])}


def _header(words: list[dict], part: str) -> dict[str, float] | None:
    by_name: dict[str, float] = {}
    for word in words:
        by_name.setdefault(word["text"].casefold(), float(word["x0"]))
    required = ("#", "description", "type", "date", "amount") if part == "part7" else (
        "#", "description", "eif", "value", "income")
    if not all(name in by_name for name in required):
        return None
    columns = {name: by_name[name] for name in required}
    if list(columns.values()) != sorted(columns.values()):
        return None
    return columns


def _words_at(words: list[dict], top: float) -> list[dict]:
    return sorted((word for word in words if abs(float(word["top"]) - top) < 2.5),
                  key=lambda word: float(word["x0"]))


def _append_line(row: dict, line_words: list[dict], columns: dict[str, float]) -> None:
    keys = [key for key in columns if key != "#"]
    starts = [columns[key] for key in keys]
    for word in line_words:
        x = float(word["x0"])
        if x < starts[0] - 6:
            continue
        index = max((i for i, start in enumerate(starts) if x >= start - 6), default=None)
        if index is not None:
            row[keys[index]].append(word["text"])


def _quarantine(row: dict, reasons: list[str]) -> dict:
    return {"section": row["section"], "page_number": row["page_number"],
            "row_number": row["row_number"], "raw_columns": row["raw_columns"],
            "reasons": sorted(set(reasons))}


def _parse_row(row: dict, meta: dict, child_parent: str | None) -> tuple[str, dict]:
    cells = {key: _compact(" ".join(value)) for key, value in row.items()
             if key in {"description", "eif", "value", "type", "date", "amount"}}
    row["raw_columns"] = cells
    evidence = {"section": row["section"], "page_number": row["page_number"],
                "row_number": row["row_number"], "asset_name": cells.get("description", ""),
                "owner": row["owner"], "raw_columns": cells}
    if row["section"] == "part7":
        kind = cells.get("type", "").casefold()
        when = _date(cells.get("date", ""))
        band = _range(cells.get("amount", ""))
        reasons = []
        if not evidence["asset_name"] or "see endnote" in evidence["asset_name"].casefold():
            reasons.append("transaction_asset_unresolved")
        if kind not in {"purchase", "sale", "exchange"}:
            reasons.append("transaction_type_unreadable")
        if when is None or (meta["report_type"] == "Annual" and
                            not when.startswith(meta["report_period_end"][:4] + "-")):
            reasons.append("transaction_date_unreadable_or_outside_period")
        if band is None:
            reasons.append("transaction_amount_unreadable_or_open")
        if reasons:
            return "quarantined", _quarantine(row, reasons)
        return "transactions", {**evidence, "transaction_type": kind,
                                "transaction_date": when, "amount_low": band[0],
                                "amount_high": band[1]}
    value_text = cells.get("value", "")
    if not evidence["asset_name"]:
        return "quarantined", _quarantine(row, ["asset_unreadable"])
    if not value_text and child_parent and row["row_number"] == child_parent:
        return "excluded", {**evidence, "reason": "unvalued_parent_container"}
    if re.fullmatch(r"None \(or less than \$1,001\)", value_text, re.I):
        return "excluded", {**evidence, "reason": "no_disclosed_period_end_value"}
    band = _range(value_text)
    reasons = []
    if band is None:
        reasons.append("holding_value_unreadable_or_open")
    if "see endnote" in evidence["asset_name"].casefold() or "see endnote" in cells.get("eif", "").casefold():
        reasons.append("asset_endnote_unresolved")
    if "value not readily ascertainable" in evidence["asset_name"].casefold():
        reasons.append("asset_value_description_conflicts_with_band")
    if band is not None and child_parent:
        reasons.append("nested_aggregate_may_double_count")
    if reasons:
        return "quarantined", _quarantine(row, reasons)
    return "holdings", {**evidence, "value_low": band[0], "value_high": band[1],
                        "report_period_end": meta["report_period_end"],
                        "holding_valuation_date": meta["holding_valuation_date"],
                        "holding_valuation_status": ("exact_period_end" if meta["holding_valuation_date"]
                                                     else "not_exact_on_cover")}


def extract_public_278e_pdf(pdf_path: Path, *, source_url: str, source_sha256: str,
                            expected_filer: str) -> dict:
    """Extract official White House source rows with exhaustive row disposition.

    The caller must archive and hash the PDF independently. No row from this
    function is eligible for production without identity, amendment and
    cross-report checks performed by the downstream candidate builder.
    """
    if not _SHA.fullmatch(source_sha256):
        raise OgeCatalogError("White House 278e evidence hash is invalid")
    url = urlsplit(source_url)
    if (url.scheme != "https" or url.hostname != "www.whitehouse.gov"
            or not url.path.startswith("/wp-content/uploads/")
            or not url.path.casefold().endswith(".pdf")):
        raise OgeCatalogError("White House 278e source URL is not an official PDF")
    if not isinstance(expected_filer, str) or _name_key(expected_filer) is None:
        raise OgeCatalogError("White House 278e expected filer is required")
    content = pdf_path.read_bytes()
    if (not content.startswith(b"%PDF-") or b"%%EOF" not in content[-4096:]
            or len(content) > MAX_PDF_BYTES):
        raise OgeCatalogError("White House 278e PDF envelope is invalid or too large")
    if hashlib.sha256(content).hexdigest() != source_sha256:
        raise OgeCatalogError("White House 278e PDF does not match archived SHA-256")
    try:
        import pdfplumber
    except ImportError:
        raise OgeCatalogError("White House 278e extraction requires pdfplumber") from None
    with pdfplumber.open(pdf_path) as document:
        if not 1 <= len(document.pages) <= MAX_PDF_PAGES:
            raise OgeCatalogError("White House 278e PDF page count is outside bounds")
        meta = _cover(document.pages[0].extract_text() or "", expected_filer)
        raw_rows: list[dict] = []
        unparsed_rows: list[dict] = []
        document_reasons: list[str] = []
        section_pages = {part: 0 for part, _ in _PARTS.values()}
        explicit_empty: set[str] = set()
        current_part: str | None = None
        current_owner = "Unknown"
        for page_number, page in enumerate(document.pages, 1):
            lines = page.extract_text_lines() or []
            words = page.extract_words() or []
            columns: dict[str, float] | None = None
            row: dict | None = None
            for line in lines:
                text = _compact(line.get("text", ""))
                if text == "Summary of Contents" or text == "Endnotes":
                    if row:
                        raw_rows.append(row)
                    row = None
                    current_part = None
                    break
                section_match = _SECTION.match(text)
                if section_match:
                    if row:
                        raw_rows.append(row)
                        row = None
                    number = int(section_match[1])
                    current_part, current_owner = _PARTS.get(number, (None, "Unknown"))
                    columns = None
                    if current_part:
                        section_pages[current_part] += 1
                    continue
                if current_part is None or re.search(r" - Page \d+\Z", text):
                    continue
                line_words = _words_at(words, float(line["top"]))
                header = _header(line_words, current_part) if text.startswith("# DESCRIPTION") else None
                if header:
                    if row:
                        raw_rows.append(row)
                        row = None
                    columns = header
                    continue
                if text == "None" or text.startswith("(N/A) - Not required"):
                    if row:
                        raw_rows.append(row)
                        row = None
                    explicit_empty.add(current_part)
                    continue
                if columns is None:
                    number_match = _ROW.match(text)
                    if number_match and line_words:
                        unparsed_rows.append({"section": current_part, "page_number": page_number,
                                              "row_number": number_match[1],
                                              "raw_columns": {"unparsed_line": text},
                                              "reasons": ["table_header_unrecognized"]})
                    continue
                number_match = _ROW.match(text)
                if number_match and line_words and float(line_words[0]["x0"]) < columns["description"] - 15:
                    if row:
                        raw_rows.append(row)
                    row = {"section": current_part, "owner": current_owner,
                           "page_number": page_number, "row_number": number_match[1],
                           **{key: [] for key in columns if key != "#"}}
                if row:
                    _append_line(row, line_words, columns)
            if row:
                raw_rows.append(row)
        holdings: list[dict] = []
        transactions: list[dict] = []
        quarantined: list[dict] = list(unparsed_rows)
        excluded: list[dict] = []
        counts: dict[tuple[str, str], int] = {}
        for row in raw_rows:
            key = row["section"], row["row_number"]
            counts[key] = counts.get(key, 0) + 1
        for row in raw_rows:
            key = row["section"], row["row_number"]
            if counts[key] > 1:
                row["raw_columns"] = {name: _compact(" ".join(value)) for name, value in row.items()
                                      if isinstance(value, list)}
                quarantined.append(_quarantine(row, ["duplicate_section_row_number"]))
                continue
            parent = row["row_number"]
            child_parent = parent if any(other["section"] == row["section"] and
                                         other["row_number"].startswith(parent + ".")
                                         for other in raw_rows) else None
            destination, parsed = _parse_row(row, meta, child_parent)
            {"holdings": holdings, "transactions": transactions,
             "quarantined": quarantined, "excluded": excluded}[destination].append(parsed)
        for part in ("part2", "part5", "part6"):
            if section_pages[part] == 0:
                document_reasons.append(f"asset_section_missing:{part}")
        if unparsed_rows:
            document_reasons.append("table_header_unrecognized")
        if meta["report_type"] in {"Annual", "Termination", "Annual Term"} and (
                section_pages["part7"] == 0 or
                not any(row["section"] == "part7" for row in raw_rows) and "part7" not in explicit_empty):
            document_reasons.append("part7_not_reconciled")
        if meta["report_type"] == "New Entrant" and any(row["section"] == "part7" for row in raw_rows):
            document_reasons.append("new_entrant_part7_unexpected")
        if meta["report_type"] in {"Termination", "Annual Term"} and (
                meta["termination_date"] and meta["filing_date"] < meta["termination_date"]):
            document_reasons.append("termination_signed_before_effective_date")
        return {"schema_version": SCHEMA, "parser_version": PARSER_VERSION,
                "source_id": "whitehouse_public", "form_type": "278e",
                "source_url": source_url, "source_sha256": source_sha256,
                **meta, "page_count": len(document.pages), "section_pages": section_pages,
                "printed_row_count": len(raw_rows) + len(unparsed_rows), "holdings": holdings,
                "transactions": transactions, "excluded": excluded,
                "quarantined": quarantined, "document_reasons": document_reasons,
                "requires_cross_report_dedup": any(row["section"] == "part7" for row in raw_rows),
                "production_qualification": "pending_identity_amendments_part7_dedup_and_quarantine"}
