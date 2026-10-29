import unittest
from unison_snapshot.oge_amount_literal import interpret_grouped_statutory_amount
from unison_snapshot.oge_278t_fields import _ordered_amount_shape


class StatutoryLiteralTests(unittest.TestCase):
    def test_complete_category_meaning_preserves_raw_and_does_not_claim_glyph_repair(self):
        for raw, values in [("$50.001 - $100,000",[50001,100000]),
                            ("$100,001 - $250.000",[100001,250000]),
                            ("$50,001 -$100.000",[50001,100000]),
                            ("$500.001 - $1.000.000",[500001,1000000])]:
            with self.subTest(raw=raw):
                result = interpret_grouped_statutory_amount(raw,field_role="amount")
                self.assertEqual(result["values"], values)
                self.assertEqual(result["raw_literal"], raw)
                self.assertFalse(result["source_text_rewritten"])
                self.assertEqual(result["punctuation_glyph_identity"],"not_asserted")

    def test_not_a_general_money_decimal_or_prefix_normalizer(self):
        for raw in ["$50.01 - $100,000", "$50.001", "$50.001 -", "50.001 - $100,000",
                    "$50.001 - $100,000 5", "foo $50.001 - $100,000", "$50.001 - $100,000.00",
                    "$50.001 - $100,001", "$0.050.001 - $100,000", "$1,000.001 - $5,000,000",
                    "$100.000 - $50,001", "Over $50.000.000", "$50.001 / $100,000"]:
            with self.subTest(raw=raw):
                self.assertIsNone(interpret_grouped_statutory_amount(raw,field_role="amount"))
        self.assertIsNone(interpret_grouped_statutory_amount("$50.001 - $100,000",field_role="description"))

    def test_normal_shape_requires_proven_order_and_keeps_explanation(self):
        reading={"ordered_ocr_text":"$50.001 - $100,000","status":"geometry_ordered_unverified",
                 "reading_coordinate_basis":"source_and_row_top_relative_consistency/v1"}
        result=_ordered_amount_shape(reading)
        self.assertEqual(result["values"],[50001,100000])
        self.assertEqual(result["literal_interpretation"]["raw_literal"],reading["ordered_ocr_text"])
        self.assertIsNone(_ordered_amount_shape({**reading,"status":"original_ocr_order_unverified"})["values"])
