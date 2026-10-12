import unittest

from unison_snapshot.house_ptr_key_fields_shadow import read_house_ptr_key_fields


def cell(text="", label="", ink=0):
    return {"raw_text": text, "label": label, "interior_dark_fraction": ink}


def amount_cells(*, primary="B", supplemental=0):
    return [cell(label=chr(ord("A") + index),
                 ink=supplemental if index == 10 else .13 if chr(ord("A") + index) == primary else .001)
            for index in range(11)]


def report():
    return {"schema_version": "house-ptr-cell-observation-shadow/v3",
            "qualification_status": "unverified_shadow_only",
            "source_sha256": "a" * 64,
            "pages": [{"page": 1, "rows": [
                {"physical_band_index": 1, "disposition": "data_candidate",
                 "cells": {"owner": cell("SP."), "asset": cell("CARLISLE INC"),
                           "event_date": cell("05/01/2026"),
                           "notification_date": cell("6/5/2026"),
                           "direction": [cell(label="purchase", ink=.12),
                                         cell(label="sale", ink=.001)],
                           "amount": amount_cells()}},
                {"physical_band_index": 2, "disposition": "unknown", "cells": None}]}]}


class HousePtrKeyFieldsShadowTests(unittest.TestCase):
    def test_single_strong_marks_and_dates_remain_unverified(self):
        result = read_house_ptr_key_fields(report())
        self.assertEqual(result["data_candidate_count"], 1)
        row = result["rows"][0]
        self.assertEqual(row["status"], "raw_fields_present_unverified")
        self.assertEqual(row["fields"]["direction_mark"], "purchase")
        self.assertEqual(row["fields"]["amount_letter"], "B")
        self.assertEqual(row["fields"]["event_date"], "2026-05-01")
        self.assertEqual(result["qualification_status"], "unverified_shadow_only")

    def test_noisy_date_and_second_mark_leave_field_blockers(self):
        observed = report()
        fields = observed["pages"][0]["rows"][0]["cells"]
        fields["event_date"]["raw_text"] = "| 05/01/2026"
        fields["amount"][0]["interior_dark_fraction"] = .04
        row = read_house_ptr_key_fields(observed)["rows"][0]
        self.assertEqual(row["status"], "raw_fields_incomplete")
        self.assertIn("event_date_unreadable", row["blocking_reasons"])
        self.assertIn("amount_mark_unresolved", row["blocking_reasons"])

    def test_spouse_special_mark_and_range_are_preserved_but_blocked(self):
        observed = report()
        observed["pages"][0]["rows"][0]["cells"]["amount"] = amount_cells(supplemental=.13)
        row = read_house_ptr_key_fields(observed)["rows"][0]
        self.assertEqual(row["fields"]["amount_letter"], "B")
        self.assertTrue(row["fields"]["spouse_dc_over_1000000_mark"])
        self.assertIn("amount_column_combination_unresolved", row["blocking_reasons"])

    def test_supplemental_box_alone_is_not_an_amount_range(self):
        observed = report()
        observed["pages"][0]["rows"][0]["cells"]["amount"] = amount_cells(
            primary=None, supplemental=.13)
        row = read_house_ptr_key_fields(observed)["rows"][0]
        self.assertIsNone(row["fields"]["amount_letter"])
        self.assertIn("amount_mark_unresolved", row["blocking_reasons"])
        self.assertIn("spouse_dc_open_ended_amount_not_representable",
                      row["blocking_reasons"])

    def test_wrong_schema_cannot_become_key_field_report(self):
        observed = report()
        observed["schema_version"] = "house-ptr-cell-observation-shadow/v2"
        with self.assertRaisesRegex(ValueError, "owner-aware"):
            read_house_ptr_key_fields(observed)
