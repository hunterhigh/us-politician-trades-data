import unittest

from unison_snapshot.oge_type_literal import interpret_official_type_literal


class OfficialTypeLiteralTests(unittest.TestCase):
    def read(self, text, role="type", values=("Purchase", "Sale", "Exchange")):
        return interpret_official_type_literal(text, field_role=role, official_values=values)

    def test_complete_source_variant_is_semantics_not_raw_repair(self):
        for raw in ("purchaso", "purchaso ", "PURCHASO"):
            result = self.read(raw)
            self.assertEqual(result["value"], "purchase")
            self.assertEqual(result["raw_text"], raw)
            self.assertFalse(result["is_glyph_or_unicode_repair"])
            self.assertTrue(result["requires_source_field_binding"])

    def test_substrings_compounds_comments_and_competing_types_rejected(self):
        for raw in ("repurchase", "purchase-related", "purchaso sale", "no purchaso",
                    "purchaso/exchange", "purchaso?", "purchas", "ourchase", "pur chaso"):
            self.assertEqual(self.read(raw)["status"], "unsupported", raw)

    def test_asset_comment_unknown_role_and_different_enum_rejected(self):
        for role in ("description", "comment", "unknown", "account"):
            self.assertEqual(self.read("purchaso", role=role)["status"], "unsupported")
        for values in (("Purchase", "Sale"), ("Purchase", "Sale", "Transfer"),
                       ("Purchase", "Purchase", "Sale")):
            self.assertEqual(self.read("purchaso", values=values)["status"], "unsupported")

    def test_other_observations_not_consumed_or_overruled(self):
        self.assertEqual(self.read("Sale")["status"], "unsupported")
        self.assertEqual(self.read("Exchange")["status"], "unsupported")
        self.assertNotIn("conflict_resolved", self.read("purchaso"))
