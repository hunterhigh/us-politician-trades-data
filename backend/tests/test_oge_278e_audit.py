"""Qualification must not turn partial 278e extractions into complete holdings."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unison_snapshot.oge_278e_audit import audit_public_278e
from unison_snapshot.oge_278e_public import PARSER_VERSION, SCHEMA


def _annual() -> dict:
    return {"schema_version": SCHEMA, "parser_version": PARSER_VERSION,
            "source_url": "https://www.whitehouse.gov/wp-content/uploads/2026/09/example.pdf",
            "source_sha256": "a" * 64, "filer_name": "Example, Ada",
            "position_line_raw": "Assistant to the President - White House",
            "report_type": "Annual", "cover_report_year": 2026,
            "filing_date": "2026-06-20",
            "signature_text": "/s/ Example, Ada [electronically signed on 06/20/2026 by Example, Ada in Integrity.gov]",
            "report_period_end": "2025-12-31", "holding_valuation_date": "2025-12-31",
            "section_pages": {"part2": 1, "part5": 1, "part6": 1, "part7": 1},
            "explicit_empty_sections": ["part5", "part6", "part7"],
            "printed_row_count": 1, "holdings": [{
                "section": "part2", "page_number": 3, "row_number": "1",
                "asset_name": "SPY ETF", "owner": "Self", "value_low": 1001,
                "value_high": 15000, "raw_columns": {"description": "SPY ETF"},
                "report_period_end": "2025-12-31", "holding_valuation_date": "2025-12-31",
                "holding_valuation_status": "exact_period_end"}],
            "transactions": [], "excluded": [], "quarantined": [],
            "document_reasons": [], "requires_cross_report_dedup": False}


class Public278eAuditTests(unittest.TestCase):
    def test_complete_source_annual_is_eligible_but_not_production_assessed(self):
        extraction = _annual()
        before = deepcopy(extraction)
        audit = audit_public_278e(extraction)
        self.assertTrue(audit["printed_rows_conserved"])
        self.assertTrue(audit["source_holdings_eligible"])
        self.assertTrue(audit["source_report_eligible"])
        self.assertEqual(audit["source_candidate_holding_count"], 1)
        self.assertEqual(audit["production_status"],
                         "not_assessed_external_identity_amendments_and_snapshot_gate")
        self.assertEqual(extraction, before)

    def test_part6_unknown_owner_is_kept_as_filer_reported(self):
        extraction = _annual()
        extraction["holdings"][0].update(section="part6", owner="Unknown")
        extraction["explicit_empty_sections"] = ["part2", "part5", "part7"]
        audit = audit_public_278e(extraction)
        self.assertTrue(audit["source_holdings_eligible"])
        self.assertEqual(audit["source_candidate_holding_count"], 1)
        self.assertEqual(audit["holding_row_audit"][0]["owner"], "Unknown")
        extraction["holdings"][0]["owner_evidence_conflict"] = [{"owner": "Self"}]
        self.assertIn("holding_owner_evidence_invalid",
                      audit_public_278e(extraction)["holding_row_audit"][0]["reasons"])

    def test_part6_owner_requires_traceable_account_evidence(self):
        extraction = _annual()
        extraction["explicit_empty_sections"] = ["part2", "part5", "part7"]
        row = extraction["holdings"][0]
        row.update(section="part6", row_number="2.1", owner="Joint")
        self.assertIn("holding_owner_evidence_invalid",
                      audit_public_278e(extraction)["holding_row_audit"][0]["reasons"])
        row["owner_evidence"] = [{"owner": "Joint", "basis": "explicit_part6_parent_account",
                                  "page_number": 3, "row_number": "2",
                                  "text": "Joint Brokerage Account #2"}]
        audit = audit_public_278e(extraction)
        self.assertTrue(audit["source_holdings_eligible"])
        self.assertEqual(audit["source_candidate_holding_count"], 1)

    def test_part7_dedup_and_transaction_quarantine_do_not_erase_clean_asset_row(self):
        extraction = _annual()
        extraction["printed_row_count"] = 2
        extraction["quarantined"] = [{"section": "part7", "page_number": 4,
                                     "row_number": "1", "reasons": ["date_missing"]}]
        extraction["requires_cross_report_dedup"] = True
        audit = audit_public_278e(extraction)
        self.assertTrue(audit["source_holdings_eligible"])
        self.assertFalse(audit["source_report_eligible"])
        self.assertIn("part7_cross_278t_dedup_pending", audit["report_blocking_reasons"])
        self.assertIn("part7_rows_quarantined", audit["report_blocking_reasons"])

    def test_unconserved_rows_and_missing_signature_fail_closed(self):
        extraction = _annual()
        extraction["printed_row_count"] = 2
        extraction["signature_text"] = ""
        audit = audit_public_278e(extraction)
        self.assertFalse(audit["printed_rows_conserved"])
        self.assertFalse(audit["source_holdings_eligible"])
        self.assertIn("printed_rows_not_conserved", audit["holding_blocking_reasons"])
        self.assertIn("filer_signature_unverified", audit["report_blocking_reasons"])

    def test_entrant_and_termination_cannot_invent_valuation_date(self):
        entrant = _annual()
        entrant.update(report_type="New Entrant", cover_report_year=None,
                       report_period_end=None, holding_valuation_date=None,
                       appointment_date="2025-01-22")
        entrant["holdings"][0].update(report_period_end=None,
                                       holding_valuation_date=None,
                                       holding_valuation_status="not_exact_on_cover")
        audit = audit_public_278e(entrant)
        self.assertFalse(audit["source_holdings_eligible"])
        self.assertIn("holding_valuation_date_not_exact", audit["holding_blocking_reasons"])
        term = deepcopy(entrant)
        term.update(report_type="Termination", report_period_end="2026-01-02",
                    termination_date="2026-01-02", filing_date="2025-12-18",
                    signature_text="/s/ Example, Ada [electronically signed on 12/18/2025 by Example, Ada in Integrity.gov]")
        term["holdings"][0]["report_period_end"] = "2026-01-02"
        audit = audit_public_278e(term)
        self.assertIn("termination_signed_before_effective_date", audit["report_blocking_reasons"])
        self.assertFalse(audit["source_report_eligible"])


if __name__ == "__main__":
    unittest.main()
