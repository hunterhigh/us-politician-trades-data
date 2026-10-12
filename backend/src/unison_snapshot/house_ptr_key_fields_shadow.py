"""Conservative key-field reading of located House paper PTR slots.

These readings are source-image hints. They never create a transaction ID,
qualification decision, amount range, or candidate publication record.
"""

from __future__ import annotations

import re
from datetime import datetime


DATE_SHAPE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")
OWNER_CODES = {"SP", "JT", "DC"}


def _date(cell: dict) -> tuple[str | None, str]:
    raw = cell.get("raw_text", "").strip()
    if not DATE_SHAPE.fullmatch(raw):
        return None, "not_exact_date_shape"
    try:
        value = datetime.strptime(raw, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None, "invalid_calendar_date"
    return value, "ocr_date_shape_unverified"


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


def read_house_ptr_key_fields(observation: dict) -> dict:
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
            event, event_status = _date(cells["event_date"])
            notice, notice_status = _date(cells["notification_date"])
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
                         "blocking_reasons": blockers})
    return {"schema_version": "house-ptr-key-fields-shadow/v1",
            "source_sha256": observation["source_sha256"],
            "qualification_status": "unverified_shadow_only",
            "data_candidate_count": len(rows), "rows": rows}
