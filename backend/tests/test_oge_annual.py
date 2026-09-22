"""Qualification boundaries for OGE annual 278e table extraction."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_annual import (
    _audit_part7_numbering, _parse_rows, _range, _recover_part7_lines, _reject_duplicate_page_rows,
    _reject_nested_aggregates, extract_annual_pdf,
)


class _Page:
    def __init__(self, text, rows):
        self.text = text
        self.rows = rows

    def extract_text(self):
        return self.text

    def extract_tables(self):
        return [self.rows]

    def extract_text_lines(self):
        return [{"text": line} for line in self.text.splitlines()]


class _Document:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class OgeAnnualTests(unittest.TestCase):
    def test_decimal_part7_number_fails_closed_without_crashing(self):
        state = {"account": None, "next": {}, "counts": {}, "errors": []}
        rows = [["#", "Description", "Type", "Date", "Amount"],
                ["", "INVESTMENT ACCOUNT #1", "", "", ""],
                ["1.1", "Asset", "purchase", "03/20/2025", "$1,001 - $15,000"]]
        _audit_part7_numbering(state, rows, 1)
        self.assertIn("account_row_number_not_integer",
                      [row["reason"] for row in state["errors"]])

    def test_range_accepts_only_published_bands(self):
        self.assertEqual(_range("$1 ,001 - $15,000"), (1001, 15000))
        self.assertEqual(_range("$1,000,001 to $5,000,000"), (1000001, 5000000))
        for value in ("Over $50,000,000", "$10,000 - $20,000", "$S,001 - $15,000", ""):
            self.assertIsNone(_range(value))

    def test_transactions_never_guess_date_or_amount(self):
        rows = [
            ["#", "Description", "Type", "Date", "Amount"],
            ["1", "ABC Corp", "purchase", "03/20/2025", "$1,001 - $15,000"],
            ["2", "DEF Corp", "sale", "03/20/2025\n04/20/2025", "$1,001 - $15,000"],
            ["3", "GHI Corp", "purchase", "02/30/2025", "$1,001 - $15,000"],
            ["4", "JKL Corp", "purchase", "03/20/2025", "Over $50,000,000"],
            ["", "INVESTMENT ACCOUNT #2", "", "", ""],
        ]
        parsed, quarantined, excluded = _parse_rows("part7", "Unknown", 11, rows, 2025)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["row_number"], "1")
        self.assertEqual(len(quarantined), 3)
        self.assertEqual(excluded, [])
        self.assertIn("transaction_date_unreadable_or_outside_year", quarantined[0]["reasons"])

    def test_holdings_exclude_zero_year_end_and_quarantine_open_value(self):
        rows = [
            ["#", "Description", "EIF", "Value", "Income Type", "Income Amount"],
            ["1", "Stock A", "N/A", "$15,001 - $50,000", "", "None"],
            ["2", "Sold stock B", "N/A", "None (or less than $1,001)", "Capital Gains", "$5,001"],
            ["3", "Company C", "N/A", "Over $50,000,000", "", ""],
            ["4", "***Marked D", "N/A", "$1,001 - $15,000", "", ""],
            ["5", "Value not readily ascertainable asset", "N/A", "$1,001 - $15,000", "", ""],
        ]
        parsed, quarantined, excluded = _parse_rows("part6", "Unknown", 7, rows, 2025)
        self.assertEqual([row["asset_name"] for row in parsed], ["Stock A"])
        self.assertEqual([row["reason"] for row in excluded], ["no_disclosed_year_end_value"])
        self.assertEqual(len(quarantined), 3)
        self.assertIn("asset_footnote_unresolved", quarantined[1]["reasons"])

    def test_line_recovery_requires_every_field_on_same_printed_row(self):
        page = _Page("Part 7: Transactions\n1. SPY ETF purchase 03/20/2025 $1,001 -$15,000\n"
                     "2. QQQ ETF purchase 03/20/2025\n3.", [])
        parsed, quarantined = _recover_part7_lines(page, 11, 2025)
        self.assertEqual([row["row_number"] for row in parsed], ["1"])
        self.assertEqual(len(quarantined), 1)

    def test_nested_and_duplicate_rows_do_not_survive(self):
        parent = {"section": "part2", "page_number": 3, "row_number": "1",
                  "owner": "Self", "asset_name": "Account", "raw_value": "$1,001 - $15,000"}
        child = {"section": "part2", "page_number": 3, "row_number": "1.1",
                 "owner": "Self", "asset_name": "ETF", "raw_value": "$1,001 - $15,000"}
        quarantine = []
        self.assertEqual(_reject_nested_aggregates([parent, child], quarantine, []), [child])
        self.assertIn("nested_aggregate_may_double_count", quarantine[0]["reasons"])
        duplicate = _reject_duplicate_page_rows([child, dict(child)], quarantine)
        self.assertEqual(duplicate, [])
        self.assertEqual(sum("duplicate_page_row_number" in row["reasons"]
                             for row in quarantine), 2)

    def test_numbering_resets_by_account_and_rejects_gap(self):
        state = {"account": None, "next": {}, "counts": {}, "errors": []}
        header = ["#", "Description", "Type", "Date", "Amount"]
        _audit_part7_numbering(state, [header,
            ["", "INVESTMENT ACCOUNT #1", "", "", ""],
            ["1", "A", "purchase", "1/1/2025", "$1,001 - $15,000"],
            ["2", "B", "purchase", "1/2/2025", "$1,001 - $15,000"],
        ], 159)
        _audit_part7_numbering(state, [header,
            ["3", "C", "sale", "1/3/2025", "$1,001 - $15,000"],
            ["", "INVESTMENT ACCOUNT #2", "", "", ""],
            ["1", "D", "purchase", "1/4/2025", "$1,001 - $15,000"],
        ], 160)
        self.assertEqual(state["counts"], {1: 3, 2: 1})
        self.assertEqual(state["errors"], [])
        _audit_part7_numbering(state, [header,
            ["3", "E", "sale", "1/5/2025", "$1,001 - $15,000"],
        ], 161)
        self.assertEqual(state["errors"][-1]["reason"], "account_row_number_nonconsecutive")

    def test_complete_pdf_extracts_but_leaves_production_gate_pending(self):
        cover = ("OGE Form 278e\nReport Type: Annual\nYear (Annual Report only): 2025\n"
                 "Last Name First Name MI Position\nVance JD Vice President\n"
                 "OGE Received 6/29/2026")
        pages = [
            _Page(cover, []),
            _Page("Part 2: Filer's Employment Assets & Income and Retirement Accounts", [
                ["#", "Description", "EIF", "Value", "Income Type", "Income Amount"],
                ["1", "SPY ETF", "Yes", "$100,001 - $250,000", "", ""],
            ]),
            _Page("Part 7: Transactions", [
                ["#", "Description", "Type", "Date", "Amount"],
                ["1", "SPY ETF", "purchase", "03/20/2025", "$1,001 - $15,000"],
            ]),
        ]
        content = b"%PDF-1.7\nfixture\n%%EOF"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.pdf"
            path.write_bytes(content)
            with patch.dict(sys.modules, {"pdfplumber": type("PdfPlumber", (), {
                    "open": staticmethod(lambda _: _Document(pages))})}):
                output = extract_annual_pdf(
                    path, source_url="https://extapps2.oge.gov/sample.pdf",
                    source_sha256=hashlib.sha256(content).hexdigest(), expected_filer="JD Vance")
        self.assertEqual(output["report_period_end"], "2025-12-31")
        self.assertEqual(output["oge_received_on"], "2026-06-29")
        self.assertIsNone(output["filing_date"])
        self.assertEqual(len(output["holdings"]), 1)
        self.assertEqual(len(output["transactions"]), 1)
        self.assertTrue(output["requires_cross_report_dedup"])

    def test_wrong_filer_and_hash_fail_closed(self):
        content = b"%PDF-1.7\nfixture\n%%EOF"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.pdf"
            path.write_bytes(content)
            with self.assertRaises(OgeCatalogError):
                extract_annual_pdf(path, source_url="https://extapps2.oge.gov/sample.pdf",
                                   source_sha256="0" * 64, expected_filer="JD Vance")


if __name__ == "__main__":
    unittest.main()
