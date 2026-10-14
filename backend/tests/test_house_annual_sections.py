from __future__ import annotations

import unittest

from unison_snapshot.house_annual_sections import observe_annual_sections


def word(text: str, x0: float, top: float) -> dict:
    return {"text": text, "x0": x0, "top": top, "size": 8}


class HouseAnnualSectionTests(unittest.TestCase):
    def test_parent_leaf_and_schedule_b_stay_separate_from_formal_holdings(self):
        extraction = {
            "schema_version": "house-holding-extraction/v1",
            "source_sha256": "a" * 64,
            "source": {"document_id": "10000001"},
            "rows": [
                {"extraction_id": "h1", "asset_name": "Retirement Account ⇒ Example Fund",
                 "evidence": {"page": 1, "top_points": 100}, "review_reasons": []},
                {"extraction_id": "h2", "asset_name": "Direct Stock",
                 "evidence": {"page": 1, "top_points": 120}, "review_reasons": []},
            ],
        }
        page = {"width": 612, "height": 792, "words": [
            word("Asset", 25, 100), word("Owner", 280, 100),
            word("Date", 319, 100), word("Tx.", 401, 100),
            word("Amount", 440, 100), word("Cap.", 523, 100),
            word("Example", 25, 130), word("Fund", 65, 130),
            word("[MF]", 105, 130), word("SP", 280, 130),
            word("02/18/2025", 319, 130), word("S", 401, 130),
            word("$15,001", 440, 130), word("-", 475, 130),
            word("$50,000", 481, 130),
        ]}
        result = observe_annual_sections([page], extraction)
        self.assertEqual(result["schedule_a_extraction_row_count"], 2)
        self.assertEqual(result["schedule_a_parent_leaf_row_count"], 1)
        self.assertEqual(result["schedule_a_rows"][0]["parent_account_raw"],
                         "Retirement Account")
        self.assertEqual(result["schedule_a_rows"][0]["leaf_asset_raw"],
                         "Example Fund")
        self.assertEqual(result["schedule_b_date_anchored_row_count"], 1)
        row = result["schedule_b_rows"][0]
        self.assertEqual((row["asset_raw"], row["transaction_date"],
                          row["amount_low"], row["amount_high"]),
                         ("Example Fund [MF]", "2025-02-18", 15001, 50000))
        self.assertEqual(result["production_eligible_transaction_count"], 0)
        self.assertEqual(row["ptr_overlap_status"], "unassessed")


if __name__ == "__main__":
    unittest.main()
