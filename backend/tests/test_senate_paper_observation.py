"""Read-only sidecar must bind fixed grid slots to current archived bytes."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from unison_snapshot.senate_paper_observation import (
    compare_archived_batch, project_pending_slots,
)
from unison_snapshot.senate_slots_shadow import EVIDENCE_COMMIT


REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"
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
            result = compare_archived_batch(root, DOCS, self._batch())
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
        self.assertTrue(all(slot["candidate_transaction_id"] is None
                            for slot in report["slots"]))

    def test_changed_source_version_remains_coverage_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._archive(root)
            manifest["manifest_sha256"] = "0" * 64
            path = root / "senate_efd" / "paper_pages" / DOCUMENT_ID / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            result = compare_archived_batch(root, DOCS, self._batch())
        self.assertEqual(result["coverage_unknown_report_count"], 1)
        self.assertEqual(result["physical_grid_slot_count"], 0)
        self.assertEqual(result["reports"][0]["slots"], [])

    def test_extraction_from_different_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._archive(root)
            batch = self._batch()
            batch["extractions"][0]["source_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "extraction and archive differ"):
                compare_archived_batch(root, DOCS, batch)

    def test_fixed_data_slots_project_only_to_pending_ledger(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._archive(root)
            observed = compare_archived_batch(root, DOCS, self._batch())
            projected = project_pending_slots(observed)
        self.assertEqual(projected["pending_row_count"], 36)
        self.assertEqual(projected["account_scoped_pending_row_count"], 36)
        self.assertEqual(projected["legacy_extracted_report_pending_row_count"], 36)
        self.assertEqual(projected["unknown_slot_count"], 5)
        self.assertEqual(projected["unobserved_page_count"], 1)
        self.assertEqual(len({(row["source_slot"]["page_number"],
                               row["source_slot"]["grid_slot"])
                              for row in projected["pending_rows"]}), 36)
        self.assertTrue(all(row["status"] == "pending_source_verification" and
                            row["formal_transaction_id"] is None and
                            row["account_heading_slot"] is not None and
                            row["account_scope_status"] ==
                            "source_bound_visual_scope_unverified_identity" and
                            row["source_slot"]["entrypoint_source_sha256"] ==
                            observed["reports"][0]["entrypoint_source_sha256"]
                            for row in projected["pending_rows"]))

    def test_projection_rejects_account_heading_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._archive(root)
            observed = compare_archived_batch(root, DOCS, self._batch())
            observed["reports"][0]["account_scope_observation"]["rows"][0][
                "account_heading_slot"]["asset_crop_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "account heading differs"):
                project_pending_slots(observed)

    def test_projection_rejects_duplicate_physical_slot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._archive(root)
            observed = compare_archived_batch(root, DOCS, self._batch())
            observed["reports"][0]["slots"][1]["grid_slot"] = observed[
                "reports"][0]["slots"][0]["grid_slot"]
            with self.assertRaisesRegex(ValueError, "duplicated or unbound"):
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
            observed = compare_archived_batch(root, DOCS, self._batch(),
                                              date_reader=date_reader)
            projected = project_pending_slots(observed)
        self.assertEqual(len(calls), 36)
        self.assertEqual(projected["pending_row_count"], 36)
        self.assertTrue(all(row["date_cell_observation_status"] == "conflict" and
                            row["date_cell_conflict"] and
                            row["formal_transaction_id"] is None
                            for row in projected["pending_rows"]))


if __name__ == "__main__":
    unittest.main()
