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

    def test_missing_event_date_and_next_schedule_are_not_lost_or_mixed(self):
        extraction = {"schema_version": "house-holding-extraction/v1",
                      "source_sha256": "a" * 64, "source": {"document_id": "10000001"}, "rows": []}
        page = {"width": 612, "height": 792, "words": [
            word("Asset", 25, 100), word("Owner", 280, 100), word("Date", 319, 100),
            word("Tx.", 401, 100), word("Amount", 440, 100), word("Cap.", 523, 100),
            word("MissingDate", 25, 130), word("[ST]", 120, 130), word("SP", 280, 130),
            word("P", 401, 130), word("$1,001", 440, 130), word("-", 475, 130),
            word("$15,000", 481, 130),
            word("Schedule", 25, 180), word("C", 75, 180),
            word("Outside", 25, 200), word("[ST]", 120, 200),
            word("02/18/2025", 319, 200), word("S", 401, 200), word("$15,001", 440, 200),
        ]}
        result = observe_annual_sections([page], extraction)
        self.assertEqual(result["schedule_b_observed_row_count"], 1)
        self.assertEqual(result["schedule_b_date_anchored_row_count"], 0)
        row = result["schedule_b_rows"][0]
        self.assertEqual((row["asset_name"], row["owner"], row["transaction_type"]),
                         ("MissingDate", "Spouse", "purchase"))
        self.assertIn("transaction_date_unresolved", row["problems"])
        self.assertEqual(row["evidence"]["bbox_points"][-1], 180)
        self.assertEqual(row["disposition"], "pending_relationship_and_qualification")

    def test_explicit_nested_account_path_is_preserved_without_parent_duplication(self):
        extraction = {"schema_version": "house-holding-extraction/v1", "source_sha256": "a" * 64,
                      "rows": [{"extraction_id": "one", "asset_name": "Trust ⇒ Retirement ⇒ Fund",
                                "review_reasons": [], "evidence": {"page": 1}}]}
        result = observe_annual_sections([{"width": 612, "height": 792, "words": []}], extraction)
        self.assertEqual(result["schedule_a_extraction_row_count"], 1)
        self.assertEqual(result["schedule_a_parent_leaf_row_count"], 1)
        row = result["schedule_a_rows"][0]
        self.assertEqual(row["account_path_raw"], ["Trust", "Retirement"])
        self.assertEqual(row["leaf_asset_raw"], "Fund")
        self.assertEqual(row["hierarchy_status"], "parent_path_and_leaf_observed")

    def test_bad_type_and_date_still_have_financial_row_disposition_before_footer(self):
        extraction = {"schema_version": "house-holding-extraction/v1", "source_sha256": "a" * 64, "rows": []}
        page = {"width": 612, "height": 792, "words": [
            word("Asset", 25, 100), word("Owner", 280, 100), word("Date", 319, 100),
            word("Tx.", 401, 100), word("Amount", 440, 100), word("Cap.", 523, 100),
            word("Trust", 25, 130), word("[TR]", 65, 130), word("⇒", 100, 130),
            word("Fund", 120, 130), word("[MF]", 155, 130),
            word("unknown", 319, 130), word("Q", 401, 130), word("$1,001", 440, 130),
            word("-", 475, 130), word("$15,000", 481, 130),
            word("https://fd.house.gov/reference/asset-type-codes.aspx", 25, 180),
            word("Outside", 25, 200), word("01/01/2025", 319, 200), word("P", 401, 200),
        ]}
        result = observe_annual_sections([page], extraction)
        self.assertEqual(result["schedule_b_observed_row_count"], 1)
        row = result["schedule_b_rows"][0]
        self.assertEqual(row["account_path_raw"], ["Trust [TR]"])
        self.assertEqual(row["leaf_asset_raw"], "Fund")
        self.assertEqual(row["asset_type_code"], "MF")
        self.assertIn("transaction_type_unresolved", row["problems"])
        self.assertIn("transaction_date_unresolved", row["problems"])

    def test_schedule_b_continuation_preserves_first_row_dates_owner_and_identity(self):
        extraction = {"schema_version": "house-holding-extraction/v1", "source_sha256": "a" * 64, "rows": []}
        header = [word("Asset", 25, 100), word("Owner", 280, 100), word("Date", 319, 100),
                  word("Tx.", 401, 100), word("Amount", 440, 100), word("Cap.", 523, 100)]
        first = {"width": 612, "height": 792, "words": header + [
            word("Retirement", 25, 700), word("⇒", 110, 700), word("SP", 280, 700),
            word("01/02/2025", 319, 700), word("P", 401, 700),
            word("$1,001", 440, 700), word("-", 480, 700)]}
        second = {"width": 612, "height": 792, "words": header + [
            word("Example", 25, 130), word("Fund", 65, 130), word("[MF]", 105, 130),
            word("$15,000", 440, 130)]}
        result = observe_annual_sections([first, second], extraction)
        self.assertEqual(result["schedule_b_observed_row_count"], 1)
        row = result["schedule_b_rows"][0]
        self.assertEqual((row["transaction_date"], row["owner"], row["transaction_type"]),
                         ("2025-01-02", "Spouse", "purchase"))
        self.assertEqual((row["amount_low"], row["amount_high"]), (1001, 15000))
        self.assertEqual(row["account_path_raw"], ["Retirement"])
        self.assertEqual(row["evidence"]["continuation_segments"][0]["page"], 2)
        self.assertEqual(row["problems"], [])
        # A new explicit owner prevents accidental merging of independent disclosures.
        second["words"].append(word("JT", 280, 130))
        separate = observe_annual_sections([first, second], extraction)
        self.assertEqual(separate["schedule_b_observed_row_count"], 2)
        second["words"] = [item for item in second["words"] if item["text"] != "JT"]
        second["words"].append(word("unreadable", 319, 130))
        conflict = observe_annual_sections([first, second], extraction)
        self.assertEqual(conflict["schedule_b_observed_row_count"], 2)
        self.assertEqual(conflict["schedule_b_rows"][1]["transaction_date_raw"], "unreadable")


if __name__ == "__main__":
    unittest.main()
