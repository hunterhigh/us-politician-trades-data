"""Contract checks for the read-only 278-T page coverage probe."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.oge_278t_page_coverage import _raster_rule_consensus, reconcile_raster_ocr_rows


class Oge278TPageShadowTests(unittest.TestCase):
    def test_raster_grid_census_requires_three_matching_strips(self):
        clean = [[109, 123, 137, 151, 165, 179],
                 [109.3, 123.2, 137.1, 151.2, 165.1, 179.2],
                 [108.9, 122.8, 136.8, 150.8, 164.8, 178.8]]
        self.assertEqual(len(_raster_rule_consensus(clean)), 6)
        self.assertEqual(_raster_rule_consensus(clean[:2]), [])
        self.assertEqual(_raster_rule_consensus([clean[0], clean[1], clean[2][1:]]),
                         [])

    def test_raster_slots_survive_when_ocr_has_no_date_or_amount_anchor(self):
        sha = "a" * 64
        census = {"source_sha256": sha, "results": [
            {"page_number": 3, "status": "physical_slots_located",
             "physical_row_bands": [[100, 115], [115, 130], [130, 145]]},
            {"page_number": 4, "status": "coverage_unknown",
             "physical_row_bands": []},
        ]}
        result = reconcile_raster_ocr_rows(
            census, [{"page_number": 3, "geometry_top": 103},
                     {"page_number": 3, "geometry_top": 134},
                     {"page_number": 3, "geometry_top": 160}],
            expected_sha256=sha, ocr_source_sha256=sha)
        self.assertEqual(result["physical_slot_count"], 3)
        self.assertEqual([slot["status"] for slot in result["slots"]], [
            "ocr_row_mapped", "physical_slot_without_ocr_row", "ocr_row_mapped"])
        self.assertEqual(result["slots"][1]["content_status"], "unverified")
        self.assertEqual(result["unmatched_ocr_rows"], [
            {"page_number": 3, "ocr_row_index": 2}])
        self.assertEqual(result["unknown_pages"], [4])
        self.assertFalse(result["production_mutation_authorized"])

    def test_raster_ocr_reconciliation_rejects_changed_source_and_overlaps(self):
        sha = "a" * 64
        census = {"source_sha256": sha, "results": [
            {"page_number": 3, "status": "physical_slots_located",
             "physical_row_bands": [[100, 120], [115, 130]]},
        ]}
        with self.assertRaisesRegex(ValueError, "source binding"):
            reconcile_raster_ocr_rows(census, [], expected_sha256="b" * 64,
                                      ocr_source_sha256=sha)
        with self.assertRaisesRegex(ValueError, "overlap"):
            reconcile_raster_ocr_rows(census, [], expected_sha256=sha,
                                      ocr_source_sha256=sha)

    def test_raster_reconciliation_keeps_no_ocr_and_unselected_pages_explicit(self):
        sha = "a" * 64
        census = {"source_sha256": sha, "results": [
            {"page_number": 2, "status": "physical_slots_located",
             "physical_row_bands": [[100, 115]]},
        ]}
        empty = reconcile_raster_ocr_rows(census, [], expected_sha256=sha,
                                          ocr_source_sha256=sha)
        self.assertEqual(empty["slots"][0]["status"],
                         "physical_slot_without_ocr_row")
        partial = reconcile_raster_ocr_rows(
            census, [{"page_number": 3, "geometry_top": 105}],
            expected_sha256=sha, ocr_source_sha256=sha)
        self.assertEqual(partial["ocr_rows_outside_selected_pages"], [
            {"page_number": 3, "ocr_row_index": 0}])
        with self.assertRaisesRegex(ValueError, "source binding"):
            reconcile_raster_ocr_rows(census, [], expected_sha256=sha,
                                      ocr_source_sha256="b" * 64)
        for bad_top in (-1, float("nan"), float("inf"), True):
            with self.subTest(bad_top=bad_top):
                with self.assertRaisesRegex(ValueError, "geometry"):
                    reconcile_raster_ocr_rows(
                        census, [{"page_number": 2, "geometry_top": bad_top}],
                        expected_sha256=sha, ocr_source_sha256=sha)

if __name__ == "__main__":
    unittest.main()
