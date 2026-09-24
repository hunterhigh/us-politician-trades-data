"""Fail-closed counterexamples for scanned White House annual holdings gates."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unison_snapshot.oge_278e_audit import audit_public_278e
from unison_snapshot.oge_278e_public import PARSER_VERSION, SCHEMA
from unison_snapshot.whitehouse_annual_review import build_annual_review
from unison_snapshot.whitehouse_scanned_annual import apply_scanned_annual_corrections


TRUMP_SHA = "1cc7951c6f72fab008e921903c9a1d03d41a9910239f954e208b501d608553a3"
TRUMP_URL = ("https://www.whitehouse.gov/wp-content/uploads/2026/06/"
             "President-Donald-J.-Trump-2025-Annual-Report.pdf")


def _holding(*, section: str = "part2", page_number: int = 7,
             row_number: str = "1", asset_name: str = "Example Fund",
             raw_description: str | None = None,
             raw_value: str = "$1,001 - $15,000", owner: str = "Self",
             value_low: int = 1001, value_high: int = 15000) -> dict:
    return {
        "section": section,
        "page_number": page_number,
        "row_number": row_number,
        "asset_name": asset_name,
        "owner": owner,
        "raw_columns": {
            "description": asset_name if raw_description is None else raw_description,
            "eif": "No",
            "value": raw_value,
            "income": "None",
        },
        "value_low": value_low,
        "value_high": value_high,
        "report_period_end": "2025-12-31",
        "holding_valuation_date": "2025-12-31",
        "holding_valuation_status": "exact_period_end",
        "ocr_mean_confidence": 96.0,
        "ocr_min_confidence": 90.0,
    }


def _annual(*, holdings: list[dict] | None = None) -> dict:
    rows = holdings if holdings is not None else [_holding()]
    present = {row["section"] for row in rows}
    return {
        "schema_version": SCHEMA,
        "parser_version": PARSER_VERSION,
        "source_url": "https://www.whitehouse.gov/wp-content/uploads/2026/09/example.pdf",
        "source_sha256": "a" * 64,
        "filer_name": "Example, Ada",
        "position_line_raw": "Assistant to the President - White House",
        "report_type": "Annual",
        "cover_report_year": 2025,
        "filing_date": "2026-06-20",
        "signature_text": ("/s/ Example, Ada [electronically signed on 06/20/2026 "
                           "by Example, Ada in Integrity.gov]"),
        "report_period_end": "2025-12-31",
        "holding_valuation_date": "2025-12-31",
        "extraction_method": "tesseract_ocr_geometry",
        "ocr_engine": "tesseract test",
        "section_pages": {"part2": 1, "part5": 1, "part6": 1, "part7": 1},
        "explicit_empty_sections": [part for part in ("part2", "part5", "part6", "part7")
                                    if part not in present],
        "printed_row_count": len(rows),
        "holdings": rows,
        "transactions": [],
        "excluded": [],
        "quarantined": [],
        "document_reasons": [],
        "requires_cross_report_dedup": False,
    }


def _trump_partial(*, page_number: int, owner: str = "Unknown") -> dict:
    row = _holding(section="part6", page_number=page_number, row_number="2.1",
                   asset_name="Trump Fixed-Source Example", owner=owner)
    extraction = _annual(holdings=[row])
    extraction.update({
        "source_url": TRUMP_URL,
        "source_sha256": TRUMP_SHA,
        "filer_name": "Donald Trump",
        "position_line_raw": "President",
        "filing_date": None,
        "signature_text": None,
        # The fixed source has Part 6 on PDF pages 7-158 and Part 7 from 159.
        "section_pages": {"part2": 1, "part5": 1, "part6": 152, "part7": 687},
        "explicit_empty_sections": ["part2", "part5", "part7"],
        "printed_row_count": 2,
        "quarantined": [{
            "section": "part6",
            "page_number": 100,
            "row_number": "999",
            "owner": "Unknown",
            "raw_columns": {"description": "Unreadable Asset", "value": ""},
            "reasons": ["holding_value_unreadable_or_open"],
            "ocr_mean_confidence": 96.0,
            "ocr_min_confidence": 90.0,
        }],
        "document_reasons": ["filer_handwritten_signature_or_date_unverified"],
    })
    return extraction


class WhiteHouseAnnualGateCounterexampleTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def _review_fixture(self, extraction: dict) -> dict:
        url = extraction["source_url"]
        document_id = "wh-url:" + hashlib.sha256(url.encode()).hexdigest()[:24]
        relative = (Path("whitehouse/extractions") / document_id[7:] /
                    extraction["source_sha256"] /
                    f"{PARSER_VERSION.replace('/', '-')}.json")
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(extraction), encoding="utf-8")
        return {
            "schema_version": "whitehouse-public-coverage/v1",
            "source_id": "whitehouse_public_disclosures",
            "report_link_count": 1,
            "reports": [{
                "document_id": document_id,
                "document_url": url,
                "filer_name_from_label": extraction["filer_name"],
                "document_type_from_label": "278e_annual",
                "archive_sha256_versions": [extraction["source_sha256"]],
                "review_state": "extracted_with_issues",
            }],
        }

    def test_open_raw_value_cannot_be_disguised_as_a_closed_statutory_band(self) -> None:
        extraction = _annual(holdings=[_holding(
            raw_value="$50,000,001 and over",
            value_low=25_000_001,
            value_high=50_000_000,
        )])

        audit = audit_public_278e(extraction)

        decision = audit["holding_row_audit"][0]
        self.assertFalse(decision["source_candidate_eligible"])
        self.assertIn("holding_value_evidence_invalid", decision["reasons"])
        self.assertFalse(audit["source_holdings_eligible"])

    def test_valued_parent_and_child_cannot_both_enter_holdings(self) -> None:
        parent = _holding(section="part6", row_number="1",
                          asset_name="Investment Account #1", owner="Unknown")
        child = _holding(section="part6", row_number="1.1",
                         asset_name="Example Fund", owner="Unknown")
        audit = audit_public_278e(_annual(holdings=[parent, child]))
        by_number = {row["row_number"]: row for row in audit["holding_row_audit"]}

        self.assertFalse(by_number["1"]["source_candidate_eligible"])
        self.assertIn("holding_parent_child_double_count", by_number["1"]["reasons"])
        self.assertTrue(by_number["1.1"]["source_candidate_eligible"])
        self.assertFalse(audit["source_holdings_eligible"])

    def test_trump_part6_rows_on_part7_pages_fail_row_and_partial_report_gates(self) -> None:
        for page_number in (159, 845):
            with self.subTest(page_number=page_number):
                extraction = _trump_partial(page_number=page_number)
                corrected = apply_scanned_annual_corrections(extraction)
                audit = audit_public_278e(corrected)
                decision = audit["holding_row_audit"][0]
                self.assertFalse(decision["source_candidate_eligible"])
                self.assertIn("holding_section_page_mismatch", decision["reasons"])
                self.assertFalse(audit["source_holdings_eligible"])

                coverage = self._review_fixture(extraction)
                review = build_annual_review(coverage, self.root,
                                             coverage_sha256="b" * 64)
                self.assertFalse(review["reports"][0]["source_candidate_eligible"])
                self.assertEqual(review["reports"][0]["holding_coverage_status"],
                                 "ineligible")
                self.assertFalse(review["holdings"][0]["source_holdings_eligible"])

    def test_part6_owner_evidence_on_a_part7_page_is_not_traceable(self) -> None:
        extraction = _trump_partial(page_number=158, owner="Joint")
        extraction["holdings"][0]["owner_evidence"] = [{
            "owner": "Joint",
            "basis": "explicit_part6_parent_account",
            "page_number": 159,
            "row_number": "2",
            "text": "Joint Brokerage Account #2",
        }]
        corrected = apply_scanned_annual_corrections(extraction)

        audit = audit_public_278e(corrected)

        decision = audit["holding_row_audit"][0]
        self.assertFalse(decision["source_candidate_eligible"])
        self.assertIn("holding_owner_evidence_invalid", decision["reasons"])
        coverage = self._review_fixture(extraction)
        review = build_annual_review(coverage, self.root, coverage_sha256="b" * 64)
        self.assertFalse(review["reports"][0]["source_candidate_eligible"])


if __name__ == "__main__":
    unittest.main()
