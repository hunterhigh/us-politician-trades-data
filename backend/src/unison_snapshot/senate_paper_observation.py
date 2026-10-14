"""Source-bound, read-only comparison of archived Senate paper PTR grid slots.

The fixed grid inventories are observations of one archived source version.
Changed reports remain coverage-unknown; this module never qualifies trades.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

from .senate_slots_shadow import EVIDENCE_COMMIT, SCHEMA as SLOT_SCHEMA, classify_ledger

SCHEMA = "senate-paper-slot-comparison/v1"
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
                           extraction_batch: dict) -> dict:
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
            read_page, calibrate_columns=True)["reports"][0]
        old_by_page = Counter(row["page_number"] for row in
                              extraction_by_id.get(document_id, {}).get("transactions", []))
        page_comparison = [{**page, "legacy_extraction_count": old_by_page.get(
            page["page_number"], 0)} for page in classified["pages"]]
        observed_page_numbers = {page["page_number"] for page in page_comparison}
        unobserved_page_numbers = [page["page_number"] for page in manifest["pages"]
                                   if page["page_number"] not in observed_page_numbers]
        results.append({**common, "coverage_status": "fixed_slots_observed",
                        "physical_grid_slot_count": classified["physical_grid_slot_count"],
                        "label_counts": classified["label_counts"],
                        "pages": page_comparison,
                        "unobserved_page_numbers": unobserved_page_numbers,
                        "slots": classified["slots"]})
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
