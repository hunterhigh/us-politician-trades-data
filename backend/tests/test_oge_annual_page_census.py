"""Offline crosswalk of independently inspected Vance annual PDF pages 10/11."""
from __future__ import annotations

import json
from pathlib import Path
import unittest


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class OgeAnnualPageCensusTests(unittest.TestCase):
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
