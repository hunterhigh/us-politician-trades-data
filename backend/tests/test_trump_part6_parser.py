"""Adversarial, source-bound geometry cases for Trump's scanned Part 6.

These are deliberately small OCR pages rather than copies of the 927-page
source.  They exercise the row-disposition boundary, not production eligibility.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unison_snapshot.ocr_geometry import OcrPage
from unison_snapshot.oge_278e_public import _extract_page_rows


TRUMP_URL = (
    "https://www.whitehouse.gov/wp-content/uploads/2026/06/"
    "President-Donald-J.-Trump-2025-Annual-Report.pdf"
)
TRUMP_SHA256 = "1cc7951c6f72fab008e921903c9a1d03d41a9910239f954e208b501d608553a3"


def _page(*lines: str | list[tuple[str, float] | tuple[str, float, float]],
          top_overrides: dict[int, float] | None = None) -> OcrPage:
    """Create positioned OCR words with one independent TSV line per argument."""

    words: list[dict] = []
    for line_number, line in enumerate(lines, 1):
        top = (top_overrides or {}).get(line_number, 12.0 * line_number)
        if isinstance(line, str):
            placed = []
            x = 30.0
            for token in line.split():
                placed.append((token, x, 96.0))
                x += 6 * len(token) + 6
        else:
            placed = [(item[0], item[1], item[2] if len(item) == 3 else 96.0)
                      for item in line]
        for token, x, confidence in placed:
            words.append({"text": token, "x0": float(x),
                          "x1": float(x) + 5 * len(token),
                          "top": top,
                          "bottom": top + 9.0,
                          "size": 9.0, "ocr_confidence": float(confidence),
                          "block_num": 1, "par_num": 1,
                          "line_num": line_number})
    return OcrPage(width=684.0, height=792.0, words=words)


def _header(*, damaged: bool = True) -> list[tuple[str, float]]:
    return [("#", 15.8), ("Descriptic" if damaged else "DESCRIPTION", 38.2),
            ("EIF__|" if damaged else "EIF", 452.5), ("Value", 476.6),
            ("Income", 562.7), ("Type", 590), ("Income", 611.3),
            ("Amount", 645)]


def _asset(number: str, name: str, *, band: str = "$1,001 - $15,000",
           value_confidence: float = 96.0,
           income_band: str = "") -> list[tuple[str, float, float]]:
    placed: list[tuple[str, float, float]] = [(number, 16, 96.0),
                                              (name, 40, 96.0),
                                              ("Yes", 452, 96.0)]
    placed += [(token, 480 + offset * 22, value_confidence)
               for offset, token in enumerate(band.split())]
    placed += [(token, 615 + offset * 27, 96.0)
               for offset, token in enumerate(income_band.split())]
    return placed


def _meta(filer_name: str = "Donald J. Trump") -> dict:
    return {"filer_name": filer_name, "report_type": "Annual",
            "report_period_end": "2025-12-31",
            "holding_valuation_date": "2025-12-31"}


def _extract(pages: list[OcrPage], *, source_bound: bool = True,
             other_source: bool = False) -> dict:
    kwargs = {"initial_reasons": []}
    if other_source:
        kwargs.update(source_url="https://www.whitehouse.gov/wp-content/uploads/2026/06/other.pdf",
                      source_sha256="a" * 64)
    elif source_bound:
        kwargs.update(source_url=TRUMP_URL, source_sha256=TRUMP_SHA256)
    return _extract_page_rows(pages, _meta("Ada Example" if other_source else "Donald J. Trump"),
                              **kwargs)


class TrumpPart6ParserTests(unittest.TestCase):
    def test_damaged_header_with_ordered_geometry_recovers_only_value_band(self):
        result = _extract([_page(
            "6. Other Assets and Income", _header(),
            "INVESTMENT ACCOUNT #1",
            _asset("1", "ALPHA", income_band="$5,001 - $15,000"),
            "7. Transactions", "None")])
        self.assertEqual(result["printed_row_count"], 1)
        self.assertEqual(len(result["holdings"]), 1)
        holding = result["holdings"][0]
        self.assertEqual(holding["asset_name"], "ALPHA")
        self.assertEqual((holding["value_low"], holding["value_high"]),
                         (1001, 15000))
        self.assertEqual(holding["owner"], "Unknown")
        self.assertNotIn("table_header_unrecognized", result["document_reasons"])

    def test_income_amount_cannot_fill_missing_value_column(self):
        result = _extract([_page(
            "6. Other Assets and Income", _header(),
            "INVESTMENT ACCOUNT #1",
            _asset("1", "INCOMEONLY", band="", income_band="$5,001 - $15,000"),
            "7. Transactions", "None")])
        self.assertEqual(result["printed_row_count"], 1)
        self.assertEqual(result["holdings"], [])
        self.assertEqual(len(result["quarantined"]), 1)
        self.assertIn("holding_value_unreadable_or_open",
                      result["quarantined"][0]["reasons"])

    def test_fused_eif_value_word_keeps_original_ocr_evidence(self):
        result = _extract([_page(
            "6. Other Assets and Income", _header(),
            "INVESTMENT ACCOUNT #1",
            [("1", 16), ("ALPHA", 40), ("N/A__|$1,001", 452),
             ("-", 505), ("$15,000", 515)],
            "7. Transactions", "None")])
        self.assertEqual(len(result["holdings"]), 1)
        row = result["holdings"][0]
        self.assertEqual(row["raw_columns"]["value"], "$1,001 - $15,000")
        self.assertEqual(row["ocr_word_repairs"][0]["original_text"],
                         "N/A__|$1,001")
        self.assertGreaterEqual(row["ocr_field_confidence"]["value"]["min"], 85)
        self.assertEqual(result["source_row_census_status"], "ocr_detected_rows_only")
        self.assertFalse(result["source_row_census_complete"])

    def test_row_number_one_restart_in_distinct_accounts_is_not_a_duplicate(self):
        result = _extract([_page(
            "6. Other Assets and Income", _header(),
            "INVESTMENT ACCOUNT #1", _asset("1", "ALPHA"),
            "INVESTMENT ACCOUNT #2", _asset("1", "BETA"),
            "7. Transactions", "None")])
        self.assertEqual(result["printed_row_count"], 2)
        self.assertEqual({row["asset_name"] for row in result["holdings"]},
                         {"ALPHA", "BETA"})
        self.assertFalse(any("duplicate_section_row_number" in row["reasons"]
                             for row in result["quarantined"]))

    def test_same_account_ocr_number_collision_keeps_distinct_physical_rows(self):
        result = _extract([_page(
            "6. Other Assets and Income", _header(),
            "INVESTMENT ACCOUNT #1", _asset("1", "ALPHA"),
            _asset("1", "BETA"), "7. Transactions", "None")])
        self.assertEqual(result["printed_row_count"], 2)
        self.assertEqual({row["asset_name"] for row in result["holdings"]},
                         {"ALPHA", "BETA"})
        self.assertEqual({row["row_number"] for row in result["holdings"]}, {"1"})
        self.assertEqual({row["account_scope"] for row in result["holdings"]},
                         {"investment-account-1"})
        self.assertEqual(len({row["source_row_locator"] for row in result["holdings"]}), 2)
        self.assertEqual(result["quarantined"], [])

    def test_same_account_ocr_number_collision_across_pages_is_not_a_duplicate(self):
        result = _extract([
            _page("6. Other Assets and Income", _header(),
                  "INVESTMENT ACCOUNT #1", _asset("1", "ALPHA")),
            _page(_header(), _asset("1", "BETA"),
                  "7. Transactions", "None")])
        self.assertEqual(result["printed_row_count"], 2)
        self.assertEqual({(row["asset_name"], row["page_number"])
                          for row in result["holdings"]},
                         {("ALPHA", 1), ("BETA", 2)})
        self.assertEqual(len({row["source_row_locator"] for row in result["holdings"]}), 2)
        self.assertEqual(result["quarantined"], [])

    def test_same_page_same_y_duplicate_ocr_row_stays_quarantined(self):
        result = _extract([_page(
            "6. Other Assets and Income", _header(),
            "INVESTMENT ACCOUNT #1", _asset("1", "ALPHA"),
            _asset("1", "ALPHA"), "7. Transactions", "None",
            top_overrides={5: 48.0})])
        self.assertEqual(result["printed_row_count"], 2)
        self.assertEqual(result["holdings"], [])
        self.assertEqual(len(result["quarantined"]), 2)
        self.assertEqual(len({row["source_row_locator"] for row in result["quarantined"]}), 1)
        self.assertTrue(all(any("duplicate" in reason for reason in row["reasons"])
                            for row in result["quarantined"]))

    def test_account_context_and_columns_survive_continuation_page(self):
        result = _extract([
            _page("6. Other Assets and Income", _header(),
                  "INVESTMENT ACCOUNT #1", _asset("1", "ALPHA")),
            _page(_header(), _asset("2", "BETA"),
                  "INVESTMENT ACCOUNT #2", _asset("1", "GAMMA"),
                  "7. Transactions", "None")])
        self.assertEqual(result["printed_row_count"], 3)
        self.assertEqual({row["asset_name"] for row in result["holdings"]},
                         {"ALPHA", "BETA", "GAMMA"})

    def test_low_critical_confidence_is_retained_for_qualification_not_parser_veto(self):
        result = _extract([_page(
            "6. Other Assets and Income", _header(),
            "INVESTMENT ACCOUNT #1",
            _asset("1", "UNCERTAIN", value_confidence=40.0),
            _asset("2", "OPENVALUE", band="Over $50,000,000"),
            "7. Transactions", "None")])
        self.assertEqual(result["printed_row_count"], 2)
        self.assertEqual(len(result["holdings"]), 1)
        self.assertEqual(result["holdings"][0]["asset_name"], "UNCERTAIN")
        self.assertIn("holding_ocr_confidence_below_threshold",
                      result["holdings"][0]["parser_recovery"]["original_quarantine_reasons"])
        self.assertEqual(result["holdings"][0]["ocr_field_confidence"]["value"]["min"], 40.0)
        self.assertEqual(len(result["quarantined"]), 1)
        self.assertIn("holding_value_unreadable_or_open",
                      result["quarantined"][0]["reasons"])

    def test_physical_locator_replaces_unreadable_printed_number_only_in_verified_account(self):
        result = _extract([_page(
            "6. Other Assets and Income", _header(),
            "INVESTMENT ACCOUNT #1",
            [("ALPHA", 40), ("Yes", 452), ("$1,001", 480),
             ("-", 502), ("$15,000", 524)],
            "7. Transactions", "None")])
        self.assertEqual(len(result["holdings"]), 1)
        row = result["holdings"][0]
        self.assertEqual(row["source_row_locator"], "p1-y480")
        self.assertIn("row_number_ocr_unreadable",
                      row["parser_recovery"]["original_quarantine_reasons"])

    def test_value_word_crossing_income_boundary_is_not_structured_holding(self):
        result = _extract([_page(
            "6. Other Assets and Income", _header(),
            "INVESTMENT ACCOUNT #1",
            [("1", 16), ("ALPHA", 40), ("Yes", 452),
             ("$1,001", 480), ("-", 510), ("$15,000", 550)],
            "7. Transactions", "None")])
        self.assertEqual(result["holdings"], [])
        self.assertIn("holding_value_geometry_invalid",
                      result["quarantined"][0]["reasons"])

    def test_page_159_part7_boundary_cannot_become_part6_holdings(self):
        # The fixed source's OCR has neither a normal "7. Transactions" title
        # nor the ordinary "# ..." transaction header at this transition.
        # The first trade is still a printed row and must not inherit Part 6.
        pages = [_page() for _ in range(157)]
        pages.append(_page("6. Other Assets and Income", _header(),
                           "INVESTMENT ACCOUNT #1", _asset("1", "ALPHA")))
        # Empty OCR lines place the observed hints around y=25 and the
        # transaction header around y=84, with its first row near y=120.
        pages.append(_page(
            "", "Jn truction_for Part 7", "", "", "Part i", "",
            [("*", 16), ("Description", 38), ("Type", 452),
             ("Date", 520), ("Amount", 610)], "", "",
            [("1", 16), ("ISHARES", 40), ("TRUST", 95),
             ("purchase", 452), ("9/18/2025", 520),
             ("$1,000,001", 615), ("-", 640), ("$5,000,000", 655)]))
        result = _extract(pages)
        self.assertEqual(result["printed_row_count"], 2)
        self.assertEqual([row["asset_name"] for row in result["holdings"]], ["ALPHA"])
        trade_rows = [row for disposition in ("transactions", "quarantined", "excluded")
                      for row in result[disposition] if row["page_number"] == 159]
        self.assertEqual(len(trade_rows), 1)
        self.assertEqual(trade_rows[0]["section"], "part7")

    def test_legacy_unbound_report_does_not_accept_damaged_header(self):
        result = _extract([_page(
            "6. Other Assets and Income", _header(), _asset("1", "ALPHA"),
            "7. Transactions", "None")], source_bound=False)
        self.assertEqual(result["printed_row_count"], 1)
        self.assertEqual(result["holdings"], [])
        self.assertEqual(result["quarantined"][0]["reasons"],
                         ["table_header_unrecognized"])

    def test_other_source_does_not_accept_trump_specific_header_recovery(self):
        result = _extract([_page(
            "6. Other Assets and Income", _header(), _asset("1", "ALPHA"),
            "7. Transactions", "None")], other_source=True)
        self.assertEqual(result["printed_row_count"], 1)
        self.assertEqual(result["holdings"], [])
        self.assertEqual(result["quarantined"][0]["reasons"],
                         ["table_header_unrecognized"])


if __name__ == "__main__":
    unittest.main()
