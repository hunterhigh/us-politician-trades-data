import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.house import HouseFilingCandidate, HouseIndexError
from unison_snapshot.house_holdings import (EXTRACTION_SCHEMA, archive_financial_document,
                                            extract_schedule_a_pages,
                                            qualify_financial_report)


SHA = "a" * 64


def word(text, x0, top, size=8):
    return {"text": text, "x0": x0, "top": top, "size": size}


def page(*, bad_value=False):
    words = [
        word("Asset", 25, 100), word("Owner", 282, 100), word("Value", 321, 100),
        word("of", 351, 100), word("Asset", 363, 100),
        word("Example", 25, 130), word("Corp", 72, 130), word("(EXM)", 115, 130),
        word("[ST]", 160, 130), word("SP", 282, 130),
        word("Undetermined" if bad_value else "$1,001", 321, 130),
        *([] if bad_value else [word("-", 354, 130), word("$15,000", 360, 130)]),
        word("Income-only", 25, 170), word("asset", 75, 170), word("[OT]", 120, 170),
        word("None", 321, 170),
        word("*", 25, 205), word("https://fd.house.gov/reference/asset-type-codes.aspx", 31, 205),
    ]
    return {"width": 612, "height": 792, "words": words,
            "text": "Asset Owner Value of Asset"}


def wrapped_page():
    words = [
        word("Asset", 25, 100), word("Owner", 275, 100), word("Value", 314, 100),
        word("Income", 396, 100), word("Retirement", 25, 130), word("plan", 75, 130),
        word("⇒", 110, 130), word("SP", 275, 130), word("$50,001", 314, 130), word("-", 350, 130),
        word("Example", 25, 141), word("Fund", 70, 141), word("(EXM)", 110, 141),
        word("[MF]", 155, 141), word("$100,000", 314, 141),
    ]
    return {"width": 612, "height": 792, "words": words,
            "text": "Asset Owner Value of Asset"}


def extraction(rows):
    return {
        "schema_version": EXTRACTION_SCHEMA, "schedule_a_complete": True,
        "source_sha256": SHA, "report_period_end": "2025-12-31",
        "source": {"source_id": "house_clerk", "source_url":
                   "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2025/10000001.pdf",
                   "document_id": "10000001", "filing_type": "O", "report_type": "Annual Report",
                   "filer_name": "Hon. Ada Example", "state_district": "CA01",
                   "filing_year": 2025, "filed_date": "2026-05-01"},
        "rows": rows,
    }


IDENTITY = {
    "status": "matched_automatically", "document_id": "10000001",
    "person_id": "house:A000001", "official_name": "Ada Example", "state": "CA",
    "state_district": "CA01", "party": "D",
    "evidence_url": "https://bioguide.congress.gov/search/bio/A000001",
    "roster_sha256": "b" * 64,
    "match_basis": "official_roster_exact_district_first_last_name",
}


class HouseHoldingTests(unittest.TestCase):
    def test_geometry_extracts_explicit_value_and_accounts_for_none(self):
        rows, explicit_none = extract_schedule_a_pages([page()], source_sha256=SHA)
        self.assertFalse(explicit_none)
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["ticker"], rows[0]["value_low"], rows[0]["value_high"]),
                         ("EXM", 1001, 15000))
        self.assertEqual(rows[0]["owner"], "Spouse")
        self.assertTrue(rows[1]["excluded_no_reportable_value"])

    def test_qualification_emits_canonical_holding_without_inference(self):
        rows, _ = extract_schedule_a_pages([page()], source_sha256=SHA)
        result = qualify_financial_report(extraction(rows), IDENTITY)
        self.assertTrue(result["production_eligible"])
        self.assertEqual(len(result["holdings"]), 1)
        holding = result["holdings"][0]
        self.assertEqual(holding["report_period_end"], "2025-12-31")
        self.assertEqual(holding["ticker_mapping_basis"], "filing_explicit")
        self.assertEqual(len(result["excluded"]), 1)

    def test_wrapped_asset_and_value_are_one_row(self):
        rows, _ = extract_schedule_a_pages([wrapped_page()], source_sha256=SHA)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["asset_name"], "Retirement plan ⇒ Example Fund")
        self.assertEqual(rows[0]["ticker"], "EXM")
        self.assertEqual((rows[0]["value_low"], rows[0]["value_high"]), (50001, 100000))

    def test_one_unsupported_row_quarantines_the_whole_report(self):
        rows, _ = extract_schedule_a_pages([page(bad_value=True)], source_sha256=SHA)
        result = qualify_financial_report(extraction(rows), IDENTITY)
        self.assertFalse(result["production_eligible"])
        self.assertEqual(result["holdings"], [])
        self.assertIn("value_not_representable", result["qualification_reasons"])

    def test_archive_only_accepts_index_confirmed_annual_report(self):
        content = b"%PDF-1.7\nfixture\n%%EOF\n"
        annual = HouseFilingCandidate("house_clerk", "10000001", "O", "Ada Example", "CA01",
                                      2025, "2026-05-01",
                                      "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2025/10000001.pdf",
                                      "official_raw_unparsed")
        ptr = HouseFilingCandidate(**{**annual.__dict__, "filing_type": "P",
            "document_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/10000001.pdf"})
        with tempfile.TemporaryDirectory() as temporary:
            meta = archive_financial_document(Path(temporary), annual, content, {},
                                              retrieved_at="2026-09-21T00:00:00Z")
            self.assertEqual(meta["report_type"], "Annual Report")
            self.assertEqual(hashlib.sha256(content).hexdigest(), meta["sha256"])
            with self.assertRaises(HouseIndexError):
                archive_financial_document(Path(temporary), ptr, content, {})


if __name__ == "__main__":
    unittest.main()
