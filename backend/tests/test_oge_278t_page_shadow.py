"""Contract checks for the read-only 278-T page coverage probe."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.oge_278t_page_shadow import (
    _number_suggestion, _raster_rule_consensus, inspect_page,
)


class Page:
    width = 800
    height = 600

    def __init__(self, text="", *, raster=False, rules=(), words=()):
        self._text = text
        self._words = list(words)
        self.images = ([{"x0": 0, "x1": 800, "top": 0, "bottom": 600}]
                       if raster else [])
        self.lines = [{"x0": 20, "x1": 600, "y0": 600 - top,
                       "y1": 600 - top, "top": top}
                      for top in rules]

    def extract_text(self):
        return self._text

    def extract_words(self):
        return self._words


def header_words():
    return [{"text": label, "top": 40, "x0": left}
            for label, left in (("DESCRIPTION", 80), ("TYPE", 400),
                                ("DATE", 500), ("NOTIFICATION", 580),
                                ("AMOUNT", 700))]


class Oge278TPageShadowTests(unittest.TestCase):
    def test_native_header_and_printed_number_anchors(self):
        words = header_words() + [
            {"text": "1", "top": 80, "x0": 35},
            {"text": "2", "top": 110, "x0": 35},
            {"text": "5", "top": 300, "x0": 35},
            {"text": "Endnotes", "top": 200, "x0": 35},
        ]
        page = Page("# DESCRIPTION TYPE DATE NOTIFICATION AMOUNT " * 3,
                    words=words)
        result = inspect_page(page, 1)
        self.assertEqual(result["coverage"], "table_header_found")
        self.assertEqual(result["row_basis"], "printed_number_anchors")
        self.assertEqual(result["candidate_row_bands"], [[80., 80.], [110., 110.]])

    def test_raster_with_dense_irregular_grid_is_candidate_not_counted(self):
        page = Page(raster=True, rules=list(range(80, 500, 17)) + [531])
        result = inspect_page(page, 2)
        self.assertEqual(result["coverage"], "table_geometry_candidate")
        self.assertEqual(result["text_mode"], "raster_without_text")

    def test_header_bounded_grid_excludes_title_rules_and_keeps_unread_number(self):
        words = [{**word, "top": 90} for word in header_words()]
        words += [{"text": "41", "top": 122, "x0": 80},
                  {"text": "43", "top": 156, "x0": 80}]
        page = Page("Description Type Date Notification Amount " * 3,
                    raster=True, words=words,
                    rules=[56, 67, 78, 89, 121, 138, 155, 172])
        result = inspect_page(page, 2)
        self.assertEqual(result["row_basis"], "header_bounded_grid_slots")
        self.assertEqual(result["candidate_row_bands"],
                         [[121., 138.], [138., 155.], [155., 172.]])
        self.assertEqual(result["unresolved_row_bands"], [[138., 155.]])
        self.assertEqual(result["row_coverage"], "physical_slots_with_unread_number")

    def test_missing_first_or_middle_rule_is_unknown_grid(self):
        words = [{**word, "top": 90} for word in header_words()]
        words += [{"text": str(number), "top": top + 1, "x0": 80}
                  for number, top in enumerate((121, 138, 155, 172), 1)]
        for rules in ([56, 67, 78, 89, 138, 155, 172, 189],
                      [56, 67, 78, 89, 121, 138, 172, 189]):
            with self.subTest(rules=rules):
                result = inspect_page(Page("Transaction table " * 8,
                                           raster=True, words=words,
                                           rules=rules), 3)
                self.assertEqual(result["row_coverage"], "unknown_grid")
                self.assertEqual(result["row_basis"],
                                 "unverified_printed_number_anchors")
                self.assertTrue(result["unresolved_row_regions"])

    def test_partial_ocr_header_uses_grid_without_fabricating_missing_label(self):
        words = [{**word, "top": 90} for word in header_words()
                 if word["text"] != "AMOUNT"]
        words += [{"text": str(number), "top": top + 1, "x0": 80}
                  for number, top in enumerate((121, 138, 155), 1)]
        page = Page("Transactions Description Type Date Notification " * 3,
                    raster=True, words=words,
                    rules=[56, 67, 78, 89, 121, 138, 155, 172])
        result = inspect_page(page, 4)
        self.assertEqual(result["coverage"], "table_geometry_candidate")
        self.assertFalse(result["header_complete"])
        self.assertEqual(result["row_coverage"], "physical_slots_located")

    def test_stitches_column_fragments_with_small_vertical_jitter(self):
        words = [{**word, "top": 90} for word in header_words()]
        words += [{"text": str(number), "top": top + 2, "x0": 80}
                  for number, top in enumerate((121, 138, 155), 1)]
        page = Page("Transaction table " * 8, raster=True, words=words)
        page.lines = []
        for top in (121, 138, 155, 172):
            for left, right, jitter in ((80, 110, 0), (110, 300, 1),
                                        (300, 480, 2), (480, 610, 2.8),
                                        (610, 725, 3.4)):
                page.lines.append({"x0": left, "x1": right,
                                   "y0": 600 - top - jitter,
                                   "y1": 600 - top - jitter,
                                   "top": top + jitter})
        result = inspect_page(page, 5)
        self.assertEqual(result["row_coverage"], "physical_slots_located")
        self.assertEqual(result["rule_source"], "stitched_segments")
        self.assertEqual(len(result["candidate_row_bands"]), 3)

    def test_geometry_can_close_while_ocr_number_sequence_remains_suspect(self):
        words = [{**word, "top": 90} for word in header_words()]
        words += [{"text": label, "top": top + 1, "x0": 80}
                  for label, top in (("132", 121), ("133", 138),
                                     ("138", 155))]
        result = inspect_page(Page("Transaction table " * 8, raster=True,
                                   words=words, rules=[89, 121, 138, 155, 172]), 7)
        self.assertEqual(result["row_coverage"], "physical_slots_located")
        self.assertEqual(result["number_sequence_warnings"], [
            {"slot_index": 3, "previous_label": 133, "observed_label": 138}])

    def test_cell_ocr_requires_distinct_crops_and_keeps_conflict_unresolved(self):
        one_crop = [
            {"x_left": 83, "y_offset": offset, "text": "136", "confidence": 96}
            for offset in (-1, 0)]
        self.assertIsNone(_number_suggestion(one_crop))
        self.assertEqual(_number_suggestion(one_crop + [
            {"x_left": 82, "y_offset": 0, "text": "136", "confidence": 91}]),
                         "136")
        self.assertIsNone(_number_suggestion(one_crop + [
            {"x_left": 82, "y_offset": 0, "text": "138", "confidence": 91}]))

    def test_raster_grid_census_requires_three_matching_strips(self):
        clean = [[109, 123, 137, 151, 165, 179],
                 [109.3, 123.2, 137.1, 151.2, 165.1, 179.2],
                 [108.9, 122.8, 136.8, 150.8, 164.8, 178.8]]
        self.assertEqual(len(_raster_rule_consensus(clean)), 6)
        self.assertEqual(_raster_rule_consensus(clean[:2]), [])
        self.assertEqual(_raster_rule_consensus([clean[0], clean[1], clean[2][1:]]),
                         [])

    def test_raster_without_table_evidence_remains_unknown(self):
        result = inspect_page(Page(raster=True), 3)
        self.assertEqual(result["coverage"], "coverage_unknown_raster")
        self.assertEqual(result["candidate_row_bands"], [])

    def test_native_endnotes_is_not_transaction_table(self):
        result = inspect_page(Page("Endnotes. Summary of Contents " * 5), 4)
        self.assertEqual(result["coverage"], "non_table_section")


if __name__ == "__main__":
    unittest.main()
