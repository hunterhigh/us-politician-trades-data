"""Synthetic source binding contracts; real source accuracy uses frozen receipts."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_oge_dual_axis_fields import dual_input, bind_secondary
from test_oge_content_relations import EDGES
from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns
from unison_snapshot.oge_278t_fields import observe_field_shapes, observe_content_rows
from unison_snapshot.oge_278t_source_glyph_fields import SOURCE_GLYPH_MODE


def fixture(root):
    source = root / "source.pdf"; source.write_bytes(b"synthetic source binding contract")
    data = dual_input(); data["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    page = data["pages"][0]; slot = page["slots"][0]
    slot["words"][3]["text"] = "7/1512025 "
    slot.update(_observe_slot_columns(slot["words"], EDGES, page=page))
    record = {"source_object_index": 99, "raw_text": "7/1512025 ", "interpreted_text": "7/15/2025 ",
              "status": "interpreted", "allowed_field_roles": ["transaction_date"],
              "bbox_displayed_mediabox": slot["words"][3]["bbox_pdf_points"],
              "matrix_pdf_bottom_left": [1, 0, 0, 1, 82, 90]}
    observed = {"source_sha256": data["source_sha256"], "page_number": 2,
                "source_text_manifest_sha256": "source-manifest", "observations": [record]}
    visibility = {"bindings": {"0": {"source_object_index": 99, "slot_index": 1, "column_index": 3,
        "source_object_manifest_sha256": "source-manifest", "matrix_pdf_bottom_left": record["matrix_pdf_bottom_left"]}}}
    return source, data, observed, visibility


def source_context(observed, visibility):
    stack = ExitStack()
    stack.enter_context(patch("unison_snapshot.pdf_source_glyphs.observe_source_glyph_interpretations", return_value=observed))
    stack.enter_context(patch("unison_snapshot.pdf_text_baselines.source_text_baseline_bindings", return_value=visibility))
    return stack


class SourceGlyphFieldTests(unittest.TestCase):
    def test_opt_in_appends_read_raw_primary_and_normal_relation_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            source, data, observed, visibility = fixture(Path(folder)); before = deepcopy(data)
            old = observe_field_shapes([data])
            with source_context(observed, visibility):
                content = observe_content_rows([data], document_page_count=3,
                    source_pdf_path=source, source_glyph_mode=SOURCE_GLYPH_MODE)
            field = content["field_observations"]["pages"][0]["slots"][0]
            self.assertEqual(field["field_shapes"], old["pages"][0]["slots"][0]["field_shapes"])
            self.assertEqual(field["raw_columns"], old["pages"][0]["slots"][0]["raw_columns"])
            reads = field["field_readings"]["transaction_date"]["observations"]
            self.assertEqual(len(reads), 2)
            self.assertEqual(reads[1]["method"], "source_glyph_interpretation")
            self.assertEqual(reads[1]["shape"]["value"], "2025-07-15")
            self.assertEqual(reads[1]["raw_ocr_text"], "7/1512025 ")
            self.assertEqual(content["slot_content"][0]["relation_evidence"]["status"], "supported_observation_relation")
            self.assertEqual(data, before)
            self.assertEqual(old, observe_field_shapes([data], source_glyph_mode=None))
            self.assertEqual(content, json.loads(json.dumps(content)))

    def test_another_recognized_date_conflict_stays_unresolved(self):
        with tempfile.TemporaryDirectory() as folder:
            source, data, observed, visibility = fixture(Path(folder))
            other = bind_secondary(data, "transaction_date", "07/16/2025")
            with source_context(observed, visibility):
                content = observe_content_rows([data], document_page_count=3, secondary_observations=[other],
                    source_pdf_path=source, source_glyph_mode=SOURCE_GLYPH_MODE)
            evidence = content["slot_content"][0]["relation_evidence"]
            self.assertIn("transaction_date_observation_conflict", evidence["blocking_reasons"])
            self.assertEqual(evidence["status"], "unresolved")

    def test_wrong_raw_box_object_field_manifest_and_unknown_visibility_not_supported(self):
        for kind in ("raw", "box", "object", "field", "manifest", "visibility"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                source, data, observed, visibility = fixture(Path(folder))
                if kind == "raw": observed["observations"][0]["raw_text"] += "changed"
                if kind == "box": observed["observations"][0]["bbox_displayed_mediabox"] = [82.01, 102, 93, 108]
                if kind == "object": visibility["bindings"]["0"]["source_object_index"] = 100
                if kind == "field": visibility["bindings"]["0"]["column_index"] = 2
                if kind == "manifest": visibility["bindings"]["0"]["source_object_manifest_sha256"] = "changed"
                if kind == "visibility": visibility["bindings"] = {}
                with source_context(observed, visibility):
                    result = observe_field_shapes([data], source_pdf_path=source, source_glyph_mode=SOURCE_GLYPH_MODE)
                self.assertEqual(len(result["pages"][0]["slots"][0]["field_readings"]["transaction_date"]["observations"]), 1)
                self.assertTrue(result["source_glyph_rejections"])

    def test_entire_field_quad_is_required_and_baseline_cannot_clear_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            source, data, observed, visibility = fixture(Path(folder)); page = data["pages"][0]; slot = page["slots"][0]
            box = [99, 102, 100.1, 108]
            slot["words"][3]["bbox_pdf_points"] = box
            slot.update(_observe_slot_columns(slot["words"], EDGES, page=page))
            observed["observations"][0]["bbox_displayed_mediabox"] = box
            with source_context(observed, visibility):
                result = observe_field_shapes([data], source_pdf_path=source, source_glyph_mode=SOURCE_GLYPH_MODE)
            self.assertIn("source_Date_word_not_fully_inside_actual_field_quad", [r["reason"] for r in result["source_glyph_rejections"]])

    def test_fragment_never_supplies_date_and_wrong_source_mode_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source, data, observed, visibility = fixture(Path(folder))
            observed["observations"][0]["interpreted_text"] = "5/202"
            with source_context(observed, visibility):
                result = observe_field_shapes([data], source_pdf_path=source, source_glyph_mode=SOURCE_GLYPH_MODE)
            self.assertEqual(result["source_glyph_field_summary"]["unsupported_or_fragment_records"], 1)
            for kwargs in ({"source_glyph_mode": "unknown"}, {"source_glyph_mode": SOURCE_GLYPH_MODE},
                           {"source_glyph_mode": SOURCE_GLYPH_MODE, "source_pdf_path": Path(folder) / "missing.pdf"}):
                with self.assertRaises(ValueError): observe_field_shapes([data], **kwargs)
            source.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "differs"):
                observe_field_shapes([data], source_glyph_mode=SOURCE_GLYPH_MODE, source_pdf_path=source)

    def test_materializer_consumes_interpreted_date_without_changing_raw_cells_or_id(self):
        from test_whitehouse_278t_saved_ocr import SavedWhiteHouseTests
        from unison_snapshot.whitehouse_278t import materialize_saved_whitehouse_278t, DUAL_AXIS_MODE
        from test_whitehouse_278t_generic import consume
        from unison_snapshot.builder import build
        case = SavedWhiteHouseTests()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / "glyph").mkdir(); (root / "wh").mkdir()
            _, data, observed, visibility = fixture(root / "glyph")
            inputs, _ = case.fixture(root / "wh"); inputs["geometry_mode"] = DUAL_AXIS_MODE
            metadata = json.loads(Path(inputs["archive_metadata_path"]).read_bytes())
            data["source_sha256"] = observed["source_sha256"] = metadata["sha256"]
            with case.context(data): before = materialize_saved_whitehouse_278t(inputs)
            enabled = {**inputs, "source_glyph_mode": SOURCE_GLYPH_MODE}
            with case.context(data), source_context(observed, visibility):
                after = materialize_saved_whitehouse_278t(enabled)
                candidate, audit = consume(after)
                again, _ = consume(after, candidate)
            old_row = (before["transactions"] + before["quarantined"])[0]
            new_row = (after["transactions"] + after["quarantined"])[0]
            self.assertEqual(old_row["extraction_id"], new_row["extraction_id"])
            self.assertEqual(old_row["cells"], new_row["cells"])
            self.assertEqual(new_row["transaction_date"], "2025-07-15")
            self.assertEqual(candidate["transactions"], again["transactions"])
            self.assertEqual(len(candidate["transactions"]), 1)
            self.assertEqual(after["observation_receipt"]["source_glyph_mode"], SOURCE_GLYPH_MODE)
            self.assertTrue(build(candidate, generated_at="2026-06-20T00:00:00Z", allow_production=True).manifest)
