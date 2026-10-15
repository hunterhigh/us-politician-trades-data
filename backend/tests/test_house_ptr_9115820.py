from __future__ import annotations

import sys
import json
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unison_snapshot.house_ptr_9115820 import (
    DOCUMENT_ID, REVIEW_SHA256, SOURCE_SHA256,
)
from unison_snapshot.house_ptr_fixed_paper import _fixed_transaction, _sha


class HousePaper9115820Tests(unittest.TestCase):
    def test_source_review_is_pinned_to_code(self):
        path = (Path(__file__).resolve().parents[2] /
                "docs/house-paper-9115820-source-review.json")
        review = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(_sha(review), REVIEW_SHA256)
        self.assertEqual(review["document_id"], DOCUMENT_ID)
        self.assertEqual(review["source_sha256"], SOURCE_SHA256)
        self.assertEqual(review["physical_counts"],
                         {"data_candidate": 86, "section_heading": 8, "blank": 1})
        self.assertEqual(len(review["section_headings"]), 8)
        self.assertEqual(len(review["owner_corrections"]), 1)
        self.assertEqual(review["prior_data_event_month"], "2026-03")

    def test_disclosure_slot_id_uses_this_source_and_owner(self):
        amount = {"low": 50001, "high": 100000}
        row = _fixed_transaction(
            4, 16, "2026-04-28", "VANGUARD INDEX FDS", "MICHAEL MCCAUL",
            "sale", amount,
            "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/9115820.pdf",
            document_id=DOCUMENT_ID, source_sha256=SOURCE_SHA256,
            owner="Self", filed_at="2026-05-04T00:00:00Z")
        self.assertEqual(row["filing_id"], DOCUMENT_ID)
        self.assertEqual(row["owner"], "Self")
        self.assertEqual(row["amount_low"], 50001)
        self.assertIn("Account: MICHAEL MCCAUL", row["asset_name"])
        self.assertNotEqual(row["id"], _fixed_transaction(
            4, 16, "2026-04-28", "VANGUARD INDEX FDS", "MICHAEL MCCAUL",
            "sale", amount, row["source_url"])["id"])


if __name__ == "__main__":
    unittest.main()
