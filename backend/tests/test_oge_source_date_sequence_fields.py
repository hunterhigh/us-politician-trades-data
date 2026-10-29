"""Explicit source-sequence consumer contracts with synthetic renderer/glyph proof."""
from copy import deepcopy
from contextlib import ExitStack
import hashlib,json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import test_oge_source_quad_cells as quad_tests
from unison_snapshot.oge_278t_source_quad_cells import observe_saved_source_quad_cells
from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns
from unison_snapshot.oge_278t_field_shadow import observe_content_rows,observe_field_shapes
from unison_snapshot.oge_278t_source_date_sequence_fields import MODE


def fixture(root):
    source,data,render,saved=quad_tests.SourceQuadCellTests().fixture(root)
    page=data['pages'][0];slot=page['slots'][0];word=slot['words'][3];word['text']='7/9/2025 '
    slot.update(_observe_slot_columns(slot['words'],page['column_edges_pdf_points'],page=page))
    cell=saved[0]['cells'][0];cell['tsv_utf8']=cell['tsv_utf8'].replace('06/01/2026','7/19/2025')
    cell['recipe']['tsv_sha256']=hashlib.sha256(cell['tsv_utf8'].encode()).hexdigest()
    record={'status':'source_slash_nine_supported','source_object_index':99,'raw_text':word['text'],
        'bbox_displayed_mediabox':word['bbox_pdf_points'],'matrix_pdf_bottom_left':[1,0,0,1,80,40],
        'slash_nine_glyph_indices':[1,2]}
    producer={'source_sha256':data['source_sha256'],'page_number':2,'source_text_manifest_sha256':'f'*64,'observations':[record]}
    binding={'source_object_index':99,'slot_index':1,'column_index':3,'source_object_manifest_sha256':'f'*64,
        'matrix_pdf_bottom_left':record['matrix_pdf_bottom_left']}
    return source,data,render,saved,producer,{'bindings':{'0':binding}}


def context(render,producer,visibility):
    stack=ExitStack();stack.enter_context(patch('unison_snapshot.pdf_text_objects.render_pdf_text_objects',return_value=render))
    stack.enter_context(patch('unison_snapshot.pdf_source_date_sequences.observe_source_date_sequences',return_value=producer))
    stack.enter_context(patch('unison_snapshot.pdf_text_baselines.source_text_baseline_bindings',return_value=visibility));return stack


def read(source,data,saved,engine='synthetic saved TSV',layer='source_text_objects_auxiliary'):
    return observe_saved_source_quad_cells(source,slots_observation=data,field='transaction_date',saved_pages=saved,layer=layer,ocr_engine=engine)


class SourceDateSequenceFieldTests(unittest.TestCase):
    def test_explicit_mode_preserves_raw_shapes_but_explains_auxiliary_extra_digit(self):
        with TemporaryDirectory() as folder:
            source,data,render,saved,producer,visibility=fixture(Path(folder));before=deepcopy(data)
            with context(render,producer,visibility):
                aux=read(source,data,saved)
                old=observe_content_rows([data],document_page_count=3,secondary_observations=[aux],source_pdf_path=source)
                new=observe_content_rows([data],document_page_count=3,secondary_observations=[aux],source_pdf_path=source,source_date_sequence_mode=MODE)
            self.assertIn('transaction_date_observation_conflict',old['slot_content'][0]['relation_evidence']['blocking_reasons'])
            self.assertEqual(new['slot_content'][0]['relation_evidence']['status'],'supported_observation_relation')
            oldrow=old['field_observations']['pages'][0]['slots'][0];row=new['field_observations']['pages'][0]['slots'][0]
            self.assertEqual(oldrow['field_shapes'],row['field_shapes']);self.assertEqual(data,before)
            reads=row['field_readings']['transaction_date']['observations']
            self.assertEqual(reads[1]['raw_ocr_text'],'7/19/2025');self.assertEqual(reads[1]['shape']['value'],'2025-07-19')
            self.assertIs(reads[1]['comparison_eligible'],False);self.assertEqual(reads[2]['shape']['value'],'2025-07-09')
            self.assertEqual(row['field_readings']['transaction_date']['recognized_observation_count'],3)
            self.assertEqual(row['field_readings']['transaction_date']['comparable_observation_count'],2)

    def test_another_valid_date_conflict_is_not_outvoted(self):
        with TemporaryDirectory() as folder:
            source,data,render,saved,producer,visibility=fixture(Path(folder));other=deepcopy(saved)
            cell=other[0]['cells'][0];cell['tsv_utf8']=cell['tsv_utf8'].replace('7/19/2025','7/29/2025');cell['recipe']['tsv_sha256']=hashlib.sha256(cell['tsv_utf8'].encode()).hexdigest()
            with context(render,producer,visibility):
                a=read(source,data,saved);b=read(source,data,other,engine='second recognizer')
                result=observe_content_rows([data],document_page_count=3,secondary_observations=[a,b],source_pdf_path=source,source_date_sequence_mode=MODE)
            self.assertIn('transaction_date_observation_conflict',result['slot_content'][0]['relation_evidence']['blocking_reasons'])

    def test_wrong_object_raw_box_manifest_visibility_or_unbound_field_cannot_resolve(self):
        for kind in ('raw','bbox','object','manifest','visibility','field','source','page'):
            with self.subTest(kind=kind),TemporaryDirectory() as folder:
                source,data,render,saved,producer,visibility=fixture(Path(folder))
                if kind=='raw':producer['observations'][0]['raw_text']='7/19/2025 '
                if kind=='bbox':producer['observations'][0]['bbox_displayed_mediabox']=[0,0,1,1]
                if kind=='object':producer['observations'][0]['source_object_index']=100
                if kind=='manifest':producer['source_text_manifest_sha256']='0'*64
                if kind=='visibility':visibility['bindings']={}
                if kind=='field':
                    p=data['pages'][0];s=p['slots'][0];s['words'][3]['bbox_pdf_points']=[71,102,78,108];s.update(_observe_slot_columns(s['words'],p['column_edges_pdf_points'],page=p))
                if kind=='source':producer['source_sha256']='0'*64
                if kind=='page':producer['page_number']=3
                with context(render,producer,visibility):
                    aux=read(source,data,saved)
                    if kind in ('source','page'):
                        with self.assertRaises(ValueError):observe_field_shapes([data],secondary_observations=[aux],source_pdf_path=source,source_date_sequence_mode=MODE)
                    else:
                        out=observe_field_shapes([data],secondary_observations=[aux],source_pdf_path=source,source_date_sequence_mode=MODE)
                        reads=out['pages'][0]['slots'][0]['field_readings']['transaction_date']['observations']
                        self.assertEqual(len(reads),2);self.assertNotIn('comparison_eligible',reads[1])

    def test_full_source_image_read_is_not_disproved_by_text_only_glyphs(self):
        with TemporaryDirectory() as folder:
            source,data,render,_,producer,visibility=fixture(Path(folder))
            # Build the full-source crop honestly; a different underlying image
            # is allowed to contain evidence absent from the text-only layer.
            _,_,_,saved=quad_tests.SourceQuadCellTests().fixture(Path(folder),layer='full_source')
            cell=saved[0]['cells'][0];cell['tsv_utf8']=cell['tsv_utf8'].replace('06/01/2026','7/19/2025');cell['recipe']['tsv_sha256']=hashlib.sha256(cell['tsv_utf8'].encode()).hexdigest()
            with context(render,producer,visibility):
                aux=read(source,data,saved,layer='full_source')
                out=observe_content_rows([data],document_page_count=3,secondary_observations=[aux],source_pdf_path=source,source_date_sequence_mode=MODE)
            self.assertIn('transaction_date_observation_conflict',out['slot_content'][0]['relation_evidence']['blocking_reasons'])

    def test_normal_materializer_candidate_builder_repeat_and_tamper_validation(self):
        from test_whitehouse_278t_saved_ocr import SavedWhiteHouseTests
        from test_whitehouse_278t_generic import consume
        from unison_snapshot.whitehouse_278t import materialize_saved_whitehouse_278t,DUAL_AXIS_MODE,validate_generic_whitehouse_report
        from unison_snapshot.builder import build
        from unison_snapshot.oge import OgeCatalogError
        with TemporaryDirectory() as folder:
            root=Path(folder);(root/'quad').mkdir();(root/'wh').mkdir()
            _,data,render,saved,producer,visibility=fixture(root/'quad');case=SavedWhiteHouseTests();inputs,_=case.fixture(root/'wh')
            source=Path(inputs['archived_pdf_path']);sha=hashlib.sha256(source.read_bytes()).hexdigest()
            data['source_sha256']=producer['source_sha256']=render['receipt']['source_sha256']=sha
            saved[0]['render_receipt']=deepcopy(render['receipt']);inputs['geometry_mode']=DUAL_AXIS_MODE
            with case.context(data),context(render,producer,visibility):
                auxiliary=read(source,data,saved);path=root/'secondary.json';raw=json.dumps([auxiliary]).encode();path.write_bytes(raw)
                inputs.update(secondary_observations_path=str(path),secondary_observations_sha256=hashlib.sha256(raw).hexdigest())
                before=materialize_saved_whitehouse_278t(inputs)
                enabled={**inputs,'source_date_sequence_mode':MODE};after=materialize_saved_whitehouse_278t(enabled)
                candidate,audit=consume(after);repeat,_=consume(after,candidate)
                self.assertEqual(candidate,repeat);self.assertEqual(len(candidate['transactions']),1)
                self.assertEqual(after['observation_receipt']['source_date_sequence_mode'],MODE)
                self.assertEqual(after['transactions'][0]['cells'],before['quarantined'][0]['cells'])
                self.assertEqual(after['transactions'][0]['extraction_id'],before['quarantined'][0]['extraction_id'])
                self.assertTrue(build(candidate,generated_at='2026-06-20T00:00:00Z',allow_production=True).manifest)
                changed=deepcopy(after)
                changed['content_observations']['field_observations']['pages'][0]['slots'][0]['field_readings']['transaction_date']['observations'][1]['comparison_eligible']=True
                with self.assertRaises(OgeCatalogError):validate_generic_whitehouse_report(changed)

    def test_invalid_mode_and_missing_source_are_rejected(self):
        with TemporaryDirectory() as folder:
            source,data,render,saved,producer,visibility=fixture(Path(folder))
            for kwargs in ({'source_date_sequence_mode':'unknown'}, {'source_date_sequence_mode':MODE},
                    {'source_date_sequence_mode':MODE,'source_pdf_path':Path(folder)/'missing.pdf'}):
                with self.assertRaises(ValueError):observe_field_shapes([data],**kwargs)

if __name__=='__main__':unittest.main()
