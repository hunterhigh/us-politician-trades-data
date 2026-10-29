"""Synthetic relation safeguards; these fixtures do not measure OCR accuracy."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from test_oge_content_relations import saved, column_read, EDGES
from test_oge_observed_candidate import candidate, bundle, ROW
from unison_snapshot.oge_278t_fields import observe_content_rows, observe_field_shapes
from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns
from unison_snapshot.oge_278t_page_coverage import reconcile_raster_ocr_rows


DATA = ["1", "ALPHA", "purchase", "01/02/2026", "No", "$1,001 - $15,000"]
HEADER = ["#", "Description", "Type", "Date", "Notification", "Amount"]


class ContentFragmentTests(unittest.TestCase):
    def observe(self, inputs):
        return observe_content_rows(inputs, document_page_count=5)

    def test_owner_account_and_notice_are_not_disclosures(self):
        data = saved([["", "Filer assets", "", "", "", ""],
                      ["", "Investment account #2", "", "", "", ""], DATA,
                      ["", "Intentionally left blank", "", "", "", ""]])
        out = self.observe([data])
        self.assertEqual([r["disposition"] for r in out["slot_content"]],
                         ["heading", "heading", "data_candidate", "note"])
        context = out["slot_content"][2]["account_context"]
        self.assertEqual(context["title"], "Investment account #2")
        self.assertEqual(context["heading_slot_ref"], {"page_number": 2, "slot_key": "2"})
        self.assertEqual(out["logical_row_field_evidence"][0]["account_context"], context)
        self.assertEqual(out["source_report_ledger"]["logical_row_count"], 1)

    def test_conflicting_structural_label_cannot_establish_account(self):
        data = saved([["", "Investment account #2", "", "", "", ""], DATA])
        fields = observe_field_shapes([data])
        row = fields["pages"][0]["slots"][0]
        reading = deepcopy(row["field_readings"]["description"]["observations"][0])
        reading["shape"]["value"] = "Retirement account #3"
        row["field_readings"]["description"]["observations"].append(reading)
        with patch("unison_snapshot.oge_278t_fields.observe_field_shapes", return_value=fields):
            out = self.observe([data])
        label, disclosure = out["slot_content"]
        self.assertEqual(label["disposition"], "unknown")
        self.assertIn("structural_description_conflict", label["structural_evidence"]["blocking_reasons"])
        self.assertEqual(disclosure["account_context"]["status"], "unknown")

    def test_crossing_label_cannot_establish_account(self):
        data = saved([["", "Investment account #2", "", "", "", ""], DATA])
        slot = data["pages"][0]["slots"][0]
        slot["words"][0]["bbox_pdf_points"][0] = 9.9
        slot.update(_observe_slot_columns(slot["words"], EDGES))
        out = self.observe([data])
        self.assertEqual(out["slot_content"][0]["disposition"], "unknown")
        self.assertEqual(out["slot_content"][1]["account_context"]["status"], "unknown")

    def test_title_uses_physical_word_order_without_rewriting_original(self):
        data = saved([["", "Investment account #2", "", "", "", ""], DATA])
        slot = data["pages"][0]["slots"][0]
        slot["words"] = [{"text": text, "bbox_pdf_points": box} for text, box in [
            ("#2", [51, 102, 65, 108]), ("account", [31, 102, 50, 108]),
            ("Investment", [11, 102, 30, 108])]]
        slot.update(_observe_slot_columns(slot["words"], EDGES))
        out = self.observe([data])
        self.assertEqual(out["slot_content"][0]["disposition"], "heading")
        self.assertEqual(out["slot_content"][1]["account_context"]["title"], "Investment account #2")
        self.assertEqual(out["slot_content"][0]["structural_evidence"]["description_text"],
                         "Investment account #2")
        self.assertEqual(slot["column_observation"]["description"]["raw_ocr_text"], "#2 account Investment")

    def test_adjacent_continuation_keeps_both_fragments_unresolved(self):
        data = saved([DATA, ["", "Continued from previous page: SERIES B", "", "", "", ""]])
        before = deepcopy(data)
        out = self.observe([data])
        self.assertEqual(data, before)
        self.assertEqual(out["source_report_ledger"]["logical_row_count"], 0)
        self.assertEqual(out["fragment_relations"][0]["candidate_parent_slot_refs"],
                         [{"page_number": 2, "slot_key": "1"}])
        self.assertIn("explicit_continuation_relation_unresolved",
                      out["slot_content"][0]["relation_evidence"]["blocking_reasons"])
        self.assertEqual(out["slot_content"][1]["disposition"], "unknown")

    def test_description_only_without_marker_is_not_a_proven_join(self):
        out = self.observe([saved([DATA, ["", "SERIES B", "", "", "", ""]])])
        self.assertEqual(out["source_report_ledger"]["logical_row_count"], 1)
        self.assertEqual(out["fragment_relations"][0]["candidate_parent_slot_refs"], [])
        self.assertEqual(out["fragment_relations"][0]["status"], "unresolved")

    def test_cross_page_header_does_not_turn_table_continuation_into_new_trade(self):
        first = saved([DATA])
        next_page = saved([HEADER, ["", "Continued: SERIES B", "", "", "", ""]])
        next_page["pages"][0]["page_number"] = 3
        out = self.observe([next_page, first])
        self.assertEqual(out["source_report_ledger"]["logical_row_count"], 0)
        self.assertEqual(out["fragment_relations"][0]["candidate_parent_slot_refs"],
                         [{"page_number": 2, "slot_key": "1"}])

    def test_missing_page_and_unknown_geometry_are_not_crossed(self):
        first = saved([DATA])
        last = saved([["", "Continued: SERIES B", "", "", "", ""]])
        last["pages"][0]["page_number"] = 4
        for middle in [[], [saved()]]:
            if middle:
                middle[0]["pages"][0].update(page_number=3, column_geometry="unknown")
            out = self.observe([first, *middle, last])
            self.assertEqual(out["source_report_ledger"]["logical_row_count"], 1)
            self.assertEqual(out["fragment_relations"][-1]["candidate_parent_slot_refs"], [])

    def test_distinct_accounts_keep_equal_assets_as_two_rows(self):
        out = self.observe([saved([["", "Investment account #1", "", "", "", ""], DATA,
                                  ["", "Investment account #2", "", "", "", ""], DATA])])
        self.assertEqual(out["source_report_ledger"]["logical_row_count"], 2)
        self.assertEqual([r["account_context"]["title"] for r in out["logical_row_field_evidence"]],
                         ["Investment account #1", "Investment account #2"])

    def test_owner_section_and_unreadable_gap_do_not_inherit_account_title(self):
        out = self.observe([saved([["", "Investment account #1", "", "", "", ""], DATA,
                                  ["", "Spouse assets", "", "", "", ""], DATA,
                                  ["", "?", "", "", "", ""], DATA])])
        contexts = [r["account_context"] for r in out["logical_row_field_evidence"]]
        self.assertEqual([r["status"] for r in contexts], ["observed", "none_observed", "unknown"])

    def test_multiple_transactions_in_one_cell_remain_unsplit(self):
        data = saved([["1", "ALPHA", "purchase sale", "01/02/2026 01/03/2026", "No",
                       "$1,001 - $15,000 $15,001 - $50,000"]])
        out = self.observe([data])
        self.assertEqual(out["source_report_ledger"]["logical_row_count"], 0)
        self.assertEqual(out["fragment_relations"][0]["kind"], "multiple_transaction_signals")

    def test_duplicate_ocr_tokens_do_not_invent_a_second_transaction(self):
        data = saved([["1", "ALPHA", "purchase", "01/02/2026 1/2/2026", "No",
                       "$1,001 - $15,000 $1,001 - $15,000"]])
        out = observe_content_rows([data], document_page_count=5, secondary_observations=[
            column_read(data, "transaction_date", "01/02/2026"),
            column_read(data, "amount", "$1,001 - $15,000")])
        self.assertEqual(out["source_report_ledger"]["logical_row_count"], 1)
        self.assertEqual(out["fragment_relations"], [])
        self.assertEqual(out["field_observations"]["pages"][0]["slots"][0]["raw_columns"]["amount"],
                         "$1,001 - $15,000 $1,001 - $15,000")

    def test_materializer_really_consumes_fragment_blocker(self):
        # Existing synthetic consumer fixture; no real-source accuracy claim.
        inputs = bundle([[*ROW[:1], "Continued: ALPHA", *ROW[2:]]])
        result, audit = candidate(inputs)
        self.assertEqual(len(result["transactions"]), 1)  # only retained legacy
        reasons = audit["reports"][0]["observation_receipt"]["rows"][0]["reasons"]
        self.assertIn("explicit_continuation_relation_unresolved", reasons)

    def test_number_sequence_does_not_grant_coverage_or_qualification(self):
        out = self.observe([saved([DATA, [*DATA[:1], "Continued: ALPHA", *DATA[2:]]])])
        self.assertEqual(out["source_report_ledger"]["logical_row_count"], 0)
        self.assertIsNone(out["source_report_ledger"]["qualified_transaction_count"])
        self.assertEqual(out["source_report_ledger"]["unobserved_pages"], [1, 3, 4, 5])
        self.assertEqual(out["printed_number_observation"]["source_sha256"], "a" * 64)

    def test_empty_confirmed_grid_has_no_synthetic_logical_row(self):
        out = self.observe([saved([])])
        self.assertEqual(out["source_report_ledger"]["logical_row_count"], 0)
        self.assertEqual(out["printed_number_observation"]["pages"][0]["slots"], [])

    def test_nonfinite_or_negative_fragment_geometry_cannot_claim_a_slot(self):
        for band in [[float("nan"), 120], [100, float("inf")], [-.5, 120], [True, 120]]:
            with self.subTest(band=band), self.assertRaises(ValueError):
                reconcile_raster_ocr_rows(
                    {"source_sha256": "a" * 64, "results": [{"page_number": 2,
                     "status": "physical_slots_located", "physical_row_bands": [band]}]}, [],
                    expected_sha256="a" * 64, ocr_source_sha256="a" * 64)


if __name__ == "__main__":
    unittest.main()
