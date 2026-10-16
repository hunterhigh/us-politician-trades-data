import unittest

from unison_snapshot.source_row_ledger import (page_ledger, report_ledger, cell_observation,
                                               senate_shadow_pages,
                                               senate_shadow_cell_observations,
                                               oge_278t_shadow_pages,
                                               house_ptr_shadow_pages,
                                               house_ptr_cell_shadow_ledger)


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
    def test_house_cell_shadow_preserves_rows_without_known_columns(self):
        report = {"schema_version": "house-ptr-cell-observation-shadow/v1",
                  "qualification_status": "unverified_shadow_only",
                  "source_sha256": "a" * 64, "render_dpi": 200,
                  "pages": [{"page": 1, "region_status": "located_shadow",
                             "cell_status": "unobserved_unknown_columns",
                             "coordinate_frame": "original_page",
                             "rows": [
                                 {"physical_band_index": 1,
                                  "disposition": "data_candidate",
                                  "band": [.1, .2], "cells": None},
                                 {"physical_band_index": 2,
                                  "disposition": "blank",
                                  "band": [.2, .3], "cells": None}]}]}
        ledger = house_ptr_cell_shadow_ledger(report)
        self.assertEqual(ledger["pages"][0]["physical_slot_count"], 2)
        self.assertEqual(ledger["pages"][0]["disposition_counts"],
                         {"blank": 1, "data_candidate": 1})
        self.assertEqual(ledger["cells"], [])
        self.assertIsNone(ledger["pages"][0]["qualified_transaction_count"])

    def test_house_cell_shadow_keeps_raw_values_without_interpretation(self):
        cell = {"label": "event_date", "status": "raw_unverified",
                "value": None, "bbox_pixels": [10, 20, 30, 40],
                "crop_sha256": "b" * 64, "raw_text": "07/24/26",
                "reading": "date_shape_match", "interior_dark_fraction": .01,
                "raw_ocr_confidence": 93.0}
        report = {"schema_version": "house-ptr-cell-observation-shadow/v1",
                  "qualification_status": "unverified_shadow_only",
                  "source_sha256": "a" * 64, "render_dpi": 200,
                  "pages": [{"page": 1, "region_status": "located_shadow",
                             "cell_status": "raw_observed_unverified",
                             "rows": [{"physical_band_index": 1,
                                       "disposition": "data_candidate",
                                       "band": [.1, .2],
                                       "cells": {"event_date": cell,
                                                 "direction": [], "amount": []}}]}]}
        ledger = house_ptr_cell_shadow_ledger(report)
        self.assertEqual(len(ledger["cells"]), 1)
        self.assertEqual(ledger["cells"][0]["raw"]["text"], "07/24/26")
        self.assertIsNone(ledger["cells"][0]["interpreted"])
        self.assertEqual(ledger["cells"][0]["crop_sha256"], "b" * 64)
        cell["value"] = "2026-07-24"
        with self.assertRaisesRegex(ValueError, "promoted value"):
            house_ptr_cell_shadow_ledger(report)

    def test_house_v2_carries_asset_and_notification_without_interpretation(self):
        cell = {"label": "asset", "status": "raw_unverified", "value": None,
                "bbox_pixels": [10, 20, 30, 40], "crop_sha256": "b" * 64,
                "raw_text": "XYZ", "reading": "raw_unclassified",
                "interior_dark_fraction": .01, "raw_ocr_confidence": 93.0}
        report = {"schema_version": "house-ptr-cell-observation-shadow/v2",
                  "qualification_status": "unverified_shadow_only",
                  "source_sha256": "a" * 64, "render_dpi": 200,
                  "pages": [{"page": 1, "region_status": "located_shadow",
                             "cell_status": "raw_observed_unverified",
                             "rows": [{"physical_band_index": 1,
                                       "disposition": "data_candidate",
                                       "band": [.1, .2],
                                       "cells": {"event_date": {**cell, "label": "event_date"},
                                                 "asset": cell,
                                                 "notification_date": {**cell,
                                                                       "label": "notification_date"},
                                                 "direction": [], "amount": []}}]}]}
        ledger = house_ptr_cell_shadow_ledger(report)
        self.assertEqual({item["field"] for item in ledger["cells"]},
                         {"asset", "event_date", "notification_date"})
        self.assertTrue(all(item["interpreted"] is None for item in ledger["cells"]))
        self.assertEqual(ledger["pages"][0]["adapter_version"],
                         "house-ptr-cell-shadow/v2/200dpi")

    def test_house_v3_carries_owner_separately_from_asset(self):
        cell = {"label": "owner", "status": "raw_unverified", "value": None,
                "bbox_pixels": [10, 20, 30, 40], "crop_sha256": "b" * 64,
                "raw_text": "SP", "reading": "raw_unclassified",
                "interior_dark_fraction": .01, "raw_ocr_confidence": 93.0}
        cells = {name: {**cell, "label": name} for name in
                 ("owner", "asset", "event_date", "notification_date")}
        cells.update({"direction": [], "amount": []})
        report = {"schema_version": "house-ptr-cell-observation-shadow/v3",
                  "qualification_status": "unverified_shadow_only",
                  "source_sha256": "a" * 64, "render_dpi": 200,
                  "pages": [{"page": 1, "region_status": "located_shadow",
                             "cell_status": "raw_observed_unverified",
                             "rows": [{"physical_band_index": 1,
                                       "disposition": "data_candidate",
                                       "band": [.1, .2], "cells": cells}]}]}
        ledger = house_ptr_cell_shadow_ledger(report)
        self.assertEqual({item["field"] for item in ledger["cells"]},
                         {"owner", "asset", "event_date", "notification_date"})
        self.assertEqual(ledger["pages"][0]["adapter_version"],
                         "house-ptr-cell-shadow/v3/200dpi")

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

    def test_278t_adapter_keeps_unread_number_as_unknown_slot(self):
        report = {"source_sha256": "e" * 64, "pages": [
            {"page_number": 2, "coverage": "table_geometry_candidate",
             "row_coverage": "physical_slots_with_unread_number",
             "candidate_row_bands": [[100, 120], [120, 140]],
             "unresolved_row_bands": [[120, 140]],
             "number_sequence_warnings": []},
            {"page_number": 3, "coverage": "coverage_unknown_raster",
             "row_coverage": "unknown", "candidate_row_bands": [],
             "unresolved_row_regions": []},
        ]}
        pages = oge_278t_shadow_pages(report, source_id="whitehouse")
        self.assertEqual(pages[0]["physical_slot_count"], 2)
        self.assertEqual(pages[0]["slot_coverage"], "partial_unknown")
        self.assertEqual(pages[1]["coverage"], "unknown")
        self.assertEqual(pages[1]["physical_slot_count"], 0)

    def test_house_adapter_keeps_provisional_date_bands_unknown(self):
        pages = house_ptr_shadow_pages([{
            "status": "coverage_unknown", "reason": "asset_without_event_date",
            "physical_row_bands": [
                {"band": [.1, .2], "disposition": "data_candidate"},
                {"band": [.2, .3], "disposition": "unknown"},
            ]}], document_sha256="f" * 64, dpi=150)
        self.assertEqual(pages[0]["coverage"], "unknown")
        self.assertEqual(pages[0]["physical_slot_count"], 2)
        self.assertEqual(pages[0]["slot_coverage"], "partial_unknown")

    def test_house_rotated_coordinates_remain_in_rotated_frame(self):
        pages = house_ptr_shadow_pages([{
            "status": "located_shadow", "coordinate_frame": "rotated_page_90",
            "physical_row_bands": [
                {"band": [.1, .2], "disposition": "data_candidate"}],
        }], document_sha256="f" * 64, dpi=150)
        self.assertEqual(pages[0]["coordinate_frame"], "rotated_page_90")

    def test_senate_cell_reads_remain_separate_observations(self):
        sha = "a" * 64
        box = [10, 20, 30, 40]
        report = {"entrypoint_source_sha256": "b" * 64, "slots": [{
            "page_number": 1, "grid_slot": 3, "key_cells": {
                "direction": [{"label": "purchase", "box_px": box,
                               "grayscale_sha256": sha,
                               "dark_sample_count": 30, "mark_signal": True}],
                "amount": [],
                "date": {"box_px": box, "grayscale_sha256": sha,
                         "page_ocr_raw": "01/02/26", "page_ocr_parsed": "2026-01-02",
                         "cell_ocr": {"raw": "01/03/26", "engine": "tesseract-cell",
                                      "min_confidence": 83.0},
                         "cell_ocr_parsed": "2026-01-03"},
            }}]}
        observations = senate_shadow_cell_observations(report)
        self.assertEqual(len(observations), 3)
        self.assertEqual([row["interpreted"] for row in observations[-2:]],
                         ["2026-01-02", "2026-01-03"])
        self.assertEqual(observations[0]["slot_key"], "3")
        with self.assertRaises(ValueError):
            cell_observation(source_id="senate_efd", document_sha256="b" * 64,
                             page_number=1, adapter_version="v1", slot_key="3",
                             field="date", box_px=[10, 20, 10, 40],
                             crop_sha256=sha, engine="ocr", raw="")

    def test_senate_date_consensus_keeps_conflicting_raw_variants(self):
        variant = lambda raw, parsed: {"raw": raw, "parsed": parsed,
                                       "engine": "tesseract-cell", "min_confidence": 85.0}
        read = {"status": "conflict", "date": None,
                "engine": "three-pass-strict-date-shadow", "variants": {
                    "x1": variant("1/9/26", "2026-01-09"),
                    "x2": variant("4/9/26", "2026-04-09"),
                    "x3": variant("1/9/26", "2026-01-09")}}
        report = {"entrypoint_source_sha256": "b" * 64, "slots": [{
            "page_number": 1, "grid_slot": 1, "key_cells": {
                "direction": [], "amount": [],
                "date": {"box_px": [1, 2, 3, 4], "grayscale_sha256": "a" * 64,
                         "page_ocr_raw": "", "page_ocr_parsed": None,
                         "cell_ocr": read, "cell_ocr_parsed": None}}}]}
        observed = senate_shadow_cell_observations(report)
        self.assertEqual(len(observed), 5)
        self.assertEqual(observed[-1]["field"], "transaction_date_consensus")
        self.assertEqual(observed[-1]["raw"], "conflict")
        self.assertIsNone(observed[-1]["interpreted"])
