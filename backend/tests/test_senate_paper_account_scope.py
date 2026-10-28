from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import unittest

from unison_snapshot.senate_paper_account_scope import (
    observe_reviewed_account_scopes, confirmed_visual_review_from_fields,
)
from senate_source_fixture import EVIDENCE_COMMIT, fixed_archive_pages

REPO = Path(__file__).resolve().parents[2]
OBSERVATIONS = REPO / "backend/data/source_observations/senate_efd"
class SenatePaperAccountScopeTests(unittest.TestCase):
    @staticmethod
    def _inputs() -> tuple[dict, dict]:
        ledger = json.loads((OBSERVATIONS / "senate-paper-first-report-rows.json").read_text(
            encoding="utf-8"))
        fields = json.loads((OBSERVATIONS / "senate-paper-first-report-fields.json").read_text(
            encoding="utf-8"))
        return ledger, confirmed_visual_review_from_fields(fields, ledger)

    def test_fixed_account_scopes_preserve_repeated_underlying_assets(self):
        result = observe_reviewed_account_scopes(*self._inputs())
        self.assertEqual(result["physical_grid_slot_count"], 65)
        self.assertEqual(result["account_heading_count"], 9)
        self.assertEqual(result["visually_confirmed_data_row_count"], 36)
        self.assertEqual(result["unscoped_data_row_count"], 0)
        self.assertEqual(result["distinct_unscoped_field_combination_count"], 18)
        self.assertEqual(result["repeated_unscoped_field_group_count"], 9)
        self.assertEqual(result["repeated_unscoped_field_row_count"], 27)
        self.assertEqual(result["within_heading_duplicate_row_count"], 0)
        self.assertEqual(result["distinct_scoped_field_combination_count"], 36)
        self.assertTrue(all(row["formal_transaction_id"] is None and
                            row["account_heading_slot"] is not None
                            for row in result["rows"]))
        self.assertEqual(result["rows"][0]["account_heading_slot"]["grid_slot"], 1)
        self.assertEqual(result["rows"][0]["account_heading_visual_raw"],
                         "(S) 3M's Partners LLC")
        self.assertEqual(result["account_headings"][0]["heading_status"],
                         "confirmed_fixed_image")

    def test_changed_visual_mark_fails_closed(self):
        ledger, visual = self._inputs()
        visual = deepcopy(visual)
        visual["rows"][0]["direction"] = "purchase"
        with self.assertRaisesRegex(ValueError, "visual mark disagrees"):
            observe_reviewed_account_scopes(ledger, visual)

    def test_fixed_field_audit_derives_same_visual_rows(self):
        ledger, visual = self._inputs()
        fields = json.loads((OBSERVATIONS / "senate-paper-first-report-fields.json")
                            .read_text(encoding="utf-8"))
        derived = confirmed_visual_review_from_fields(fields, ledger)
        self.assertEqual(derived, visual)
        fields["rows"][0]["asset"]["ocr"]["joined_text"] = "different"
        with self.assertRaisesRegex(ValueError, "differs from fixed source"):
            confirmed_visual_review_from_fields(fields, ledger)

    def test_changed_account_heading_fails_closed(self):
        ledger, visual = self._inputs()
        visual["account_headings"][0]["asset_crop_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "visual account heading differs"):
            observe_reviewed_account_scopes(ledger, visual)

    def test_source_identity_must_match_fixed_archive(self):
        ledger, visual = self._inputs()
        ledger["evidence_commit"] = "0" * 40
        with self.assertRaisesRegex(ValueError, "source identity changed"):
            observe_reviewed_account_scopes(ledger, visual)

    def test_fixed_archive_page_and_crop_hashes(self):
        target = (f"{EVIDENCE_COMMIT}:senate_efd/paper_pages/"
                  "068274e1-4b7a-4453-a242-563dde4c10d8/manifest.json")
        if subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", target],
                          capture_output=True).returncode:
            self.skipTest("fixed Senate paper archive unavailable")
        inventory = json.loads((OBSERVATIONS / "senate-paper-52-page-inventory.json")
                               .read_text(encoding="utf-8"))
        read_page = fixed_archive_pages(REPO, inventory)
        result = observe_reviewed_account_scopes(*self._inputs(),
                                                    page_bytes=read_page)
        self.assertTrue(result["archive_bytes_verified"])
        self.assertEqual(result["visually_confirmed_data_row_count"], 36)


if __name__ == "__main__":
    unittest.main()
