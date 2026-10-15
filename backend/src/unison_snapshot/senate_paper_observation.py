"""Source-bound, read-only comparison of archived Senate paper PTR grid slots.

The fixed grid inventories are observations of one archived source version.
Changed reports remain coverage-unknown; this module never qualifies trades.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Callable

from .senate_slots_shadow import EVIDENCE_COMMIT, SCHEMA as SLOT_SCHEMA, classify_ledger

SCHEMA = "senate-paper-slot-comparison/v2"
PENDING_SCHEMA = "senate-paper-pending-slot-projection/v2"
LEDGER_FILES = (
    "senate-paper-first-report-rows.json",
    "senate-paper-viewer-remainders.json",
    "senate-paper-legacy-extracted-grid.json",
)


def _fixed_ledgers(docs_root: Path) -> dict[str, dict]:
    ledgers: dict[str, dict] = {}
    for name in LEDGER_FILES:
        source = json.loads((docs_root / name).read_text(encoding="utf-8"))
        for report in source.get("reports", [source]):
            document_id = report["document_id"]
            if document_id in ledgers:
                raise ValueError(f"duplicate Senate paper ledger: {document_id}")
            ledgers[document_id] = {"evidence_commit": source["evidence_commit"],
                                    **report}
    return ledgers


def compare_archived_batch(evidence_root: Path, docs_root: Path,
                           extraction_batch: dict, *,
                           date_reader: Callable | None = None) -> dict:
    """Observe only exact fixed page bytes; compare counts without matching IDs."""
    ledgers = _fixed_ledgers(docs_root)
    extraction_by_id = {item["document_id"]: item for item in
                        extraction_batch.get("extractions", [])}
    failure_by_id = {item["document_id"]: item for item in
                     extraction_batch.get("failures", [])}
    if set(extraction_by_id) & set(failure_by_id):
        raise ValueError("Senate paper report is both extracted and failed")
    documents = set(extraction_by_id) | set(failure_by_id)
    if len(documents) != extraction_batch.get("report_count"):
        raise ValueError("Senate paper extraction batch has duplicate or missing reports")
    results = []
    for document_id in sorted(documents):
        ledger = ledgers.get(document_id)
        manifest_path = evidence_root / "senate_efd" / "paper_pages" / document_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("document_id") != document_id or not manifest.get("evidence_complete"):
            raise ValueError(f"Senate paper manifest incomplete: {document_id}")
        source_sha = manifest["entrypoint_source_sha256"]
        extraction = extraction_by_id.get(document_id) or failure_by_id.get(document_id)
        extracted_sha = extraction.get("source_sha256")
        if extracted_sha is not None and extracted_sha != source_sha:
            raise ValueError(f"Senate paper extraction and archive differ: {document_id}")
        common = {"document_id": document_id,
                  "entrypoint_source_sha256": source_sha,
                  "manifest_sha256": manifest["manifest_sha256"],
                  "legacy_extraction_count": len(extraction_by_id.get(document_id, {}).get(
                      "transactions", [])),
                  "legacy_extraction_failed": document_id in failure_by_id}
        if (ledger is None or ledger.get("entrypoint_source_sha256") != source_sha or
                ledger.get("manifest_sha256") != manifest["manifest_sha256"]):
            results.append({**common, "coverage_status": "unknown_source_version",
                            "physical_grid_slot_count": None, "label_counts": {},
                            "pages": [], "unobserved_page_numbers": [
                                page["page_number"] for page in manifest["pages"]],
                            "slots": []})
            continue
        page_paths = {}
        for page in manifest["pages"]:
            path = evidence_root / page["archive_path"]
            if not path.resolve().is_relative_to(evidence_root.resolve()):
                raise ValueError("Senate paper page escaped evidence root")
            page_paths[page["page_number"]] = (path, page["sha256"])

        def read_page(requested_document: str, number: int) -> bytes:
            if requested_document != document_id:
                raise ValueError("Senate paper ledger crossed report boundary")
            path, expected_sha = page_paths[number]
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected_sha:
                raise ValueError("Senate paper archived page SHA mismatch")
            return raw

        classified = classify_ledger(
            {"evidence_commit": ledger["evidence_commit"], "reports": [ledger]},
            read_page, calibrate_columns=True,
            date_reader=date_reader)["reports"][0]
        old_by_page = Counter(row["page_number"] for row in
                              extraction_by_id.get(document_id, {}).get("transactions", []))
        page_comparison = [{**page, "legacy_extraction_count": old_by_page.get(
            page["page_number"], 0)} for page in classified["pages"]]
        observed_page_numbers = {page["page_number"] for page in page_comparison}
        unobserved_page_numbers = [page["page_number"] for page in manifest["pages"]
                                   if page["page_number"] not in observed_page_numbers]
        account_scope = None
        from .senate_paper_account_scope import (
            DOCUMENT_ID as ACCOUNT_SCOPE_DOCUMENT_ID,
            audit_first_report_account_scopes, confirmed_visual_review_from_fields,
        )
        if document_id == ACCOUNT_SCOPE_DOCUMENT_ID:
            fields = json.loads((docs_root / "senate-paper-first-report-fields.json")
                                .read_text(encoding="utf-8"))
            visual = confirmed_visual_review_from_fields(fields, ledger)
            account_scope = audit_first_report_account_scopes(
                ledger, visual, page_bytes=read_page)
            classified_data = {(slot["page_number"], slot["grid_slot"])
                               for slot in classified["slots"]
                               if slot["label"] == "data_observed"}
            scoped_data = {(row["source_slot"]["page_number"],
                            row["source_slot"]["grid_slot"])
                           for row in account_scope["rows"]}
            if classified_data != scoped_data:
                raise ValueError("Senate paper account scope and data slots differ")
        results.append({**common, "coverage_status": "fixed_slots_observed",
                        "physical_grid_slot_count": classified["physical_grid_slot_count"],
                        "label_counts": classified["label_counts"],
                        "pages": page_comparison,
                        "unobserved_page_numbers": unobserved_page_numbers,
                        "slots": classified["slots"],
                        "account_scope_observation": account_scope})
    return {"schema_version": SCHEMA,
            "fixed_inventory_evidence_commit": EVIDENCE_COMMIT,
            "slot_classification_schema_version": SLOT_SCHEMA,
            "interpretation": "read-only physical slot comparison; no qualified transactions or IDs",
            "report_count": len(results),
            "observed_report_count": sum(item["coverage_status"] == "fixed_slots_observed"
                                         for item in results),
            "coverage_unknown_report_count": sum(item["coverage_status"] != "fixed_slots_observed"
                                                 for item in results),
            "physical_grid_slot_count": sum(item["physical_grid_slot_count"] or 0
                                            for item in results),
            "observed_page_count": sum(len(item["pages"]) for item in results),
            "unobserved_page_count": sum(len(item["unobserved_page_numbers"])
                                         for item in results),
            "label_counts": dict(sorted(sum((Counter(item["label_counts"])
                                             for item in results), Counter()).items())),
            "reports": results}


def project_pending_slots(observation: dict) -> dict:
    """Retain every observed data slot as a pending, non-candidate fact.

    This projection has no formal transaction ID and never asserts that an
    OCR reading or checkbox is an accurate transaction field. Existing paper
    extraction rows may overlap these physical slots; no string-only dedup is
    attempted here.
    """
    if (observation.get("schema_version") != SCHEMA or
            observation.get("fixed_inventory_evidence_commit") != EVIDENCE_COMMIT or
            observation.get("slot_classification_schema_version") != SLOT_SCHEMA):
        raise ValueError("Senate paper slot observation schema or source changed")
    reports = observation.get("reports")
    if not isinstance(reports, list) or len(reports) != observation.get("report_count"):
        raise ValueError("Senate paper slot observation reports are incomplete")
    pending, unknown = [], []
    account_scoped_count = 0
    seen: set[tuple[str, int, int]] = set()
    for report in reports:
        document_id = report["document_id"]
        source_sha = report["entrypoint_source_sha256"]
        if report["coverage_status"] != "fixed_slots_observed":
            continue
        page_sha = {page["page_number"]: page["page_sha256"]
                    for page in report["pages"]}
        if len(page_sha) != len(report["pages"]):
            raise ValueError("Senate paper slot observation repeats a page")
        from .senate_paper_account_scope import DOCUMENT_ID as ACCOUNT_SCOPE_DOCUMENT_ID
        scope = report.get("account_scope_observation")
        if document_id == ACCOUNT_SCOPE_DOCUMENT_ID and scope is None:
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
                    heading["asset_crop_sha256"]):
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
    return {"schema_version": PENDING_SCHEMA,
            "source_id": "senate_efd",
            "fixed_inventory_evidence_commit": EVIDENCE_COMMIT,
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
