import unittest

from unison_snapshot.house_ptr_amount_header import LABELS, evaluate_amount_header_pages, interpret_amount_column


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

    def test_ten_column_form_does_not_invent_a_spouse_supplement(self):
        observed = page(1, missing="K")
        observed["amount_column_count"] = 10
        checked = evaluate_amount_header_pages("a" * 64, [observed])
        self.assertEqual(checked["status"], "printed_header_correlated")
        self.assertEqual(checked["ranges"]["B"]["low"], 15001)
        self.assertNotIn("K", checked["ranges"])
        del observed["amount_column_count"]
        self.assertEqual(evaluate_amount_header_pages("a" * 64, [observed])["status"],
                         "header_unresolved")

    def test_unread_page_and_mixed_column_geometry_stay_unknown(self):
        observed = page(2, missing="K")
        observed["amount_column_count"] = 10
        result = evaluate_amount_header_pages("a" * 64,
            [{"page": 1, "status": "header_not_selected"}, observed])
        self.assertEqual(result["status"], "header_unresolved")
        self.assertIsNone(result["ranges"])
        observed["page"] = 1
        result = evaluate_amount_header_pages("a" * 64, [observed, page(2)])
        self.assertEqual(result["status"], "header_unresolved")

    def test_positioned_single_column_requires_its_actual_correct_numerals(self):
        reading = {"status": "raw_column_observed", "label": "B", "raw": "$15001 og\n$50 000"}
        self.assertEqual(interpret_amount_column(reading), {
            "letter": "B", "low": 15001, "high": 50000, "kind": "range"})
        for raw in ("$150001 og\n$50 000", "$15001\n$50 000 999", "$15001\n$50 000\n$1"):
            reading["raw"] = raw
            self.assertIsNone(interpret_amount_column(reading))
        reading.update(label="J", raw="Over $50,000,000")
        self.assertIs(interpret_amount_column(reading)["low_exclusive"], True)
        reading["raw"] = "$50,000,000"
        self.assertIsNone(interpret_amount_column(reading))

    def test_page_census_is_required(self):
        with self.assertRaisesRegex(ValueError, "census"):
            evaluate_amount_header_pages("a" * 64, [page(2)])
