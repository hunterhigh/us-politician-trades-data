from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_annual_scan_sections import (
    HARRIS_2023_DOCUMENT_ID, HARRIS_2023_SOURCE_SHA256, _row_blocks,
    _trade_observation, observe_scan_sections)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class OgeAnnualScanSectionsTests(unittest.TestCase):
    def test_fixed_harris_part7_row_snapshot_accounts_for_100_physical_slots(self) -> None:
        source = json.loads((FIXTURES / "oge_annual_harris_2023_part7_physical_rows.json")
                            .read_text(encoding="utf-8"))
        summary = json.loads((FIXTURES / "oge_annual_harris_2023_scan_sections_summary.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(source["source_sha256"], HARRIS_2023_SOURCE_SHA256)
        self.assertEqual(source["source_document_id"], HARRIS_2023_DOCUMENT_ID)
        counts: Counter[str] = Counter()
        for page in source["part7_pages"]:
            lines = [{"text": "# Description Type Date Amount", "x0": 20,
                      "top": 104, "bottom": 115}]
            lines.extend({"text": " ".join(row["raw_lines"]), "x0": row["x0"],
                          "top": row["top"], "bottom": row["bottom"]}
                         for row in page["rows"])
            blocks = _row_blocks(lines, 0)
            self.assertEqual([row["number"] for row in blocks],
                             [str(number) for number in range(1, 21)])
            for block, expected in zip(blocks, page["rows"]):
                observation = _trade_observation(block)
                self.assertEqual(observation["classification"],
                                 expected["classification"])
                counts[observation["classification"]] += 1
        self.assertEqual(counts, {"filled_transaction": 95, "blank_template": 5})
        self.assertEqual(summary["part7_slot_counts"], dict(counts))
        self.assertEqual(summary["section_pages"],
                         {"part2": [3], "part5": [6, 7], "part6": [8],
                          "part7": [9, 10, 11, 12, 13]})
        self.assertEqual(summary["production_eligible_transaction_count"], 0)
        self.assertEqual(summary["annual_filing_date_status"], "unverified")

    def test_malformed_filled_row_remains_unknown(self) -> None:
        block = {"number": "1", "raw_lines": ["1. Example asset purchase unclear $1,001 - $15,000"]}
        self.assertEqual(_trade_observation(block)["classification"], "filled_unknown")

    def test_source_hash_mismatch_fails_before_page_reading(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / "report.pdf"
            pdf.write_bytes(b"%PDF-1.7\nwrong bytes\n%%EOF")
            with self.assertRaisesRegex(OgeCatalogError, "archived hash"):
                observe_scan_sections(pdf, source_sha256=HARRIS_2023_SOURCE_SHA256,
                                      document_id=HARRIS_2023_DOCUMENT_ID)


if __name__ == "__main__":
    unittest.main()
