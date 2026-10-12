import unittest

from unison_snapshot.house_ptr_amount_header import LABELS, evaluate_amount_header_pages


def page(number, *, first="$1,000-$15,000", missing=None):
    text = [first, *(value for label, value in LABELS.items() if label != missing)]
    return {"page": number, "status": "header_ocr_observed",
            "raw_header_text": "\n".join(text)}


class HouseAmountHeaderTests(unittest.TestCase):
    def test_two_pages_confirm_printed_a_variant_and_open_ended_columns(self):
        report = evaluate_amount_header_pages("a" * 64, [page(1), page(2)])
        self.assertEqual(report["status"], "printed_header_correlated")
        self.assertEqual(report["ranges"]["A"],
                         {"low": 1000, "high": 15000, "kind": "range"})
        self.assertEqual(report["ranges"]["J"]["high"], None)
        self.assertEqual(report["ranges"]["K"]["kind"],
                         "spouse_dc_special_open_ended")

    def test_missing_or_conflicting_header_fails_closed(self):
        missing = evaluate_amount_header_pages("a" * 64, [page(1), page(2, missing="G")])
        self.assertEqual(missing["status"], "header_unresolved")
        variant = evaluate_amount_header_pages("a" * 64, [
            page(1), page(2, first="$1,001-$15,000")])
        self.assertEqual(variant["status"], "header_unresolved")

    def test_page_census_is_required(self):
        with self.assertRaisesRegex(ValueError, "census"):
            evaluate_amount_header_pages("a" * 64, [page(2)])
