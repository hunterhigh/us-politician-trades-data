"""Source-bound counterexamples for Trump's scanned Part 7 transactions."""
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
                          "top": top, "bottom": top + 9.0, "size": 9.0,
                          "ocr_confidence": float(confidence),
                          "block_num": 1, "par_num": 1,
                          "line_num": line_number})
    return OcrPage(width=792.0, height=612.0, words=words)


def _header(marker: str = "*") -> list[tuple[str, float]]:
    return [(marker, 3.2), ("Description", 37.4), ("Type", 612.4),
            ("Date", 660.2), ("Amount", 706.3)]


def _trade(number: str, asset: str, *, kind: str = "Purchase",
           when: str = "9/18/2025", band: str = "$1,001 - $15,000",
           confidence: float = 96.0) -> list[tuple[str, float, float]]:
    words = [(number, 3.6, confidence), (asset, 37.8, confidence),
             (kind, 610.9, confidence), (when, 680.0, confidence),
             ("|", 703.8, confidence)]
    words.extend((token, 706.3 + offset * 25, confidence)
                 for offset, token in enumerate(band.split()))
    return words


def _meta() -> dict:
    return {"filer_name": "Donald Trump", "report_type": "Annual",
            "report_period_end": "2025-12-31",
            "holding_valuation_date": "2025-12-31",
            "filing_date": None, "termination_date": None}


def _extract(pages: list[OcrPage], *, other_source: bool = False,
             enabled: bool = True) -> dict:
    source_url = ("https://www.whitehouse.gov/wp-content/uploads/2026/06/other.pdf"
                  if other_source else TRUMP_URL)
    source_sha256 = "a" * 64 if other_source else TRUMP_SHA256
    return _extract_page_rows(pages, _meta(), initial_reasons=[],
                              source_url=source_url, source_sha256=source_sha256,
                              enable_trump_part7_recovery=enabled)


def _transaction_page(*rows, marker: str = "*") -> OcrPage:
    return _page("Instruction for Part 7", "Part i", _header(marker),
                 "INVESTMENT ACCOUNT #1", *rows)


class TrumpPart7ParserTests(unittest.TestCase):
    def test_fixed_header_recovers_transaction_not_holding(self) -> None:
        result = _extract([_transaction_page(_trade("1", "ALPHA"))])
        self.assertEqual(result["holdings"], [])
        self.assertEqual(len(result["transactions"]), 1)
        row = result["transactions"][0]
        self.assertEqual(row["asset_name"], "ALPHA")
        self.assertEqual(row["owner"], "Unknown")
        self.assertEqual((row["amount_low"], row["amount_high"]), (1001, 15000))
        self.assertEqual(row["source_row_locator"], "p1-y600")
        self.assertEqual(row["account_scope"], "investment-account-1")
        self.assertEqual(row["raw_columns"]["amount"], "$1,001 - $15,000")
        evidence = row["transaction_geometry_evidence"]
        self.assertEqual(evidence["column_bounds"]["amount_start"], 706.3)
        border = [word for word in evidence["words"]
                  if word["repair_method"] == "drop_printed_amount_border"]
        self.assertEqual(border[0]["original_text"], "|")

    def test_star_and_equals_headers_are_both_source_bound(self) -> None:
        result = _extract([
            _transaction_page(_trade("1", "ALPHA"), marker="*"),
            _transaction_page(_trade("1", "ALPHA", kind="Sale"), marker="="),
        ])
        self.assertEqual(len(result["transactions"]), 2)
        self.assertEqual([row["transaction_type"] for row in result["transactions"]],
                         ["purchase", "sale"])
        self.assertEqual(len({row["source_row_locator"]
                              for row in result["transactions"]}), 2)

    def test_same_asset_multiple_trades_are_not_text_deduplicated(self) -> None:
        result = _extract([_transaction_page(
            _trade("7", "ALPHA", kind="Purchase", when="9/18/2025"),
            _trade("7", "ALPHA", kind="Sale", when="11/4/2025"),
        )])
        self.assertEqual(len(result["transactions"]), 2)
        self.assertEqual({row["transaction_type"] for row in result["transactions"]},
                         {"purchase", "sale"})
        self.assertEqual(len({row["source_row_locator"]
                              for row in result["transactions"]}), 2)

    def test_duplicate_same_physical_row_is_quarantined_twice(self) -> None:
        page = _transaction_page(
            _trade("7", "ALPHA"), _trade("7", "ALPHA"))
        # Two OCR lines at the same physical y overlap horizontally, so they
        # must remain two observations sharing one locator, not be merged.
        for word in page._words:
            if word["line_num"] == 6:
                word["top"] = word["bottom"] = 60.0
        result = _extract([page])
        self.assertEqual(result["transactions"], [])
        duplicates = [row for row in result["quarantined"]
                      if "duplicate_section_row_number" in row["reasons"]]
        self.assertEqual(len(duplicates), 2)
        self.assertEqual({row["source_row_locator"] for row in duplicates},
                         {"p1-y600"})

    def test_split_type_date_and_amount_rejoin_with_audit_repairs(self) -> None:
        page = _transaction_page(
            [("9", 3.6), ("ALPHA", 37.8)],
            [("Pur", 610.9), ("9/18/", 680.0), ("$1,001", 706.3)],
            [("chase", 610.9), ("2025", 680.0), ("-", 706.3)],
            [("$15,000", 706.3)],
        )
        result = _extract([page])
        self.assertEqual(len(result["transactions"]), 1)
        row = result["transactions"][0]
        self.assertEqual(row["transaction_type"], "purchase")
        self.assertEqual(row["transaction_date"], "2025-09-18")
        self.assertEqual((row["amount_low"], row["amount_high"]), (1001, 15000))
        methods = {repair["method"] for repair in
                   row["parser_recovery"]["field_repairs"]}
        self.assertEqual(methods, {"join_split_type_tokens", "join_split_date_tokens"})
        self.assertGreater(len(row["transaction_geometry_evidence"]["words"]), 4)

    def test_disjoint_same_y_ocr_fragments_merge_into_one_physical_row(self) -> None:
        page = _page(
            "Instruction for Part 7", "Part i", _header(),
            "INVESTMENT ACCOUNT #1",
            [("9", 3.6), ("ALPHA", 37.8)],
            [("Purchase", 610.9)],
            [("1/1/2025", 660.2)],
            [("$1,001", 706.3), ("-", 750.0), ("$15,000", 765.0)],
            top_overrides={5: 60.0, 6: 60.0, 7: 60.0, 8: 60.0},
        )
        result = _extract([page])
        self.assertEqual(len(result["transactions"]), 1)
        row = result["transactions"][0]
        self.assertEqual(row["source_row_locator"], "p1-y600")
        self.assertEqual(row["asset_name"], "ALPHA")
        self.assertEqual(row["transaction_type"], "purchase")
        self.assertEqual((row["amount_low"], row["amount_high"]), (1001, 15000))

    def test_unreadable_row_number_stays_separate_and_quarantined(self) -> None:
        result = _extract([_transaction_page(
            _trade("1", "ALPHA"), _trade("ot", "BETA"))])
        self.assertEqual([row["asset_name"] for row in result["transactions"]], ["ALPHA"])
        beta = next(row for row in result["quarantined"]
                    if row["raw_columns"].get("description") == "BETA")
        self.assertIn("row_number_ocr_unreadable", beta["reasons"])
        self.assertEqual(beta["source_row_locator"], "p1-y720")

    def test_invalid_transaction_amount_never_becomes_holding_value(self) -> None:
        result = _extract([_transaction_page(
            _trade("1", "ALPHA", band="$1,001 - $20,000"))])
        self.assertEqual(result["holdings"], [])
        self.assertEqual(result["transactions"], [])
        self.assertIn("transaction_amount_unreadable_or_open",
                      result["quarantined"][0]["reasons"])

    def test_ocr_period_thousands_separator_is_repaired_only_in_amount_column(self) -> None:
        result = _extract([_transaction_page(
            _trade("1", "ALPHA", band="$250.001 - $500.000"))])
        row = result["transactions"][0]
        self.assertEqual((row["amount_low"], row["amount_high"]), (250001, 500000))
        self.assertEqual(row["raw_columns"]["amount"], "$250.001 - $500.000")
        self.assertEqual(row["parser_recovery"]["field_repairs"][0]["method"],
                         "normalize_thousands_separator")

    def test_other_source_does_not_accept_damaged_header(self) -> None:
        page = _page("7. Transactions", _header("*"), _trade("1", "ALPHA"))
        result = _extract([page], other_source=True)
        self.assertEqual(result["transactions"], [])
        self.assertEqual(result["quarantined"][0]["reasons"],
                         ["table_header_unrecognized"])

    def test_default_v7_entrypoint_remains_inert_until_integration_review(self) -> None:
        result = _extract([_transaction_page(_trade("1", "ALPHA"))], enabled=False)
        self.assertEqual(result["transactions"], [])
        self.assertEqual(result["quarantined"][0]["reasons"],
                         ["table_header_unrecognized"])


if __name__ == "__main__":
    unittest.main()
