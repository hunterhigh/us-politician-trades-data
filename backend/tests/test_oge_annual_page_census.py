"""Offline crosswalk of independently inspected Vance annual PDF pages 10/11."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.oge_annual import _parse_rows


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class OgeAnnualPageCensusTests(unittest.TestCase):
    def test_opt_in_v2_accounts_for_fixed_report_scope_without_promotion(self) -> None:
        extraction = json.loads((FIXTURES / "oge_annual_vance_2026_v2.json").read_text(
            encoding="utf-8"))
        census = json.loads((FIXTURES / "oge_annual_vance_2026_full_census.json").read_text(
            encoding="utf-8"))
        self.assertEqual(extraction["parser_version"], "oge-278e-tables/v2")
        self.assertEqual(extraction["source_sha256"], census["oge_source_sha256"])
        self.assertEqual(extraction["page_count"], len(census["pages"]))
        detected = Counter(row["page_number"] for collection in
                           ("holdings", "transactions", "quarantined", "excluded")
                           for row in extraction[collection])
        for page in census["pages"]:
            self.assertEqual(detected[page["page"]],
                             page["filled_rows"] if page["adapter_scope"] else 0,
                             f"page {page['page']}")
        self.assertEqual(sum(detected.values()), census["adapter_scope_filled_rows"])
        self.assertEqual(census["total_filled_rows"],
                         census["adapter_scope_filled_rows"] +
                         census["outside_adapter_scope_filled_rows"])
        self.assertFalse(extraction["part7_numbering"]["row_reconciliation_complete"])
        self.assertIsNone(extraction["filing_date"])
        self.assertTrue(extraction["requires_cross_report_dedup"])
        self.assertEqual(extraction["production_qualification"],
                         "blocked_by_part7_numbering_or_reconciliation")

    def test_merged_header_preserves_each_populated_page5_row_in_quarantine(self) -> None:
        source = json.loads((FIXTURES / "oge_annual_vance_2026_page5_table.json").read_text(
            encoding="utf-8"))
        self.assertEqual(source["page_number"], 5)
        old_parsed, old_quarantined, old_excluded = _parse_rows(
            "part2", "Self", 5, source["table"], 2025)
        self.assertEqual((len(old_parsed), len(old_quarantined), len(old_excluded)),
                         (0, 1, 0))
        parsed, quarantined, excluded = _parse_rows(
            "part2", "Self", 5, source["table"], 2025,
            preserve_valued_unreadable=True)
        self.assertEqual((len(parsed), len(quarantined), len(excluded)), (0, 9, 0))
        self.assertTrue(all("table_header_unrecognized" in row["reasons"]
                            for row in quarantined))
        self.assertIn("$3,000", quarantined[0]["cells"][5])
        self.assertIn("$5,500", quarantined[-1]["cells"][5])

    def test_merged_printed_number_cell_preserves_all_populated_rows(self) -> None:
        source = json.loads((FIXTURES / "oge_annual_vance_2026_page10_table.json").read_text(
            encoding="utf-8"))
        self.assertEqual(source["page_number"], 10)
        self.assertEqual(source["source_sha256"],
                         "bcad0b4e58789135b758b5a73fc9584ff4bf9bff9cf52b8e4ca9d4189dbe9e3d")
        old_parsed, old_quarantined, old_excluded = _parse_rows(
            "part6", "Unknown", 10, source["table"], 2025)
        self.assertEqual((len(old_parsed), len(old_quarantined), len(old_excluded)),
                         (12, 8, 0))
        parsed, quarantined, excluded = _parse_rows(
            "part6", "Unknown", 10, source["table"], 2025,
            preserve_valued_unreadable=True)
        self.assertEqual((len(parsed), len(quarantined), len(excluded)), (12, 10, 0))
        self.assertEqual(len(parsed) + len(quarantined) + len(excluded), 22)
        missing_before = {"Marcus by Goldman Sachs Savings Account",
                          "Navy Federal Credit Union Cash Account"}
        recovered_into_quarantine = {row["cells"][1] for row in quarantined
                                     if len(row["cells"]) > 1}
        self.assertTrue(missing_before <= recovered_into_quarantine)
        for row in quarantined:
            if len(row["cells"]) > 1 and row["cells"][1] in missing_before:
                self.assertIn("row_number_unreadable", row["reasons"])

    def test_visual_page_rows_are_accounted_for_or_named_as_missing(self) -> None:
        extraction = json.loads((FIXTURES / "oge_annual_vance_2026.json").read_text(encoding="utf-8"))
        census = json.loads((FIXTURES / "oge_annual_vance_2026_page_census.json").read_text(encoding="utf-8"))
        self.assertEqual(extraction["source_sha256"], census["source_sha256"])
        part6 = census["pages"]["part6"]
        part7 = census["pages"]["part7"]
        self.assertEqual((part6["page"], part7["page"]), (10, 11))
        self.assertEqual(len(part6["rows"]), 22)
        self.assertEqual(len(part7["filled_rows"]), 10)
        self.assertEqual([row["number"] for row in part7["filled_rows"]],
                         [str(number) for number in range(1, 11)])
        self.assertEqual(part7["blank_template_numbers"], list(range(11, 21)))
        self.assertEqual([row["number"] for row in part6["rows"] if "." not in row["number"]],
                         [str(number) for number in range(1, 14)])

        used: set[tuple[str, int]] = set()
        for part, rows in (("part6", part6["rows"]),
                           ("part7", part7["filled_rows"])):
            for visual in rows:
                collection, index = visual["collection"], visual["index"]
                if collection is None:
                    self.assertIsNone(index)
                    continue
                identity = (collection, index)
                self.assertNotIn(identity, used)
                used.add(identity)
                source_row = extraction[collection][index]
                self.assertEqual(source_row["section"], part)
                self.assertEqual(source_row["page_number"],
                                 census["pages"][part]["page"])
                if source_row.get("row_number") is not None:
                    self.assertEqual(source_row["row_number"], visual["number"])
                if collection in {"holdings", "transactions"} and "asset" in visual:
                    self.assertEqual(source_row["asset_name"], visual["asset"])
        all_detected = {(collection, index) for collection in
                        ("holdings", "transactions", "excluded", "quarantined")
                        for index, row in enumerate(extraction[collection])
                        if row["section"] in {"part6", "part7"}}
        self.assertEqual(used, all_detected)
        missing = [row for row in part6["rows"] if row["collection"] is None]
        self.assertEqual([row["number"] for row in missing], ["11", "13"])
        self.assertEqual(len(used), 30)


if __name__ == "__main__":
    unittest.main()
