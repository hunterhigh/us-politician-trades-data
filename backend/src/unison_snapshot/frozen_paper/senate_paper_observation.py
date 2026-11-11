"""Source-bound, read-only comparison of archived Senate paper PTR grid slots.

The fixed grid inventories are observations of one archived source version.
Changed reports remain coverage-unknown; this module never qualifies trades.
"""
from __future__ import annotations

from collections import Counter

from .senate_slot_inventory import SCHEMA as SLOT_SCHEMA
from ..source_row_ledger import report_ledger, senate_shadow_pages
from .senate_observation_data import DEFAULT_ROOT, load_bundle

SCHEMA = "senate-paper-slot-comparison/v3"
PENDING_SCHEMA = "senate-paper-pending-slot-projection/v3"


def _source_report_relations(report: dict) -> dict:
    """Bind classified data observations, without treating them as qualified facts."""
    count = report.get("archive_page_count")
    if type(count) is not int or count < 1:
        raise ValueError("Senate paper archive page count is missing")
    numbers = [page["page_number"] for page in report["pages"]]
    missing = report["unobserved_page_numbers"]
    if (len(set(numbers)) != len(numbers) or len(set(missing)) != len(missing)
            or set(numbers) & set(missing)
            or sorted(numbers + missing) != list(range(1, count + 1))):
        raise ValueError("Senate paper archive pages are missing, repeated or unbound")
    if (report["coverage_status"] != "fixed_slots_observed" and
            (report["slots"] or report["pages"] or report["physical_grid_slot_count"] is not None)):
        raise ValueError("Senate paper unknown source version contains observed slots")
    if report["coverage_status"] == "fixed_slots_observed":
        if len(report["slots"]) != report["physical_grid_slot_count"]:
            raise ValueError("Senate paper report slot count differs from observations")
        if any(slot["page_number"] not in numbers for slot in report["slots"]):
            raise ValueError("Senate paper slot references an unobserved page")
        for page in report["pages"]:
            labels = Counter(slot["label"] for slot in report["slots"]
                             if slot["page_number"] == page["page_number"])
            if dict(labels) != page["label_counts"]:
                raise ValueError("Senate paper page label counts differ from slots")
        pages = senate_shadow_pages(report)
    else:
        pages = []
    return report_ledger(
        source_id="senate_efd", document_sha256=report["entrypoint_source_sha256"],
        adapter_version="senate-paper-slot-comparison/v3/report-relations-v1",
        page_count=count, pages=pages, logical_rows=[
            {"local_key": f"p{slot['page_number']}:slot{slot['grid_slot']}",
             "slot_refs": [{"page_number": slot["page_number"],
                            "slot_key": str(slot["grid_slot"])}],
             "observation_ref": f"slots/{index}"}
            for index, slot in enumerate(report["slots"])
            if slot["label"] == "data_observed"])


def project_pending_slots(observation: dict, *, observations_root=DEFAULT_ROOT) -> dict:
    """Retain every observed data slot as a pending, non-candidate fact.

    This projection has no formal transaction ID and never asserts that an
    OCR reading or checkbox is an accurate transaction field. Existing paper
    extraction rows may overlap these physical slots; no string-only dedup is
    attempted here.
    """
    bundle = load_bundle(observations_root)
    account_reviews = bundle['account_reviews']
    reference_commit = bundle['evidence_commit']
    if (observation.get("schema_version") != SCHEMA or
            observation.get("fixed_inventory_evidence_commit") != reference_commit or
            observation.get("slot_classification_schema_version") != SLOT_SCHEMA):
        raise ValueError("Senate paper slot observation schema or source changed")
    reports = observation.get("reports")
    if not isinstance(reports, list) or len(reports) != observation.get("report_count"):
        raise ValueError("Senate paper slot observation reports are incomplete")
    pending, unknown = [], []
    account_scoped_count = 0
    seen: set[tuple[str, int, int]] = set()
    relations = []
    carries_relations = any("archive_page_count" in report or
                            "source_report_ledger" in report for report in reports)
    for index, report in enumerate(reports):
        document_id = report["document_id"]
        source_sha = report["entrypoint_source_sha256"]
        if carries_relations:
            expected = _source_report_relations(report)
            if report.get("source_report_ledger") != expected:
                raise ValueError("Senate paper source report relations differ from observations")
            relations.append({"document_id": document_id,
                              "observation_ref": f"reports/{index}", "ledger": expected})
        if report["coverage_status"] != "fixed_slots_observed":
            continue
        page_sha = {page["page_number"]: page["page_sha256"]
                    for page in report["pages"]}
        if len(page_sha) != len(report["pages"]):
            raise ValueError("Senate paper slot observation repeats a page")
        scope = report.get("account_scope_observation")
        if document_id in account_reviews and scope is None:
            raise ValueError("Senate paper fixed account scope is missing")
        scope_rows = {}
        heading_by_slot = {}
        if scope is not None:
            if (scope.get("document_id") != document_id or
                    scope.get("entrypoint_source_sha256") != source_sha or
                    scope.get("archive_bytes_verified") is not True):
                raise ValueError("Senate paper account scope source differs")
            scope_rows = {(row["source_slot"]["page_number"],
                           row["source_slot"]["grid_slot"]): row
                          for row in scope["rows"]}
            heading_by_slot = {(row["page_number"], row["grid_slot"]): row
                               for row in scope["account_headings"]}
            if (len(scope_rows) != len(scope["rows"]) or
                    len(heading_by_slot) != len(scope["account_headings"])):
                raise ValueError("Senate paper account scope slots repeat")
        counts: Counter[str] = Counter()
        matched_scope_rows = set()
        for slot in report["slots"]:
            label = slot["label"]
            counts[label] += 1
            number, grid = slot["page_number"], slot["grid_slot"]
            key = (document_id, number, grid)
            if key in seen or number not in page_sha:
                raise ValueError("Senate paper physical slot is duplicated or unbound")
            seen.add(key)
            source_slot = {"document_id": document_id,
                           "entrypoint_source_sha256": source_sha,
                           "page_number": number,
                           "page_sha256": page_sha[number],
                           "grid_slot": grid,
                           "row_crop_sha256": slot["row_crop_sha256"]}
            if label == "unknown":
                unknown.append({"source_slot": source_slot,
                                "coverage_status": "unknown_physical_slot"})
            elif label == "data_observed":
                scoped = scope_rows.get((number, grid))
                if scope is not None:
                    if (scoped is None or scoped["source_slot"] != source_slot or
                            scoped["status"] !=
                            "account_scoped_pending_relationship_review" or
                            scoped["formal_transaction_id"] is not None):
                        raise ValueError("Senate paper account scope data differs")
                    matched_scope_rows.add((number, grid))
                    account_scoped_count += 1
                heading = scoped["account_heading_slot"] if scoped else None
                heading_key = (heading["page_number"], heading["grid_slot"]) if heading else None
                heading_observation = heading_by_slot.get(heading_key) if heading_key else None
                if heading is not None and (heading_observation is None or
                    heading_observation["page_sha256"] != heading["page_sha256"] or
                    heading_observation["asset_crop_sha256"] !=
                    heading["asset_crop_sha256"] or
                    heading_observation.get("heading_status") !=
                    "confirmed_fixed_image" or
                    not heading_observation.get("printed_heading")):
                    raise ValueError("Senate paper account heading differs")
                date = slot["key_cells"]["date"]
                cell_ocr = date["cell_ocr"] or {}
                cell_status = cell_ocr.get("status", "not_run")
                if cell_status not in ("not_run", "agreement", "conflict",
                                       "insufficient", "unreadable"):
                    raise ValueError("Senate paper date-cell status changed")
                pending.append({
                    "source_slot": source_slot,
                    "source_url": ("https://efdsearch.senate.gov/search/view/paper/"
                                   f"{document_id}/"),
                    "status": "pending_source_verification",
                    "pending_reasons": ["paper_slot_fields_unverified",
                                        "paper_report_relationship_unverified"],
                    "asset_ocr_raw": slot["asset_ocr_raw"],
                    "date_page_ocr_raw": date["page_ocr_raw"],
                    "date_page_ocr_parsed": date["page_ocr_parsed"],
                    "date_cell_ocr_parsed": date["cell_ocr_parsed"],
                    "date_cell_observation_status": cell_status,
                    "date_cell_conflict": bool(date["conflict"] or
                                               cell_status == "conflict"),
                    "direction_mark_observations": slot["type_marks"],
                    "amount_mark_observations": slot["amount_marks"],
                    "legacy_extraction_report_row_count": report["legacy_extraction_count"],
                    "legacy_extraction_failed": report["legacy_extraction_failed"],
                    "account_scope_status": ("source_bound_visual_scope_unverified_identity"
                                             if scoped else "not_observed"),
                    "account_heading_slot": heading,
                    "account_heading_ocr_raw": (heading_observation["heading_ocr_raw"]
                                                if heading_observation else None),
                    "account_heading_visual_raw": (
                        heading_observation["printed_heading"]
                        if heading_observation else None),
                    "formal_transaction_id": None,
                })
        if scope is not None and matched_scope_rows != set(scope_rows):
            raise ValueError("Senate paper account scope lost data slots")
        if dict(counts) != report["label_counts"] or sum(counts.values()) != report[
                "physical_grid_slot_count"]:
            raise ValueError("Senate paper slot labels do not conserve")
    if len(pending) != observation.get("label_counts", {}).get("data_observed", 0):
        raise ValueError("Senate paper data slots did not all reach pending projection")
    if len(unknown) != observation.get("label_counts", {}).get("unknown", 0):
        raise ValueError("Senate paper unknown slots did not all reach coverage ledger")
    result = {"schema_version": PENDING_SCHEMA,
            "source_id": "senate_efd",
            "fixed_inventory_evidence_commit": reference_commit,
            "observation_schema_version": SCHEMA,
            "interpretation": "pending physical slots only; no qualified or published trades",
            "report_count": len(reports),
            "pending_row_count": len(pending),
            "account_scoped_pending_row_count": account_scoped_count,
            "legacy_failed_report_pending_row_count": sum(
                row["legacy_extraction_failed"] for row in pending),
            "legacy_extracted_report_pending_row_count": sum(
                not row["legacy_extraction_failed"] for row in pending),
            "unknown_slot_count": len(unknown),
            "unobserved_page_count": observation["unobserved_page_count"],
            "pending_rows": pending,
            "unknown_slots": unknown}
    # Additive nested contract: historical v3 observations retain byte-for-byte
    # projection semantics. New observations must reproduce their source links.
    if carries_relations:
        if (len({item["document_id"] for item in relations}) != len(relations) or
                sum(len(item["ledger"]["unobserved_pages"]) for item in relations) !=
                observation["unobserved_page_count"]):
            raise ValueError("Senate paper report relation coverage summary differs")
        if sum(item["ledger"]["logical_row_count"] for item in relations) != len(pending):
            raise ValueError("Senate paper source links lost pending data observations")
        result["source_report_ledgers"] = relations
    return result
