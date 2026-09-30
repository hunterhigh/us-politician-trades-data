"""Fixed-archive replay of the four remaining viewer-only Senate reports."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest


REPO = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO / "backend" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
HAS_PIL = importlib.util.find_spec("PIL") is not None
if HAS_PIL:
    SPEC = importlib.util.spec_from_file_location(
        "audit_senate_paper_viewer_remainders",
        SCRIPT_DIR / "audit_senate_paper_viewer_remainders.py")
    assert SPEC and SPEC.loader
    MODULE = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(MODULE)


@unittest.skipUnless(HAS_PIL, "optional Senate image audit dependency unavailable")
class ViewerRemainderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        target = ("80f086267677b8fde34314f24024bdc505d97cb0:"
                  "senate_efd/paper_pages/3a4c5095-028a-4614-a692-836719da4e63/manifest.json")
        if subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", target],
                          capture_output=True).returncode:
            raise unittest.SkipTest("fixed official Senate viewer archive unavailable")

    def test_every_physical_slot_stays_unresolved(self):
        result = MODULE.audit(REPO, REPO / "docs/senate-paper-52-page-inventory.json")
        self.assertEqual(len(result["reports"]), 4)
        self.assertEqual(sum(r["page_count"] for r in result["reports"]), 21)
        self.assertEqual(sum(r["physical_grid_slot_count"] for r in result["reports"]), 277)
        expected = {
            "3a4c5095-028a-4614-a692-836719da4e63": (65, 5, 9, 51),
            "a0d25e8f-fe54-4328-a7ea-504da008742b": (99, 6, 14, 79),
            "d02263c3-381d-4ee9-8d84-2c44d9baa59e": (65, 6, 17, 42),
            "ec20cd93-6702-4a29-b3a6-983f4b17f365": (48, 2, 11, 35),
        }
        for report in result["reports"]:
            slots, headings, blanks, unknown = expected[report["document_id"]]
            self.assertEqual(report["physical_grid_slot_count"], slots)
            self.assertEqual(report["disposition_counts"], {
                "heading_text_candidate_quarantined": headings,
                "blank_appearance_unresolved": blanks,
                "content_or_noise_unresolved": unknown,
            })
            self.assertTrue(all(row["candidate_transaction_id"] is None
                                for row in report["slots"]))
            self.assertEqual(len({(row["page_number"], row["grid_slot"])
                                  for row in report["slots"]}), slots)

    def test_legacy_extracted_reports_keep_grid_census_separate_from_candidates(self):
        result = MODULE.audit(REPO, REPO / "docs/senate-paper-52-page-inventory.json",
                              MODULE.LEGACY_EXTRACTED_IDS)
        self.assertEqual(len(result["reports"]), 4)
        self.assertEqual(sum(r["page_count"] for r in result["reports"]), 26)
        self.assertEqual(sum(r["physical_grid_slot_count"] for r in result["reports"]), 362)
        self.assertEqual({r["document_id"]: r["physical_grid_slot_count"]
                          for r in result["reports"]}, {
            "929216d5-5dbd-429c-858c-1e9332924627": 134,
            "d337c392-e0aa-428e-be93-44a327b90d08": 82,
            "f028d2ce-4ab7-41a8-a67a-91675b6941d7": 82,
            "f873aeb4-adbb-4934-a188-79416a2e4c76": 64,
        })
        self.assertTrue(all(slot["candidate_transaction_id"] is None
                            for report in result["reports"] for slot in report["slots"]))


if __name__ == "__main__":
    unittest.main()
