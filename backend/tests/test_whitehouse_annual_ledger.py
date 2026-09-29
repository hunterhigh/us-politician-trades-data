"""Offline checks for the OGE annual shadow evidence ledger adapter."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_oge_278e_audit import _annual

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.oge_278e_audit import audit_public_278e
from unison_snapshot.whitehouse_annual_ledger import build_annual_shadow_ledger


class WhiteHouseAnnualLedgerTests(unittest.TestCase):
    def sample(self) -> dict:
        extraction = _annual()
        holding = deepcopy(extraction["holdings"][0])
        holding.update(section="part6", owner="Unknown", page_number=4,
                       row_number="2.1", source_row_locator="p4-y120")
        transaction = {
            "section": "part7", "page_number": 6, "row_number": "1",
            "source_row_locator": "p6-y214", "asset_name": "SPY ETF",
            "owner": "Unknown", "raw_columns": {
                "description": "SPY ETF", "type": "purchase",
                "date": "03/20/2025", "amount": "$1,001 - $15,000"},
            "transaction_type": "purchase", "transaction_date": "2025-03-20",
            "amount_low": 1001, "amount_high": 15000,
        }
        extraction["holdings"] = [holding]
        extraction["transactions"] = [transaction]
        extraction["printed_row_count"] = 2
        extraction["explicit_empty_sections"] = ["part2", "part5"]
        extraction["recognized_source_row_counts"] = {
            "part2": 0, "part5": 0, "part6": 1, "part7": 1}
        extraction["source_row_census_status"] = "offline_synthetic_fixture"
        extraction["source_row_census_complete"] = False
        return extraction

    def test_ledger_rows_and_manifest_validate_but_remain_shadow_only(self) -> None:
        extraction = self.sample()
        audit = audit_public_278e(extraction)
        ledger = build_annual_shadow_ledger(
            extraction, audit, run_id="offline-oge-annual-001",
            code_commit="a" * 40, started_at="2026-09-29T02:00:00Z",
            completed_at="2026-09-29T02:00:01Z", trigger="replay")
        self.assertEqual(ledger["manifest"]["accounted_rows"], 2)
        self.assertEqual(ledger["manifest"]["counts"]["qualified_rows"], 2)
        self.assertEqual(len({row["idempotency_key"] for row in ledger["rows"]}), 1)
        self.assertEqual(ledger["publication_status"], "shadow_only_not_promoted")
        self.assertTrue(ledger["part7_cross_report_dedup_required"])
        self.assertEqual(ledger["part7_publication_status"], "pending_278t_reconciliation")
        self.assertEqual({row["parser"]["rules_version"] for row in ledger["rows"]},
                         {"whitehouse-278e-part6-part7-shadow/v1"})

    def test_unlocated_rows_fail_closed_instead_of_inventing_page_evidence(self) -> None:
        extraction = self.sample()
        extraction["holdings"][0]["page_number"] = 0
        audit = audit_public_278e(extraction)
        with self.assertRaisesRegex(ValueError, "verified positive page"):
            build_annual_shadow_ledger(
                extraction, audit, run_id="offline-oge-annual-002",
                code_commit="a" * 40, started_at="2026-09-29T02:00:00Z")


if __name__ == "__main__":
    unittest.main()
