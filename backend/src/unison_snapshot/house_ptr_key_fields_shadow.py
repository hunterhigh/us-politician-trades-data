"""Conservative key-field reading of located House paper PTR slots.

These readings are source-image hints. They never create a transaction ID,
qualification decision, amount range, or candidate publication record.
"""

from __future__ import annotations

import re
from datetime import datetime


DATE_SHAPE = re.compile(r"\d{1,2}(?P<separator>[/\-])\d{1,2}(?P=separator)\d{4}")
EDGE_NOISE = re.compile(r"[|.\-'\"*_\u00b0\u2018\u2019\ufffd]{0,3}")
OWNER_CODES = {"SP", "JT", "DC"}


def _source_cell(cell: dict) -> dict:
    """Keep the exact observed crop beside a field interpretation."""
    return {name: cell.get(name) for name in (
        "label", "raw_text", "raw_ocr_confidence", "crop_sha256",
        "bbox_normalized", "interior_dark_fraction")}


def _source_quality_flags(cells: dict, event_status: str,
                          notice_status: str) -> list[str]:
    """Prioritize later source checks; OCR confidence is never fact confidence."""
    flags = []
    for name in ("owner", "asset", "event_date", "notification_date"):
        confidence = cells[name].get("raw_ocr_confidence")
        if isinstance(confidence, (int, float)) and confidence < 70:
            flags.append(f"{name}_ocr_confidence_below_70")
    if "edge_noise" in event_status:
        flags.append("event_date_ocr_edge_noise")
    if "edge_noise" in notice_status:
        flags.append("notification_date_ocr_edge_noise")
    if notice_status in {"not_exact_date_shape", "invalid_calendar_date"}:
        flags.append("notification_date_ocr_unreadable")
    return flags


def _date(cell: dict, *, filing_year: int | None = None) -> tuple[str | None, str]:
    raw = cell.get("raw_text", "").strip()
    pattern = (re.compile(r"\d{1,2}(?P<separator>[/\-])\d{1,2}(?P=separator)(?:\d{4}|\d{2})")
               if type(filing_year) is int and 2000 <= filing_year <= 2099 else DATE_SHAPE)
    match = pattern.fullmatch(raw)
    edge_noise = False
    if match is None:
        matches = list(pattern.finditer(raw))
        if len(matches) != 1:
            return None, "not_exact_date_shape"
        match = matches[0]
        before, after = raw[:match.start()].strip(), raw[match.end():].strip()
        if not before and not after:
            return None, "not_exact_date_shape"
        if not EDGE_NOISE.fullmatch(before) or not EDGE_NOISE.fullmatch(after):
            return None, "not_exact_date_shape"
        edge_noise = True
    try:
        date_text = match.group().replace("-", "/")
        short_year = len(date_text.rsplit("/", 1)[-1]) == 2
        if short_year:
            # The official filing year supplies the century, never a clock or
            # Python's rolling two-digit-year pivot. Older/future years stay unresolved.
            year = filing_year // 100 * 100 + int(date_text.rsplit("/", 1)[-1])
            if year not in {filing_year, filing_year - 1}:
                return None, "short_year_outside_filing_period"
            date_text = date_text.rsplit("/", 1)[0] + "/" + str(year)
        value = datetime.strptime(date_text, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None, "invalid_calendar_date"
    if short_year:
        return value, ("ocr_short_year_date_shape_with_edge_noise_unverified" if edge_noise
                       else "ocr_short_year_date_shape_unverified")
    if match.group("separator") == "-":
        return value, ("ocr_dash_date_shape_with_edge_noise_unverified" if edge_noise
                       else "ocr_dash_date_shape_unverified")
    return value, ("ocr_date_shape_with_edge_noise_unverified" if edge_noise
                   else "ocr_date_shape_unverified")


def _single_mark(cells: list[dict]) -> tuple[str | None, str]:
    if not cells:
        return None, "missing_mark_columns"
    scores = [cell.get("interior_dark_fraction") for cell in cells]
    if any(not isinstance(score, (int, float)) for score in scores):
        return None, "missing_ink_measurement"
    ranked = sorted(range(len(scores)), key=lambda index: scores[index], reverse=True)
    if scores[ranked[0]] < .08:
        return None, "no_strong_mark"
    if len(ranked) > 1 and scores[ranked[1]] > .025:
        return None, "multiple_possible_marks"
    return cells[ranked[0]]["label"], "single_strong_mark_unverified"


def _amount_marks(cells: list[dict]) -> tuple[str | None, str, bool | None, str]:
    """Separate A-J ranges from the spouse/DC over-$1m special K box."""
    labels = [cell.get("label") for cell in cells]
    if labels not in ([chr(ord("A") + i) for i in range(10)],
                      [chr(ord("A") + i) for i in range(11)]):
        return None, "unknown_amount_columns", None, "unknown_supplemental_column"
    amount, amount_status = _single_mark(cells[:10])
    if len(cells) == 10:
        return amount, amount_status, None, "supplemental_column_absent"
    ink = cells[10].get("interior_dark_fraction")
    if not isinstance(ink, (int, float)):
        return amount, amount_status, None, "supplemental_ink_unmeasured"
    if ink >= .08:
        return amount, amount_status, True, "supplemental_mark_present_unverified"
    if ink <= .025:
        return amount, amount_status, False, "supplemental_mark_absent_unverified"
    return amount, amount_status, None, "supplemental_mark_uncertain"


def read_house_ptr_key_fields(observation: dict, *, filing_year: int | None = None) -> dict:
    """Keep physical rows and field blockers without promoting OCR to facts."""
    if observation.get("schema_version") != "house-ptr-cell-observation-shadow/v3":
        raise ValueError("House paper key fields require owner-aware cell observations")
    if observation.get("qualification_status") != "unverified_shadow_only":
        raise ValueError("House paper key fields cannot read promoted observations")
    rows = []
    for page in observation["pages"]:
        for physical in page["rows"]:
            if physical["disposition"] != "data_candidate":
                continue
            cells = physical["cells"]
            if cells is None:
                rows.append({"page": page["page"],
                             "physical_band_index": physical["physical_band_index"],
                             "status": "unreadable_columns", "fields": None,
                             "blocking_reasons": ["columns_unobserved"]})
                continue
            owner_raw = cells["owner"]["raw_text"]
            owner = re.sub(r"[^A-Za-z]", "", owner_raw).upper()
            owner_code = owner if owner in OWNER_CODES else None
            asset_raw = " ".join(cells["asset"]["raw_text"].split())
            event, event_status = _date(cells["event_date"], filing_year=filing_year)
            notice, notice_status = _date(cells["notification_date"], filing_year=filing_year)
            direction, direction_status = _single_mark(cells["direction"])
            amount, amount_status, spouse_dc_mark, spouse_dc_status = _amount_marks(cells["amount"])
            # Preserve the notice-date OCR result, but the current frontend uses
            # the transaction date and the report's filed_at for its two clocks.
            blockers = [name for name, valid in (
                ("owner_unreadable", owner_code is not None),
                ("asset_unreadable", bool(asset_raw)),
                ("event_date_unreadable", event is not None),
                ("direction_mark_unresolved", direction is not None),
                ("amount_mark_unresolved", amount is not None)) if not valid]
            if spouse_dc_mark is True and amount is not None:
                blockers.append("amount_column_combination_unresolved")
            if spouse_dc_mark is True and amount is None:
                blockers.append("spouse_dc_open_ended_amount_not_representable")
            rows.append({"page": page["page"],
                         "physical_band_index": physical["physical_band_index"],
                         "status": "raw_fields_present_unverified" if not blockers
                         else "raw_fields_incomplete",
                         "fields": {"owner_code": owner_code, "owner_raw": owner_raw,
                                    "asset_raw": asset_raw, "event_date": event,
                                    "event_date_status": event_status,
                                    "notification_date": notice,
                                    "notification_date_status": notice_status,
                                    "direction_mark": direction,
                                    "direction_status": direction_status,
                                    "amount_letter": amount,
                                    "amount_status": amount_status,
                                    "spouse_dc_over_1000000_mark": spouse_dc_mark,
                                    "spouse_dc_mark_status": spouse_dc_status},
                         "source_field_evidence": {
                             name: _source_cell(cells[name]) for name in (
                                 "owner", "asset", "event_date", "notification_date")
                         } | {
                             "direction": [_source_cell(cell) for cell in cells["direction"]],
                             "amount": [_source_cell(cell) for cell in cells["amount"]]},
                         "source_quality_flags": _source_quality_flags(
                             cells, event_status, notice_status),
                         "blocking_reasons": blockers})
    return {"schema_version": "house-ptr-key-fields-shadow/v1",
            "source_sha256": observation["source_sha256"],
            "qualification_status": "unverified_shadow_only",
            "data_candidate_count": len(rows), "rows": rows}
