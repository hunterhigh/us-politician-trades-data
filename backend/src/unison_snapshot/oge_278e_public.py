"""Strict source-row extraction for public White House OGE Form 278e PDFs.

This is an evidence extraction, not a canonical snapshot producer. In particular,
New Entrant and early-filed Termination assets have no proven point valuation date;
Part 7 transactions must be reconciled with 278-T reports before publication.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from .oge import OgeCatalogError
from .oge_annual import _range
from .ocr_geometry import OcrGeometryError, OcrPage, ocr_pdf_pages


SCHEMA = "whitehouse-public-278e-extraction/v1"
PARSER_VERSION = "whitehouse-278e-hybrid-geometry/v5"
TRUMP_2025_PARSER_VERSION = "whitehouse-278e-hybrid-geometry/v8"
TRUMP_2025_PREVIOUS_PARSER_VERSIONS = (
    "whitehouse-278e-hybrid-geometry/v6",
    "whitehouse-278e-hybrid-geometry/v7",
)
# The qualification layer names its immediately preceding reviewed parser.
TRUMP_2025_PREVIOUS_PARSER_VERSION = TRUMP_2025_PREVIOUS_PARSER_VERSIONS[-1]
TRUMP_2025_SOURCE_SHA256 = "1cc7951c6f72fab008e921903c9a1d03d41a9910239f954e208b501d608553a3"
TRUMP_2025_SOURCE_URL = ("https://www.whitehouse.gov/wp-content/uploads/2026/06/"
                         "President-Donald-J.-Trump-2025-Annual-Report.pdf")
TRUMP_2025_PAGE_COUNT = 927
TRUMP_2025_LEGACY_OCR_ENGINE = "tesseract 5.3.4"
LEGACY_PARSER_VERSIONS = ("whitehouse-278e-hybrid-geometry/v4",
                          "whitehouse-278e-positioned-text/v2")
SUPPORTED_PARSER_VERSIONS = (PARSER_VERSION, *LEGACY_PARSER_VERSIONS,
                             *TRUMP_2025_PREVIOUS_PARSER_VERSIONS,
                             TRUMP_2025_PARSER_VERSION)
MAX_PDF_BYTES = 200 * 1024 * 1024
MAX_PDF_PAGES = 1200
MAX_INLINE_OCR_PAGES = 100
OCR_CHECKPOINT_SCHEMA = "whitehouse-278e-ocr-checkpoint/v1"
OCR_SHARD_SCHEMA = "whitehouse-278e-ocr-geometry-shard/v1"
OCR_MINIMUM_ROW_MEAN_CONFIDENCE = 85.0
OCR_MINIMUM_CRITICAL_CONFIDENCE = 60.0
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SECTION = re.compile(
    r"^(?:Part\s+)?([1-9])(?:\.|:)\s+"
    r"(Filer's Employment Assets|Spouse's Employment Assets|Other Assets and Income|"
    r"Transactions|Liabilities|Gifts and Travel|Filer's Employment Agreements|"
    r"Filer's Sources|Filer's Positions)", re.I)
_SIGNATURE = re.compile(r"^/s/\s+(.+?)\s+\[electronically signed on\s+(\d{2}/\d{2}/\d{4})\s+by\s+(.+?)\s+in Integrity\.gov\]", re.I | re.M)
_BAND = re.compile(r"\$[\d,]+\s*-\s*\$[\d,]+")
_DAY = re.compile(r"\d{1,2}/\d{1,2}/\d{4}\Z")
_PARTS = {2: ("part2", "Self"), 5: ("part5", "Spouse"),
          6: ("part6", "Unknown"), 7: ("part7", "Unknown")}
_TRUMP_ACCOUNT = re.compile(r"^INVESTMENT\s+ACCOUNT\s*#\s*([1-9]\d*)$", re.I)
_TRUMP_WEAK_ACCOUNT = re.compile(r"^(?:Ac\s*)?#\s*([1-9]\d*)$", re.I)
_TRUMP_OTHER_SCOPES = {
    "FAMILY TRUST 1*": "family-trust-1",
    "DONALD J TRUMP": "donald-j-trump",
    "DONALD J. TRUMP REVOCABLE TRUST": "donald-j-trump-revocable-trust",
}


def parser_version_for_source(source_url: str, source_sha256: str) -> str:
    """Version the one scanned layout without churning unrelated 278e reports."""

    return (TRUMP_2025_PARSER_VERSION if
            source_url == TRUMP_2025_SOURCE_URL and
            source_sha256 == TRUMP_2025_SOURCE_SHA256 else PARSER_VERSION)


class OcrCheckpointPending(OgeCatalogError):
    """A bounded OCR run completed safely but more page shards remain."""

    def __init__(self, status: dict):
        super().__init__("White House 278e checkpointed OCR is pending")
        self.status = status


def _compact(value: str) -> str:
    return " ".join(value.split())


def _raw_text_columns(row: dict) -> dict[str, str]:
    """Serialize visible cell lists without mixing private OCR audit arrays."""

    columns: dict[str, str] = {}
    for name, value in row.items():
        if not isinstance(value, list) or name.startswith("_"):
            continue
        if not all(isinstance(item, str) for item in value):
            raise OgeCatalogError("White House 278e row contains a non-text cell value")
        columns[name] = _compact(" ".join(value))
    return columns


def _ocr_field_confidence(row: dict) -> dict[str, dict[str, float | int]]:
    values = row.get("_ocr_field_confidences", {})
    return {name: {"mean": round(sum(scores) / len(scores), 2),
                   "min": round(min(scores), 2), "word_count": len(scores)}
            for name, scores in values.items() if scores}


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
    if "," not in value:
        parts = [part for part in parts if part not in {
            "president", "vice", "the", "honorable", "mr", "mrs", "ms", "dr"}]
    if len(parts) < 2:
        return None
    if "," in value:
        return parts[0], parts[1]
    return parts[-1], parts[0]


def _ocr_cover(page, expected_filer: str) -> tuple[dict, list[str]]:
    """Read printed scan fields without treating reviewer dates as filer dates."""

    lines = [_compact(line.get("text", "")) for line in page.extract_text_lines()]
    normalized = "\n".join(lines).replace("Fonn", "Form").replace("fonn", "form")
    if (not re.search(r"OGE\s+Form\s+278e", normalized, re.I) or
            not re.search(r"Public\s+Financial\s+Disclosure\s+Report", normalized, re.I)):
        raise OgeCatalogError("White House OCR PDF is not an OGE Form 278e")
    report_match = re.search(r"Report\s+Type:\s*(Annual|New Entrant|Termination|Annual[ /]Termination|Annual Term)(?:\s+Report)?", normalized, re.I)
    if report_match is None:
        raise OgeCatalogError("White House OCR 278e report type is unverified")
    report_type = re.sub(r"\s+", " ", report_match[1]).casefold().replace("/", " ")
    report_type = {"annual": "Annual", "new entrant": "New Entrant",
                   "termination": "Termination", "annual termination": "Annual Term",
                   "annual term": "Annual Term"}[report_type]
    year_match = re.search(r"Year\s*\(Annual\s+Report\s+only\)\s*:\s*(20\d{2})", normalized, re.I)
    cover_year = int(year_match[1]) if year_match else None
    if report_type == "Annual" and (cover_year is None or cover_year < 2025):
        raise OgeCatalogError("White House OCR annual report year is unverified")
    if report_type != "Annual" and cover_year is not None:
        raise OgeCatalogError("White House OCR 278e report type conflicts with annual year")

    expected = _name_key(expected_filer)
    if expected is None:
        raise OgeCatalogError("White House OCR 278e expected filer is invalid")
    last, first = expected
    try:
        start = next(index for index, line in enumerate(lines)
                     if "filer's information" in line.casefold().replace("’", "'"))
    except StopIteration:
        raise OgeCatalogError("White House OCR 278e filer box is missing") from None
    candidates = []
    for line_index, line in enumerate(lines[start + 1:], start + 1):
        if "other federal government positions" in line.casefold():
            break
        tokens = re.findall(r"[A-Za-z]+", line)
        folded = [token.casefold() for token in tokens]
        if first in folded and last in folded:
            candidates.append((line_index, line, tokens, folded))
    if len(candidates) != 1:
        raise OgeCatalogError("White House OCR 278e filer does not uniquely match the cover")
    filer_line_index, filer_line, tokens, folded = candidates[0]
    first_index, last_index = folded.index(first), folded.index(last)
    identity_indexes = {first_index, last_index}
    position_tokens = [token for index, token in enumerate(tokens) if index not in identity_indexes]
    position_line = _compact(" ".join(position_tokens))
    if not position_line and filer_line_index + 1 < len(lines):
        next_line = lines[filer_line_index + 1]
        if "other federal government positions" not in next_line.casefold():
            position_line = next_line
    if not position_line:
        raise OgeCatalogError("White House OCR 278e position is unverified")

    appointment = re.search(r"Date\s+of\s+Appointment:\s*(\d{1,2}/\d{1,2}/\d{4})", normalized, re.I)
    termination = re.search(r"Date\s+of\s+Termination:\s*(\d{1,2}/\d{1,2}/\d{4})", normalized, re.I)
    appointment_on = _date(appointment[1]) if appointment else None
    termination_on = _date(termination[1]) if termination else None
    if report_type == "New Entrant" and appointment_on is None:
        raise OgeCatalogError("White House OCR New Entrant appointment date is unverified")
    if report_type in {"Termination", "Annual Term"} and termination_on is None:
        raise OgeCatalogError("White House OCR Termination date is unverified")
    # The scanned public form's explicitly entered annual year is the reporting
    # calendar year.  Keep this separate from the Integrity-generated text form,
    # whose tested export labels the filing cycle and is handled by _cover().
    period_end = f"{cover_year}-12-31" if report_type == "Annual" else (
        termination_on if report_type in {"Termination", "Annual Term"} else None)
    valuation_date = period_end if report_type == "Annual" else None
    filer_name = f"{tokens[first_index]} {tokens[last_index]}"
    return ({"filer_name": filer_name, "position_line_raw": position_line,
             "position_title_raw": position_line, "agency_office_raw": None,
             "report_type": report_type, "cover_report_year": cover_year,
             "report_period_end": period_end, "holding_valuation_date": valuation_date,
             "appointment_date": appointment_on, "termination_date": termination_on,
             "filing_date": None, "signature_text": None,
             "ocr_filer_line": filer_line},
            ["filer_handwritten_signature_or_date_unverified"])


def _row_number(value: str) -> str | None:
    token = value.split(maxsplit=1)[0] if value.split() else ""
    token = token.strip("[]|(){}:,;")
    token = token.rstrip(".")
    token = token.replace("I", "1").replace("l", "1").replace("|", "1")
    return token if re.fullmatch(r"[1-9]\d*(?:\.[1-9]\d*)*", token) else None


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


def _trump_part6_header(words: list[dict]) -> dict[str, float] | None:
    """Recognize this scan's damaged labels only with its complete geometry."""

    anchors: dict[str, float] = {}
    for word in sorted(words, key=lambda item: float(item["x0"])):
        raw = word["text"].casefold()
        name = re.sub(r"[^a-z#]", "", raw)
        x = float(word["x0"])
        if name == "#" and 8 <= x <= 25:
            anchors["#"] = x
        elif name in {"de", "descriptic", "description"} and 30 <= x <= 55:
            anchors["description"] = x
        elif name.startswith("eif") and 440 <= x <= 465:
            anchors["eif"] = x
        elif name == "value" and 465 <= x <= 495:
            anchors["value"] = x
        elif name == "income" and 545 <= x <= 580:
            anchors["income"] = x
        elif name == "type" and 575 <= x <= 605:
            anchors["type"] = x
        elif name == "income" and 600 <= x <= 625:
            anchors["income_amount_label"] = x
        elif name == "amount" and 625 <= x <= 655:
            anchors["amount"] = x
    required = ("#", "description", "eif", "value", "income", "type",
                "income_amount_label", "amount")
    if not all(key in anchors for key in required):
        return None
    if list(anchors[key] for key in required) != sorted(anchors[key] for key in required):
        return None
    return {key: anchors[key] for key in ("#", "description", "eif", "value", "income")}


def _header(words: list[dict], part: str, *, trump_part6: bool = False) -> dict[str, float] | None:
    if trump_part6 and part == "part6":
        return _trump_part6_header(words)
    by_name: dict[str, float] = {}
    for word in words:
        raw = word["text"].casefold()
        name = "#" if "#" in raw else re.sub(r"[^a-z]", "", raw)
        if name:
            by_name.setdefault(name, float(word["x0"]))
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
            if isinstance(word.get("ocr_confidence"), (int, float)):
                row.setdefault("_ocr_confidences", []).append(float(word["ocr_confidence"]))


def _append_trump_part6_line(row: dict, line_words: list[dict],
                             columns: dict[str, float]) -> None:
    """Separate EIF/Value OCR fusion without borrowing from the income column."""

    starts = [columns[key] for key in ("description", "eif", "value", "income")]
    for word in line_words:
        x = float(word["x0"])
        if x < starts[0] - 6:
            continue
        raw = str(word["text"])
        field = ("description", "eif", "value", "income")[max(
            i for i, start in enumerate(starts) if x >= start - 6)]
        if field == "description":
            anchor = {
                "original_text": str(word["text"]), "x0": x,
                "x1": float(word["x1"]), "top": float(word["top"]),
                "ocr_confidence": word.get("ocr_confidence")}
            row.setdefault("_description_cell_anchor", anchor)
            row.setdefault("_description_anchor", anchor)
        split = raw.find("$")
        if (field == "eif" and split > 0 and
                float(word.get("x1", x)) >= starts[2] - 6 and
                re.fullmatch(r"(?:N/A|Yes|No)?[_|\s]*", raw[:split], re.I)):
            prefix = raw[:split].rstrip("_| ")
            if prefix:
                row["eif"].append(prefix)
            row["value"].append(raw[split:])
            fields = ("eif", "value") if prefix else ("value",)
            value_text = raw[split:]
            value_repair = "split_eif_value_at_printed_dollar"
            row.setdefault("_ocr_word_repairs", []).append({
                "page_number": row["page_number"], "original_text": raw,
                "x0": x, "x1": float(word["x1"]),
                "method": "split_eif_value_at_printed_dollar"})
        elif field == "value" and re.match(r"^[_|]+\$", raw):
            row["value"].append(raw.lstrip("_|"))
            fields = ("value",)
            value_text = raw.lstrip("_|")
            value_repair = "strip_leading_table_border_before_dollar"
            row.setdefault("_ocr_word_repairs", []).append({
                "page_number": row["page_number"], "original_text": raw,
                "x0": x, "x1": float(word["x1"]),
                "method": "strip_leading_table_border_before_dollar"})
        else:
            row[field].append(raw)
            fields = (field,)
            value_text = raw if field == "value" else None
            value_repair = None
        if value_text is not None:
            row.setdefault("_value_word_evidence", []).append({
                "original_text": raw, "value_text": value_text,
                "x0": x, "x1": float(word["x1"]),
                "top": float(word["top"]),
                "ocr_confidence": word.get("ocr_confidence"),
                "repair_method": value_repair})
        if isinstance(word.get("ocr_confidence"), (int, float)):
            score = float(word["ocr_confidence"])
            row.setdefault("_ocr_confidences", []).append(score)
            for name in fields:
                row.setdefault("_ocr_field_confidences", {}).setdefault(name, []).append(score)


def _recover_trump_number_border_prefix(row: dict, word: dict,
                                        columns: dict[str, float]) -> None:
    """Keep a printed asset-name prefix fused into the numbered table border.

    This does not certify the printed row number: OCR can drop its leading
    digits, so the physical locator remains the row identity.
    """

    raw = str(word["text"])
    match = re.fullmatch(r"([0-9]{1,4})_{2,4}\|(\*{0,2})([A-Za-z][A-Za-z.&-]*)", raw)
    if not match or float(word["x0"]) >= columns["description"] - 8:
        return
    prefix = match[3]
    row["description"].append(prefix)
    row["_description_border_split_evidence"] = {
        "method": "source_bound_part6_number_border_split/v1",
        "original_text": raw, "number_glyph": match[1],
        "footnote_marker": match[2] or None, "description_prefix": prefix,
        "x0": float(word["x0"]), "x1": float(word["x1"]),
        "top": float(word["top"]),
        "ocr_confidence": word.get("ocr_confidence")}
    row["_description_anchor"] = {
        "original_text": raw, "x0": float(word["x0"]),
        "x1": float(word["x1"]), "top": float(word["top"]),
        "ocr_confidence": word.get("ocr_confidence"),
        "embedded_in_number_border": True}
    if isinstance(word.get("ocr_confidence"), (int, float)):
        row.setdefault("_ocr_field_confidences", {}).setdefault(
            "description", []).append(float(word["ocr_confidence"]))


def _trump_part6_row_relationships(rows: list[dict]) -> None:
    """Record source-bound neighbors without changing a damaged row number."""

    scopes: dict[str, list[dict]] = {}
    for row in rows:
        if row["section"] == "part6" and row.get("account_scope"):
            scopes.setdefault(row["account_scope"], []).append(row)
    for scope, scope_rows in scopes.items():
        nested_visible = any(re.fullmatch(r"[0-9]+\.[0-9]+", row["row_number"])
                             for row in scope_rows)
        for index, row in enumerate(scope_rows):
            number_word = row.get("_number_column_word")
            raw_number = number_word["original_text"] if number_word else ""
            body_glyph = (bool(re.fullmatch(r"[0-9]{1,4}(?:_{2,4}\|?)?",
                                            raw_number)) or
                          bool(re.fullmatch(
                              r"[0-9]{1,4}_{2,4}\|\*{0,2}[A-Za-z][A-Za-z.&-]*",
                              raw_number)))
            description = " ".join(row.get("description", []))
            aggregate = bool(re.search(r"\b(?:SUBTOTAL|TOTAL)\b", description, re.I))
            anchor = row.get("_description_anchor")
            cell_anchor = row.get("_description_cell_anchor")

            def neighbor(other: dict | None) -> dict | None:
                if other is None:
                    return None
                word = other.get("_number_column_word")
                return {"source_row_locator": other["source_row_locator"],
                        "row_number": other["row_number"],
                        "number_column_text": word["original_text"] if word else None,
                        "description_anchor": other.get("_description_anchor"),
                        "description_cell_anchor": other.get("_description_cell_anchor")}

            previous = scope_rows[index - 1] if index else None
            following = scope_rows[index + 1] if index + 1 < len(scope_rows) else None
            basis = []
            if body_glyph:
                basis.append("printed_number_column_body_glyph")
            if cell_anchor:
                basis.append("separate_description_cell")
            if row.get("_value_word_evidence"):
                basis.append("separate_value_cell")
            if not nested_visible:
                basis.append("no_visible_decimal_hierarchy_in_account_scope")
            if aggregate:
                basis.append("aggregate_label_detected")
            if "account_heading_unverified" in row.get("_row_reasons", []):
                basis.append("account_heading_unverified")
            flat_candidate = (body_glyph and cell_anchor and
                              bool(row.get("_value_word_evidence")) and
                              not nested_visible and not aggregate and
                              "account_heading_unverified" not in
                              row.get("_row_reasons", []))
            row["_row_relationship_evidence"] = {
                "method": "source_bound_part6_neighbor_geometry/v1",
                "classification": ("flat_numbered_item_candidate" if flat_candidate
                                   else "unresolved"),
                "basis": basis, "source_row_locator": row["source_row_locator"],
                "account_scope": scope, "number_column_word": number_word,
                "description_anchor": anchor,
                "description_cell_anchor": cell_anchor,
                "previous_row": neighbor(previous), "next_row": neighbor(following),
                "parent_source_row_locator": None,
                "scope_has_visible_decimal_numbering": nested_visible}


def _trump_value_geometry_evidence(row: dict) -> dict | None:
    columns = row.get("_value_column_bounds")
    words = row.get("_value_word_evidence")
    if not isinstance(columns, dict) or not isinstance(words, list) or not words:
        return None
    return {"method": "source_bound_part6_value_column/v1",
            "column_bounds": columns, "words": words}


def _trump_value_geometry_valid(row: dict) -> bool:
    """A Value band must be assembled only from this source's Value cell."""

    geometry = _trump_value_geometry_evidence(row)
    if geometry is None:
        return False
    bounds = geometry["column_bounds"]
    eif, value, income = (bounds[key] for key in
                          ("eif_start", "value_start", "income_start"))
    if not 440 <= eif < value < income <= 580:
        return False
    words = geometry["words"]
    if _compact(" ".join(word["value_text"] for word in words)) != _compact(
            " ".join(row["value"])):
        return False
    for word in words:
        x0, x1 = word["x0"], word["x1"]
        raw, extracted = word["original_text"], word["value_text"]
        repair = word["repair_method"]
        if not (x0 < x1 <= income - 2 and extracted):
            return False
        if repair == "split_eif_value_at_printed_dollar":
            split = raw.find("$")
            if (not eif - 8 <= x0 < value - 6 or x1 < value - 6 or
                    split <= 0 or extracted != raw[split:] or
                    not re.fullmatch(r"(?:N/A|Yes|No)?[_|\s]*",
                                     raw[:split], re.I)):
                return False
        elif repair == "strip_leading_table_border_before_dollar":
            if not (value - 6 <= x0 < income - 8 and
                    re.match(r"^[_|]+\$", raw) and
                    extracted == raw.lstrip("_|")):
                return False
        elif repair is None:
            if not (value - 6 <= x0 < income - 8 and extracted == raw):
                return False
        else:
            return False
    return True


def _quarantine(row: dict, reasons: list[str]) -> dict:
    result = {"section": row["section"], "page_number": row["page_number"],
            "row_number": row["row_number"], "raw_columns": row["raw_columns"],
            "owner": row.get("owner", "Unknown"),
            "owner_evidence": row.get("owner_evidence"),
            "reasons": sorted(set(reasons))}
    if row.get("account_scope"):
        result["account_scope"] = row["account_scope"]
        result["account_scope_evidence"] = row.get("account_scope_evidence")
    if row.get("source_row_locator"):
        result["source_row_locator"] = row["source_row_locator"]
    if row.get("_ocr_word_repairs"):
        result["ocr_word_repairs"] = row["_ocr_word_repairs"]
    if row.get("_ocr_field_confidences"):
        result["ocr_field_confidence"] = _ocr_field_confidence(row)
    geometry = _trump_value_geometry_evidence(row)
    if geometry:
        result["value_geometry_evidence"] = geometry
    if row.get("_row_relationship_evidence"):
        result["row_relationship_evidence"] = row["_row_relationship_evidence"]
    if row.get("_description_border_split_evidence"):
        result["description_border_split_evidence"] = row["_description_border_split_evidence"]
    if row.get("owner_evidence_conflict"):
        result["owner_evidence_conflict"] = row["owner_evidence_conflict"]
    confidences = row.get("_ocr_confidences", [])
    if confidences:
        result["ocr_mean_confidence"] = round(sum(confidences) / len(confidences), 2)
        result["ocr_min_confidence"] = round(min(confidences), 2)
    return result


def _explicit_part6_owner(description: str) -> str | None:
    """Only account headings that themselves state the beneficiary/ownership."""
    if re.fullmatch(r"Joint Brokerage Account #\d+", description, re.I):
        return "Joint"
    if re.fullmatch(r"Child Brokerage \d+", description, re.I):
        return "Dependent Child"
    return None


def _part6_endnote_owners(pages: list[object]) -> dict[str, list[dict]]:
    """Bind explicit ownership labels to exact Part 6 row numbers only."""
    found: dict[str, list[dict]] = {}
    in_endnotes = False
    for page_number, page in enumerate(pages, 1):
        for line in (page.extract_text() or "").splitlines():
            line = _compact(line)
            if line == "Endnotes":
                in_endnotes = True
                continue
            if line == "Summary of Contents":
                return found
            if not in_endnotes:
                continue
            match = re.match(r"^6\.\s+([1-9]\d*(?:\.\d+)*)\s+"
                             r"(Spousal asset|Dependent child asset|Filer(?:'s|’s) asset)\.\s*", line, re.I)
            if match:
                owner = {"spousal asset": "Spouse", "dependent child asset": "Dependent Child",
                         "filer's asset": "Self", "filer’s asset": "Self"}[match[2].casefold()]
                found.setdefault(match[1], []).append({
                    "owner": owner, "basis": "explicit_part6_endnote",
                    "page_number": page_number, "row_number": match[1], "text": line})
    return found


def _assign_part6_owners(raw_rows: list[dict], pages: list[object]) -> None:
    parents: dict[str, dict] = {}
    for row in raw_rows:
        if row["section"] != "part6":
            continue
        description = _compact(" ".join(row["description"]))
        owner = _explicit_part6_owner(description)
        if owner:
            parents[row["row_number"]] = {
                "owner": owner, "basis": "explicit_part6_parent_account",
                "page_number": row["page_number"], "row_number": row["row_number"],
                "text": description}
    endnotes = _part6_endnote_owners(pages)
    for row in raw_rows:
        if row["section"] != "part6":
            continue
        number = row["row_number"]
        candidates = list(endnotes.get(number, []))
        candidates += [evidence for parent, evidence in parents.items()
                       if number.startswith(parent + ".")]
        if not candidates:
            continue
        owners = {evidence["owner"] for evidence in candidates}
        if len(owners) == 1:
            row["owner"] = owners.pop()
            row["owner_evidence"] = candidates
        else:
            row["owner_evidence_conflict"] = candidates


def _parse_row(row: dict, meta: dict, child_parent: str | None, *,
               trump_source_bound: bool = False) -> tuple[str, dict]:
    cells = {key: _compact(" ".join(value)) for key, value in row.items()
             if key in {"description", "eif", "value", "type", "date", "amount"}}
    row["raw_columns"] = cells
    evidence = {"section": row["section"], "page_number": row["page_number"],
                "row_number": row["row_number"], "asset_name": cells.get("description", ""),
                "owner": row["owner"], "raw_columns": cells}
    if row.get("account_scope"):
        evidence["account_scope"] = row["account_scope"]
        evidence["account_scope_evidence"] = row.get("account_scope_evidence")
    if row.get("source_row_locator"):
        evidence["source_row_locator"] = row["source_row_locator"]
    if row.get("_ocr_word_repairs"):
        evidence["ocr_word_repairs"] = row["_ocr_word_repairs"]
    if row.get("_ocr_field_confidences"):
        evidence["ocr_field_confidence"] = _ocr_field_confidence(row)
    geometry = _trump_value_geometry_evidence(row)
    if geometry:
        evidence["value_geometry_evidence"] = geometry
    if row.get("_row_relationship_evidence"):
        evidence["row_relationship_evidence"] = row["_row_relationship_evidence"]
    if row.get("_description_border_split_evidence"):
        evidence["description_border_split_evidence"] = row["_description_border_split_evidence"]
    confidences = row.get("_ocr_confidences", [])
    if confidences:
        evidence["ocr_mean_confidence"] = round(sum(confidences) / len(confidences), 2)
        evidence["ocr_min_confidence"] = round(min(confidences), 2)
    if row.get("owner_evidence"):
        evidence["owner_evidence"] = row["owner_evidence"]
    if row["section"] == "part7":
        kind = cells.get("type", "").casefold()
        when = _date(cells.get("date", ""))
        band = _range(cells.get("amount", ""))
        reasons = list(row.get("_row_reasons", []))
        if not evidence["asset_name"] or "see endnote" in evidence["asset_name"].casefold():
            reasons.append("transaction_asset_unresolved")
        if kind not in {"purchase", "sale", "exchange"}:
            reasons.append("transaction_type_unreadable")
        if when is None or (meta["report_type"] == "Annual" and
                            not when.startswith(meta["report_period_end"][:4] + "-")):
            reasons.append("transaction_date_unreadable_or_outside_period")
        if band is None:
            reasons.append("transaction_amount_unreadable_or_open")
        if confidences and (evidence["ocr_mean_confidence"] < OCR_MINIMUM_ROW_MEAN_CONFIDENCE or
                            evidence["ocr_min_confidence"] < OCR_MINIMUM_CRITICAL_CONFIDENCE):
            reasons.append("transaction_ocr_confidence_below_threshold")
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
    reasons = list(row.get("_row_reasons", []))
    recovered_reasons: list[str] = []
    if (trump_source_bound and row["section"] == "part6" and
            "row_number_ocr_unreadable" in reasons and
            row.get("source_row_locator") and row.get("account_scope") and
            "account_heading_unverified" not in reasons):
        reasons.remove("row_number_ocr_unreadable")
        recovered_reasons.append("row_number_ocr_unreadable")
    if row.get("owner_evidence_conflict"):
        reasons.append("owner_evidence_conflicts")
    if band is None:
        reasons.append("holding_value_unreadable_or_open")
    if trump_source_bound and row["section"] == "part6" and band is not None and (
            not _trump_value_geometry_valid(row)):
        reasons.append("holding_value_geometry_invalid")
    if "see endnote" in evidence["asset_name"].casefold() or "see endnote" in cells.get("eif", "").casefold():
        reasons.append("asset_endnote_unresolved")
    if "value not readily ascertainable" in evidence["asset_name"].casefold():
        reasons.append("asset_value_description_conflicts_with_band")
    if band is not None and child_parent:
        reasons.append("nested_aggregate_may_double_count")
    if confidences and (evidence["ocr_mean_confidence"] < OCR_MINIMUM_ROW_MEAN_CONFIDENCE or
                        evidence["ocr_min_confidence"] < OCR_MINIMUM_CRITICAL_CONFIDENCE):
        if trump_source_bound and row["section"] == "part6":
            recovered_reasons.append("holding_ocr_confidence_below_threshold")
        else:
            reasons.append("holding_ocr_confidence_below_threshold")
    if reasons:
        return "quarantined", _quarantine(row, reasons)
    if trump_source_bound and row["section"] == "part6":
        if geometry is None:
            return "quarantined", _quarantine(row, ["holding_value_geometry_missing"])
        evidence["parser_recovery"] = {
            "method": "source_bound_part6_structured_row/v1",
            "original_quarantine_reasons": sorted(recovered_reasons)}
    return "holdings", {**evidence, "value_low": band[0], "value_high": band[1],
                        "report_period_end": meta["report_period_end"],
                        "holding_valuation_date": meta["holding_valuation_date"],
                        "holding_valuation_status": ("exact_period_end" if meta["holding_valuation_date"]
                                                     else "not_exact_on_cover")}


def _extract_page_rows(pages: list[object], meta: dict, *,
                       initial_reasons: list[str], source_url: str | None = None,
                       source_sha256: str | None = None) -> dict:
    trump_layout = (parser_version_for_source(source_url, source_sha256) ==
                    TRUMP_2025_PARSER_VERSION)
    raw_rows: list[dict] = []
    unparsed_rows: list[dict] = []
    document_reasons = list(initial_reasons)
    section_pages = {part: 0 for part, _ in _PARTS.values()}
    explicit_empty: set[str] = set()
    current_part: str | None = None
    current_owner = "Unknown"
    current_scope: dict | None = None
    known_columns: dict[str, dict[str, float]] = {}
    reached_summary = False
    for page_number, page in enumerate(pages, 1):
        lines = page.extract_text_lines() or []
        words = page.extract_words() or []
        # This scan prints an OCR-damaged "Part i" instead of "Part 7" on
        # transaction pages.  The page-level instructions and transaction
        # header are both present on every such page; never carry Part 6 into it.
        transaction_page = trump_layout and any(
            re.search(r"\bPart\s*7\b", _compact(line.get("text", "")), re.I)
            and float(line.get("top", 1000)) < 45 for line in lines) and any(
            re.search(r"\bDescription\s+Type\s+Date\s+Amount\b",
                      _compact(line.get("text", "")), re.I)
            and float(line.get("top", 1000)) < 110 for line in lines)
        if transaction_page:
            current_part, current_owner, current_scope = "part7", "Unknown", None
            section_pages["part7"] += 1
        # Integrity.gov continuation pages repeat the table header but omit the
        # numbered section heading.  Preserve the active section and its column
        # geometry until an explicit new section, endnotes, or summary changes it.
        columns: dict[str, float] | None = known_columns.get(current_part)
        row: dict | None = None
        for line_index, line in enumerate(lines, 1):
            text = _compact(line.get("text", ""))
            if text == "Summary of Contents" or text == "Endnotes":
                if row:
                    raw_rows.append(row)
                row = None
                current_part = None
                reached_summary = text == "Summary of Contents"
                break
            section_match = _SECTION.match(text)
            if section_match:
                if row:
                    raw_rows.append(row)
                    row = None
                number = int(section_match[1])
                previous_part = current_part
                current_part, current_owner = _PARTS.get(number, (None, "Unknown"))
                if current_part != previous_part:
                    current_scope = None
                columns = known_columns.get(current_part) if current_part else None
                if current_part and not (current_part == "part7" and transaction_page):
                    section_pages[current_part] += 1
                continue
            if current_part is None or re.search(r" - Page \d+\Z", text):
                continue
            line_words = line.get("words") or _words_at(words, float(line["top"]))
            header = (_header(line_words, current_part,
                              trump_part6=trump_layout) if line_words else None)
            if header:
                if row:
                    raw_rows.append(row)
                    row = None
                columns = header
                known_columns[current_part] = header
                continue
            if trump_layout and current_part == "part6":
                account = _TRUMP_ACCOUNT.fullmatch(text)
                other_scope = _TRUMP_OTHER_SCOPES.get(text.upper())
                weak_account = _TRUMP_WEAK_ACCOUNT.fullmatch(text)
                if account or other_scope or weak_account:
                    if row:
                        raw_rows.append(row)
                        row = None
                    if account:
                        scope_id = f"investment-account-{account[1]}"
                        verified = True
                    elif other_scope:
                        scope_id, verified = other_scope, True
                    else:
                        scope_id, verified = f"investment-account-{weak_account[1]}", False
                    current_scope = {
                        "id": scope_id, "verified": verified,
                        "evidence": {"page_number": page_number, "text": text}}
                    continue
            if (re.fullmatch(r"(?:[1Il|][.]?\s+)?None", text, re.I) or
                    text.startswith("(N/A) - Not required")):
                if row:
                    raw_rows.append(row)
                    row = None
                explicit_empty.add(current_part)
                continue
            number = _row_number(text)
            if columns is None:
                if number and line_words:
                    unparsed = {"section": current_part, "page_number": page_number,
                                "row_number": number,
                                "raw_columns": {"unparsed_line": text},
                                "reasons": ["table_header_unrecognized"]}
                    if trump_layout and current_part == "part6" and current_scope:
                        unparsed["account_scope"] = current_scope["id"]
                        unparsed["account_scope_evidence"] = current_scope["evidence"]
                    if trump_layout and current_part == "part6":
                        unparsed["source_row_locator"] = (
                            f"p{page_number}-y{round(float(line['top']) * 10)}")
                    unparsed_rows.append(unparsed)
                continue
            first_in_number_column = bool(line_words) and (
                float(line_words[0]["x0"]) < columns["description"] - 8)
            looks_numbered = bool(line_words) and bool(re.match(
                r"[0-9Il|]", str(line_words[0].get("text", ""))))
            # A damaged printed number may vanish entirely.  On this fixed
            # Part 6 layout, a fresh description plus an EIF/Value cell is a
            # visible row even without a trustworthy number.  Keep it as an
            # individually quarantined row instead of merging it into the
            # preceding asset or silently losing it.
            trump_geometry_row = (trump_layout and current_part == "part6" and
                                  any(columns["description"] - 6 <= float(word["x0"]) <
                                      columns["eif"] - 8 for word in line_words) and
                                  any(columns["eif"] - 8 <= float(word["x0"]) <
                                      columns["income"] - 8 for word in line_words))
            if ((first_in_number_column and len(line_words) > 1 and
                 (number or looks_numbered)) or trump_geometry_row):
                if row:
                    raw_rows.append(row)
                top = int(round(float(line["top"]) * 10))
                row = {"section": current_part, "owner": current_owner,
                       "page_number": page_number,
                       "row_number": number or f"ocr-p{page_number}-y{top}",
                       **{key: [] for key in columns if key != "#"}}
                if number is None:
                    row["_row_reasons"] = ["row_number_ocr_unreadable"]
                if trump_layout and current_part == "part6":
                    row["source_row_locator"] = f"p{page_number}-y{top}"
                    row["_value_column_bounds"] = {
                        "eif_start": columns["eif"],
                        "value_start": columns["value"],
                        "income_start": columns["income"]}
                    if current_scope:
                        row["account_scope"] = current_scope["id"]
                        row["account_scope_evidence"] = current_scope["evidence"]
                    if not current_scope or not current_scope["verified"]:
                        row.setdefault("_row_reasons", []).append("account_heading_unverified")
                    if first_in_number_column:
                        first_word = line_words[0]
                        row["_number_column_word"] = {
                            "original_text": str(first_word["text"]),
                            "x0": float(first_word["x0"]),
                            "x1": float(first_word["x1"]),
                            "top": float(first_word["top"]),
                            "ocr_confidence": first_word.get("ocr_confidence")}
                        _recover_trump_number_border_prefix(row, first_word, columns)
                if ((not trump_layout or current_part != "part6" or first_in_number_column) and
                        isinstance(line_words[0].get("ocr_confidence"), (int, float))):
                    score = float(line_words[0]["ocr_confidence"])
                    row["_ocr_confidences"] = [score]
                    if trump_layout and current_part == "part6":
                        row.setdefault("_ocr_field_confidences", {})["row_number"] = [score]
            if row:
                if trump_layout and current_part == "part6":
                    _append_trump_part6_line(row, line_words, columns)
                else:
                    _append_line(row, line_words, columns)
        if row:
            raw_rows.append(row)
        if reached_summary:
            break

    holdings: list[dict] = []
    transactions: list[dict] = []
    quarantined: list[dict] = list(unparsed_rows)
    excluded: list[dict] = []
    if trump_layout:
        _trump_part6_row_relationships(raw_rows)
    counts: dict[tuple[str, str], int] = {}
    for row in raw_rows:
        key = (row["section"], row["source_row_locator"] if trump_layout and
               row["section"] == "part6" else row["row_number"])
        counts[key] = counts.get(key, 0) + 1
    if not trump_layout:
        _assign_part6_owners(raw_rows, pages)
    for row in raw_rows:
        scope = row.get("account_scope") if trump_layout and row["section"] == "part6" else None
        key = (row["section"], row["source_row_locator"] if trump_layout and
               row["section"] == "part6" else row["row_number"])
        if counts[key] > 1:
            row["raw_columns"] = _raw_text_columns(row)
            quarantined.append(_quarantine(row, ["duplicate_section_row_number"]))
            continue
        parent = row["row_number"]
        child_parent = parent if any(other["section"] == row["section"] and
                                     (not trump_layout or row["section"] != "part6" or
                                      other.get("account_scope") == scope) and
                                     other["row_number"].startswith(parent + ".")
                                     for other in raw_rows) else None
        destination, parsed = _parse_row(row, meta, child_parent,
                                         trump_source_bound=trump_layout)
        {"holdings": holdings, "transactions": transactions,
         "quarantined": quarantined, "excluded": excluded}[destination].append(parsed)
    for part in ("part2", "part5", "part6"):
        if section_pages[part] == 0:
            document_reasons.append(f"asset_section_missing:{part}")
        elif (part not in explicit_empty and
              not any(row["section"] == part for row in raw_rows + unparsed_rows)):
            document_reasons.append(f"asset_section_unreconciled:{part}")
    if unparsed_rows:
        document_reasons.append("table_header_unrecognized")
    if meta["report_type"] in {"Annual", "Termination", "Annual Term"} and (
            section_pages["part7"] == 0 or
            not any(row["section"] == "part7" for row in raw_rows) and "part7" not in explicit_empty):
        document_reasons.append("part7_not_reconciled")
    if meta["report_type"] == "New Entrant" and any(
            row["section"] == "part7" for row in raw_rows):
        document_reasons.append("new_entrant_part7_unexpected")
    if (meta["report_type"] in {"Termination", "Annual Term"} and
            meta.get("termination_date") and meta.get("filing_date") and
            meta["filing_date"] < meta["termination_date"]):
        document_reasons.append("termination_signed_before_effective_date")
    result = {"section_pages": section_pages,
            "explicit_empty_sections": sorted(explicit_empty),
            "printed_row_count": len(raw_rows) + len(unparsed_rows), "holdings": holdings,
            "transactions": transactions, "excluded": excluded,
            "quarantined": quarantined, "document_reasons": sorted(set(document_reasons)),
            "requires_cross_report_dedup": any(
                row["section"] == "part7" for row in raw_rows + unparsed_rows)}
    if trump_layout:
        # Disposition conservation covers rows found in the OCR geometry; it
        # does not prove that every row printed in the PDF was recognized.
        result["source_row_census_status"] = "ocr_detected_rows_only"
        result["source_row_census_complete"] = False
    return result


def extract_public_278e_pdf(pdf_path: Path, *, source_url: str, source_sha256: str,
                            expected_filer: str, ocr_executable: str | None = None) -> dict:
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
        native_cover = document.pages[0].extract_text() or ""
        cover_reasons: list[str] = []
        ocr_engine = None
        extraction_method = "native_pdf_text"
        try:
            meta = _cover(native_cover, expected_filer)
            pages = list(document.pages)
        except OgeCatalogError:
            if len(document.pages) > MAX_INLINE_OCR_PAGES:
                raise OgeCatalogError("White House 278e requires checkpointed OCR") from None
            try:
                pages, ocr_engine = ocr_pdf_pages(
                    document, executable=ocr_executable, max_pages=MAX_INLINE_OCR_PAGES)
            except OcrGeometryError as exc:
                raise OgeCatalogError(f"White House 278e OCR failed: {exc}") from None
            meta, cover_reasons = _ocr_cover(pages[0], expected_filer)
            extraction_method = "tesseract_ocr_geometry"
        rows = _extract_page_rows(pages, meta, initial_reasons=cover_reasons,
                                  source_url=source_url, source_sha256=source_sha256)
        return {"schema_version": SCHEMA,
                "parser_version": parser_version_for_source(source_url, source_sha256),
                "source_id": "whitehouse_public", "form_type": "278e",
                "source_url": source_url, "source_sha256": source_sha256,
                **meta, "page_count": len(document.pages),
                "extraction_method": extraction_method, "ocr_engine": ocr_engine,
                **rows,
                "production_qualification": "pending_identity_amendments_part7_dedup_and_quarantine"}


def _write_checkpoint_shard(path: Path, value: dict) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise OgeCatalogError("White House 278e OCR checkpoint shard conflicts")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                     suffix=".tmp", delete=False) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(path)


def extract_public_278e_pdf_checkpointed(
        pdf_path: Path, *, source_url: str, source_sha256: str, expected_filer: str,
        checkpoint_root: Path, page_limit: int = 50, shard_size: int = 25,
        ocr_executable: str | None = None,
        legacy_checkpoint_root: Path | None = None) -> dict:
    """Resume bounded OCR shards and merge only after every source page exists.

    Each shard is immutable and bound to the PDF hash, parser version, page
    range, and Tesseract version. Incomplete work raises ``OcrCheckpointPending``
    so callers can persist progress without recording a parser failure.
    """

    if (type(page_limit) is not int or type(shard_size) is not int or
            not 1 <= shard_size <= 50 or not shard_size <= page_limit <= 100):
        raise OgeCatalogError("White House 278e OCR checkpoint bounds are invalid")
    if not _SHA.fullmatch(source_sha256):
        raise OgeCatalogError("White House 278e evidence hash is invalid")
    url = urlsplit(source_url)
    if (url.scheme != "https" or url.hostname != "www.whitehouse.gov" or
            not url.path.startswith("/wp-content/uploads/") or
            not url.path.casefold().endswith(".pdf")):
        raise OgeCatalogError("White House 278e source URL is not an official PDF")
    if not isinstance(expected_filer, str) or _name_key(expected_filer) is None:
        raise OgeCatalogError("White House 278e expected filer is required")
    version = parser_version_for_source(source_url, source_sha256)
    if legacy_checkpoint_root is not None and version != TRUMP_2025_PARSER_VERSION:
        raise OgeCatalogError("White House legacy OCR reuse is not source-bound")
    content = pdf_path.read_bytes()
    if (not content.startswith(b"%PDF-") or b"%%EOF" not in content[-4096:] or
            len(content) > MAX_PDF_BYTES or
            hashlib.sha256(content).hexdigest() != source_sha256):
        raise OgeCatalogError("White House 278e checkpoint PDF is invalid")
    try:
        import pdfplumber
    except ImportError:
        raise OgeCatalogError("White House 278e extraction requires pdfplumber") from None
    checkpoint_root = Path(checkpoint_root)
    reuse_legacy = legacy_checkpoint_root is not None
    shard_root = Path(legacy_checkpoint_root) if reuse_legacy else checkpoint_root
    if reuse_legacy and (shard_root.resolve() == checkpoint_root.resolve() or
                         any(checkpoint_root.glob("pages-*.json"))):
        raise OgeCatalogError("White House legacy OCR reuse cannot overwrite checkpoints")
    with pdfplumber.open(pdf_path) as document:
        page_count = len(document.pages)
        if not MAX_INLINE_OCR_PAGES < page_count <= MAX_PDF_PAGES:
            raise OgeCatalogError("White House 278e checkpoint page count is outside bounds")
        if reuse_legacy and page_count != TRUMP_2025_PAGE_COUNT:
            raise OgeCatalogError("White House legacy OCR page count conflicts with source")
        try:
            _cover(document.pages[0].extract_text() or "", expected_filer)
        except OgeCatalogError:
            pass
        else:
            raise OgeCatalogError("White House 278e checkpointed OCR is not required")
        ranges = [(start, min(start + shard_size - 1, page_count))
                  for start in range(1, page_count + 1, shard_size)]
        expected_names = {f"pages-{start:04d}-{end:04d}.json": (start, end)
                          for start, end in ranges}
        unexpected = [path for path in shard_root.glob("pages-*.json")
                      if path.name not in expected_names]
        if unexpected:
            raise OgeCatalogError("White House 278e OCR checkpoint has unexpected shards")
        shards: dict[tuple[int, int], dict] = {}
        engines = set()
        for name, (start, end) in expected_names.items():
            path = shard_root / name
            if not path.is_file():
                continue
            try:
                shard = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise OgeCatalogError("White House 278e OCR checkpoint shard is invalid") from None
            page_rows = shard.get("pages")
            if (shard.get("schema_version") != OCR_SHARD_SCHEMA or
                    shard.get("parser_version") != (PARSER_VERSION if reuse_legacy else version) or
                    shard.get("source_sha256") != source_sha256 or
                    shard.get("source_url") != source_url or
                    shard.get("page_start") != start or shard.get("page_end") != end or
                    not isinstance(shard.get("ocr_engine"), str) or
                    not shard["ocr_engine"].casefold().startswith("tesseract ") or
                    (reuse_legacy and shard["ocr_engine"] != TRUMP_2025_LEGACY_OCR_ENGINE) or
                    not isinstance(page_rows, list) or len(page_rows) != end - start + 1 or
                    [row.get("page_number") for row in page_rows
                     if isinstance(row, dict)] != list(range(start, end + 1)) or
                    any(not isinstance(row.get("width"), (int, float)) or
                        not isinstance(row.get("height"), (int, float)) or
                        not isinstance(row.get("words"), list) or
                        any(not isinstance(word, dict) for word in row["words"])
                        for row in page_rows if isinstance(row, dict))):
                raise OgeCatalogError("White House 278e OCR checkpoint shard is unbound")
            shards[(start, end)] = shard
            engines.add(shard["ocr_engine"])
        if len(engines) > 1:
            raise OgeCatalogError("White House 278e OCR checkpoint engine changed")
        if reuse_legacy and len(shards) != len(ranges):
            raise OgeCatalogError("White House legacy OCR checkpoint is incomplete")

        created = 0
        processed_pages = 0
        for start, end in ranges:
            if (start, end) in shards:
                continue
            size = end - start + 1
            if processed_pages and processed_pages + size > page_limit:
                break
            try:
                pages, engine = ocr_pdf_pages(
                    document, executable=ocr_executable,
                    page_numbers=list(range(start, end + 1)), max_pages=shard_size)
            except OcrGeometryError as exc:
                raise OgeCatalogError(f"White House 278e OCR failed: {exc}") from None
            if engines and engine not in engines:
                raise OgeCatalogError("White House 278e OCR checkpoint engine changed")
            engines.add(engine)
            shard = {"schema_version": OCR_SHARD_SCHEMA,
                     "parser_version": version,
                     "source_url": source_url, "source_sha256": source_sha256,
                     "ocr_engine": engine, "page_start": start, "page_end": end,
                     "pages": [{"page_number": number, "width": page.width,
                                "height": page.height, "words": page.extract_words()}
                               for number, page in zip(range(start, end + 1), pages, strict=True)]}
            _write_checkpoint_shard(
                checkpoint_root / f"pages-{start:04d}-{end:04d}.json", shard)
            shards[(start, end)] = shard
            created += 1
            processed_pages += size

    completed_pages = sum(end - start + 1 for start, end in shards)
    status = {"schema_version": OCR_CHECKPOINT_SCHEMA,
              "parser_version": version,
              "source_url": source_url, "source_sha256": source_sha256,
              "page_count": page_count, "shard_size": shard_size,
              "shard_count": len(ranges), "completed_shard_count": len(shards),
              "completed_page_count": completed_pages,
              "pending_page_count": page_count - completed_pages,
              "created_shard_count": created}
    if reuse_legacy:
        status["reused_shard_count"] = len(shards)
        status["reused_parser_version"] = PARSER_VERSION
    if len(shards) != len(ranges):
        raise OcrCheckpointPending(status)

    pages = []
    for start, end in ranges:
        for row in shards[(start, end)]["pages"]:
            pages.append(OcrPage(width=float(row["width"]), height=float(row["height"]),
                                 words=row["words"]))
    meta, cover_reasons = _ocr_cover(pages[0], expected_filer)
    rows = _extract_page_rows(pages, meta, initial_reasons=cover_reasons,
                              source_url=source_url, source_sha256=source_sha256)
    return {"schema_version": SCHEMA, "parser_version": version,
            "source_id": "whitehouse_public", "form_type": "278e",
            "source_url": source_url, "source_sha256": source_sha256,
            **meta, "page_count": page_count,
            "extraction_method": "tesseract_ocr_geometry_checkpointed",
            "ocr_engine": next(iter(engines)), "ocr_checkpoint": status,
            **rows,
            "production_qualification":
            "pending_identity_amendments_part7_dedup_and_quarantine"}
