import unittest

from unison_snapshot.house_ptr_duplicate_observation import observe_repeated_trade_signatures


FIELDS = {"asset_raw": "CALIFORNIA ST GO", "owner_code": "SP",
          "event_date": "2026-05-08", "notification_date": "2026-06-05",
          "direction_mark": "purchase", "amount_letter": "C",
          "spouse_dc_over_1000000_mark": False}


class HousePaperDuplicateObservationTests(unittest.TestCase):
    def test_repeated_signature_preserves_distinct_source_slots(self):
        reading = {"schema_version": "house-ptr-key-fields-shadow/v1",
                   "source_sha256": "a" * 64, "data_candidate_count": 3,
                   "rows": [{"page": 1, "physical_band_index": index,
                             "status": "raw_fields_present_unverified",
                             "fields": FIELDS.copy()} for index in (2, 3, 4)]}
        sections = {"schema_version": "house-ptr-section-context/v1",
                    "source_sha256": "a" * 64,
                    "data_rows": [{"page": 1, "physical_band_index": index,
                                   "section_key": "section-one" if index < 4
                                   else "section-two"} for index in (2, 3, 4)]}
        result = observe_repeated_trade_signatures(reading, sections)
        self.assertEqual(result["repeated_group_count"], 1)
        self.assertEqual(result["repeated_physical_row_count"], 2)
        self.assertEqual(result["groups"][0]["slots"], [
            {"page": 1, "physical_band_index": 2},
            {"page": 1, "physical_band_index": 3}])

    def test_incomplete_reading_cannot_create_repeat_group(self):
        reading = {"schema_version": "house-ptr-key-fields-shadow/v1",
                   "source_sha256": "a" * 64, "data_candidate_count": 2,
                   "rows": [{"page": 1, "physical_band_index": index,
                             "status": "raw_fields_incomplete",
                             "fields": {**FIELDS, "notification_date": None}}
                            for index in (1, 2)]}
        sections = {"schema_version": "house-ptr-section-context/v1",
                    "source_sha256": "a" * 64,
                    "data_rows": [{"page": 1, "physical_band_index": index,
                                   "section_key": "section-one"} for index in (1, 2)]}
        result = observe_repeated_trade_signatures(reading, sections)
        self.assertEqual(result["repeated_group_count"], 0)
