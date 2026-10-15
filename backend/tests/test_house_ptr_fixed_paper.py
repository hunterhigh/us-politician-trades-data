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
from unison_snapshot.house_ptr_paper_qualification import _pending_transaction_projection
from unison_snapshot.house_ptr_paper_temporal import PARSER_VERSION


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

    def fixture(self):
        rows = []
        for index in range(15):
            key = f"paper-slot:{SOURCE_SHA256}:1:{index + 1}"
            rows.append({
                "observation_key": key,
                "evidence": {"page": 1, "physical_band_index": index + 1},
                "raw_fields": {
                    "owner_code": "SP", "asset_raw": f"Asset {index}",
                    "event_date": "2026-05-01", "event_date_status": "ocr_date_shape_unverified",
                    "direction_mark": "purchase", "direction_status": "single_strong_mark_unverified",
                    "amount_letter": "A", "amount_status": "single_strong_mark_unverified",
                },
                "amount_range_observation": {"low": 1000, "high": 15000, "kind": "range"},
                "reasons": ["prior_same_filer_report_relation_unresolved"],
            })
        value = {
            "document_id": "9116141", "source_sha256": SOURCE_SHA256,
            "parser_version": PARSER_VERSION,
            "identity": {"person_id": "house:M001157"},
            "source_review_sha256": "a" * 64,
            "transactions": [{"id": f"house-ptr:{index:024d}"} for index in range(119)],
            "quarantined": rows,
            "qualification": {"method": "fixed_source_visual_review_v1",
                              "qualified_count": 119, "quarantined_count": 15},
        }
        metadata = {"document_id": "9116141", "sha256": SOURCE_SHA256,
                    "source_id": "house_clerk", "filing_type": "P", "filing_year": 2026,
                    "filer_name": "Hon. Michael T. McCaul", "state_district": "TX10",
                    "filed_date": "2026-06-10",
                    "source_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/9116141.pdf"}
        value["pending_transaction_projection"] = _pending_transaction_projection(value, metadata)
        return value

    def test_partial_paper_keeps_fifteen_pending_disclosure_slots(self):
        self.assertEqual(_paper_pending_count(self.fixture()), 15)

    def test_partial_paper_rejects_a_missing_qualified_row(self):
        value = self.fixture()
        value["transactions"].pop()
        with self.assertRaises(HouseIndexError):
            _paper_pending_count(value)

    def test_partial_paper_rejects_a_promoted_pending_slot(self):
        value = self.fixture()
        value["quarantined"][0]["reasons"] = []
        with self.assertRaises(HouseIndexError):
            _paper_pending_count(value)


if __name__ == "__main__":
    unittest.main()
