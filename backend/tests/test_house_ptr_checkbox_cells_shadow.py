from __future__ import annotations

import json
from pathlib import Path
import unittest

from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_ptr_checkbox_cells_shadow import _fingerprint, build_checkbox_cells_shadow


ROOT = Path(__file__).resolve().parents[2]


class HousePtrCheckboxCellsShadowTests(unittest.TestCase):
    def test_amount_grid_fingerprint_distinguishes_two_layouts(self):
        ten = [.125, .171, .425, .448, .471, .494, .556]
        ten += [.617 + index * .0328 for index in range(11)]
        eleven = [.138, .163, .333, .360, .386, .412, .439, .491]
        eleven += [.552 + index * .0322 for index in range(12)]
        self.assertEqual(_fingerprint(ten)["layout_family"],
                         "legacy_checkbox_10_amount_columns")
        self.assertEqual(_fingerprint(eleven)["layout_family"],
                         "legacy_checkbox_11_amount_columns")

    def test_missing_amount_rule_fails_closed(self):
        boundaries = [.125, .171, .425, .448, .471, .494, .556]
        boundaries += [.617 + index * .0328 for index in range(11)]
        boundaries.pop()
        with self.assertRaisesRegex(HouseIndexError, "fingerprint"):
            _fingerprint(boundaries)

    def test_wrong_source_fails_before_render(self):
        with self.assertRaisesRegex(HouseIndexError, "source or failed-grid identity mismatch"):
            build_checkbox_cells_shadow(b"wrong", {}, {})

    def test_fixed_reports_conserve_rows_cells_and_quarantine(self):
        expected = {"9115704": (7, 3, 10), "9116260": (7, 4, 11),
                    "9116326": (8, 4, 11)}
        for document_id, (row_count, actions, amounts) in expected.items():
            with self.subTest(document_id=document_id):
                grid = json.loads((ROOT / "docs" /
                    f"house-ptr-failed-grid-{document_id}-shadow.json").read_text(encoding="utf-8"))
                report = json.loads((ROOT / "docs" /
                    f"house-ptr-checkbox-cells-{document_id}-shadow.json").read_text(encoding="utf-8"))
                self.assertEqual(report["source_sha256"], grid["source_sha256"])
                self.assertEqual(report["counts"]["physical_rows"], row_count)
                self.assertEqual(report["counts"]["quarantined_rows"], row_count)
                self.assertEqual(report["counts"]["qualified_rows"], 0)
                self.assertEqual({row["physical_locator"] for row in report["rows"]},
                                 {row["locator"] for row in grid["physical_rows"]})
                for page in report["pages"]:
                    self.assertEqual(page["action_columns"], actions)
                    self.assertEqual(page["amount_columns"], amounts)
                for row in report["rows"]:
                    self.assertEqual(row["disposition"],
                                     "quarantined_shadow_unverified_cells")
                    self.assertIsNone(row["candidate_id"])
                    cells = row["cells"]
                    self.assertEqual(len(cells["action"]), actions)
                    self.assertEqual(len(cells["amount"]), amounts)
                    all_cells = [cells[key] for key in ("owner", "asset",
                                  "transaction_date", "notification_date")]
                    all_cells += cells["action"] + cells["amount"]
                    self.assertEqual(len({cell["locator"] for cell in all_cells}),
                                     len(all_cells))
                    self.assertTrue(all(cell["status"] == "unread_unverified"
                                        and cell["value"] is None for cell in all_cells))


if __name__ == "__main__":
    unittest.main()
