"""Synthetic description provenance and OCR integration; not accuracy evidence."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_oge_content_relations import saved
from unison_snapshot.oge_278t_field_shadow import observe_content_rows, observe_field_shapes
from unison_snapshot.oge_278t_type_column_shadow import observe_description_column, observe_type_column


def word(text, left=1, top=2, right=25, bottom=8):
    return {"text": text, "x0": left, "x1": right, "top": top, "bottom": bottom, "ocr_confidence": 92.}


class DescriptionColumnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdf = self.root / "source.pdf"
        self.pdf.write_bytes(b"synthetic PDF envelope, rendering is mocked")
        self.base = saved()
        self.base["source_sha256"] = hashlib.sha256(self.pdf.read_bytes()).hexdigest()

    def produce(self, words=None, field="description", base=None, page_numbers=None):
        crops = []
        def crop(bounds):
            crops.append(bounds)
            return SimpleNamespace(to_image=lambda **kwargs: SimpleNamespace(original=object()))
        page = SimpleNamespace(height=200, crop=crop)
        document = SimpleNamespace(pages=[page, page, page])
        opened = unittest.mock.MagicMock()
        opened.__enter__.return_value = document
        ocr = SimpleNamespace(extract_words=lambda: words if words is not None else [word("ALPHA")])
        fn = observe_description_column if field == "description" else observe_type_column
        with patch.dict(sys.modules, {"pdfplumber": SimpleNamespace(open=lambda _: opened)}), patch(
                "unison_snapshot.oge_278t_type_column_shadow.ocr_rendered_image", return_value=(ocr, "synthetic-column")):
            kwargs = {"page_numbers": page_numbers} if field == "description" else {}
            result = fn(self.pdf, slots_observation=base or self.base, **kwargs)
        self.assertEqual(crops[0][0:3:2], (10.6, 68.5) if field == "description" else (70.6, 78.5))
        return result

    def test_complete_description_is_bound_and_does_not_change_primary(self):
        reading = self.produce()
        before = deepcopy((self.base, reading))
        out = observe_content_rows([self.base], document_page_count=2, secondary_observations=[reading])
        self.assertEqual(out["source_report_ledger"]["logical_row_count"], 1)
        field = out["field_observations"]["pages"][0]["slots"][0]["field_readings"]["description"]
        self.assertEqual(field["consistency"], "matching_observations")
        self.assertEqual(field["observations"][1]["shape"]["value"], "ALPHA")
        self.assertEqual((self.base, reading), before)
        self.assertEqual(out["field_observations"]["summary"], observe_field_shapes([self.base])["summary"])

    def test_multiline_physical_order_keeps_original_detection_indices(self):
        base = deepcopy(self.base)
        base["pages"][0]["slots"][0]["column_observation"]["description"]["raw_ocr_text"] = "ALPHA"
        reading = self.produce([word("BETA", 26, 1.8, 45, 6), word("ALPHA", 1, 2, 25, 6.1),
                                word("SERIES-A", 1, 8, 35, 12)], base=base)
        slot = reading["pages"][0]["slots"][0]
        self.assertEqual(slot["column_raw_ocr_text"], "BETA ALPHA SERIES-A")
        self.assertEqual(slot["column_reading"]["ordered_ocr_text"], "ALPHA BETA SERIES-A")
        self.assertEqual(slot["column_reading"]["line_word_indices"], [[1, 0], [2]])
        self.assertEqual(slot["column_shape"]["value"], "ALPHA BETA SERIES-A")
        out = observe_field_shapes([base], secondary_observations=[reading])
        field = out["pages"][0]["slots"][0]["field_readings"]["description"]
        self.assertEqual(field["consistency"], "conflicting_observations")

    def test_case_spacing_and_financial_characters_are_not_silently_corrected(self):
        for text in ["Alpha", "AL PHA", "ALPHA-5.25%-2031-A"]:
            with self.subTest(text=text):
                reading = self.produce([word(text)])
                out = observe_content_rows([self.base], document_page_count=2, secondary_observations=[reading])
                self.assertIn("description_observation_conflict", out["slot_content"][0]["relation_evidence"]["blocking_reasons"])
                self.assertEqual(out["source_report_ledger"]["logical_row_count"], 0)

    def test_unassigned_text_retains_coordinates_and_blocks_auxiliary_geometry(self):
        reading = self.produce([word("ALPHA"), word("SPILL", 1, -4, 25, 8)])
        page = reading["pages"][0]
        self.assertEqual(page["words_outside_slots"], 1)
        self.assertEqual(page["unassigned_detections"][0]["text"], "SPILL")
        self.assertEqual(page["slots"][0]["unassigned_detection_indices"], [0])
        out = observe_field_shapes([self.base], secondary_observations=[reading])
        obs = out["pages"][0]["slots"][0]["field_readings"]["description"]["observations"][1]
        self.assertEqual(obs["geometry_status"], "ambiguous")
        self.assertEqual(obs["unassigned_detections"], page["unassigned_detections"])
        self.assertIn("unassigned_description_detections_overlap_slot", obs["geometry_issues"])

    def test_empty_description_is_not_blank_evidence(self):
        reading = self.produce([])
        slot = reading["pages"][0]["slots"][0]
        self.assertEqual(slot["column_shape"], {"status": "unknown", "value": None})
        out = observe_field_shapes([self.base], secondary_observations=[reading])
        self.assertEqual(out["pages"][0]["slots"][0]["field_readings"]["description"]["consistency"],
                         "single_recognized_observation")

    def test_stale_input_reading_order_or_unassigned_references_rejected(self):
        reading = self.produce([word("ALPHA"), word("SPILL", 1, -4, 25, 8)])
        for mutate in [
            lambda r: r.update(input_page_canonical_sha256={"2": "b" * 64}),
            lambda r: r["pages"][0]["slots"][0]["column_reading"].update(ordered_ocr_text="BETA"),
            lambda r: r["pages"][0]["slots"][0].update(unassigned_detection_indices=[]),
            lambda r: r["pages"][0].update(words_outside_slots=0),
            lambda r: r["pages"][0]["unassigned_detections"][0].update(reason="outside_slot_bands"),
        ]:
            bad = deepcopy(reading)
            mutate(bad)
            with self.assertRaises(ValueError):
                observe_field_shapes([self.base], secondary_observations=[bad])

    def test_type_entry_keeps_legacy_contract(self):
        reading = self.produce([word("purchase", right=6), {"top": 160, "bottom": 170}], field="type")
        self.assertEqual(reading["schema_version"], "oge-278t-type-column-observation/v1")
        self.assertNotIn("input_observation_canonical_sha256", reading)
        self.assertNotIn("unassigned_detections", reading["pages"][0])
        self.assertEqual(reading["summary"], {"matching_shapes": 1, "observed_slots": 1})
        self.assertEqual(reading["pages"][0]["slots"][0]["column_shape"], "purchase")
        self.assertEqual(reading["pages"][0]["words_outside_slots"], 1)

    def test_page_bound_cache_can_be_reused_after_primary_batch_regrouping(self):
        grouped = deepcopy(self.base)
        extra = deepcopy(grouped["pages"][0])
        extra["page_number"] = 3
        grouped["pages"].append(extra)
        reading = self.produce(base=grouped, page_numbers=[2])
        self.assertEqual([p["page_number"] for p in reading["pages"]], [2])
        self.assertEqual(set(reading["input_page_canonical_sha256"]), {"2"})
        out = observe_field_shapes([self.base], secondary_observations=[reading])
        self.assertEqual(out["pages"][0]["slots"][0]["field_readings"]["description"]["consistency"], "matching_observations")

    def test_center_rounding_cannot_silently_move_description_to_next_slot(self):
        reading = self.produce([word("ALPHA"), word("EDGE", 1, 12, 25, 17.999)])
        item = reading["pages"][0]["unassigned_detections"][0]
        self.assertEqual(item["reason"], "center_rounding_ambiguous")
        out = observe_field_shapes([self.base], secondary_observations=[reading])
        obs = out["pages"][0]["slots"][0]["field_readings"]["description"]["observations"][1]
        self.assertEqual(obs["geometry_status"], "ambiguous")

    def test_cli_reuses_saved_description_without_executing_ocr(self):
        reading = self.produce()
        primary, secondary, target = [self.root / name for name in ["primary.json", "description.json", "out.json"]]
        primary.write_text(json.dumps(self.base), encoding="utf-8")
        secondary.write_text(json.dumps(reading), encoding="utf-8")
        backend = Path(__file__).resolve().parents[1]
        subprocess.run([sys.executable, str(backend / "scripts/observe_oge_278t_fields.py"),
                        "--observations", str(primary), "--read-description-pdf", str(self.pdf),
                        "--description-output", str(secondary), "--description-executable", "does-not-exist",
                        "--document-page-count", "2", "--output", str(target)], cwd=backend,
                       env={**os.environ, "PYTHONPATH": str(backend / "src")}, check=True, capture_output=True)
        out = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(out["source_report_ledger"]["logical_row_count"], 1)
        self.assertEqual(json.loads(secondary.read_text()), reading)


if __name__ == "__main__":
    unittest.main()
