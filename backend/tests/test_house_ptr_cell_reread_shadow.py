from __future__ import annotations

import json
from pathlib import Path
import unittest

from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_ptr_cell_reread_shadow import _comparison, build_cell_reread_shadow


ROOT = Path(__file__).resolve().parents[2]


class HousePtrCellRereadShadowTests(unittest.TestCase):
    def test_read_conflict_and_ambiguous_image_fail_closed(self):
        self.assertEqual(_comparison("TI2A126", {"status": "clear", "text": "7/24/26"}),
                         "ocr_conflicts_with_image_observation")
        self.assertEqual(_comparison("United Health", {"status": "ambiguous"}),
                         "image_observation_unresolved")
        self.assertEqual(_comparison("02/20/26", {"status": "clear", "text": "02/20/26"}),
                         "ocr_agrees_with_image_observation")

    def test_wrong_source_fails_before_ocr(self):
        with self.assertRaisesRegex(HouseIndexError, "source identity mismatch"):
            build_cell_reread_shadow(b"wrong pdf", {}, {}, Path("missing-tesseract"))

    def test_fixed_rereads_preserve_both_ocr_passes_and_quarantine(self):
        expected = {"9115704": 2, "9116260": 1, "9116326": 1}
        for document_id, count in expected.items():
            with self.subTest(document_id=document_id):
                report = json.loads((ROOT / "docs" /
                    f"house-ptr-cell-reread-{document_id}-shadow.json").read_text(encoding="utf-8"))
                cells = json.loads((ROOT / "docs" /
                    f"house-ptr-checkbox-cells-{document_id}-shadow.json").read_text(encoding="utf-8"))
                self.assertEqual(report["source_sha256"], cells["source_sha256"])
                self.assertEqual(report["file_status"], "failed_open")
                self.assertEqual(report["qualified_rows"], 0)
                self.assertEqual(len(report["selected_rows"]), count)
                self.assertTrue({row["physical_locator"] for row in report["selected_rows"]}
                                <= {row["physical_locator"] for row in cells["rows"]})
                for row in report["selected_rows"]:
                    self.assertIsNone(row["candidate_id"])
                    self.assertEqual(row["disposition"],
                                     "quarantined_shadow_field_evidence_incomplete")
                    self.assertIn("report_failed_open", row["blocking_reasons"])
                    self.assertIn("action_checkbox_not_machine_verified", row["blocking_reasons"])
                    self.assertIn("amount_checkbox_not_machine_verified", row["blocking_reasons"])
                    for name in ("asset", "transaction_date", "notification_date"):
                        field = row["fields"][name]
                        self.assertIn("original_page_ocr_raw", field)
                        self.assertIn("crop_ocr_raw", field)
                        self.assertIn("image_observation", field)
        report = json.loads((ROOT / "docs" /
            "house-ptr-cell-reread-9116260-shadow.json").read_text(encoding="utf-8"))
        self.assertIn("notification_date_ocr_conflicts_with_image_observation",
                      report["selected_rows"][0]["blocking_reasons"])
        self.assertIn("notification_date_original_crop_ocr_conflict",
                      report["selected_rows"][0]["blocking_reasons"])


if __name__ == "__main__":
    unittest.main()
