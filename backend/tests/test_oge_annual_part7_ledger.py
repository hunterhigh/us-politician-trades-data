from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.oge_annual_part7_ledger import observe_annual_part7


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class OgeAnnualPart7LedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extraction = _fixture("oge_annual_vance_2026_v2.json")
        self.evidence = _fixture("oge_annual_vance_2026_part7_evidence.json")
        self.census = _fixture("oge_annual_vance_2026_page_census.json")

    def test_fixed_page_accounts_for_filled_and_blank_slots_without_promotion(self) -> None:
        result = observe_annual_part7(self.extraction, self.evidence, self.census)
        self.assertEqual(result["source_sha256"],
                         "bcad0b4e58789135b758b5a73fc9584ff4bf9bff9cf52b8e4ca9d4189dbe9e3d")
        self.assertEqual((result["physical_slot_count"], result["filled_transaction_count"],
                          result["blank_template_count"], result["unaccounted_slot_count"]),
                         (20, 10, 10, 0))
        self.assertEqual(result["production_eligible_transaction_count"], 0)
        self.assertEqual([slot["classification"] for slot in result["slots"]],
                         ["filled_transaction"] * 10 + ["blank_template"] * 10)
        self.assertEqual(len({slot["slot_id"] for slot in result["slots"]}), 20)
        self.assertEqual(len({row["pending_transaction_id"] for row in
                              result["pending_transactions"]}), 10)
        self.assertTrue(all(row["disposition"] == "quarantined" and
                            row["official_278t_duplicate_status"] == "unresolved"
                            for row in result["pending_transactions"]))
        self.assertEqual([row["transaction_date"] for row in
                          result["pending_transactions"][6:9]],
                         ["2025-07-14", "2025-10-14", "2025-12-15"])

    def test_census_or_parser_drift_fails_closed(self) -> None:
        changes = []
        changed = deepcopy(self.census)
        changed["source_sha256"] = "0" * 64
        changes.append((self.extraction, self.evidence, changed))
        changed = deepcopy(self.census)
        changed["pages"]["part7"]["blank_template_numbers"].remove(20)
        changes.append((self.extraction, self.evidence, changed))
        changed = deepcopy(self.census)
        changed["pages"]["part7"]["filled_rows"][1]["index"] = 0
        changes.append((self.extraction, self.evidence, changed))
        changed = deepcopy(self.extraction)
        changed["transactions"][0]["row_number"] = "2"
        changes.append((changed, self.evidence, self.census))
        for extraction, evidence, census in changes:
            with self.subTest(census=census, extraction=extraction), self.assertRaises(ValueError):
                observe_annual_part7(extraction, evidence, census)


if __name__ == "__main__":
    unittest.main()
