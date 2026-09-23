"""Source-bound partial annual recovery stays narrow and auditable."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unison_snapshot.oge_278e_audit import audit_public_278e
from unison_snapshot.oge_278e_public import PARSER_VERSION, SCHEMA
from unison_snapshot.whitehouse_scanned_annual import apply_scanned_annual_corrections


VANCE_SHA = "d43f25659a26474faae4df8218ff352e3c01a2eaf17b6ae35ae07649bbe90c3d"
VANCE_URL = ("https://www.whitehouse.gov/wp-content/uploads/2026/06/"
             "Vice-President-JD-Vance-2025-Annual-Report.pdf")


def _vance() -> dict:
    return {
        "schema_version": SCHEMA, "parser_version": PARSER_VERSION,
        "source_url": VANCE_URL, "source_sha256": VANCE_SHA,
        "filer_name": "JD Vance", "position_line_raw": "Vice President",
        "report_type": "Annual", "cover_report_year": 2025,
        "filing_date": None, "signature_text": None,
        "report_period_end": "2025-12-31", "holding_valuation_date": "2025-12-31",
        "extraction_method": "tesseract_ocr_geometry", "ocr_engine": "tesseract test",
        "section_pages": {"part2": 1, "part5": 1, "part6": 1, "part7": 1},
        "explicit_empty_sections": ["part5", "part6", "part7"],
        "printed_row_count": 1, "holdings": [], "transactions": [], "excluded": [],
        "quarantined": [{
            "section": "part2", "page_number": 3, "row_number": "1.1",
            "owner": "Self", "owner_evidence": None,
            "raw_columns": {"description": "|SPY-SPDR S&P",
                            "eif": "Yes", "value": "|$100,001 - $250,000"},
            "reasons": ["holding_ocr_confidence_below_threshold",
                        "holding_value_unreadable_or_open"],
            "ocr_mean_confidence": 86.6, "ocr_min_confidence": 34.87,
        }],
        "document_reasons": ["filer_handwritten_signature_or_date_unverified"],
        "requires_cross_report_dedup": False,
    }


class ScannedAnnualTests(unittest.TestCase):
    def test_attested_pdf_recovers_only_exact_border_artifact_row(self) -> None:
        corrected = apply_scanned_annual_corrections(_vance())
        self.assertEqual(corrected["filing_date"], "2026-06-23")
        self.assertEqual(corrected["source_bound_recovered_holding_count"], 1)
        self.assertEqual(corrected["quarantined"], [])
        self.assertEqual(corrected["document_reasons"], [])
        row = corrected["holdings"][0]
        self.assertEqual(row["asset_name"], "SPY-SPDR S&P")
        self.assertEqual((row["value_low"], row["value_high"]), (100001, 250000))
        audit = audit_public_278e(corrected)
        self.assertNotIn("filer_signature_unverified", audit["holding_blocking_reasons"])
        self.assertTrue(audit["holding_row_audit"][0]["source_candidate_eligible"])

    def test_unknown_hash_and_ambiguous_internal_bar_are_not_recovered(self) -> None:
        unknown = _vance()
        unknown["source_sha256"] = "a" * 64
        self.assertIs(apply_scanned_annual_corrections(unknown), unknown)
        ambiguous = _vance()
        ambiguous["quarantined"][0]["raw_columns"]["description"] = "Narya Fund |, LP"
        corrected = apply_scanned_annual_corrections(ambiguous)
        self.assertEqual(corrected["source_bound_recovered_holding_count"], 0)
        self.assertEqual(len(corrected["quarantined"]), 1)


if __name__ == "__main__":
    unittest.main()
