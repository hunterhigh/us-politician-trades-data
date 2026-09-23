"""Evidence rules for public White House OGE 278-T PDFs."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.whitehouse_278t import (
    _ocr_date, parse_whitehouse_278t_pdf, quarantine_duplicate_report_groups,
)


PDF = b"%PDF-1.7\nexample\n%%EOF"
SHA = hashlib.sha256(PDF).hexdigest()
URL = "https://www.whitehouse.gov/wp-content/uploads/2026/04/example.pdf"
ROW = ["1", "Example Inc. (EXM)", "Purchase", "05/01/2025", "No", "$1,001 - $15,000"]
TEXT = (
    "Periodic Transaction Report (OGE Form 278-T)\n"
    "Filer's Information\nExample, Ada\n"
    "Deputy Counsel to the President, Trump-Vance (2025) - White House\n"
    "Electronic Signature - I certify that this is correct.\n"
    "/s/ Example, Ada [electronically signed on 06/03/2025 by Example, Ada in Integrity.gov]\n"
    "Agency Ethics Official's Opinion\n"
    "/s/ Official, Bea [electronically signed on 06/12/2025 by Official, Bea in Integrity.gov]\n"
    "Transactions\n"
)


class WhiteHouse278TTests(unittest.TestCase):
    def extract(self, text=TEXT, rows=None, **kwargs):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.pdf"
            path.write_bytes(PDF)
            with patch("unison_snapshot.whitehouse_278t._extract_pdf",
                       return_value=(text, [(2, list(ROW))] if rows is None else rows)):
                return parse_whitehouse_278t_pdf(
                    path, source_url=URL, source_sha256=SHA,
                    document_id="whitehouse-report-1", filer_name="Example, Ada", **kwargs)

    def test_filer_electronic_signature_and_transaction_fields(self):
        result = self.extract()
        self.assertEqual(result["filed_at"], "2025-06-03")
        self.assertEqual(result["signature_method"], "electronic")
        self.assertTrue(result["evidence_complete"])
        self.assertEqual(result["pdf_filer_name"], "Example, Ada")
        self.assertEqual(result["pdf_position_title"],
                         "Deputy Counsel to the President, Trump-Vance (2025)")
        self.assertEqual(result["pdf_agency_label"], "White House")
        self.assertIn("06/03/2025", result["filer_signature_evidence"])
        self.assertEqual((result["transactions"][0]["ticker"],
                          result["transactions"][0]["transaction_type"],
                          result["transactions"][0]["transaction_date"],
                          result["transactions"][0]["amount_low"]),
                         ("EXM", "purchase", "2025-05-01", 1001))

    def test_reviewer_date_and_index_date_cannot_fill_missing_filer_date(self):
        text = TEXT.replace(
            "/s/ Example, Ada [electronically signed on 06/03/2025 by Example, Ada in Integrity.gov]",
            "/s/ Example, Ada")
        result = self.extract(text)
        self.assertIsNone(result["filed_at"])
        self.assertFalse(result["evidence_complete"])
        self.assertIn("filer_signature_date_not_unique", result["document_reasons"])

    def test_filer_signature_must_match_index_person(self):
        text = TEXT.replace("/s/ Example, Ada [electronically signed on 06/03/2025 by Example, Ada",
                            "/s/ Other, Ann [electronically signed on 06/03/2025 by Other, Ann")
        result = self.extract(text)
        self.assertEqual(result["filed_at"], "2025-06-03")
        self.assertIn("filer_signature_name_mismatch", result["document_reasons"])
        self.assertFalse(result["evidence_complete"])

    def test_pdf_filer_box_must_match_index_and_signature(self):
        text = TEXT.replace("Filer's Information\nExample, Ada\n",
                            "Filer's Information\nOther, Ann\n")
        result = self.extract(text)
        self.assertEqual(result["pdf_filer_name"], "Other, Ann")
        self.assertIn("pdf_filer_name_mismatch", result["document_reasons"])
        self.assertIn("pdf_filer_signature_name_mismatch", result["document_reasons"])
        self.assertFalse(result["evidence_complete"])

    def test_missing_printed_filer_information_is_not_inferred_from_index(self):
        result = self.extract(TEXT.replace("Filer's Information", "Other Information"))
        self.assertIsNone(result["pdf_filer_name"])
        self.assertIsNone(result["pdf_agency_label"])
        self.assertIn("pdf_filer_information_not_verified", result["document_reasons"])

    def test_handwritten_signature_and_garbled_pdf_text_are_not_inferred(self):
        handwritten = self.extract(
            "Periodic Transaction Report (OGE Form 278-T)\n"
            "Filer's Signature /s/ Example, Ada Date 06/03/2025\nTransactions")
        self.assertEqual(handwritten["signature_method"], "handwritten_unverified")
        self.assertIsNone(handwritten["filed_at"])
        garbled_text = "".join(f"(cid:{n})" for n in range(25))
        with patch("unison_snapshot.whitehouse_278t._extract_ocr_pdf", return_value={
                "text": garbled_text, "records": [], "engine": "tesseract test",
                "page_count": 1, "pdf_filer_name": None,
                "pdf_position_title": None, "pdf_agency_label": None,
                "pdf_position_agency_raw": None,
                "identity_reasons": ["pdf_filer_information_not_verified"],
                "title_verified": False}):
            garbled = self.extract(garbled_text, rows=[])
        self.assertEqual(garbled["signature_method"], "unreadable_pdf_text")
        self.assertIn("transaction_table_not_found", garbled["document_reasons"])

    def test_ocr_date_repairs_only_unambiguous_digit_layout(self):
        self.assertEqual(_ocr_date("11282025"), "11/28/2025")
        self.assertEqual(_ocr_date("1/8/2025"), "01/08/2025")
        self.assertEqual(_ocr_date("21612025"), "2/6/2025")
        self.assertEqual(_ocr_date("112112025"), "1/21/2025")
        self.assertEqual(_ocr_date("111112025"), "111112025")

    def test_source_hash_and_host_are_required(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.pdf"
            path.write_bytes(PDF)
            with self.assertRaisesRegex(OgeCatalogError, "hash"):
                parse_whitehouse_278t_pdf(path, source_url=URL, source_sha256="f" * 64,
                                           document_id="x", filer_name="Example, Ada")
            with self.assertRaisesRegex(OgeCatalogError, "source URL"):
                parse_whitehouse_278t_pdf(
                    path, source_url="https://evil.example/wp-content/uploads/report.pdf",
                    source_sha256=SHA, document_id="x", filer_name="Example, Ada")

    def test_missing_number_and_unresolved_amendment_block_report(self):
        rows = [(2, list(ROW)), (2, ["3", "Another Inc.", "Sale", "05/02/2025",
                                       "No", "$15,001 - $50,000"])]
        result = self.extract(rows=rows, amended_label="Periodic Transaction Report Amendment")
        self.assertIn("transaction_row_sequence_incomplete", result["document_reasons"])
        self.assertIn("amendment_relationship_unresolved", result["document_reasons"])
        self.assertFalse(result["evidence_complete"])

    def test_revised_report_and_future_dated_transaction_are_quarantined(self):
        revised = self.extract(TEXT.replace("Transactions", "Data Revised\nTransactions"))
        self.assertIn("data_revision_relationship_unresolved", revised["document_reasons"])
        future = self.extract(rows=[(2, ["1", "Example Inc. (EXM)", "Purchase",
                                         "06/04/2025", "No", "$1,001 - $15,000"])])
        self.assertEqual(future["transactions"], [])
        self.assertEqual(future["quarantined"][0]["reasons"],
                         ["transaction_after_filer_signature"])

    def test_exact_pdf_and_same_content_duplicates_are_both_blocked(self):
        first = self.extract()
        exact = {**first, "document_id": "different-id"}
        screened = quarantine_duplicate_report_groups([first, exact])
        self.assertTrue(all(not row["evidence_complete"] for row in screened))
        self.assertTrue(all("duplicate_pdf_unresolved" in row["document_reasons"]
                            for row in screened))
        self.assertTrue(first["evidence_complete"], "duplicate screen must not mutate input")
        other_pdf = {**first, "source_sha256": "a" * 64, "document_id": "other-pdf"}
        screened = quarantine_duplicate_report_groups([first, other_pdf])
        self.assertTrue(all("duplicate_report_content_unresolved" in row["document_reasons"]
                            for row in screened))


if __name__ == "__main__":
    unittest.main()
