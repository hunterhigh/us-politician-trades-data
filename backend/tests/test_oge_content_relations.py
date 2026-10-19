"""Synthetic content/relationship boundaries, not measured OCR accuracy."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_278t_field_shadow import observe_content_rows, observe_field_shapes
from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns
from unison_snapshot.oge_transaction_adapter import adapt_oge_278t_extraction


EDGES = [0, 10, 70, 80, 100, 110, 150]


def saved(rows=None):
    rows = rows if rows is not None else [
        ["1", "ALPHA", "purchase", "01/02/2026", "No", "$1,001 - $15,000"]]
    slots = []
    for index, cells in enumerate(rows, 1):
        top = 100 + (index - 1) * 15
        words = [{"text": text, "bbox_pdf_points": [EDGES[i] + 1, top + 2, EDGES[i + 1] - 1, top + 8]}
                 for i, text in enumerate(cells) if text]
        slots.append({"slot_index": index, "band_pdf_points": [top, top + 15], "words": words,
                      "raw_ocr_text": " ".join(cells), **_observe_slot_columns(words, EDGES)})
    return {"schema_version": "oge-278t-raster-slot-observation/v2", "source_sha256": "a" * 64,
            "ocr_engine": "synthetic", "ocr_resolution_dpi": 200,
            "pages": [{"page_number": 2, "column_geometry": "oge_2024_six_column_grid",
                       "column_edges_pdf_points": EDGES, "physical_slot_count": len(slots), "slots": slots}]}


class OgeContentRelationsTests(unittest.TestCase):
    def observe(self, data):
        return observe_content_rows([data], document_page_count=4)

    def adapter_inputs(self, rows):
        fixture = json.loads((Path(__file__).parent / "fixtures/oge_278t/gold_rows_v1.json").read_text())
        extraction = fixture["extraction"]
        extraction.update(source_rows=rows, transactions=[], quarantined=[])
        return extraction, fixture["catalog_record"]

    def test_positive_data_has_local_relation_but_not_qualification(self):
        base = saved()
        before = deepcopy(base)
        out = self.observe(base)
        ledger = out["source_report_ledger"]
        self.assertEqual(ledger["disposition_counts"], {"data_candidate": 1})
        self.assertEqual(ledger["logical_row_count"], 1)
        self.assertIsNone(ledger["qualified_transaction_count"])
        self.assertEqual(ledger["unobserved_pages"], [1, 3, 4])
        self.assertIsNone(ledger["physical_slot_count"])
        self.assertEqual(out["field_observations"], observe_field_shapes([base]))
        self.assertEqual(base, before)

    def test_empty_ocr_number_and_description_alone_stay_unknown(self):
        out = self.observe(saved([["", "", "", "", "", ""],
                                  ["#", "", "", "", "", ""],
                                  ["", "continued asset words", "", "", "", ""]]))
        self.assertEqual(out["source_report_ledger"]["disposition_counts"], {"unknown": 3})
        self.assertEqual(out["source_report_ledger"]["logical_row_count"], 0)

    def test_structural_classification_requires_positive_labels(self):
        rows = [["#", "Description", "Type", "Date", "Notification", "Amount"],
                ["", "Investment account #2", "", "", "", ""],
                ["", "Intentionally left blank", "", "", "", ""],
                ["#", "ALPHA", "purchase", "01/02/2026", "No", "$1,001 - $15,000"],
                ["", "Investment account #2", "sale", "", "", ""]]
        out = self.observe(saved(rows))
        self.assertEqual([item["disposition"] for item in out["slot_content"]],
                         ["heading", "heading", "note", "data_candidate", "unknown"])
        self.assertEqual(out["source_report_ledger"]["logical_row_count"], 1)

    def test_incomplete_data_stays_candidate_with_unresolved_relation(self):
        out = self.observe(saved([["", "ALPHA", "purchase", "", "", ""]]))
        ledger = out["source_report_ledger"]
        self.assertEqual(ledger["disposition_counts"], {"data_candidate": 1})
        self.assertEqual(len(ledger["unresolved_data_slots"]), 1)
        self.assertEqual(ledger["logical_row_count"], 0)

    def test_same_asset_and_same_number_in_separate_grids_are_not_merged(self):
        cells = ["1", "ALPHA", "purchase", "01/02/2026", "No", "$1,001 - $15,000"]
        ledger = self.observe(saved([cells, cells]))["source_report_ledger"]
        self.assertEqual(ledger["logical_row_count"], 2)
        self.assertNotEqual(*[row["local_key"] for row in ledger["logical_rows"]])

    def test_multiple_dates_are_not_one_complete_row(self):
        data = saved([["1", "ALPHA", "purchase", "01/02/2026 01/03/2026", "No", "$1,001 - $15,000"]])
        self.assertEqual(self.observe(data)["source_report_ledger"]["logical_row_count"], 0)

    def test_conflicting_saved_date_blocks_single_row_and_ambiguous_read_does_not_vote(self):
        data = saved()
        read = {"schema_version": "oge-278t-date-column-observation/v1", "source_sha256": "a" * 64,
                "ocr_engine": "synthetic-column", "ocr_resolution_dpi": 300, "input_ocr_resolution_dpi": 200,
                "summary": {"observed_slots": 1}, "pages": [{"page_number": 2,
                    "status": "date_column_observed", "slot_count": 1, "slots": [{
                        "slot_index": 1, "band_pdf_points": [100, 115],
                        "column_raw_ocr_text": "01/03/2026", "page_raw_ocr_text": "01/02/2026",
                        "column_shape": {"status": "ocr_shape_only", "value": "2026-01-03", "reason": None},
                        "column_detections": [{"text": "01/03/2026", "bbox_pdf_points": [81, 102, 98, 108]}]}]}]}
        out = observe_content_rows([data], document_page_count=4, secondary_observations=[read])
        self.assertEqual(out["source_report_ledger"]["logical_row_count"], 0)
        self.assertEqual(out["slot_content"][0]["disposition"], "data_candidate")
        read["pages"][0]["slots"][0]["column_detections"][0]["bbox_pdf_points"] = [70, 102, 109, 108]
        out = observe_content_rows([data], document_page_count=4, secondary_observations=[read])
        self.assertEqual(out["source_report_ledger"]["logical_row_count"], 1)
        self.assertEqual(out["field_observations"]["pages"][0]["slots"][0]["field_readings"]["transaction_date"]["geometry_ambiguous_observation_count"], 1)

    def test_existing_cli_consumes_content_ledger_without_changing_field_output(self):
        data = saved()
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / "input.json", Path(temp) / "output.json"
            source.write_text(json.dumps(data), encoding="utf-8")
            backend = Path(__file__).resolve().parents[1]
            subprocess.run([sys.executable, str(backend / "scripts/observe_oge_278t_fields.py"),
                            "--observations", str(source), "--document-page-count", "4", "--output", str(target)],
                           cwd=backend, env={**os.environ, "PYTHONPATH": str(backend / "src")},
                           check=True, capture_output=True)
            out = json.loads(target.read_text(encoding="utf-8"))
        previous = observe_field_shapes([data])
        self.assertEqual({key: out[key] for key in previous}, previous)
        self.assertEqual(out["source_report_ledger"]["logical_row_count"], 1)

    def test_geometry_and_scope_mismatches_fail(self):
        for mutate in (
            lambda d: d["pages"][0].update(physical_slot_count=2),
            lambda d: d["pages"][0]["slots"][0].update(slot_index=2),
            lambda d: d["pages"][0]["slots"][0].update(band_pdf_points=[float("nan"), 120]),
            lambda d: d["pages"][0]["slots"][0].update(band_pdf_points=[200, 215]),
            lambda d: d["pages"][0].update(page_number=5),
            lambda d: d.update(coordinate_frame="pixels"),
        ):
            data = saved()
            mutate(data)
            with self.assertRaises(ValueError):
                self.observe(data)

    def test_unknown_geometry_is_not_silent_zero_coverage(self):
        data = saved()
        data["pages"][0].update(column_geometry="unknown")
        ledger = self.observe(data)["source_report_ledger"]
        self.assertEqual(ledger["coverage_unknown_pages"], [1, 2, 3, 4])
        self.assertEqual(ledger["unobserved_pages"], [1, 3, 4])
        self.assertEqual(ledger["observed_physical_slot_count"], 1)
        self.assertEqual(ledger["disposition_counts"], {"unknown": 1})
        data["pages"][0]["physical_slot_count"] = 2
        with self.assertRaisesRegex(ValueError, "physical slot count"):
            self.observe(data)

    def test_original_word_rounding_at_boundary_is_explicit_and_not_linked(self):
        data = saved()
        data["pages"][0]["slots"][0]["words"][0]["bbox_pdf_points"][1:4:2] = [112, 118]
        out = self.observe(data)
        self.assertTrue(out["slot_content"][0]["word_center_rounding_ambiguous"])
        self.assertEqual(out["source_report_ledger"]["logical_row_count"], 0)

    def test_old_adapter_output_is_unchanged_and_unpositioned_rows_not_zipped(self):
        extraction, catalog = self.adapter_inputs([{"page_number": 2, "cells": ["1", "ALPHA", "purchase", "", "", ""]}])
        old = adapt_oge_278t_extraction(extraction, catalog)
        new = adapt_oge_278t_extraction(extraction, catalog, raster_observations=[saved()], document_page_count=4)
        self.assertEqual({key: new[key] for key in old}, old)
        self.assertEqual(new["source_report_ledger"]["logical_row_count"], 1)
        self.assertEqual(new["parser_row_relations"][0]["status"], "parser_row_geometry_unavailable")

    def test_parser_positions_keep_zero_one_many_and_unknown_pages(self):
        rows = [{"page_number": page, "cells": ["", "ALPHA", "", "", "", ""],
                 "geometry_top": top, "coordinate_frame": "pdf_points"}
                for page, top in [(2, 102), (2, 103), (2, 80), (1, 100)]]
        extraction, catalog = self.adapter_inputs(rows)
        out = adapt_oge_278t_extraction(extraction, catalog, raster_observations=[saved()], document_page_count=4)
        self.assertEqual([row["status"] for row in out["parser_row_relations"]],
                         ["multiple_parser_rows_in_slot", "multiple_parser_rows_in_slot",
                          "outside_observed_grid", "outside_observed_grid"])
        self.assertEqual(out["slot_parser_matches"][0]["source_row_indices"], [0, 1])
        extraction["source_rows"] = rows[:1]
        out = adapt_oge_278t_extraction(extraction, catalog, raster_observations=[saved()], document_page_count=4)
        self.assertEqual(out["parser_row_relations"][0]["status"], "geometry_match_only")
        self.assertTrue(all(row["qualification"] == "not_assessed" for row in out["source_report_ledger"]["logical_rows"]))
        extraction["source_rows"][0]["page_number"] = 5
        with self.assertRaisesRegex(OgeCatalogError, "page range"):
            adapt_oge_278t_extraction(extraction, catalog, raster_observations=[saved()], document_page_count=4)

    def test_foreign_source_missing_scope_and_unbound_secondary_fail(self):
        extraction, catalog = self.adapter_inputs([])
        other = saved()
        other["source_sha256"] = "b" * 64
        with self.assertRaises(OgeCatalogError):
            adapt_oge_278t_extraction(extraction, catalog, raster_observations=[other], document_page_count=4)
        with self.assertRaises(ValueError):
            adapt_oge_278t_extraction(extraction, catalog, raster_observations=[saved()])
        with self.assertRaises(OgeCatalogError):
            adapt_oge_278t_extraction(extraction, catalog, secondary_field_observations=[])


if __name__ == "__main__":
    unittest.main()
