import unittest

from unison_snapshot.source_row_ledger import page_ledger, report_ledger, senate_shadow_pages


BASE = dict(source_id="house_clerk", document_sha256="a" * 64,
            page_number=2, adapter_version="house-paper-short/v1")


class SourceReportLedgerTests(unittest.TestCase):
    def page(self, number=1, disposition="data_candidate"):
        return page_ledger(**(BASE | {"page_number": number}), coverage="located", slots=[
            {"local_key": "1", "bounds": [10, 20], "disposition": disposition}])

    def row(self, key="row", *numbers, **extra):
        return {"local_key": key, "slot_refs": [
            {"page_number": number, "slot_key": "1"} for number in numbers or (1,)], **extra}

    def report(self, pages=None, rows=None, **extra):
        return report_ledger(source_id="house_clerk", document_sha256="a" * 64,
                             adapter_version="test/v1", page_count=extra.pop("page_count", 1),
                             pages=pages if pages is not None else [self.page()],
                             logical_rows=rows if rows is not None else [self.row()], **extra)

    def test_continuation_links_two_pages_to_one_unqualified_record(self):
        result = self.report(pages=[self.page(1), self.page(2)],
                             rows=[self.row("continued", 1, 2)], page_count=2)
        self.assertEqual(result["observed_physical_slot_count"], 2)
        self.assertEqual(result["logical_row_count"], 1)
        self.assertEqual(result["relation_coverage"], "accounted")
        self.assertIsNone(result["qualified_transaction_count"])
        self.assertEqual(result["logical_rows"][0]["qualification"], "not_assessed")

    def test_missing_page_is_unknown_not_successful_zero_rows(self):
        result = self.report(page_count=2)
        self.assertEqual(result["unobserved_pages"], [2])
        self.assertEqual(result["observed_physical_slot_count"], 1)
        self.assertIsNone(result["physical_slot_count"])
        self.assertEqual(result["relation_coverage"], "partial_unknown")

    def test_equal_row_count_cannot_hide_a_lost_data_slot(self):
        with self.assertRaisesRegex(ValueError, "lost"):
            self.report(pages=[self.page(1), self.page(2)],
                        rows=[self.row("first", 1, split_basis="split"),
                              self.row("duplicate", 1, split_basis="split")],
                        page_count=2)

    def test_unexplained_duplicate_slot_cannot_become_two_records(self):
        with self.assertRaisesRegex(ValueError, "split evidence"):
            self.report(rows=[self.row("first"), self.row("duplicate")])

    def test_explicit_split_is_distinct_from_two_physical_slots(self):
        result = self.report(rows=[self.row("first", split_basis="two printed subrows"),
                                   self.row("second", split_basis="two printed subrows")])
        self.assertEqual(result["logical_row_count"], 2)
        self.assertEqual(result["linked_data_slot_count"], 1)

    def test_unreadable_data_slot_keeps_reason_instead_of_disappearing(self):
        result = self.report(rows=[], unresolved_data_slots=[{
            "page_number": 1, "slot_key": "1", "reason": "date_unreadable"}])
        self.assertEqual(result["logical_row_count"], 0)
        self.assertEqual(result["observed_physical_slot_count"], 1)
        self.assertEqual(result["relation_coverage"], "partial_unknown")

    def test_nondata_and_unknown_slots_cannot_generate_records(self):
        for disposition in ("blank", "heading", "note", "unknown"):
            with self.subTest(disposition=disposition), self.assertRaisesRegex(ValueError, "non-data"):
                self.report(pages=[self.page(disposition=disposition)])

    def test_provenance_and_summary_changes_are_rejected(self):
        page = self.page()
        page["source_id"] = "oge"
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.report(pages=[page])
        page = self.page()
        page["physical_slot_count"] = 0
        with self.assertRaisesRegex(ValueError, "counts"):
            self.report(pages=[page])

    def test_duplicate_and_outside_pages_are_rejected(self):
        for pages in ([self.page(), self.page()], [self.page(2)]):
            with self.subTest(pages=pages), self.assertRaisesRegex(ValueError, "bounds"):
                self.report(pages=pages)

    def test_formal_id_cannot_leak_from_an_observation_row(self):
        with self.assertRaisesRegex(ValueError, "formal IDs"):
            self.report(rows=[self.row(id="formal-transaction-id")])

    def test_one_slot_cannot_be_both_linked_and_unresolved(self):
        with self.assertRaisesRegex(ValueError, "linked and unresolved"):
            self.report(unresolved_data_slots=[{
                "page_number": 1, "slot_key": "1", "reason": "unclear"}])


class SourceRowLedgerTests(unittest.TestCase):
    def test_page_ledger_conserves_observed_slots_without_qualifying_them(self):
        page = page_ledger(**BASE, coverage="located", slots=[
        {"local_key": "r1", "bounds": [100, 120],
         "disposition": "data_candidate", "observation_ref": "crop:a"},
        {"local_key": "r2", "bounds": [120, 140],
         "disposition": "blank"},
        ])
        self.assertEqual(page["physical_slot_count"], 2)
        self.assertEqual(page["disposition_counts"],
                         {"blank": 1, "data_candidate": 1})
        self.assertIsNone(page["qualified_transaction_count"])


    def test_unknown_table_location_is_explicit_and_keeps_observed_slots(self):
        page = page_ledger(**BASE, coverage="unknown", coverage_reason="skewed_rule",
                       unresolved_regions=[[140, 200]], slots=[
        {"local_key": "r1", "bounds": [100, 120],
         "disposition": "data_candidate"},
        {"local_key": "r2", "bounds": [120, 140],
         "disposition": "unknown"},
        ])
        self.assertEqual(page["physical_slot_count"], 2)
        self.assertEqual(page["disposition_counts"]["unknown"], 1)
        self.assertEqual(page["unresolved_regions"], [[140, 200]])
        self.assertEqual(page["slot_coverage"], "partial_unknown")


    def test_page_ledger_rejects_false_closure(self):
        cases = [
            {"coverage": "unknown", "slots": []},
            {"coverage": "non_table", "coverage_reason": "cover", "slots": [
                {"local_key": "r", "bounds": [1, 2], "disposition": "blank"}]},
            {"coverage": "located", "slots": [
                {"local_key": "r", "bounds": [1, 2], "disposition": "blank"},
                {"local_key": "r", "bounds": [2, 3], "disposition": "heading"}]},
        ]
        for updates in cases:
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                page_ledger(**(BASE | updates))


    def test_senate_shadow_adapter_keeps_unknown_and_does_not_publish(self):
        report = {"entrypoint_source_sha256": "b" * 64,
              "physical_grid_slot_count": 2,
              "pages": [{"page_number": 1, "physical_grid_slot_count": 2}],
              "slots": [
                  {"page_number": 1, "grid_slot": 1, "row_bounds_px": [10, 20],
                   "row_crop_sha256": "c" * 64, "label": "data_observed"},
                  {"page_number": 1, "grid_slot": 2, "row_bounds_px": [20, 30],
                   "row_crop_sha256": "d" * 64, "label": "unknown"},
              ]}
        pages = senate_shadow_pages(report)
        self.assertEqual(pages[0]["coverage"], "located")
        self.assertEqual(pages[0]["slot_coverage"], "partial_unknown")
        self.assertEqual(pages[0]["disposition_counts"],
                         {"data_candidate": 1, "unknown": 1})
        self.assertIsNone(pages[0]["qualified_transaction_count"])

