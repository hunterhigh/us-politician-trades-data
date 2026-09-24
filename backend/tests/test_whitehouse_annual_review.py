"""Annual review materialization preserves filer attribution and row fates."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_oge_278e_audit import _annual, _trump_v7, _v7_holding
from unison_snapshot.oge_278e_public import TRUMP_2025_PREVIOUS_PARSER_VERSION
from unison_snapshot.whitehouse_annual_review import build_annual_review


TRUMP_SHA = "1cc7951c6f72fab008e921903c9a1d03d41a9910239f954e208b501d608553a3"
TRUMP_URL = ("https://www.whitehouse.gov/wp-content/uploads/2026/06/"
             "President-Donald-J.-Trump-2025-Annual-Report.pdf")


class WhiteHouseAnnualReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def fixture(self, extraction: dict) -> tuple[dict, Path]:
        url = extraction["source_url"]
        document_id = "wh-url:" + hashlib.sha256(url.encode()).hexdigest()[:24]
        relative = (Path("whitehouse/extractions") / document_id[7:] /
                    extraction["source_sha256"] /
                    f"{extraction['parser_version'].replace('/', '-')}.json")
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(extraction), encoding="utf-8")
        coverage = {
            "schema_version": "whitehouse-public-coverage/v1",
            "source_id": "whitehouse_public_disclosures", "report_link_count": 1,
            "reports": [{"document_id": document_id, "document_url": url,
                         "filer_name_from_label": extraction["filer_name"],
                         "document_type_from_label": "278e_annual",
                         "extraction_parser_version": extraction["parser_version"],
                         "archive_sha256_versions": [extraction["source_sha256"]],
                         "review_state": "extracted_review_only"}],
        }
        return coverage, path

    def test_unknown_part6_owner_is_attributed_to_filer_without_claiming_ownership(self) -> None:
        extraction = _annual()
        extraction["explicit_empty_sections"] = ["part2", "part5", "part7"]
        extraction["holdings"][0].update(section="part6", owner="Unknown")
        coverage, _ = self.fixture(extraction)
        result = build_annual_review(coverage, self.root, coverage_sha256="b" * 64)
        self.assertEqual((result["report_count"], result["holding_count"]), (1, 1))
        self.assertEqual(result["source_eligible_holding_count"], 1)
        row = result["holdings"][0]
        self.assertEqual(row["filer_reported_name"], "Example, Ada")
        self.assertEqual(row["asset_owner"], "Unknown")
        self.assertIsNone(row["owner_is_filer"])
        self.assertTrue(row["source_holdings_eligible"])
        self.assertEqual(result["production_status"],
                         "review_only_complete_or_source_bound_partial_rows")

    def test_quarantined_asset_blocks_entire_report_without_losing_parsed_row(self) -> None:
        extraction = _annual()
        extraction["printed_row_count"] = 2
        extraction["quarantined"] = [{"section": "part6", "page_number": 4,
                                     "row_number": "1", "reasons": ["amount_unreadable"]}]
        coverage, _ = self.fixture(extraction)
        result = build_annual_review(coverage, self.root, coverage_sha256="b" * 64)
        self.assertEqual(result["holding_count"], 1)
        self.assertEqual(result["source_eligible_holding_count"], 0)
        self.assertEqual(result["reports"][0]["quarantined_asset_count"], 1)
        self.assertIn("asset_rows_quarantined",
                      result["reports"][0]["holding_blocking_reasons"])
        self.assertFalse(result["reports"][0]["source_candidate_eligible"])

    def test_modified_archive_binding_is_rejected(self) -> None:
        extraction = _annual()
        coverage, path = self.fixture(extraction)
        changed = deepcopy(extraction)
        changed["source_sha256"] = "c" * 64
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not bound"):
            build_annual_review(coverage, self.root, coverage_sha256="b" * 64)

    def test_trump_row_ids_survive_insert_and_reorder_without_changing_old_id(self) -> None:
        def holding(page: int, number: str, name: str, locator: str | None = None) -> dict:
            row = {
                "section": "part2", "page_number": page, "row_number": number,
                "asset_name": name, "owner": "Self",
                "raw_columns": {"description": name, "eif": "N/A",
                                "value": "$1,001 to $15,000"},
                "value_low": 1001, "value_high": 15000,
                "report_period_end": "2025-12-31",
                "holding_valuation_date": "2025-12-31",
                "holding_valuation_status": "exact_period_end",
                "ocr_mean_confidence": 96.0, "ocr_min_confidence": 90.0,
            }
            if locator:
                row["source_row_locator"] = locator
            return row

        old = holding(864, "332", "Existing published asset")
        added = holding(856, "135", "Newly qualified asset", "p856-y5810")

        def extraction(rows: list[dict]) -> dict:
            result = _annual()
            result.update(source_url=TRUMP_URL, source_sha256=TRUMP_SHA,
                          filer_name="Donald Trump", position_line_raw="President",
                          cover_report_year=2025, extraction_method="tesseract_ocr_geometry",
                          ocr_engine="tesseract test", filing_date=None,
                          signature_text=None, holdings=rows,
                          printed_row_count=len(rows),
                          explicit_empty_sections=["part5", "part6", "part7"],
                          document_reasons=["filer_handwritten_signature_or_date_unverified"])
            return result

        coverage, _ = self.fixture(extraction([old]))
        first = build_annual_review(coverage, self.root, coverage_sha256="b" * 64)
        old_id = first["holdings"][0]["row_id"]

        coverage, _ = self.fixture(extraction([added, old]))
        second = build_annual_review(coverage, self.root, coverage_sha256="b" * 64)
        second_ids = {row["row_number"]: row["row_id"] for row in second["holdings"]}
        self.assertEqual(second_ids["332"], old_id)

        coverage, _ = self.fixture(extraction([old, added]))
        third = build_annual_review(coverage, self.root, coverage_sha256="b" * 64)
        third_ids = {row["row_number"]: row["row_id"] for row in third["holdings"]}
        self.assertEqual(third_ids, second_ids)

    def test_trump_v6_keeps_published_row_and_adds_two_part2_recoveries(self) -> None:
        description = (
            "Trump Marks Philippines LLC Location: Century City Makati, Philippines Licensee: "
            "Century Luxury Properties, Inc. Additional Underlying Assets: Registered "
            "Trademark(s) (values not readily ascertainable).* (See Exhibit A). Underlying "
            "Asset: U.S. bank account Location: Jupiter, FL (value represents bank account only)")
        extraction = _annual()
        extraction.update(
            parser_version=TRUMP_2025_PREVIOUS_PARSER_VERSION,
            source_url=TRUMP_URL, source_sha256=TRUMP_SHA,
            filer_name="Donald Trump", position_line_raw="President",
            cover_report_year=2025, extraction_method="tesseract_ocr_geometry",
            ocr_engine="tesseract test", filing_date=None, signature_text=None,
            printed_row_count=3, explicit_empty_sections=["part5", "part6", "part7"],
            document_reasons=["filer_handwritten_signature_or_date_unverified"],
        )
        extraction["holdings"] = [{
            "section": "part2", "page_number": 864, "row_number": "332",
            "asset_name": description, "owner": "Self",
            "raw_columns": {"description": description, "eif": "No",
                            "value": "$1,001 to $15,000"},
            "value_low": 1001, "value_high": 15000,
            "report_period_end": "2025-12-31",
            "holding_valuation_date": "2025-12-31",
            "holding_valuation_status": "exact_period_end",
            "ocr_mean_confidence": 95.06, "ocr_min_confidence": 85.43,
        }]
        extraction["quarantined"] = [
            {
                "section": "part2", "page_number": 856, "row_number": "135",
                "owner": "Self", "owner_evidence": None,
                "raw_columns": {
                    "description": ("DTW Venture LLC Underlying Assets: residential real estate "
                                    "Location: Palm Beach, FL"),
                    "eif": "N/A", "value": "|$5,000,001 to $25,000,000",
                },
                "reasons": ["holding_value_unreadable_or_open"],
                "ocr_mean_confidence": 93.19, "ocr_min_confidence": 81.42,
            },
            {
                "section": "part2", "page_number": 866, "row_number": "374.2",
                "owner": "Self", "owner_evidence": None,
                "raw_columns": {"description": "Receivable from Amazon MGM Studios",
                                "eif": "N/A", "value": "| $250,001 to $500,000"},
                "reasons": ["holding_value_unreadable_or_open"],
                "ocr_mean_confidence": 94.39, "ocr_min_confidence": 80.0,
            },
        ]
        coverage, _ = self.fixture(extraction)
        result = build_annual_review(coverage, self.root, coverage_sha256="b" * 64)
        by_number = {row["row_number"]: row for row in result["holdings"]}
        self.assertEqual(result["source_eligible_holding_count"], 3)
        self.assertEqual(set(by_number), {"332", "135", "374.2"})
        self.assertEqual(by_number["332"]["row_id"],
                         "wh-annual:8b95ffd94c805396cc7aa1d0")
        self.assertTrue(by_number["332"]["source_holdings_eligible"])
        self.assertEqual(by_number["332"]["source_bound_holding_evidence"][
            "source_row_locator"], "p864-y2772")

    def test_v7_materialization_keeps_every_source_bound_row_field(self) -> None:
        extraction = _trump_v7(_v7_holding())
        coverage, _ = self.fixture(extraction)
        result = build_annual_review(coverage, self.root, coverage_sha256="b" * 64)
        row = result["holdings"][0]
        self.assertTrue(row["source_holdings_eligible"])
        self.assertEqual(row["source_row_locator"], "p27-y1753")
        self.assertEqual(row["raw_columns"], extraction["holdings"][0]["raw_columns"])
        self.assertEqual(row["account_scope"], "investment-account-3")
        self.assertEqual(row["account_scope_evidence"]["text"],
                         "INVESTMENT ACCOUNT #3")
        self.assertEqual(row["ocr_field_confidence"],
                         extraction["holdings"][0]["ocr_field_confidence"])
        self.assertEqual(row["source_bound_value_geometry_evidence"],
                         extraction["holdings"][0]["value_geometry_evidence"])
        self.assertEqual(row["source_bound_parser_recovery"],
                         extraction["holdings"][0]["parser_recovery"])


if __name__ == "__main__":
    unittest.main()
