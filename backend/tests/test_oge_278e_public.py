"""Fail-closed extraction of public White House 278e cover and positioned rows."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_278e_public import (_assign_part6_owners, _cover, _extract_page_rows,
                                              _ocr_cover, _parse_row, _raw_text_columns,
                                              OcrCheckpointPending,
                                              extract_public_278e_pdf,
                                              extract_public_278e_pdf_checkpointed)
from unison_snapshot.ocr_geometry import OcrPage


def _cover_text(report: str, year: str = "", date_label: str = "Date of Appointment",
                date_value: str = "01/22/2025", signature_date: str = "06/20/2026") -> str:
    return ("OGE Form 278e\nPublic Financial Disclosure Report\n"
            f"Report Type: {report} Report\nYear (Annual Report only): {year}\n"
            f"{date_label}: {date_value}\nFiler's Information\n"
            "Example, Ada\nAssistant to the President - White House\n"
            "/s/ Example, Ada [electronically signed on " + signature_date +
            " by Example, Ada in Integrity.gov]")


class _Page:
    def __init__(self, lines: list[str]):
        self.lines = lines

    def extract_text(self):
        return "\n".join(self.lines)

    def extract_text_lines(self):
        return [{"text": line, "top": i * 10.0} for i, line in enumerate(self.lines)]

    def extract_words(self):
        words = []
        for i, line in enumerate(self.lines):
            if line.startswith("# DESCRIPTION EIF"):
                tokens = [("#", 35), ("DESCRIPTION", 78), ("EIF", 383),
                          ("VALUE", 469), ("INCOME", 556)]
            elif line.startswith("# DESCRIPTION TYPE"):
                tokens = [("#", 35), ("DESCRIPTION", 78), ("TYPE", 383),
                          ("DATE", 469), ("AMOUNT", 556)]
            elif line.startswith("1 SPY ETF Yes"):
                tokens = [("1", 35), ("SPY", 78), ("ETF", 100), ("Yes", 383),
                          ("$1,001", 469), ("-", 505), ("$15,000", 510)]
            elif line.startswith("2 QQQ ETF Yes"):
                tokens = [("2", 35), ("QQQ", 78), ("ETF", 100), ("Yes", 383),
                          ("$15,001", 469), ("-", 505), ("$50,000", 510)]
            elif line.startswith("1 SPY ETF Purchase"):
                tokens = [("1", 35), ("SPY", 78), ("ETF", 100), ("Purchase", 383),
                          ("03/20/2025", 469), ("$1,001", 556), ("-", 591),
                          ("$15,000", 602)]
            elif line.startswith("2 QQQ ETF Sale"):
                tokens = [("2", 35), ("QQQ", 78), ("ETF", 100), ("Sale", 383),
                          ("-", 469), ("$1,001", 556), ("-", 591),
                          ("$15,000", 602)]
            else:
                tokens = []
            words.extend({"text": text, "x0": x, "top": i * 10.0}
                         for text, x in tokens)
        return words


class _Document:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Public278eTests(unittest.TestCase):
    @staticmethod
    def ocr_page(lines):
        words = []
        for line_number, line in enumerate(lines, start=1):
            x = 30.0
            for token in line.split():
                words.append({"text": token, "x0": x, "x1": x + len(token) * 5,
                              "top": line_number * 10.0,
                              "bottom": line_number * 10.0 + 8, "size": 8.0,
                              "ocr_confidence": 96.0, "block_num": 1,
                              "par_num": 1, "line_num": line_number})
                x += len(token) * 5 + 4
        return OcrPage(width=612.0, height=792.0, words=words)

    def test_cover_separates_report_year_from_reporting_period(self):
        annual = _cover(_cover_text("Annual", "2026"), "Ada Example")
        self.assertEqual(annual["report_period_end"], "2025-12-31")
        self.assertEqual(annual["holding_valuation_date"], "2025-12-31")
        self.assertEqual(annual["filing_date"], "2026-06-20")
        self.assertEqual(annual["position_title_raw"], "Assistant to the President")
        self.assertEqual(annual["agency_office_raw"], "White House")
        entrant = _cover(_cover_text("New Entrant", date_value="01/22/2025",
                                    signature_date="05/13/2025"), "Ada Example")
        self.assertEqual(entrant["appointment_date"], "2025-01-22")
        self.assertIsNone(entrant["holding_valuation_date"])
        term = _cover(_cover_text("Termination", date_label="Date of Termination",
                                 date_value="01/02/2026", signature_date="12/18/2025"),
                      "Ada Example")
        self.assertEqual(term["termination_date"], "2026-01-02")
        self.assertIsNone(term["holding_valuation_date"])

    def test_signature_identity_is_required(self):
        with self.assertRaises(OgeCatalogError):
            _cover(_cover_text("Annual", "2026").replace("by Example, Ada", "by Someone Else"),
                   "Ada Example")

    def test_scanned_cover_preserves_identity_but_not_handwritten_filing_date(self):
        page = _Page([
            "OGE Form 278e (Updated 08/2024)",
            "Report Type: Annual",
            "Year (Annual Report only): 2025",
            "Executive Branch Personnel Public Financial Disclosure Report (OGE Form 278e)",
            "Filer’s Information",
            "Last Name First Name MI Position Agency",
            "Vance JD",
            "Vice President of the United States",
            "Other Federal Government Positions Held During the Preceding 12 Months:",
            "Filer’s Certification",
            "Signature: unreadable Date: unreadable",
            "OGE Received 6/29/2026",
        ])
        meta, reasons = _ocr_cover(page, "Vice President JD Vance")
        self.assertEqual(meta["filer_name"], "JD Vance")
        self.assertEqual(meta["position_line_raw"], "Vice President of the United States")
        self.assertEqual(meta["cover_report_year"], 2025)
        self.assertEqual(meta["report_period_end"], "2025-12-31")
        self.assertEqual(meta["holding_valuation_date"], "2025-12-31")
        self.assertIsNone(meta["filing_date"])
        self.assertEqual(reasons, ["filer_handwritten_signature_or_date_unverified"])

    def test_part6_owner_requires_explicit_parent_or_exact_endnote(self):
        def row(number, description, value=None):
            return {"section": "part6", "page_number": 4, "row_number": number,
                    "owner": "Unknown", "description": description.split(), "eif": ["N/A"],
                    "value": value.split() if value else []}
        rows = [row("1", "Child Brokerage 1"),
                row("1.1", "SPY ETF", "$1,001 - $15,000"),
                row("2", "Joint Brokerage Account #2"),
                row("2.1", "QQQ ETF", "$1,001 - $15,000"),
                row("3", "Brokerage Account #3"),
                row("3.1", "Cash", "$1,001 - $15,000"),
                row("4", "xAI See Endnote", "Over $1,000,000")]
        pages = [_Page(["Endnotes", "6. 4 Spousal asset. Recused from this asset.",
                        "Summary of Contents"])]
        _assign_part6_owners(rows, pages)
        self.assertEqual(rows[1]["owner"], "Dependent Child")
        self.assertEqual(rows[1]["owner_evidence"][0]["row_number"], "1")
        self.assertEqual(rows[3]["owner"], "Joint")
        self.assertEqual(rows[5]["owner"], "Unknown")
        self.assertEqual(rows[6]["owner"], "Spouse")
        self.assertEqual(rows[6]["owner_evidence"][0]["basis"], "explicit_part6_endnote")

    def test_conflicting_part6_owner_evidence_is_quarantined(self):
        parent = {"section": "part6", "page_number": 4, "row_number": "2",
                  "owner": "Unknown", "description": "Joint Brokerage Account #2".split(),
                  "eif": ["No"], "value": []}
        child = {"section": "part6", "page_number": 4, "row_number": "2.1",
                 "owner": "Unknown", "description": ["QQQ", "ETF"], "eif": ["Yes"],
                 "value": ["$1,001", "-", "$15,000"]}
        _assign_part6_owners([parent, child], [_Page([
            "Endnotes", "6. 2.1 Spousal asset. Conflicting note.", "Summary of Contents"])])
        self.assertEqual(child["owner"], "Unknown")
        destination, result = _parse_row(child, _cover(_cover_text("Annual", "2026"),
                                                      "Ada Example"), None)
        self.assertEqual(destination, "quarantined")
        self.assertIn("owner_evidence_conflicts", result["reasons"])

    def test_valued_parent_container_cannot_double_count_children(self):
        meta = _cover(_cover_text("Annual", "2026"), "Ada Example")
        row = {"section": "part2", "page_number": 3, "row_number": "1",
               "owner": "Self", "description": ["IRA"], "eif": ["No"],
               "value": ["$1,001", "-", "$15,000"]}
        destination, result = _parse_row(row, meta, "1")
        self.assertEqual(destination, "quarantined")
        self.assertIn("nested_aggregate_may_double_count", result["reasons"])

    def test_low_confidence_ocr_row_is_quarantined(self):
        meta = _cover(_cover_text("Annual", "2026"), "Ada Example")
        row = {"section": "part2", "page_number": 3, "row_number": "1",
               "owner": "Self", "description": ["SPY", "ETF"], "eif": ["Yes"],
               "value": ["$1,001", "-", "$15,000"],
               "_ocr_confidences": [96.0, 55.0]}
        destination, result = _parse_row(row, meta, None)
        self.assertEqual(destination, "quarantined")
        self.assertIn("holding_ocr_confidence_below_threshold", result["reasons"])
        self.assertEqual(result["ocr_min_confidence"], 55.0)

    def test_duplicate_row_raw_columns_exclude_private_ocr_arrays(self):
        row = {"description": ["SPY", "ETF"], "value": ["$1,001", "-", "$15,000"],
               "_ocr_confidences": [96.0, 55.0],
               "_row_reasons": ["row_number_ocr_unreadable"]}
        self.assertEqual(_raw_text_columns(row), {
            "description": "SPY ETF", "value": "$1,001 - $15,000"})

    def test_duplicate_ocr_rows_keep_confidence_audit_private(self):
        page = _Page([
            "2. Filer's Employment Assets & Income and Retirement Accounts",
            "# DESCRIPTION EIF VALUE INCOME TYPE INCOME AMOUNT",
            "1 SPY ETF Yes $1,001 - $15,000",
            "1 SPY ETF Yes $1,001 - $15,000",
            "5. Spouse's Employment Assets & Income and Retirement Accounts", "None",
            "6. Other Assets and Income", "None", "7. Transactions", "None",
        ])
        words = page.extract_words()
        for word in words:
            word["ocr_confidence"] = 96.0
        page.extract_words = lambda: words
        result = _extract_page_rows(
            [page], _cover(_cover_text("Annual", "2026"), "Ada Example"),
            initial_reasons=[])
        self.assertEqual(result["printed_row_count"], 2)
        self.assertEqual(len(result["holdings"]), 0)
        self.assertEqual(len(result["quarantined"]), 2)
        self.assertTrue(all(row["reasons"] == ["duplicate_section_row_number"]
                            for row in result["quarantined"]))
        self.assertTrue(all("_ocr_confidences" not in row["raw_columns"]
                            for row in result["quarantined"]))

    def test_large_scanned_report_requires_checkpointed_ocr(self):
        pages = [_Page([""]) for _ in range(101)]
        content = b"%PDF-1.7\nfixture\n%%EOF"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.pdf"
            path.write_bytes(content)
            with patch.dict(sys.modules, {"pdfplumber": type("PDFPlumber", (), {
                    "open": staticmethod(lambda _: _Document(pages))})}):
                with self.assertRaisesRegex(OgeCatalogError, "checkpointed OCR"):
                    extract_public_278e_pdf(
                        path,
                        source_url="https://www.whitehouse.gov/wp-content/uploads/2026/09/report.pdf",
                        source_sha256=hashlib.sha256(content).hexdigest(),
                        expected_filer="Ada Example")

    def test_large_ocr_report_resumes_immutable_page_shards(self):
        pages = [_Page([""]) for _ in range(101)]
        content = b"%PDF-1.7\nfixture\n%%EOF"
        cover = self.ocr_page([
            "OGE Form 278e (Updated 08/2024)", "Report Type: Annual",
            "Year (Annual Report only): 2025",
            "Executive Branch Personnel Public Financial Disclosure Report",
            "Filer's Information", "Example Ada",
            "Assistant to the President", "Other Federal Government Positions",
        ])
        blank = self.ocr_page([])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "report.pdf"
            path.write_bytes(content)
            def ocr(_document, *, page_numbers, **_kwargs):
                return [cover if number == 1 else blank for number in page_numbers], "tesseract test"
            with patch.dict(sys.modules, {"pdfplumber": type("PDFPlumber", (), {
                    "open": staticmethod(lambda _: _Document(pages))})}), patch(
                    "unison_snapshot.oge_278e_public.ocr_pdf_pages", side_effect=ocr):
                with self.assertRaises(OcrCheckpointPending) as pending:
                    extract_public_278e_pdf_checkpointed(
                        path,
                        source_url="https://www.whitehouse.gov/wp-content/uploads/2026/09/report.pdf",
                        source_sha256=hashlib.sha256(content).hexdigest(),
                        expected_filer="Ada Example", checkpoint_root=root / "checkpoint",
                        page_limit=100)
                self.assertEqual(pending.exception.status["completed_page_count"], 100)
                result = extract_public_278e_pdf_checkpointed(
                    path,
                    source_url="https://www.whitehouse.gov/wp-content/uploads/2026/09/report.pdf",
                    source_sha256=hashlib.sha256(content).hexdigest(),
                    expected_filer="Ada Example", checkpoint_root=root / "checkpoint",
                    page_limit=100)
            self.assertEqual(result["page_count"], 101)
            self.assertEqual(result["extraction_method"],
                             "tesseract_ocr_geometry_checkpointed")
            self.assertEqual(result["ocr_checkpoint"]["pending_page_count"], 0)
            self.assertEqual(len(list((root / "checkpoint").glob("pages-*.json"))), 5)

    def test_unknown_table_header_leaves_numbered_row_visible(self):
        pages = [_Page(_cover_text("Annual", "2026").splitlines()),
                 _Page(["2. Filer's Employment Assets & Income and Retirement Accounts",
                        "BROKEN COLUMN HEADER", "1 SPY ETF Yes $1,001 - $15,000",
                        "5. Spouse's Employment Assets & Income and Retirement Accounts", "None",
                        "6. Other Assets and Income", "None",
                        "7. Transactions", "None"])]
        content = b"%PDF-1.7\nfixture\n%%EOF"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.pdf"
            path.write_bytes(content)
            with patch.dict(sys.modules, {"pdfplumber": type("PDFPlumber", (), {
                    "open": staticmethod(lambda _: _Document(pages))})}):
                result = extract_public_278e_pdf(
                    path, source_url="https://www.whitehouse.gov/wp-content/uploads/2026/09/report.pdf",
                    source_sha256=hashlib.sha256(content).hexdigest(), expected_filer="Ada Example")
        self.assertEqual(result["printed_row_count"], 1)
        self.assertEqual(result["quarantined"][0]["reasons"], ["table_header_unrecognized"])
        self.assertIn("table_header_unrecognized", result["document_reasons"])

    def test_pdf_rows_reconcile_and_part7_is_never_promoted(self):
        pages = [_Page(_cover_text("Annual", "2026").splitlines()),
                 _Page(["2. Filer's Employment Assets & Income and Retirement Accounts",
                        "# DESCRIPTION EIF VALUE INCOME TYPE INCOME AMOUNT",
                        "1 SPY ETF Yes $1,001 - $15,000",
                        "7. Transactions", "# DESCRIPTION TYPE DATE AMOUNT",
                        "1 SPY ETF Purchase 03/20/2025 $1,001 - $15,000",
                        "2 QQQ ETF Sale - $1,001 - $15,000"])]
        content = b"%PDF-1.7\nfixture\n%%EOF"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.pdf"
            path.write_bytes(content)
            with patch.dict(sys.modules, {"pdfplumber": type("PDFPlumber", (), {
                    "open": staticmethod(lambda _: _Document(pages))})}):
                result = extract_public_278e_pdf(
                    path, source_url="https://www.whitehouse.gov/wp-content/uploads/2026/09/report.pdf",
                    source_sha256=hashlib.sha256(content).hexdigest(), expected_filer="Ada Example")
        self.assertEqual(result["printed_row_count"], 3)
        self.assertEqual(len(result["holdings"]), 1)
        self.assertEqual(result["holdings"][0]["owner"], "Self")
        self.assertEqual(len(result["transactions"]), 1)
        self.assertEqual(result["transactions"][0]["transaction_date"], "2025-03-20")
        self.assertEqual(result["quarantined"][0]["reasons"],
                         ["transaction_date_unreadable_or_outside_period"])
        self.assertTrue(result["requires_cross_report_dedup"])
        self.assertEqual(result["production_qualification"],
                         "pending_identity_amendments_part7_dedup_and_quarantine")

    def test_asset_table_continues_across_page_without_repeated_section_heading(self):
        pages = [
            _Page(["2. Filer's Employment Assets & Income and Retirement Accounts",
                   "# DESCRIPTION EIF VALUE INCOME TYPE INCOME AMOUNT",
                   "1 SPY ETF Yes $1,001 - $15,000"]),
            _Page(["# DESCRIPTION EIF VALUE INCOME TYPE INCOME AMOUNT",
                   "2 QQQ ETF Yes $15,001 - $50,000",
                   "5. Spouse's Employment Assets & Income and Retirement Accounts", "None",
                   "6. Other Assets and Income", "None",
                   "7. Transactions", "None"]),
        ]
        result = _extract_page_rows(
            pages, _cover(_cover_text("Annual", "2026"), "Ada Example"),
            initial_reasons=[])
        self.assertEqual(result["printed_row_count"], 2)
        self.assertEqual([row["asset_name"] for row in result["holdings"]],
                         ["SPY ETF", "QQQ ETF"])
        self.assertEqual(result["document_reasons"], [])


if __name__ == "__main__":
    unittest.main()
