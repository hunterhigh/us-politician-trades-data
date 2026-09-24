"""Qualification must not turn partial 278e extractions into complete holdings."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unison_snapshot.oge_278e_audit import audit_public_278e
from unison_snapshot.oge_278e_public import PARSER_VERSION, SCHEMA
from unison_snapshot.whitehouse_scanned_annual import (
    TRUMP_2025_PARSER_VERSION, TRUMP_2025_PREVIOUS_PARSER_VERSION,
    TRUMP_2025_SOURCE_SHA256,
    apply_scanned_annual_corrections)


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
                "value_high": 15000, "raw_columns": {
                    "description": "SPY ETF", "value": "$1,001 - $15,000"},
                "report_period_end": "2025-12-31", "holding_valuation_date": "2025-12-31",
                "holding_valuation_status": "exact_period_end"}],
            "transactions": [], "excluded": [], "quarantined": [],
            "document_reasons": [], "requires_cross_report_dedup": False}


def _v7_holding(*, row_number: str = "222", locator: str = "p27-y1753",
                asset_name: str = "IRON MTN INC NEW COM") -> dict:
    return {
        "section": "part6", "page_number": 27, "row_number": row_number,
        "asset_name": asset_name, "owner": "Unknown",
        "raw_columns": {"description": asset_name, "eif": "NIA",
                        "value": "$1,001 - $15,000"},
        "value_low": 1001, "value_high": 15000,
        "report_period_end": "2025-12-31",
        "holding_valuation_date": "2025-12-31",
        "holding_valuation_status": "exact_period_end",
        "source_row_locator": locator,
        "account_scope": "investment-account-3",
        "account_scope_evidence": {"page_number": 27,
                                   "text": "INVESTMENT ACCOUNT #3"},
        "ocr_field_confidence": {
            "row_number": {"mean": 0.0, "min": 0.0, "word_count": 1},
            "description": {"mean": 92.75, "min": 80.58, "word_count": 5},
            "value": {"mean": 45.0, "min": 10.0, "word_count": 3},
        },
        "ocr_word_repairs": [],
        "value_geometry_evidence": {
            "method": "source_bound_part6_value_column/v1",
            "column_bounds": {"eif_start": 452.52, "value_start": 476.64,
                              "income_start": 562.68},
            "words": [
                {"original_text": "$1,001", "value_text": "$1,001",
                 "x0": 480.0, "x1": 495.0, "top": 175.3,
                 "ocr_confidence": 10.0, "repair_method": None},
                {"original_text": "-", "value_text": "-",
                 "x0": 500.0, "x1": 502.0, "top": 175.3,
                 "ocr_confidence": 30.0, "repair_method": None},
                {"original_text": "$15,000", "value_text": "$15,000",
                 "x0": 506.0, "x1": 525.0, "top": 175.3,
                 "ocr_confidence": 95.0, "repair_method": None},
            ],
        },
        "parser_recovery": {
            "method": "source_bound_part6_structured_row/v1",
            "original_quarantine_reasons": ["holding_ocr_confidence_below_threshold"],
        },
    }


def _trump_v7(*rows: dict) -> dict:
    extraction = _annual()
    extraction.update(
        parser_version=TRUMP_2025_PARSER_VERSION,
        source_url=("https://www.whitehouse.gov/wp-content/uploads/2026/06/"
                    "President-Donald-J.-Trump-2025-Annual-Report.pdf"),
        source_sha256=TRUMP_2025_SOURCE_SHA256, filer_name="Donald Trump",
        position_line_raw="President", cover_report_year=2025,
        extraction_method="tesseract_ocr_geometry", ocr_engine="tesseract test",
        filing_date=None, signature_text=None,
        explicit_empty_sections=["part2", "part5", "part7"],
        document_reasons=["filer_handwritten_signature_or_date_unverified"],
        holdings=list(rows), printed_row_count=len(rows),
    )
    return apply_scanned_annual_corrections(extraction)


def _trump_v8_transaction() -> dict:
    extraction = _annual()
    extraction.update(
        parser_version=TRUMP_2025_PARSER_VERSION,
        source_url=("https://www.whitehouse.gov/wp-content/uploads/2026/06/"
                    "President-Donald-J.-Trump-2025-Annual-Report.pdf"),
        source_sha256=TRUMP_2025_SOURCE_SHA256, filer_name="Donald Trump",
        position_line_raw="President", cover_report_year=2025,
        extraction_method="tesseract_ocr_geometry", ocr_engine="tesseract test",
        filing_date=None, signature_text=None, holdings=[],
        explicit_empty_sections=["part2", "part5", "part6"],
        document_reasons=["filer_handwritten_signature_or_date_unverified"],
        printed_row_count=1, requires_cross_report_dedup=True,
    )
    extraction["transactions"] = [{
        "section": "part7", "page_number": 159, "row_number": "10",
        "asset_name": "VANGUARD GROWTH ETF", "owner": "Unknown",
        "raw_columns": {"description": "VANGUARD GROWTH ETF", "type": "purchase",
                        "date": "9/18/2025", "amount": "$1,001 - $15,000"},
        "transaction_type": "purchase", "transaction_date": "2025-09-18",
        "amount_low": 1001, "amount_high": 15000,
        "source_row_locator": "p159-y2531",
        "account_scope": "investment-account-1",
        "account_scope_evidence": {"page_number": 159,
                                   "text": "INVESTMENT ACCOUNT #1"},
        "ocr_mean_confidence": 92.0, "ocr_min_confidence": 80.0,
        "ocr_field_confidence": {},
        "transaction_geometry_evidence": {
            "method": "source_bound_part7_columns/v1",
            "column_bounds": {"description_start": 37.44, "type_start": 612.36,
                              "date_start": 660.24, "amount_start": 706.32},
            "words": [{"field": "description", "original_text": "VANGUARD",
                       "x0": 37.44, "x1": 66.6, "top": 255.24,
                       "ocr_confidence": 95.0, "included_in_raw_column": True,
                       "repair_method": None}],
        },
        "parser_recovery": {"method": "source_bound_part7_structured_row/v1",
                            "field_repairs": []},
    }]
    return apply_scanned_annual_corrections(extraction)


class Public278eAuditTests(unittest.TestCase):
    def test_source_bound_part7_transaction_gets_row_level_qualification(self):
        audit = audit_public_278e(_trump_v8_transaction())
        self.assertEqual(audit["source_candidate_transaction_count"], 1)
        self.assertTrue(audit["transaction_row_audit"][0]["source_candidate_eligible"])
        self.assertIn("part7_cross_278t_dedup_pending", audit["report_blocking_reasons"])

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

    def test_scanned_annual_year_is_the_reporting_calendar_year(self):
        extraction = _annual()
        extraction.update(extraction_method="tesseract_ocr_geometry",
                          ocr_engine="tesseract test", cover_report_year=2025,
                          report_period_end="2025-12-31",
                          holding_valuation_date="2025-12-31")
        extraction["holdings"][0].update(
            report_period_end="2025-12-31", holding_valuation_date="2025-12-31",
            ocr_mean_confidence=96.0, ocr_min_confidence=90.0)
        audit = audit_public_278e(extraction)
        self.assertNotIn("annual_period_or_valuation_unverified",
                         audit["holding_blocking_reasons"])

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

    def test_trump_v6_parser_version_is_bound_to_the_fixed_pdf(self):
        supported = {PARSER_VERSION, TRUMP_2025_PREVIOUS_PARSER_VERSION}
        wrong_source = _annual()
        wrong_source["parser_version"] = TRUMP_2025_PREVIOUS_PARSER_VERSION
        with patch("unison_snapshot.oge_278e_audit.SUPPORTED_PARSER_VERSIONS", supported):
            self.assertIn("untrusted_extraction_version",
                          audit_public_278e(wrong_source)["report_blocking_reasons"])

        fixed = _annual()
        fixed.update(
            parser_version=TRUMP_2025_PREVIOUS_PARSER_VERSION,
            source_url=("https://www.whitehouse.gov/wp-content/uploads/2026/06/"
                        "President-Donald-J.-Trump-2025-Annual-Report.pdf"),
            source_sha256=TRUMP_2025_SOURCE_SHA256, filer_name="Donald Trump",
            position_line_raw="President", cover_report_year=2025,
            extraction_method="tesseract_ocr_geometry", ocr_engine="tesseract test",
            filing_date=None, signature_text=None,
            document_reasons=["filer_handwritten_signature_or_date_unverified"],
        )
        description = (
            "Trump Marks Philippines LLC Location: Century City Makati, Philippines Licensee: "
            "Century Luxury Properties, Inc. Additional Underlying Assets: Registered "
            "Trademark(s) (values not readily ascertainable).* (See Exhibit A). Underlying "
            "Asset: U.S. bank account Location: Jupiter, FL (value represents bank account only)")
        fixed["holdings"][0].update(page_number=864, row_number="332",
                                     asset_name=description,
                                     raw_columns={"description": description, "eif": "No",
                                                  "value": "$1,001 to $15,000"},
                                     ocr_mean_confidence=96.0,
                                     ocr_min_confidence=90.0)
        fixed = apply_scanned_annual_corrections(fixed)
        with patch("unison_snapshot.oge_278e_audit.SUPPORTED_PARSER_VERSIONS", supported):
            audit = audit_public_278e(fixed)
            self.assertNotIn("untrusted_extraction_version", audit["report_blocking_reasons"])
            self.assertTrue(audit["holding_row_audit"][0]["source_candidate_eligible"])
            self.assertEqual(fixed["holdings"][0]["source_row_locator"], "p864-y2772")

            weak_cell = deepcopy(fixed)
            weak_cell["holdings"][0]["critical_field_confidence"]["description"][
                "minimum"] = 40.0
            decision = audit_public_278e(weak_cell)["holding_row_audit"][0]
            self.assertFalse(decision["source_candidate_eligible"])
            self.assertIn("holding_critical_field_confidence_invalid", decision["reasons"])

    def test_trump_part6_physical_identity_replaces_weak_printed_number_confidence(self):
        extraction = _annual()
        extraction.update(
            parser_version=TRUMP_2025_PREVIOUS_PARSER_VERSION,
            source_url=("https://www.whitehouse.gov/wp-content/uploads/2026/06/"
                        "President-Donald-J.-Trump-2025-Annual-Report.pdf"),
            source_sha256=TRUMP_2025_SOURCE_SHA256, filer_name="Donald Trump",
            position_line_raw="President", cover_report_year=2025,
            extraction_method="tesseract_ocr_geometry", ocr_engine="tesseract test",
            filing_date=None, signature_text=None,
            explicit_empty_sections=["part2", "part5", "part7"],
            document_reasons=["filer_handwritten_signature_or_date_unverified"],
        )
        row = extraction["holdings"][0]
        row.update(
            section="part6", page_number=27, row_number="222", owner="Unknown",
            asset_name="IRON MTN INC NEW COM",
            raw_columns={"description": "IRON MTN INC NEW COM", "eif": "NIA",
                         "value": "$1,001 - $15,000"},
            source_row_locator="p27-y1753", account_scope="investment-account-3",
            account_scope_evidence={"page_number": 27,
                                    "text": "INVESTMENT ACCOUNT #3"},
            ocr_field_confidence={
                "row_number": {"mean": 75.2, "min": 75.2, "word_count": 1},
                "description": {"mean": 92.75, "min": 80.58, "word_count": 5},
                "value": {"mean": 84.24, "min": 69.49, "word_count": 3},
            },
            ocr_mean_confidence=80.0, ocr_min_confidence=20.0,
        )
        corrected = apply_scanned_annual_corrections(extraction)
        decision = audit_public_278e(corrected)["holding_row_audit"][0]
        self.assertTrue(decision["source_candidate_eligible"])

        wrong_account = deepcopy(corrected)
        wrong_account["holdings"][0]["account_scope_evidence"]["text"] = (
            "INVESTMENT ACCOUNT #4")
        decision = audit_public_278e(wrong_account)["holding_row_audit"][0]
        self.assertFalse(decision["source_candidate_eligible"])
        self.assertIn("holding_row_evidence_invalid", decision["reasons"])

        weak_value = deepcopy(corrected)
        weak_value["holdings"][0]["ocr_field_confidence"]["value"]["min"] = 40.0
        decision = audit_public_278e(weak_value)["holding_row_audit"][0]
        self.assertFalse(decision["source_candidate_eligible"])
        self.assertIn("holding_critical_field_confidence_invalid", decision["reasons"])

    def test_trump_v7_numeric_investment_row_uses_geometry_not_value_score(self):
        decision = audit_public_278e(_trump_v7(_v7_holding()))["holding_row_audit"][0]
        self.assertTrue(decision["source_candidate_eligible"])

    def test_trump_v7_synthetic_identity_and_tampered_geometry_fail_closed(self):
        synthetic = _v7_holding(row_number="ocr-p27-y1753")
        decision = audit_public_278e(_trump_v7(synthetic))["holding_row_audit"][0]
        self.assertFalse(decision["source_candidate_eligible"])
        self.assertIn("holding_v7_outside_numeric_investment_account_pool",
                      decision["reasons"])

        geometry = _v7_holding()
        geometry["value_geometry_evidence"]["words"][2]["x1"] = 570.0
        decision = audit_public_278e(_trump_v7(geometry))["holding_row_audit"][0]
        self.assertFalse(decision["source_candidate_eligible"])
        self.assertIn("holding_value_geometry_evidence_invalid", decision["reasons"])

    def test_trump_v7_duplicate_and_incomplete_names_remain_ineligible(self):
        first = _v7_holding(asset_name="AIRBNB INC CLASS A")
        second = _v7_holding(row_number="223", locator="p27-y1874",
                             asset_name="AIRBNB INC CLASS A")
        decisions = audit_public_278e(_trump_v7(first, second))["holding_row_audit"]
        self.assertTrue(all(not row["source_candidate_eligible"] for row in decisions))
        self.assertTrue(all("possible_same_asset_multiple_disclosed_rows" in row["reasons"]
                            for row in decisions))

        incomplete = _v7_holding(asset_name="CORP")
        decision = audit_public_278e(_trump_v7(incomplete))["holding_row_audit"][0]
        self.assertFalse(decision["source_candidate_eligible"])
        self.assertIn("holding_asset_description_incomplete_or_noisy", decision["reasons"])

    def test_trump_v7_description_relation_and_recovery_trace_are_required(self):
        fragment = _v7_holding(asset_name="SYSTEMS INC")
        complete = _v7_holding(row_number="223", locator="p27-y1874",
                               asset_name="CISCO SYSTEMS INC")
        decisions = audit_public_278e(_trump_v7(fragment, complete))["holding_row_audit"]
        self.assertIn("holding_asset_description_relation_unresolved",
                      decisions[0]["reasons"])

        tampered = _v7_holding()
        tampered["parser_recovery"]["original_quarantine_reasons"] = [
            "row_number_ocr_unreadable"]
        decision = audit_public_278e(_trump_v7(tampered))["holding_row_audit"][0]
        self.assertFalse(decision["source_candidate_eligible"])
        self.assertIn("holding_parser_recovery_trace_invalid", decision["reasons"])


if __name__ == "__main__":
    unittest.main()
