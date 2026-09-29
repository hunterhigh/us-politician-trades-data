"""Golden offline coverage for the OGE 278-T shadow adapter."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_transaction_adapter import adapt_oge_278t_extraction


FIXTURE = (Path(__file__).parent / "fixtures" / "oge_278t" / "gold_rows_v1.json")


class OgeTransactionAdapterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_golden_rows_are_mapped_and_fully_accounted(self):
        result = adapt_oge_278t_extraction(
            self.fixture["extraction"], self.fixture["catalog_record"])
        self.assertEqual(result["counts"], self.fixture["expected_counts"])
        self.assertEqual(result["row_count"], 5)
        self.assertEqual([row["disposition"] for row in result["rows"]],
                         ["excluded", "qualified", "quarantined", "excluded", "unrecognized"])
        qualified = result["rows"][1]
        values = {item["field"]: item["normalized_value"]
                  for item in qualified["observations"] if item["selected"]}
        self.assertEqual(values["transaction_type"], "purchase")
        self.assertEqual(values["transaction_date"], "2025-05-01")
        self.assertEqual((values["amount_low"], values["amount_high"]), (1001, 15000))
        self.assertTrue(all(row["evidence_locations"][0]["page"] == 2
                            for row in result["rows"]))

    def test_identity_mismatch_quarantines_otherwise_qualified_row(self):
        extraction = self.fixture["extraction"]
        extraction["agency"] = "Different Office"
        result = adapt_oge_278t_extraction(extraction, self.fixture["catalog_record"])
        self.assertEqual(result["counts"]["qualified"], 0)
        self.assertIn("catalog_identity_or_document_mismatch", result["rows"][1]["reasons"])

    def test_missing_source_inventory_fails_closed(self):
        extraction = dict(self.fixture["extraction"])
        extraction.pop("source_rows")
        with self.assertRaisesRegex(OgeCatalogError, "source rows"):
            adapt_oge_278t_extraction(extraction, self.fixture["catalog_record"])

    def test_transaction_semantics_are_rechecked_at_adapter_boundary(self):
        extraction = self.fixture["extraction"]
        extraction["transactions"][0]["amount_high"] = 14999
        result = adapt_oge_278t_extraction(extraction, self.fixture["catalog_record"])
        self.assertEqual(result["counts"]["qualified"], 0)
        self.assertIn("amount_range_not_qualified", result["rows"][1]["reasons"])


if __name__ == "__main__":
    unittest.main()
