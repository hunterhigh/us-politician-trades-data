"""Read-only account-heading scope audit for the fixed first Senate paper PTR.

An identical asset/date/direction/band beneath two different printed section
headings is not enough evidence to merge rows. This module records the heading
locator and its archived crop hash without qualifying transactions.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import hashlib
from io import BytesIO
from typing import Callable

from .senate_slots_shadow import EVIDENCE_COMMIT

SCHEMA = "senate-paper-account-scope-audit/v1"
DOCUMENT_ID = "068274e1-4b7a-4453-a242-563dde4c10d8"


def confirmed_visual_review_from_fields(fields: dict, ledger: dict) -> dict:
    """Derive the fixed visual rows from their existing source-bound field audit."""
    if (fields.get("schema_version") != "senate-paper-first-report-field-observations/v1" or
            fields.get("document_id") != DOCUMENT_ID or
            fields.get("evidence_commit") != EVIDENCE_COMMIT or
            ledger.get("document_id") != DOCUMENT_ID or
            ledger.get("evidence_commit") != EVIDENCE_COMMIT):
        raise ValueError("Senate visual field audit source identity changed")
    source_rows = {(row["page_number"], row["grid_slot"]): row
                   for row in ledger["rows"]}
    if len(source_rows) != len(ledger["rows"]):
        raise ValueError("Senate visual field audit source slots repeat")
    reviewed = []
    for row in fields.get("rows", []):
        key = (row["page_number"], row["grid_slot"])
        source = source_rows.get(key)
        expected = row["visual_review"]["expected"]
        if (source is None or source["disposition"] !=
                "transaction_observed_quarantined" or
                row.get("row_field_status") !=
                "four_fields_confirmed_candidate_quarantined" or
                row["visual_review"].get("field_matches") !=
                {"amount": True, "asset": True, "date": True,
                 "direction": True} or
                (expected["page_number"], expected["grid_slot"]) != key or
                row["page_sha256"] != source["page_sha256"] or
                row["row_crop"]["grayscale_sha256"] != source[
                    "row_crop"]["grayscale_sha256"] or
                row["asset"]["ocr"]["crop"]["grayscale_sha256"] != source[
                    "asset_crop"]["grayscale_sha256"] or
                row["date"]["ocr"]["crop"]["grayscale_sha256"] != source[
                    "date_crop"]["grayscale_sha256"] or
                any(row[name].get("status") != "confirmed_fixed_image"
                    for name in ("asset", "date", "direction", "amount")) or
                row["asset"]["ocr"]["joined_text"] != expected["asset_text"] or
                row["date"]["ocr"]["joined_text"] != expected["date_text"] or
                row["direction"]["observed_label"] != expected["direction"] or
                row["amount"]["observed_band"] != expected["amount_band"]):
            raise ValueError("Senate visual field audit differs from fixed source")
        reviewed.append(expected)
    source_headings = {(row["page_number"], row["grid_slot"]): row
                       for row in ledger["rows"]
                       if row["disposition"] == "section_heading_excluded"}
    heading_reviews = fields.get("account_heading_visual_reviews")
    if not isinstance(heading_reviews, list) or len(heading_reviews) != len(source_headings):
        raise ValueError("Senate visual account headings are incomplete")
    reviewed_headings = []
    seen_headings = set()
    for item in heading_reviews:
        key = (item.get("page_number"), item.get("grid_slot"))
        source = source_headings.get(key)
        if (key in seen_headings or source is None or
                item.get("page_sha256") != source["page_sha256"] or
                item.get("asset_crop_sha256") !=
                source["asset_crop"]["grayscale_sha256"] or
                item.get("status") != "confirmed_fixed_image" or
                not isinstance(item.get("printed_heading"), str) or
                not item["printed_heading"].strip()):
            raise ValueError("Senate visual account heading differs from fixed source")
        seen_headings.add(key)
        reviewed_headings.append(item)
    if seen_headings != set(source_headings):
        raise ValueError("Senate visual account headings do not conserve source")
    return {"document_id": DOCUMENT_ID, "evidence_commit": EVIDENCE_COMMIT,
            "rows": reviewed, "account_headings": reviewed_headings}


def audit_first_report_account_scopes(
        ledger: dict, visual_review: dict,
        *, page_bytes: Callable[[str, int], bytes] | None = None) -> dict:
    if (ledger.get("document_id") != DOCUMENT_ID or
            visual_review.get("document_id") != DOCUMENT_ID or
            ledger.get("evidence_commit") != EVIDENCE_COMMIT or
            visual_review.get("evidence_commit") != EVIDENCE_COMMIT):
        raise ValueError("Senate account-scope source identity changed")
    rows = ledger.get("rows")
    reviewed = visual_review.get("rows")
    reviewed_headings = visual_review.get("account_headings")
    if (not isinstance(rows, list) or not isinstance(reviewed, list) or
            not isinstance(reviewed_headings, list)):
        raise ValueError("Senate account-scope rows are incomplete")
    by_slot: dict[tuple[int, int], dict] = {}
    for row in rows:
        key = (row["page_number"], row["grid_slot"])
        if key in by_slot:
            raise ValueError("Senate account-scope physical slot repeated")
        by_slot[key] = row
    if page_bytes is not None:
        from PIL import Image

        by_page: dict[int, object] = {}
        page_digests: dict[int, str] = {}
        for key, row in sorted(by_slot.items()):
            number = key[0]
            if number not in by_page:
                raw = page_bytes(DOCUMENT_ID, number)
                page_digests[number] = hashlib.sha256(raw).hexdigest()
                if page_digests[number] != row["page_sha256"]:
                    raise ValueError("Senate account-scope archived page differs")
                by_page[number] = Image.open(BytesIO(raw)).convert("L")
            if row["page_sha256"] != page_digests[number]:
                raise ValueError("Senate account-scope row page differs")
            image = by_page[number]
            for crop_name in ("row_crop", "asset_crop", "date_crop"):
                crop = row[crop_name]
                actual = hashlib.sha256(image.crop(tuple(crop["box_px"])).tobytes()).hexdigest()
                if actual != crop["grayscale_sha256"]:
                    raise ValueError("Senate account-scope archived crop differs")
    headings: dict[tuple[int, int], dict] = {}
    scoped: dict[tuple[int, int], tuple[int, int] | None] = {}
    active: tuple[int, int] | None = None
    previous_page: int | None = None
    for key, row in sorted(by_slot.items()):
        page = key[0]
        if page != previous_page:
            active = None  # Never infer an account across a page break.
            previous_page = page
        if row["disposition"] == "section_heading_excluded":
            active = key
            headings[key] = {
                "page_number": page,
                "grid_slot": key[1],
                "page_sha256": row["page_sha256"],
                "row_crop_sha256": row["row_crop"]["grayscale_sha256"],
                "asset_crop_sha256": row["asset_crop"]["grayscale_sha256"],
                "heading_ocr_raw": row["asset_ocr_raw"],
            }
        scoped[key] = active
    visual_headings = {(item["page_number"], item["grid_slot"]): item
                       for item in reviewed_headings}
    if len(visual_headings) != len(reviewed_headings) or set(visual_headings) != set(headings):
        raise ValueError("Senate visual account headings do not conserve slots")
    for key, heading in headings.items():
        item = visual_headings[key]
        if (item["page_sha256"] != heading["page_sha256"] or
                item["asset_crop_sha256"] != heading["asset_crop_sha256"] or
                item["status"] != "confirmed_fixed_image" or
                not isinstance(item["printed_heading"], str) or
                not item["printed_heading"].strip()):
            raise ValueError("Senate visual account heading differs from source")
        heading["printed_heading"] = item["printed_heading"].strip()
        heading["heading_status"] = item["status"]
    data_keys = {key for key, row in by_slot.items()
                 if row["disposition"] == "transaction_observed_quarantined"}
    visual_keys = {(item["page_number"], item["grid_slot"]) for item in reviewed}
    if len(visual_keys) != len(reviewed) or visual_keys != data_keys:
        raise ValueError("Senate account-scope visual rows do not conserve data slots")
    output_rows = []
    by_fields: dict[tuple, list[dict]] = defaultdict(list)
    by_scoped_fields: dict[tuple, list[dict]] = defaultdict(list)
    for item in sorted(reviewed, key=lambda row: (row["page_number"], row["grid_slot"])):
        key = (item["page_number"], item["grid_slot"])
        source_row = by_slot[key]
        if (item["direction"] != source_row["observed_type"] or
                item["amount_band"] != source_row["observed_amount_band"]):
            raise ValueError("Senate account-scope visual mark disagrees with source")
        try:
            event_day = datetime.strptime(item["date_text"], "%m/%d/%y").date().isoformat()
        except ValueError:
            raise ValueError("Senate account-scope visual date is invalid") from None
        heading_key = scoped[key]
        heading = headings.get(heading_key) if heading_key is not None else None
        field_key = (item["asset_text"].strip().casefold(), event_day,
                     item["direction"], item["amount_band"])
        result = {
            "source_slot": {
                "document_id": DOCUMENT_ID,
                "entrypoint_source_sha256": ledger["entrypoint_source_sha256"],
                "page_number": key[0],
                "page_sha256": source_row["page_sha256"],
                "grid_slot": key[1],
                "row_crop_sha256": source_row["row_crop"]["grayscale_sha256"],
            },
            "account_heading_slot": (
                {"page_number": heading_key[0], "grid_slot": heading_key[1],
                 "page_sha256": heading["page_sha256"],
                 "asset_crop_sha256": heading["asset_crop_sha256"]}
                if heading is not None else None),
            "account_heading_visual_raw": (
                heading["printed_heading"] if heading is not None else None),
            "asset_visual_raw": item["asset_text"],
            "transaction_date_visual": event_day,
            "direction_visual": item["direction"],
            "amount_band_visual": item["amount_band"],
            "status": "account_scoped_pending_relationship_review",
            "formal_transaction_id": None,
        }
        output_rows.append(result)
        by_fields[field_key].append(result)
        by_scoped_fields[(heading_key, field_key)].append(result)
    repeated_field_rows = [row for group in by_fields.values() if len(group) > 1
                           for row in group]
    within_heading_duplicates = [row for group in by_scoped_fields.values()
                                 if len(group) > 1 for row in group]
    return {
        "schema_version": SCHEMA,
        "source_id": "senate_efd",
        "document_id": DOCUMENT_ID,
        "evidence_commit": EVIDENCE_COMMIT,
        "entrypoint_source_sha256": ledger["entrypoint_source_sha256"],
        "archive_bytes_verified": page_bytes is not None,
        "interpretation": "account-heading relationships only; no qualified or published trades",
        "physical_grid_slot_count": len(rows),
        "account_heading_count": len(headings),
        "visually_confirmed_data_row_count": len(output_rows),
        "unscoped_data_row_count": sum(row["account_heading_slot"] is None
                                       for row in output_rows),
        "distinct_unscoped_field_combination_count": len(by_fields),
        "repeated_unscoped_field_group_count": sum(len(group) > 1
                                                    for group in by_fields.values()),
        "repeated_unscoped_field_row_count": len(repeated_field_rows),
        "within_heading_duplicate_row_count": len(within_heading_duplicates),
        "distinct_scoped_field_combination_count": len(by_scoped_fields),
        "account_headings": [headings[key] for key in sorted(headings)],
        "rows": output_rows,
    }
