"""Synthetic field provenance and conflict tests; not OCR accuracy evidence."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from unison_snapshot.oge_278t_field_shadow import observe_field_shapes
from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns


EDGES = [0, 10, 70, 80, 100, 110, 150]


def original():
    words = [{"text": text, "bbox_pdf_points": box} for text, box in (
        ("1", [1, 102, 4, 108]), ("ALPHA", [20, 102, 40, 108]),
        ("purchase", [69.9, 102, 79, 108]), ("01/02/2026", [81, 102, 99, 108]),
        ("No|$15,001", [106, 102, 122, 108]), ("-", [123, 102, 124, 108]),
        ("$50,000", [125, 102, 148, 108]), ("|", [109.8, 110, 110.2, 112]),
        ("outside", [155, 102, 165, 108]))]
    slot = {"slot_index": 1, "band_pdf_points": [100, 115], "words": words,
            "raw_ocr_text": " ".join(word["text"] for word in words),
            **_observe_slot_columns(words, EDGES)}
    return {"schema_version": "oge-278t-raster-slot-observation/v2",
            "source_sha256": "a" * 64, "ocr_engine": "test-page-engine", "ocr_resolution_dpi": 200,
            "pages": [{"page_number": 2, "column_geometry": "oge_2024_six_column_grid",
                       "column_edges_pdf_points": EDGES, "physical_slot_count": 1, "slots": [slot]}]}


def secondary(base, field="transaction_date", raw="01/02/2026"):
    from unison_snapshot.oge_278t_field_shadow import _date_shape, _amount_shape
    columns = base["pages"][0]["slots"][0]["column_observation"]
    names = {"transaction_date": ["transaction_date"], "type": ["type"],
             "amount": ["notification", "amount"]}[field]
    first, last = {"transaction_date": (81, 98), "type": (71, 78), "amount": (101, 148)}[field]
    box = [first, 102, last, 108]
    shape = {"transaction_date": _date_shape, "amount": _amount_shape}.get(field)
    slot = {"slot_index": 1, "band_pdf_points": [100, 115], "column_raw_ocr_text": raw,
            "page_raw_ocr_text": " ".join(columns[name]["raw_ocr_text"] for name in names),
            "column_shape": shape(raw) if shape else raw.casefold(),
            "column_words" if field == "type" else "column_detections": [
                {"text": raw, "bbox_pdf_points": box, "confidence": .9}],
            "qualification": "not_assessed"}
    kind, status = {"transaction_date": ("date", "date"), "amount": ("amount", "amount"),
                    "type": ("type", "type")}[field]
    return {"schema_version": f"oge-278t-{kind}-column-observation/v{'2' if kind == 'amount' else '1'}",
            "source_sha256": base["source_sha256"], "ocr_engine": "test-column-engine",
            "ocr_resolution_dpi": 300, "input_ocr_resolution_dpi": 200,
            "summary": {"observed_slots": 1},
            "pages": [{"page_number": 2, "status": status + "_column_observed", "slot_count": 1,
                       "slots": [slot]}]}


class LinkedFieldEvidenceTests(unittest.TestCase):
    def row(self, base=None, readings=None):
        return observe_field_shapes([base or original()], secondary_observations=readings)["pages"][0]["slots"][0]

    def test_word_geometry_warnings_have_field_scope_and_keep_joined_token(self):
        base = original()
        before = deepcopy(base)
        row = self.row(base)
        crossing = row["word_evidence"]["crossing_words"]
        self.assertEqual([item["text"] for item in crossing], ["purchase", "No|$15,001", "|"])
        self.assertEqual(crossing[0]["boundaries"][0]["shorter_overhang_pdf_points"], .1)
        self.assertEqual(row["word_evidence"]["unassigned_word_indices"], [8])
        amount = row["field_readings"]["amount"]
        self.assertEqual(amount["observations"][0]["raw_columns"], ["notification", "amount"])
        self.assertTrue(all(w["scope"] == "within_combined_columns" for w in amount["geometry_warnings"]))
        self.assertEqual(row["field_readings"]["transaction_date"]["geometry_warnings"], [])
        self.assertEqual(row["field_shapes"]["type"]["value"], "purchase")
        self.assertIn("boundary_crossing_words", row["issues"])
        self.assertEqual(base, before)

    def test_non_template_amount_remains_explicit_without_correcting_digits(self):
        base = original()
        base["pages"][0]["slots"][0]["words"][4]["text"] = "No|$15,002"
        slot = base["pages"][0]["slots"][0]
        slot.update(_observe_slot_columns(slot["words"], EDGES))
        reading = self.row(base)["field_readings"]["amount"]["observations"][0]
        self.assertEqual(reading["shape"]["values"], [15002, 50000])
        self.assertEqual(reading["template_category"]["status"], "non_template_numeric_shape")

    def test_saved_column_indices_cannot_silently_lose_a_word(self):
        base = original()
        base["pages"][0]["slots"][0]["column_observation"]["type"]["word_indices"] = []
        with self.assertRaisesRegex(ValueError, "word references"):
            self.row(base)

    def test_base_engine_resolution_and_source_positions_are_retained(self):
        result = observe_field_shapes([original()])
        reading = result["pages"][0]["slots"][0]["field_readings"]["type"]["observations"][0]
        self.assertEqual((reading["engine"], reading["resolution_dpi"]), ("test-page-engine", 200))
        self.assertEqual(reading["source_ref"], "observations/0/pages/0/slots/0")
        self.assertEqual(len(result["input_refs"][0]["canonical_content_sha256"]), 64)
        self.assertFalse(result["production_mutation_authorized"])

    def test_matching_and_conflicting_reads_are_both_retained_without_selection(self):
        base = original()
        matching = secondary(base)
        conflicting = secondary(base, raw="01/03/2026")
        conflicting["ocr_resolution_dpi"] = 400
        row = self.row(base, [matching, conflicting])
        field = row["field_readings"]["transaction_date"]
        self.assertEqual(field["consistency"], "conflicting_observations")
        self.assertEqual(len(field["observations"]), 3)
        self.assertEqual(row["field_shapes"]["transaction_date"]["value"], "2026-01-02")
        self.assertNotIn("selected_value", field)
        self.assertEqual(row["qualification"], "not_assessed")

    def test_amount_and_type_reads_join_existing_row(self):
        base = original()
        row = self.row(base, [secondary(base, "amount", "$15,001 - $50,000"),
                              secondary(base, "type", "purchase")])
        self.assertEqual(row["field_readings"]["amount"]["consistency"], "matching_observations")
        self.assertEqual(row["field_readings"]["type"]["consistency"], "matching_observations")

    def test_old_amount_observation_schema_joins_without_fabricating_a_new_read(self):
        base = original()
        read = secondary(base, "amount", "$15,001 - $50,000")
        read["schema_version"] = "oge-278t-amount-column-observation/v1"
        field = self.row(base, [read])["field_readings"]["amount"]
        self.assertEqual(field["observation_count"], 2)
        self.assertEqual(field["observations"][1]["template_category"]["status"],
                         "bounded_template_numeric_shape")

    def test_unknown_schema_and_foreign_or_duplicate_page_are_rejected(self):
        for kind in ("schema", "foreign_page", "duplicate_page"):
            with self.subTest(kind=kind):
                base = original()
                read = secondary(base)
                if kind == "schema": read["schema_version"] = "oge-278t-date-column-observation/v99"
                if kind == "foreign_page": read["pages"][0]["page_number"] = 3
                if kind == "duplicate_page": read["pages"] *= 2
                with self.assertRaises(ValueError):
                    self.row(base, [read])

    def test_source_band_original_text_and_input_resolution_must_match(self):
        for kind in ("source", "band", "text", "resolution"):
            with self.subTest(kind=kind):
                base = original()
                read = secondary(base)
                if kind == "source": read["source_sha256"] = "b" * 64
                if kind == "band": read["pages"][0]["slots"][0]["band_pdf_points"] = [101, 116]
                if kind == "text": read["pages"][0]["slots"][0]["page_raw_ocr_text"] = "other"
                if kind == "resolution": read["input_ocr_resolution_dpi"] = 300
                with self.assertRaises(ValueError):
                    self.row(base, [read])

    def test_missing_duplicate_and_foreign_slots_cannot_hide_behind_counts(self):
        for kind in ("missing", "duplicate", "foreign"):
            with self.subTest(kind=kind):
                base = original()
                read = secondary(base)
                if kind == "missing": read["pages"][0]["slots"] = []
                if kind == "duplicate": read["pages"][0]["slots"] *= 2
                if kind == "foreign": read["pages"][0]["slots"][0]["slot_index"] = 2
                with self.assertRaisesRegex(ValueError, "slots are missing"):
                    self.row(base, [read])

    def test_summary_and_duplicate_read_identity_are_checked(self):
        base = original()
        read = secondary(base)
        with self.assertRaisesRegex(ValueError, "identity repeated"):
            self.row(base, [read, deepcopy(read)])
        read["summary"]["observed_slots"] = 2
        with self.assertRaisesRegex(ValueError, "summary differs"):
            self.row(base, [read])

    def test_stale_shape_and_detection_from_other_field_or_row_are_rejected(self):
        for kind in ("shape", "row", "field", "raw"):
            with self.subTest(kind=kind):
                base = original()
                read = secondary(base)
                slot = read["pages"][0]["slots"][0]
                if kind == "shape": slot["column_shape"]["value"] = "2026-01-04"
                if kind == "row": slot["column_detections"][0]["bbox_pdf_points"] = [81, 120, 99, 128]
                if kind == "field": slot["column_detections"][0]["bbox_pdf_points"] = [20, 102, 40, 108]
                if kind == "raw": slot["column_detections"][0]["text"] = "01/04/2026"
                with self.assertRaises(ValueError):
                    self.row(base, [read])

    def test_centered_but_oversized_box_is_retained_without_counting_as_agreement(self):
        for box in ([0, 0, 180, 210], [81, 95, 98, 120]):
            with self.subTest(box=box):
                base = original()
                read = secondary(base)
                read["pages"][0]["slots"][0]["column_detections"][0]["bbox_pdf_points"] = box
                field = self.row(base, [read])["field_readings"]["transaction_date"]
                self.assertEqual(field["consistency"], "single_recognized_observation")
                self.assertEqual(field["recognized_observation_count"], 2)
                self.assertEqual(field["comparable_observation_count"], 1)
                self.assertEqual(field["geometry_ambiguous_observation_count"], 1)
                self.assertEqual(field["observations"][1]["geometry_status"], "ambiguous")
                self.assertEqual(field["observations"][1]["detections"][0]["bbox_pdf_points"], box)

    def test_secondary_unknown_page_stays_unknown_and_cannot_carry_rows(self):
        base = original()
        read = secondary(base)
        read["pages"][0].update(status="geometry_unknown", slots=[])
        read["summary"]["observed_slots"] = 0
        result = observe_field_shapes([base], secondary_observations=[read])
        self.assertEqual(result["secondary_geometry_unknown_pages"][0]["status"], "geometry_unknown")
        self.assertEqual(result["pages"][0]["slots"][0]["field_readings"]["transaction_date"][
            "consistency"], "single_recognized_observation")
        read["pages"][0]["slots"] = [{}]
        with self.assertRaisesRegex(ValueError, "unknown secondary geometry"):
            self.row(base, [read])

    def test_existing_cli_consumes_saved_secondary_inputs_without_ocr(self):
        repo = Path(__file__).resolve().parents[2]
        base = original()
        read = secondary(base)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first, second, output = [root / name for name in ("first.json", "second.json", "output.json")]
            first.write_text(json.dumps(base), encoding="utf-8")
            second.write_text(json.dumps(read), encoding="utf-8")
            process = subprocess.run([sys.executable, str(repo / "backend/scripts/observe_oge_278t_fields.py"),
                "--observations", str(first), "--secondary-observations", str(second),
                "--output", str(output)], capture_output=True, text=True,
                env=dict(os.environ, PYTHONPATH=str(repo / "backend/src")))
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")),
                             observe_field_shapes([base], secondary_observations=[read]))
            self.assertIn("secondary_field_summary", json.loads(process.stdout))


if __name__ == "__main__":
    unittest.main()
