from __future__ import annotations

import sys
import json
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_candidate import _paper_pending_count
from unison_snapshot.house_ptr_fixed_paper import (
    RELATION_REVIEW_SHA256, REVIEW_SHA256, SOURCE_SHA256, _sha,
)


class FixedHousePaperTests(unittest.TestCase):
    def test_fixed_visual_review_is_pinned_to_code(self):
        path = Path(__file__).resolve().parents[2] / "docs/house-paper-9116141-source-review.json"
        review = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(_sha(review), REVIEW_SHA256)
        self.assertEqual(review["source_sha256"], SOURCE_SHA256)
        self.assertEqual((review["expected_eligible_count"],
                          review["expected_relation_pending_count"]), (119, 15))
        self.assertEqual(len(review["date_corrections"]), 3)

    def test_prior_report_relationship_review_is_pinned_to_code(self):
        path = (Path(__file__).resolve().parents[2] /
                "docs/house-paper-9116141-prior-relationship-review.json")
        review = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(_sha(review), RELATION_REVIEW_SHA256)
        self.assertEqual(len(review["target_pending_rows"]), 15)
        self.assertEqual(review["prior_latest_data_event_date"], "2026-04-30")
        self.assertEqual({row["event_date"] for row in review["target_pending_rows"]},
                         {"2026-05-01", "2026-05-04"})

    def test_complete_paper_has_no_pending_projection(self):
        self.assertEqual(_paper_pending_count({"transactions": [{}]}), 0)

    def test_partial_paper_projection_cannot_reenter_candidate(self):
        with self.assertRaises(HouseIndexError):
            _paper_pending_count({
                "transactions": [{"id": "already-qualified"}],
                "quarantined": [],
                "pending_transaction_projection": {},
            })


if __name__ == "__main__":
    unittest.main()
