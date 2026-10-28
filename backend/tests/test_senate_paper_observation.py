"""Read-only sidecar must bind fixed grid slots to current archived bytes."""
from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
import subprocess
import tempfile
import unittest

from unison_snapshot.senate_paper_observation import (
    SCHEMA, compare_archived_batch, project_pending_slots, _source_report_relations,
)
from unison_snapshot.senate_slot_inventory import SCHEMA as SLOT_SCHEMA
from senate_source_fixture import EVIDENCE_COMMIT


REPO = Path(__file__).resolve().parents[2]
OBSERVATIONS = REPO / "backend/data/source_observations/senate_efd"
DOCUMENT_ID = "068274e1-4b7a-4453-a242-563dde4c10d8"


class SenatePaperObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        target = f"{EVIDENCE_COMMIT}:senate_efd/paper_pages/{DOCUMENT_ID}/manifest.json"
        if subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", target],
                          capture_output=True).returncode:
            raise unittest.SkipTest("fixed Senate paper archive unavailable")

    def _archive(self, root: Path) -> dict:
        def fixed_bytes(path: str) -> bytes:
            return subprocess.run(["git", "-C", str(REPO), "show",
                                   f"{EVIDENCE_COMMIT}:{path}"],
                                  capture_output=True, check=True).stdout

        manifest_path = f"senate_efd/paper_pages/{DOCUMENT_ID}/manifest.json"
        manifest = json.loads(fixed_bytes(manifest_path))
        output = root / manifest_path
        output.parent.mkdir(parents=True)
        output.write_bytes(fixed_bytes(manifest_path))
        for page in manifest["pages"]:
            path = root / page["archive_path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(fixed_bytes(page["archive_path"]))
        return manifest

    @staticmethod
    def _batch() -> dict:
        return {"report_count": 1, "extractions": [{"document_id": DOCUMENT_ID,
                "transactions": [{"page_number": 2}, {"page_number": 2}]}],
                "failures": []}

    def test_fixed_report_preserves_all_slots_and_compares_legacy_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._archive(root)
            result = compare_archived_batch(root, OBSERVATIONS, self._batch())
        self.assertEqual(result["observed_report_count"], 1)
        self.assertEqual(result["physical_grid_slot_count"], 65)
        self.assertEqual(result["observed_page_count"] + result["unobserved_page_count"], 5)
        report = result["reports"][0]
        self.assertEqual(report["coverage_status"], "fixed_slots_observed")
        self.assertEqual(report["legacy_extraction_count"], 2)
        scope = report["account_scope_observation"]
        self.assertTrue(scope["archive_bytes_verified"])
        self.assertEqual(scope["visually_confirmed_data_row_count"], 36)
        self.assertEqual(scope["account_heading_count"], 9)
        self.assertEqual(sum(page["legacy_extraction_count"] for page in report["pages"]), 2)
        self.assertEqual(sum(report["label_counts"].values()), 65)
        relation = report["source_report_ledger"]
        self.assertEqual(relation["observed_physical_slot_count"], 65)
        self.assertEqual(relation["logical_row_count"], 36)
        self.assertEqual(relation["unobserved_pages"], report["unobserved_page_numbers"])
        self.assertIsNone(relation["physical_slot_count"])
        self.assertIsNone(relation["qualified_transaction_count"])
        for row in relation["logical_rows"]:
            slot = report["slots"][int(row["observation_ref"].split("/")[-1])]
            self.assertEqual(row["slot_refs"], [{"page_number": slot["page_number"],
                                               "slot_key": str(slot["grid_slot"])}])
        self.assertTrue(all(slot["candidate_transaction_id"] is None
                            for slot in report["slots"]))

    def test_changed_source_version_remains_coverage_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._archive(root)
            manifest["manifest_sha256"] = "0" * 64
            path = root / "senate_efd" / "paper_pages" / DOCUMENT_ID / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            result = compare_archived_batch(root, OBSERVATIONS, self._batch())
        self.assertEqual(result["coverage_unknown_report_count"], 1)
        self.assertEqual(result["physical_grid_slot_count"], 0)
        self.assertEqual(result["reports"][0]["slots"], [])
        relation = project_pending_slots(result)["source_report_ledgers"][0]["ledger"]
        self.assertEqual(relation["coverage_unknown_pages"], [1, 2, 3, 4, 5])
        self.assertEqual(relation["observed_physical_slot_count"], 0)
        self.assertIsNone(relation["physical_slot_count"])

    def test_extraction_from_different_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._archive(root)
            batch = self._batch()
            batch["extractions"][0]["source_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "extraction and archive differ"):
                compare_archived_batch(root, OBSERVATIONS, batch)

    def test_fixed_data_slots_project_only_to_pending_ledger(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._archive(root)
            observed = compare_archived_batch(root, OBSERVATIONS, self._batch())
            projected = project_pending_slots(observed)
        self.assertEqual(projected["pending_row_count"], 36)
        self.assertEqual(projected["account_scoped_pending_row_count"], 36)
        self.assertEqual(projected["pending_rows"][0]["account_heading_visual_raw"],
                         "(S) 3M's Partners LLC")
        self.assertEqual(projected["legacy_extracted_report_pending_row_count"], 36)
        self.assertEqual(projected["unknown_slot_count"], 5)
        self.assertEqual(projected["unobserved_page_count"], 1)
        self.assertEqual(len({(row["source_slot"]["page_number"],
                               row["source_slot"]["grid_slot"])
                              for row in projected["pending_rows"]}), 36)
        self.assertTrue(all(row["status"] == "pending_source_verification" and
                            row["formal_transaction_id"] is None and
                            row["account_heading_slot"] is not None and
                            row["account_heading_visual_raw"] and
                            row["account_scope_status"] ==
                            "source_bound_visual_scope_unverified_identity" and
                            row["source_slot"]["entrypoint_source_sha256"] ==
                            observed["reports"][0]["entrypoint_source_sha256"]
                            for row in projected["pending_rows"]))

    def test_projection_rejects_account_heading_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._archive(root)
            observed = compare_archived_batch(root, OBSERVATIONS, self._batch())
            observed["reports"][0]["account_scope_observation"]["rows"][0][
                "account_heading_slot"]["asset_crop_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "account heading differs"):
                project_pending_slots(observed)

    def test_projection_rejects_missing_visual_heading(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._archive(root)
            observed = compare_archived_batch(root, OBSERVATIONS, self._batch())
            observed["reports"][0]["account_scope_observation"]["account_headings"][0][
                "printed_heading"] = ""
            with self.assertRaisesRegex(ValueError, "account heading differs"):
                project_pending_slots(observed)

    def test_projection_rejects_duplicate_physical_slot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._archive(root)
            observed = compare_archived_batch(root, OBSERVATIONS, self._batch())
            observed["reports"][0]["slots"][1]["grid_slot"] = observed[
                "reports"][0]["slots"][0]["grid_slot"]
            with self.assertRaisesRegex(ValueError, "duplicated or unbound|slot key missing or duplicated"):
                project_pending_slots(observed)

    def test_date_cell_reader_is_recorded_without_qualification(self):
        calls = []

        def date_reader(crop):
            calls.append(crop.size)
            return {"status": "conflict", "date": None,
                    "valid_read_count": 2, "variants": {}}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._archive(root)
            observed = compare_archived_batch(root, OBSERVATIONS, self._batch(),
                                              date_reader=date_reader)
            projected = project_pending_slots(observed)
        self.assertEqual(len(calls), 36)
        self.assertEqual(projected["pending_row_count"], 36)
        self.assertTrue(all(row["date_cell_observation_status"] == "conflict" and
                            row["date_cell_conflict"] and
                            row["formal_transaction_id"] is None
                            for row in projected["pending_rows"]))


class SenateReportRelationTests(unittest.TestCase):
    """Synthetic contract tests; these do not measure official OCR accuracy."""

    @staticmethod
    def observation():
        def slot(page, grid, label):
            return {"page_number": page, "grid_slot": grid,
                    "row_bounds_px": [grid * 100, (grid + 1) * 100],
                    "row_crop_sha256": "b" * 64, "label": label,
                    "asset_ocr_raw": "Example asset", "type_marks": ["Purchase"],
                    "amount_marks": ["$1,001 - $15,000"],
                    "key_cells": {"date": {
                        "page_ocr_raw": "01/02/2026", "page_ocr_parsed": "2026-01-02",
                        "cell_ocr_parsed": None, "cell_ocr": None, "conflict": False}}}
        slots = [slot(1, 1, "heading_candidate"), slot(1, 2, "data_observed"),
                 slot(1, 3, "unknown"), slot(2, 1, "blank_appearance"),
                 slot(2, 2, "data_observed")]
        report = {"document_id": "11111111-1111-4111-8111-111111111111",
                  "entrypoint_source_sha256": "a" * 64,
                  "coverage_status": "fixed_slots_observed", "archive_page_count": 3,
                  "physical_grid_slot_count": 5, "slots": slots,
                  "label_counts": dict(Counter(s["label"] for s in slots)),
                  "unobserved_page_numbers": [3],
                  "legacy_extraction_count": 2, "legacy_extraction_failed": False,
                  "pages": [{"page_number": number, "page_sha256": "c" * 64,
                             "physical_grid_slot_count": sum(s["page_number"] == number for s in slots),
                             "label_counts": dict(Counter(s["label"] for s in slots
                                                          if s["page_number"] == number))}
                            for number in (1, 2)]}
        report["source_report_ledger"] = _source_report_relations(report)
        return {"schema_version": SCHEMA, "fixed_inventory_evidence_commit": EVIDENCE_COMMIT,
                "slot_classification_schema_version": SLOT_SCHEMA, "report_count": 1,
                "unobserved_page_count": 1, "label_counts": report["label_counts"],
                "reports": [report]}

    def test_source_links_preserve_same_asset_disclosure_rows_and_unknown_pages(self):
        projected = project_pending_slots(self.observation())
        self.assertEqual(projected["pending_row_count"], 2)
        self.assertEqual(projected["unknown_slot_count"], 1)
        source = projected["source_report_ledgers"][0]
        self.assertEqual(source["observation_ref"], "reports/0")
        ledger = source["ledger"]
        self.assertEqual(ledger["logical_row_count"], 2)
        self.assertIsNone(ledger["physical_slot_count"])
        self.assertIsNone(ledger["qualified_transaction_count"])
        self.assertEqual(ledger["relation_coverage"], "partial_unknown")
        self.assertEqual(ledger["disposition_counts"],
                         {"blank": 1, "data_candidate": 2, "heading": 1, "unknown": 1})

    def test_saved_legacy_projection_keeps_exact_existing_fields(self):
        observed = self.observation()
        current = project_pending_slots(observed)
        del observed["reports"][0]["archive_page_count"]
        del observed["reports"][0]["source_report_ledger"]
        legacy = project_pending_slots(observed)
        self.assertNotIn("source_report_ledgers", legacy)
        del current["source_report_ledgers"]
        self.assertEqual(current, legacy)

    def test_stale_link_with_same_totals_is_rejected(self):
        observed = self.observation()
        observed["reports"][0]["source_report_ledger"]["logical_rows"][0][
            "observation_ref"] = "slots/4"
        with self.assertRaisesRegex(ValueError, "relations differ"):
            project_pending_slots(observed)

    def test_missing_archive_page_cannot_disappear(self):
        observed = self.observation()
        observed["reports"][0]["unobserved_page_numbers"] = []
        with self.assertRaisesRegex(ValueError, "archive pages"):
            project_pending_slots(observed)

    def test_missing_link_is_not_accepted_as_legacy_when_page_count_present(self):
        observed = self.observation()
        del observed["reports"][0]["source_report_ledger"]
        with self.assertRaisesRegex(ValueError, "relations differ"):
            project_pending_slots(observed)

    def test_legacy_and_new_reports_cannot_mix_without_page_accounting(self):
        observed = self.observation()
        old = deepcopy(observed["reports"][0])
        old["document_id"] = "22222222-2222-4222-8222-222222222222"
        del old["archive_page_count"]
        del old["source_report_ledger"]
        observed["reports"].append(old)
        observed["report_count"] = 2
        with self.assertRaisesRegex(ValueError, "archive page count"):
            project_pending_slots(observed)

    def test_dangling_slot_is_rejected_even_when_report_total_is_padded(self):
        observed = self.observation()
        report = observed["reports"][0]
        report["slots"].append(deepcopy(report["slots"][1]))
        report["slots"][-1]["page_number"] = 3
        report["physical_grid_slot_count"] += 1
        with self.assertRaisesRegex(ValueError, "unobserved page"):
            project_pending_slots(observed)

    def test_stale_page_summary_is_rejected(self):
        observed = self.observation()
        observed["reports"][0]["pages"][0]["label_counts"] = {"data_observed": 3}
        with self.assertRaisesRegex(ValueError, "page label counts"):
            project_pending_slots(observed)


if __name__ == "__main__":
    unittest.main()
