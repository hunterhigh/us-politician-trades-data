"""Bounded cell producer/consumer mechanisms; synthetic pixels are not OCR gold."""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import sys
import unittest
from unittest.mock import patch

from PIL import Image
from test_oge_content_relations import saved, EDGES
from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns
from unison_snapshot.oge_278t_type_column_shadow import observe_type_cells
from unison_snapshot.oge_278t_field_shadow import observe_content_rows


def primary(raw="salo", crossing=True):
    data=saved([["1","ALPHA",raw,"01/02/2026","No","$1,001 - $15,000"]])
    page=data["pages"][0]
    page["render_coordinate_frame"]="displayed_mediabox"
    words=page["slots"][0]["words"]
    words[1]["bbox_pdf_points"]=[11,102,40,108]
    words[2]["bbox_pdf_points"]=[69.7 if crossing else 71,102,76,108]
    page["slots"][0].update(_observe_slot_columns(words,EDGES,page=page))
    return data


def read(data,text="sale"):
    data=deepcopy(data)
    calls=[]
    class Engine:
        def __call__(self,image):
            calls.append(image.shape)
            return [[[[4.,2.],[24.,2.],[24.,10.],[4.,10.]],text,.99]],None
    image=Image.new("RGB",(625,834),"white")
    page=SimpleNamespace(width=150.,height=200.,bbox=(0,0,150.,200.),
                         to_image=lambda **kw:SimpleNamespace(original=image))
    document=SimpleNamespace(pages=[page,page])
    with tempfile.TemporaryDirectory() as folder:
        source=Path(folder)/"mock.pdf";source.write_bytes(b"synthetic source")
        data["source_sha256"]=hashlib.sha256(source.read_bytes()).hexdigest()
        with patch("pdfplumber.open") as opened, \
             patch.dict(sys.modules, {"rapidocr_onnxruntime": SimpleNamespace(RapidOCR=Engine)}), \
             patch("importlib.metadata.version", return_value="synthetic-test-engine"):
            opened.return_value.__enter__.return_value=document
            result=observe_type_cells(source,slots_observation=data,selected_slots={2:[1]},
                                      crop_directory=Path(folder)/"crops")
        assert len(list((Path(folder)/"crops").glob("*.png")))==1
    return data,result,calls


class TypeCellTests(unittest.TestCase):
    def test_raw_pixels_source_map_and_actual_consumer_supply_unrecognized_type(self):
        data,aux,calls=read(primary())
        self.assertEqual(len(calls),1)
        receipt=aux["pages"][0]["slots"][0]["cell_crop"]
        self.assertEqual(receipt["raw_crop_pixel_sha256"],receipt["derived_crop_pixel_sha256"])
        self.assertEqual(receipt["field_role"],"type")
        out=observe_content_rows([data],document_page_count=3,secondary_observations=[aux])
        self.assertEqual(out["source_report_ledger"]["adapter_version"], "oge-278t-content-rows/v5-type-cells")
        relation=out["slot_content"][0]["relation_evidence"]
        self.assertEqual(relation["status"],"supported_observation_relation")
        self.assertEqual(relation["field_geometry_evidence"]["description"]["basis"],
                         "bound_type_cell_and_unaffected_description_boxes")
        self.assertEqual(data["pages"][0]["slots"][0]["column_observation"]["type"]["raw_ocr_text"],"salo")

    def test_cell_field_reaches_existing_candidate_and_final_builder(self):
        from test_oge_observed_candidate import candidate, original
        from unison_snapshot.builder import build
        data=primary()
        slot=data["pages"][0]["slots"][0]
        slot["words"][3]["text"]="05/02/2025"
        slot.update(_observe_slot_columns(slot["words"],EDGES,page=data["pages"][0]))
        data,aux,_=read(data)
        old=original();old["source_sha256"]=data["source_sha256"]
        old_id=old["transactions"][0]["extraction_id"]
        result,audit=candidate({"observations":[data],"document_page_count":3,
                                "secondary_observations":[aux]},old=old)
        self.assertEqual(len(result["transactions"]),2)
        self.assertIn(old_id,{r["id"] for r in result["transactions"]})
        row=next(r for r in result["transactions"] if r["asset_name"]=="ALPHA")
        self.assertEqual(row["transaction_type"],"sale")
        self.assertEqual(row["owner"],"Undisclosed")
        self.assertTrue(build(result,generated_at="2025-06-04T00:00:00Z",
                              allow_production=True).manifest["snapshot_id"])

    def test_explicit_opposite_read_stays_conflict(self):
        data,aux,_=read(primary("purchase"))
        relation=observe_content_rows([data],document_page_count=3,
                                      secondary_observations=[aux])["slot_content"][0]["relation_evidence"]
        self.assertIn("type_observation_conflict",relation["blocking_reasons"])

    def test_description_assigned_crossing_word_cannot_be_excused_by_type_cell(self):
        data=primary();slot=data["pages"][0]["slots"][0]
        slot["words"][1]["bbox_pdf_points"]=[66,102,70.1,108]
        slot.update(_observe_slot_columns(slot["words"],EDGES,page=data["pages"][0]))
        data,aux,_=read(data)
        relation=observe_content_rows([data],document_page_count=3,
                                      secondary_observations=[aux])["slot_content"][0]["relation_evidence"]
        self.assertIn("cross_field_word_geometry",relation["blocking_reasons"])

    def test_empty_crop_read_does_not_resolve_unknown_or_bad_box(self):
        data,aux,_=read(primary(),text="salo")
        relation=observe_content_rows([data],document_page_count=3,
                                      secondary_observations=[aux])["slot_content"][0]["relation_evidence"]
        self.assertIn("type_observation_missing",relation["blocking_reasons"])
        self.assertIn("cross_field_word_geometry",relation["blocking_reasons"])

    def test_receipt_and_source_tampering_fail_before_fact_consumption(self):
        data,aux,_=read(primary())
        for kind in ["source","crop","scale","raw_sha","derived_sha","input","page","role","selection","summary","dpi"]:
            changed=deepcopy(aux);slot=changed["pages"][0]["slots"][0];r=slot["cell_crop"]
            if kind=="source":r["source_sha256"]="b"*64
            if kind=="crop":r["crop_pixel_bounds"][0]+=1
            if kind=="scale":r["pixel_to_pdf_scale"][0]+=.01
            if kind=="raw_sha":r["raw_crop_pixel_sha256"]="invalid"
            if kind=="derived_sha":r["derived_crop_pixel_sha256"]="b"*64
            if kind=="input":changed["input_observation_canonical_sha256"]="b"*64
            if kind=="page":changed["input_page_canonical_sha256"]["2"]="b"*64
            if kind=="role":r["field_role"]="description"
            if kind=="selection":changed["selected_slots"]["2"]=[2]
            if kind=="summary":changed["summary"]["ocr_invocation_count"]=0
            if kind=="dpi":r["source_render_pixel_size"][0]+=10
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                observe_content_rows([data],document_page_count=3,secondary_observations=[changed])

    def test_primary_domain_failure_is_not_repaired_by_reading_a_cell(self):
        data=primary()
        data["pages"][0]["slots"][0]["column_model_outside_word_indices"]=[0]
        with self.assertRaisesRegex(ValueError,"primary words exceed"):
            read(data)

    def test_bounded_selection_rejects_missing_slots_and_large_or_duplicate_request(self):
        for selected in [{}, {2:[1,1]}, {2:list(range(1,11))}, {True:[1]}]:
            with self.subTest(selected=selected),self.assertRaises(ValueError):
                observe_type_cells(Path("unused"),slots_observation=primary(),selected_slots=selected)
