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
LEGACY_PARSER_VERSIONS = ("whitehouse-278e-hybrid-geometry/v4",
                          "whitehouse-278e-positioned-text/v2")
SUPPORTED_PARSER_VERSIONS = (PARSER_VERSION, *LEGACY_PARSER_VERSIONS)
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


def _header(words: list[dict], part: str) -> dict[str, float] | None:
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


def _quarantine(row: dict, reasons: list[str]) -> dict:
    result = {"section": row["section"], "page_number": row["page_number"],
            "row_number": row["row_number"], "raw_columns": row["raw_columns"],
            "owner": row.get("owner", "Unknown"),
            "owner_evidence": row.get("owner_evidence"),
            "reasons": sorted(set(reasons))}
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


def _parse_row(row: dict, meta: dict, child_parent: str | None) -> tuple[str, dict]:
    cells = {key: _compact(" ".join(value)) for key, value in row.items()
             if key in {"description", "eif", "value", "type", "date", "amount"}}
    row["raw_columns"] = cells
    evidence = {"section": row["section"], "page_number": row["page_number"],
                "row_number": row["row_number"], "asset_name": cells.get("description", ""),
                "owner": row["owner"], "raw_columns": cells}
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
    if row.get("owner_evidence_conflict"):
        reasons.append("owner_evidence_conflicts")
    if band is None:
        reasons.append("holding_value_unreadable_or_open")
    if "see endnote" in evidence["asset_name"].casefold() or "see endnote" in cells.get("eif", "").casefold():
        reasons.append("asset_endnote_unresolved")
    if "value not readily ascertainable" in evidence["asset_name"].casefold():
        reasons.append("asset_value_description_conflicts_with_band")
    if band is not None and child_parent:
        reasons.append("nested_aggregate_may_double_count")
    if confidences and (evidence["ocr_mean_confidence"] < OCR_MINIMUM_ROW_MEAN_CONFIDENCE or
                        evidence["ocr_min_confidence"] < OCR_MINIMUM_CRITICAL_CONFIDENCE):
        reasons.append("holding_ocr_confidence_below_threshold")
    if reasons:
        return "quarantined", _quarantine(row, reasons)
    return "holdings", {**evidence, "value_low": band[0], "value_high": band[1],
                        "report_period_end": meta["report_period_end"],
                        "holding_valuation_date": meta["holding_valuation_date"],
                        "holding_valuation_status": ("exact_period_end" if meta["holding_valuation_date"]
                                                     else "not_exact_on_cover")}


def _extract_page_rows(pages: list[object], meta: dict, *,
                       initial_reasons: list[str]) -> dict:
    raw_rows: list[dict] = []
    unparsed_rows: list[dict] = []
    document_reasons = list(initial_reasons)
    section_pages = {part: 0 for part, _ in _PARTS.values()}
    explicit_empty: set[str] = set()
    current_part: str | None = None
    current_owner = "Unknown"
    known_columns: dict[str, dict[str, float]] = {}
    reached_summary = False
    for page_number, page in enumerate(pages, 1):
        lines = page.extract_text_lines() or []
        words = page.extract_words() or []
        # Integrity.gov continuation pages repeat the table header but omit the
        # numbered section heading.  Preserve the active section and its column
        # geometry until an explicit new section, endnotes, or summary changes it.
        columns: dict[str, float] | None = known_columns.get(current_part)
        row: dict | None = None
        for line in lines:
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
                current_part, current_owner = _PARTS.get(number, (None, "Unknown"))
                columns = known_columns.get(current_part) if current_part else None
                if current_part:
                    section_pages[current_part] += 1
                continue
            if current_part is None or re.search(r" - Page \d+\Z", text):
                continue
            line_words = line.get("words") or _words_at(words, float(line["top"]))
            header = _header(line_words, current_part) if line_words else None
            if header:
                if row:
                    raw_rows.append(row)
                    row = None
                columns = header
                known_columns[current_part] = header
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
                    unparsed_rows.append({"section": current_part, "page_number": page_number,
                                          "row_number": number,
                                          "raw_columns": {"unparsed_line": text},
                                          "reasons": ["table_header_unrecognized"]})
                continue
            first_in_number_column = bool(line_words) and (
                float(line_words[0]["x0"]) < columns["description"] - 8)
            looks_numbered = bool(line_words) and bool(re.match(
                r"[0-9Il|]", str(line_words[0].get("text", ""))))
            if first_in_number_column and len(line_words) > 1 and (number or looks_numbered):
                if row:
                    raw_rows.append(row)
                top = int(round(float(line["top"]) * 10))
                row = {"section": current_part, "owner": current_owner,
                       "page_number": page_number,
                       "row_number": number or f"ocr-p{page_number}-y{top}",
                       **{key: [] for key in columns if key != "#"}}
                if number is None:
                    row["_row_reasons"] = ["row_number_ocr_unreadable"]
                if isinstance(line_words[0].get("ocr_confidence"), (int, float)):
                    row["_ocr_confidences"] = [float(line_words[0]["ocr_confidence"])]
            if row:
                _append_line(row, line_words, columns)
        if row:
            raw_rows.append(row)
        if reached_summary:
            break

    holdings: list[dict] = []
    transactions: list[dict] = []
    quarantined: list[dict] = list(unparsed_rows)
    excluded: list[dict] = []
    counts: dict[tuple[str, str], int] = {}
    for row in raw_rows:
        key = row["section"], row["row_number"]
        counts[key] = counts.get(key, 0) + 1
    _assign_part6_owners(raw_rows, pages)
    for row in raw_rows:
        key = row["section"], row["row_number"]
        if counts[key] > 1:
            row["raw_columns"] = _raw_text_columns(row)
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
    return {"section_pages": section_pages,
            "explicit_empty_sections": sorted(explicit_empty),
            "printed_row_count": len(raw_rows) + len(unparsed_rows), "holdings": holdings,
            "transactions": transactions, "excluded": excluded,
            "quarantined": quarantined, "document_reasons": sorted(set(document_reasons)),
            "requires_cross_report_dedup": any(row["section"] == "part7" for row in raw_rows)}


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
        rows = _extract_page_rows(pages, meta, initial_reasons=cover_reasons)
        return {"schema_version": SCHEMA, "parser_version": PARSER_VERSION,
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
        ocr_executable: str | None = None) -> dict:
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
    with pdfplumber.open(pdf_path) as document:
        page_count = len(document.pages)
        if not MAX_INLINE_OCR_PAGES < page_count <= MAX_PDF_PAGES:
            raise OgeCatalogError("White House 278e checkpoint page count is outside bounds")
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
        unexpected = [path for path in checkpoint_root.glob("pages-*.json")
                      if path.name not in expected_names]
        if unexpected:
            raise OgeCatalogError("White House 278e OCR checkpoint has unexpected shards")
        shards: dict[tuple[int, int], dict] = {}
        engines = set()
        for name, (start, end) in expected_names.items():
            path = checkpoint_root / name
            if not path.is_file():
                continue
            try:
                shard = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise OgeCatalogError("White House 278e OCR checkpoint shard is invalid") from None
            page_rows = shard.get("pages")
            if (shard.get("schema_version") != OCR_SHARD_SCHEMA or
                    shard.get("parser_version") != PARSER_VERSION or
                    shard.get("source_sha256") != source_sha256 or
                    shard.get("source_url") != source_url or
                    shard.get("page_start") != start or shard.get("page_end") != end or
                    not isinstance(shard.get("ocr_engine"), str) or
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
                     "parser_version": PARSER_VERSION,
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
              "parser_version": PARSER_VERSION,
              "source_url": source_url, "source_sha256": source_sha256,
              "page_count": page_count, "shard_size": shard_size,
              "shard_count": len(ranges), "completed_shard_count": len(shards),
              "completed_page_count": completed_pages,
              "pending_page_count": page_count - completed_pages,
              "created_shard_count": created}
    if len(shards) != len(ranges):
        raise OcrCheckpointPending(status)

    pages = []
    for start, end in ranges:
        for row in shards[(start, end)]["pages"]:
            pages.append(OcrPage(width=float(row["width"]), height=float(row["height"]),
                                 words=row["words"]))
    meta, cover_reasons = _ocr_cover(pages[0], expected_filer)
    rows = _extract_page_rows(pages, meta, initial_reasons=cover_reasons)
    return {"schema_version": SCHEMA, "parser_version": PARSER_VERSION,
            "source_id": "whitehouse_public", "form_type": "278e",
            "source_url": source_url, "source_sha256": source_sha256,
            **meta, "page_count": page_count,
            "extraction_method": "tesseract_ocr_geometry_checkpointed",
            "ocr_engine": next(iter(engines)), "ocr_checkpoint": status,
            **rows,
            "production_qualification":
            "pending_identity_amendments_part7_dedup_and_quarantine"}
