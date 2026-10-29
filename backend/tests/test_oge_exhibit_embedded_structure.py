"""Frozen structure controls are synthetic, not financial source gold."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from test_oge_native_exhibit import observation, empty_source, bundle
from test_oge_candidate import direct
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_278t_exhibit_structure import (
    PARAMETERS, SCHEMA, detect_column_tracks, observe_embedded_exhibit_columns)
from unison_snapshot.oge_278t_page_coverage import interpret_native_exhibit_page
from unison_snapshot.oge_reports import materialize_saved_oge_278t_exhibits


EDGES = [5,60,240,310,380,450,590]
TRANSFORM = {"pixel_origin_pdf_points":[0.,0.],"pixel_step_pdf_points":[1.,1.]}


def pixels():
    a=np.full((800,600),255,dtype=np.uint8)
    for x in EDGES:
        for y in range(36,110,4):
            a[y:y+2,x]=0
    return a


def structured(a=None):
    obs=observation();a=pixels() if a is None else a
    # Freeze distinct, fully contained source word boxes for this synthetic
    # structure. The production parser never changes original boxes this way.
    for i in (9,):
        obs["words"][i]["x1"]=300.
    obs["words"][10]["x1"]=375.
    block={"table_index":0,"table_bounds":obs["table_bounds"][0],
           **detect_column_tracks(a,obs["table_bounds"][0],TRANSFORM)}
    obs["embedded_column_structure"]={"schema_version":SCHEMA,"source_sha256":obs["source_sha256"],
        "page_number":obs["page_number"],"parameters":deepcopy(PARAMETERS),
        "table_bounds":deepcopy(obs["table_bounds"]),"status":"observed","blocks":[block],
        "native_page_size_pdf_points":[600.,800.],"page_bbox_pdf":[0.,0.,600.,800.],
        "page_crop_box_pdf":[0.,0.,600.,800.],"page_media_box_pdf":[0.,0.,600.,800.],
        "decoded_pixels_sha256":hashlib.sha256(a.tobytes()).hexdigest()}
    return obs


class EmbeddedStructureTests(unittest.TestCase):
    def test_normal_and_frozen_missing_competing_and_word_stroke_controls(self):
        normal=structured()
        row=interpret_native_exhibit_page(normal)["blocks"][0]
        self.assertEqual(row["status"],"replacement_row_candidate")
        self.assertEqual(row["partition_crossing_word_indices"],[])
        self.assertIn("source_embedded",row["partition_basis"])
        self.assertEqual(sorted(i for refs in row["field_word_indices"].values() for i in refs),row["body_word_indices"])
        for kind in ("missing","competing","glyph"):
            with self.subTest(kind=kind):
                a=pixels()
                if kind in {"missing","glyph"}:a[:,237:244]=255
                if kind=="competing":a[36:110,280]=0
                if kind=="glyph":
                    image=Image.fromarray(a);draw=ImageDraw.Draw(image)
                    for y in (40,75):draw.text((238,y),"HI",fill=0)
                    a=np.asarray(image)
                result=interpret_native_exhibit_page(structured(a))["blocks"][0]
                self.assertEqual(result["status"],"unresolved")
                self.assertIn("native_embedded_column_structure_unknown",result["reasons"])

    def test_missing_real_track_plus_competing_false_track_refuses_by_roles(self):
        a=pixels();a[:,237:244]=255;a[36:110,280]=0
        obs=structured(a)
        self.assertEqual(len(obs["embedded_column_structure"]["blocks"][0]["tracks"]),7)
        row=interpret_native_exhibit_page(obs)["blocks"][0]
        self.assertEqual(row["reasons"],["native_column_tracks_header_roles_conflict"])

    def test_full_word_crossing_is_unknown_without_losing_raw_word(self):
        obs=structured();obs["words"][8].update(text="•",x0=239.,x1=243.)
        before=deepcopy(obs)
        row=interpret_native_exhibit_page(obs)["blocks"][0]
        self.assertIn(8,row["unassigned_body_word_indices"])
        self.assertIn(8,row["partition_crossing_word_indices"])
        self.assertNotIn(8,[i for refs in row["field_word_indices"].values() for i in refs])
        self.assertEqual(obs,before)

    def test_header_punctuation_overhang_does_not_change_body_box_rule(self):
        obs=structured();obs["words"][3]["x0"]=239.
        row=interpret_native_exhibit_page(obs)["blocks"][0]
        self.assertEqual(row["status"],"replacement_row_candidate")
        self.assertEqual(obs["words"][3]["x0"],239.)

    def test_header_role_center_in_wrong_column_is_unresolved(self):
        obs=structured();obs["words"][3].update(x0=210.,x1=239.)
        row=interpret_native_exhibit_page(obs)["blocks"][0]
        self.assertEqual(row["status"],"unresolved")
        self.assertTrue(row["all_word_indices"])
        self.assertEqual(row["label"]["target_printed_number"],27)

    def test_materializer_reobserves_opt_in_structure_and_rejects_tampering(self):
        obs=structured();old=empty_source()
        with patch("unison_snapshot.oge_278t_page_coverage.observe_native_exhibit_page",return_value=obs) as reopen:
            result=materialize_saved_oge_278t_exhibits(old,direct(),**bundle(obs))
        self.assertTrue(reopen.call_args.kwargs["embedded_column_structure"])
        self.assertEqual(result["observation_receipt"]["observed_slot_count"],1)
        self.assertEqual(len(result["quarantined"]),1)
        self.assertEqual(result["transactions"],[])
        for field in ("decoded_pixels_sha256","parameters","table_bounds"):
            bad=deepcopy(obs);bad["embedded_column_structure"][field]="tampered"
            with patch("unison_snapshot.oge_278t_page_coverage.observe_native_exhibit_page",return_value=obs),self.assertRaises(OgeCatalogError):
                materialize_saved_oge_278t_exhibits(old,direct(),**bundle(bad))

    def test_unknown_structure_does_not_fallback_or_become_zero(self):
        obs=structured();obs["embedded_column_structure"].update(status="unknown",blocks=[],reason="unique_embedded_image_not_available")
        with patch("unison_snapshot.oge_278t_page_coverage.observe_native_exhibit_page",return_value=obs):
            result=materialize_saved_oge_278t_exhibits(empty_source(),direct(),**bundle(obs))
        receipt=result["observation_receipt"]
        self.assertEqual(receipt["observed_slot_count"],1)
        self.assertEqual(receipt["unresolved_slot_count"],1)
        self.assertFalse(receipt["coverage_complete"])
        self.assertTrue(receipt["rows"][0]["all_word_indices"])
        self.assertEqual(result["source_rows"],[])

    def test_actual_ctm_not_canvas_dimensions_and_transform_failure(self):
        class FakeImage:
            def get_matrix(self):return SimpleNamespace(a=1200.,b=0.,c=0.,d=1600.,e=10.,f=20.)
            def get_bounds(self):return (10.,20.,1210.,1620.)
            def get_bitmap(self,render):
                self.render=render
                return SimpleNamespace(to_pil=lambda:Image.fromarray(pixels()))
        image=FakeImage()
        page=SimpleNamespace(get_objects=lambda:[image],get_bbox=lambda:(0.,0.,1300.,1700.),get_rotation=lambda:0,
            get_cropbox=lambda:(0.,0.,1300.,1700.),get_mediabox=lambda:(0.,0.,1300.,1700.))
        class Document:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def __len__(self):return 1
            def __getitem__(self,index):return page
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"source.pdf";path.write_bytes(b"source")
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            with patch("pypdfium2.PdfDocument",return_value=Document()),patch("pypdfium2.PdfImage",FakeImage):
                result=observe_embedded_exhibit_columns(path,source_sha256=sha,page_number=1,table_bounds=[],native_page_size=(1300.,1700.))
                self.assertEqual(result["image_to_pdf"]["pixel_origin_pdf_points"],[10.,80.])
                self.assertEqual(result["image_to_pdf"]["pixel_step_pdf_points"],[2.,2.])
                self.assertFalse(image.render)
                page.get_rotation=lambda:90
                bad=observe_embedded_exhibit_columns(path,source_sha256=sha,page_number=1,table_bounds=[],native_page_size=(1300.,1700.))
                self.assertEqual(bad["status"],"unknown")
                self.assertEqual(bad["reason"],"embedded_image_transform_not_supported")
                page.get_rotation=lambda:0
                page.get_cropbox=lambda:(10.,0.,1300.,1700.)
                mismatch=observe_embedded_exhibit_columns(path,source_sha256=sha,page_number=1,table_bounds=[],native_page_size=(1300.,1700.))
                self.assertEqual(mismatch["status"],"unknown")
                self.assertEqual(mismatch["reason"],"native_and_image_displayed_page_frames_differ")
                page.get_cropbox=page.get_bbox
                mismatch=observe_embedded_exhibit_columns(path,source_sha256=sha,page_number=1,table_bounds=[],native_page_size=(1200.,1600.))
                self.assertEqual(mismatch["status"],"unknown")


if __name__=="__main__":unittest.main()
