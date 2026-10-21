"""Dual-axis consumers with synthetic geometry and mocked OCR, never real pages."""
from copy import deepcopy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from PIL import Image
from test_oge_278t_column_lines import modeled
from test_oge_content_relations import column_read, EDGES, saved
from unison_snapshot.oge_278t_raster_slots import (
    DUAL_MODE, _observe_slot_columns, slot_quad, table_crop_bounds, column_geometry_sha256)
from unison_snapshot.oge_278t_field_shadow import (observe_field_shapes, observe_content_rows,
    compare_field_observations, _secondary_slot_geometry, _type_cell_pdf_bounds)
from unison_snapshot.oge_278t_type_column_shadow import observe_description_column, observe_type_column, observe_type_cells
from unison_snapshot.oge_278t_date_column_shadow import observe_date_column
from unison_snapshot.oge_278t_amount_column_shadow import observe_amount_column


def dual_input(row_slope=.01, column_slope=.005):
    data=modeled(column_slope);page=data['pages'][0]
    spans=[[1.,3.],[74.,76.],[147.,149.]];xs=[2.,75.,148.];ref=75.
    model={'schema_version':'oge-278t-row-lines/v1','coordinate_frame':'displayed_mediabox',
        'reference_x_pdf_points':ref,'valid_x_span_pdf_points':[-2.,152.],
        'intercepts_y_pdf_points':[100.,115.],'slopes_dy_per_dx':[row_slope,row_slope],
        'line_observations':[{'samples':[{'x_span_pdf_points':span,'y_pdf_points':b+row_slope*(x-ref)}
            for span,x in zip(spans,xs)],'maximum_fit_residual_pdf_points':0.} for b in [100.,115.]]}
    data.update(schema_version='oge-278t-raster-slot-observation/v3',geometry_mode=DUAL_MODE)
    page.update(geometry_mode=DUAL_MODE,row_line_model=model,physical_slot_count=None,
        partial_row_geometry=True,known_physical_slot_count=1,whole_page_physical_slot_count=None,
        unresolved_regions=[],unbound_page_ocr_words=[])
    page['column_geometry_observation'].update(row_line_model=deepcopy(model),partial_row_geometry=True,
        physical_row_bands=[[100,115]],unresolved_regions=[])
    slot=page['slots'][0]
    slot.update(row_boundary_refs=[0,1],band_coordinate_basis='row_lines_at_reference_x')
    slot['quad_pdf_points']=slot_quad(page,slot)
    # Preserve a short synthetic token well inside each field, independently of slant.
    for i,word in enumerate(slot['words']):
        word['bbox_pdf_points']=[EDGES[i]+2,102,EDGES[i+1]-2,108]
    slot.update(_observe_slot_columns(slot['words'],EDGES,page=page))
    return data


def bind_secondary(data,field,raw):
    aux=column_read(data,field,raw);page=data['pages'][0]
    aux['input_column_geometry_sha256']={'2':column_geometry_sha256(page)}
    aux['pages'][0].update(geometry_mode=DUAL_MODE)
    aux['pages'][0]['slots'][0].update(_secondary_slot_geometry(page,page['slots'][0]))
    return aux


class DualAxisFieldTests(unittest.TestCase):
    def test_both_row_slope_signs_keep_word_outside_reference_band_in_actual_slot(self):
        for slope in [.01,-.01]:
            data=dual_input(slope);page=data['pages'][0];slot=page['slots'][0]
            word=slot['words'][-1]
            if slope>0:
                bottom=min(115+slope*(x-75) for x in [112,148])-.05
                word['bbox_pdf_points']=[112,bottom-.1,148,bottom]
            else:
                top=max(100+slope*(x-75) for x in [112,148])+.05
                word['bbox_pdf_points']=[112,top,148,top+.1]
            self.assertFalse(100 <= sum(word['bbox_pdf_points'][1::2])/2 < 115)
            slot.update(_observe_slot_columns(slot['words'],EDGES,page=page));before=deepcopy(data)
            out=observe_content_rows([data],document_page_count=3)
            self.assertEqual(out['source_report_ledger']['logical_row_count'],1)
            self.assertIsNone(out['source_report_ledger']['physical_slot_count'])
            self.assertEqual(out['source_report_ledger']['pages'][1]['coordinate_frame'],'pdf_points_row_lines_at_reference_x')
            self.assertEqual(out['slot_content'][0]['quad_pdf_points'],slot['quad_pdf_points'])
            self.assertEqual(data,before)

    def test_center_inside_but_corner_crossing_source_row_is_not_complete_relation(self):
        data=dual_input();p=data['pages'][0];s=p['slots'][0]
        s['words'][-1]['bbox_pdf_points']=[112,115.2,148,115.6]
        s.update(_observe_slot_columns(s['words'],EDGES,page=p))
        out=observe_content_rows([data],document_page_count=3)
        self.assertEqual(out['source_report_ledger']['logical_row_count'],0)
        evidence=out['field_observations']['pages'][0]['slots'][0]['word_evidence']
        self.assertEqual(len(evidence['row_boundary_crossing_words']),1)
        self.assertIn('amount',out['slot_content'][0]['relation_evidence']['unresolved_geometry_fields'])

    def test_dual_schema_mode_and_models_cannot_fall_back_to_rectangles(self):
        for kind in ['schema','observation_mode','page_mode','row_model','column_model','quad','row_refs','basis']:
            data=dual_input();p=data['pages'][0];s=p['slots'][0]
            if kind=='schema':data['schema_version']='oge-278t-raster-slot-observation/v2'
            if kind=='observation_mode':data.pop('geometry_mode')
            if kind=='page_mode':p.pop('geometry_mode')
            if kind=='row_model':p.pop('row_line_model')
            if kind=='column_model':p.pop('column_line_model')
            if kind=='quad':s['quad_pdf_points'][0][1]+=1
            if kind=='row_refs':s['row_boundary_refs']=[0,2]
            if kind=='basis':s.pop('band_coordinate_basis')
            with self.subTest(kind=kind),self.assertRaises(ValueError):observe_field_shapes([data])

    def test_broken_row_witness_stays_unknown_without_slots_or_invented_census(self):
        data=dual_input();page=data['pages'][0]
        page.update(column_geometry=None,slots=[],physical_slot_count=None,partial_row_geometry=False)
        page.pop('row_line_model')
        out=observe_content_rows([data],document_page_count=3)
        self.assertEqual(out['field_observations']['pages'][0]['status'],'geometry_unknown')
        self.assertEqual(out['source_report_ledger']['logical_row_count'],0)
        self.assertIsNone(out['source_report_ledger']['physical_slot_count'])

    def test_same_reference_band_cannot_hide_changed_row_model_from_secondary_or_comparison(self):
        data=dual_input();aux=bind_secondary(data,'type','purchase')
        observe_field_shapes([data],secondary_observations=[aux])
        changed=dual_input(-.01)
        with self.assertRaisesRegex(ValueError,'line model'):observe_field_shapes([changed],secondary_observations=[aux])
        changed['ocr_resolution_dpi']=300
        with self.assertRaisesRegex(ValueError,'dual-axis'):compare_field_observations(data,changed)

    def test_secondary_box_corner_crossing_is_retained_as_ambiguous(self):
        data=dual_input();aux=bind_secondary(data,'type','purchase')
        aux['pages'][0]['slots'][0]['column_words'][0]['bbox_pdf_points']=[71,99.95,78,101]
        out=observe_field_shapes([data],secondary_observations=[aux])
        obs=out['pages'][0]['slots'][0]['field_readings']['type']['observations'][1]
        self.assertEqual(obs['geometry_status'],'ambiguous')

    def test_structural_label_crossing_real_row_cannot_become_nondata(self):
        data=dual_input();p=data['pages'][0];s=p['slots'][0]
        s['words']=[{'text':'Intentionally left blank','bbox_pdf_points':[12,99.6,60,100.1]}]
        s.update(_observe_slot_columns(s['words'],EDGES,page=p))
        out=observe_content_rows([data],document_page_count=3)
        self.assertEqual(out['slot_content'][0]['disposition'],'unknown')
        self.assertIn('structural_word_geometry_ambiguous',out['slot_content'][0]['structural_evidence']['blocking_reasons'])

    def test_flat_legacy_output_objects_keep_their_original_contract(self):
        data=saved();before=deepcopy(data);out=observe_field_shapes([data])
        self.assertNotIn('row_boundary_crossing_words',out['pages'][0]['slots'][0]['word_evidence'])
        self.assertNotIn('row_boundary_refs',out['pages'][0]['slots'][0])
        self.assertEqual(data,before)

    def test_dual_content_reaches_actual_candidate_builder_and_preserves_formal_fact(self):
        from test_oge_observed_candidate import candidate, original
        from test_oge_candidate import base, catalog
        from unison_snapshot.oge_candidate import build_oge_candidate
        from unison_snapshot.builder import build
        data=dual_input();page=data['pages'][0];slot=page['slots'][0]
        slot['words'][3]['text']='05/02/2025'
        slot.update(_observe_slot_columns(slot['words'],EDGES,page=page))
        old=original();before=deepcopy(old)
        baseline,_=build_oge_candidate(catalog(),[old],base(),data_cutoff_at='2025-06-04T00:00:00Z')
        result,audit=candidate({'observations':[data],'document_page_count':3},old)
        self.assertEqual(old,before)
        prior={v['id']:v for v in baseline['transactions']}
        self.assertEqual({v['id']:v for v in result['transactions'] if v['id'] in prior},prior)
        self.assertEqual(len(result['transactions']),2)
        self.assertEqual(next(v for v in result['transactions'] if v['asset_name']=='ALPHA')['owner'],'Undisclosed')
        self.assertTrue(build(result,generated_at='2025-06-04T00:00:00Z',allow_production=True).manifest['snapshot_id'])

    def test_date_amount_rapid_crossing_rows_are_recorded_not_assigned(self):
        for column in ['transaction_date','notification_amount']:
            from unison_snapshot.oge_278t_rapid_column_shadow import observe_rapid_column
            data=dual_input();p=data['pages'][0];slot=p['slots'][0]
            start,end=(3,4) if column=='transaction_date' else (4,6)
            bounds=table_crop_bounds(p,[slot],start,end)
            # Center is inside, but the bottom-left corner crosses the sloped row.
            x0,x1=EDGES[start]+2,EDGES[end]-2
            low=115+.01*(x0-75);high=115+.01*(x1-75)
            box=[x0,low-.1,x1,(low+high)/2]
            local=[[(x-bounds[0])*300/72,(y-bounds[1])*300/72] for x,y in [(box[0],box[1]),(box[2],box[1]),(box[2],box[3]),(box[0],box[3])]]
            class Engine:
                def __call__(self,image):return [[local,'RAW',.99]],None
            with TemporaryDirectory() as directory:
                pdf=Path(directory)/'source.pdf';pdf.write_bytes(b'synthetic')
                data['source_sha256']=hashlib.sha256(pdf.read_bytes()).hexdigest()
                page=SimpleNamespace(height=200,crop=lambda b:SimpleNamespace(to_image=lambda **kw:SimpleNamespace(original=Image.new('RGB',(100,100),'white'))))
                with patch('pdfplumber.open') as opened,patch.dict(sys.modules,{'rapidocr_onnxruntime':SimpleNamespace(RapidOCR=Engine)}),patch('unison_snapshot.oge_278t_rapid_column_shadow.version',return_value='mock'):
                    opened.return_value.__enter__.return_value=SimpleNamespace(pages=[page,page,page])
                    out=observe_rapid_column(pdf,slots_observation=data,column=column)
            self.assertEqual(out['pages'][0]['detections_outside_slots'],1)
            self.assertEqual(out['pages'][0]['slots'][0]['detections'],[])
            self.assertEqual(out['pages'][0]['unassigned_detections'][0]['reason'],'crosses_source_slot_or_field')


class DualAxisColumnTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.pdf=Path(self.temp.name)/'mock.pdf';self.pdf.write_bytes(b'synthetic dual-axis source')
        self.data=dual_input();self.data['source_sha256']=hashlib.sha256(self.pdf.read_bytes()).hexdigest()

    def produce_word(self,field,boxes):
        p=self.data['pages'][0];slot=p['slots'][0];start=1 if field=='description' else 2
        expected=table_crop_bounds(p,[slot],start,start+1);crops=[]
        def crop(bounds):crops.append(bounds);return SimpleNamespace(to_image=lambda **kw:SimpleNamespace(original=object()))
        page=SimpleNamespace(height=200,crop=crop);document=SimpleNamespace(pages=[page,page,page])
        words=[{'text':text,'x0':b[0]-expected[0],'top':b[1]-expected[1],
                'x1':b[2]-expected[0],'bottom':b[3]-expected[1],'ocr_confidence':95.} for text,b in boxes]
        with patch('pdfplumber.open') as opened,patch('unison_snapshot.oge_278t_type_column_shadow.ocr_rendered_image',
                return_value=(SimpleNamespace(extract_words=lambda:words),'mock-engine')):
            opened.return_value.__enter__.return_value=document
            fn=observe_description_column if field=='description' else observe_type_column
            result=fn(self.pdf,slots_observation=self.data)
        self.assertEqual(crops,[expected]);return result

    def test_description_and_type_producers_use_quad_crop_and_link_actual_consumer(self):
        for field,text,box in [('description','ALPHA',[12,102,60,108]),('type','purchase',[72,102,77,108])]:
            aux=self.produce_word(field,[(text,box)])
            self.assertEqual(aux['pages'][0]['geometry_mode'],DUAL_MODE)
            self.assertEqual(aux['pages'][0]['slots'][0]['row_boundary_refs'],[0,1])
            out=observe_content_rows([self.data],document_page_count=3,secondary_observations=[aux])
            self.assertEqual(out['source_report_ledger']['logical_row_count'],1)

    def test_description_crossing_box_is_unassigned_not_joined_to_neighbour(self):
        aux=self.produce_word('description',[('ALPHA',[12,102,60,108]),('EDGE',[12,99.6,60,100.1])])
        self.assertEqual(aux['pages'][0]['words_outside_slots'],1)
        out=observe_content_rows([self.data],document_page_count=3,secondary_observations=[aux])
        self.assertEqual(out['field_observations']['pages'][0]['slots'][0]['field_readings']['description']['observations'][1]['geometry_status'],'ambiguous')

    def test_date_amount_rapid_producers_preserve_quad_and_do_not_accept_outside_corner(self):
        for field,fn,start,end,raw in [('transaction_date',observe_date_column,3,4,'01/02/2026'),('amount',observe_amount_column,4,6,'$1,001 - $15,000')]:
            p=self.data['pages'][0];slot=p['slots'][0];bounds=table_crop_bounds(p,[slot],start,end);crops=[]
            def crop(b):crops.append(b);return SimpleNamespace(to_image=lambda **kw:SimpleNamespace(original=Image.new('RGB',(100,100),'white')))
            page=SimpleNamespace(height=200,crop=crop);doc=SimpleNamespace(pages=[page,page,page])
            box=[EDGES[start]+2,102,EDGES[end]-2,108]
            local=[[ (x-bounds[0])*300/72,(y-bounds[1])*300/72] for x,y in [(box[0],box[1]),(box[2],box[1]),(box[2],box[3]),(box[0],box[3])]]
            class Engine:
                def __call__(self,image):return [[local,raw,.99]],None
            with patch('pdfplumber.open') as opened,patch.dict(sys.modules,{'rapidocr_onnxruntime':SimpleNamespace(RapidOCR=Engine)}),patch('unison_snapshot.oge_278t_rapid_column_shadow.version',return_value='mock'):
                opened.return_value.__enter__.return_value=doc
                aux=fn(self.pdf,slots_observation=self.data)
            self.assertEqual(crops,[bounds])
            self.assertEqual(aux['pages'][0]['slots'][0]['quad_pdf_points'],slot['quad_pdf_points'])
            self.assertEqual(observe_content_rows([self.data],document_page_count=3,secondary_observations=[aux])['source_report_ledger']['logical_row_count'],1)

    def test_single_type_cell_crop_receipt_reproduces_dual_bounds(self):
        p=self.data['pages'][0];slot=p['slots'][0]
        image=Image.new('RGB',(625,834),'white')
        page=SimpleNamespace(width=150.,height=200.,bbox=(0,0,150.,200.),to_image=lambda **kw:SimpleNamespace(original=image))
        class Engine:
            def __call__(self,image):return [[[[4.,4.],[20.,4.],[20.,14.],[4.,14.]],'purchase',.99]],None
        with patch('pdfplumber.open') as opened,patch.dict(sys.modules,{'rapidocr_onnxruntime':SimpleNamespace(RapidOCR=Engine)}),patch('importlib.metadata.version',return_value='mock'):
            opened.return_value.__enter__.return_value=SimpleNamespace(pages=[page,page,page])
            aux=observe_type_cells(self.pdf,slots_observation=self.data,selected_slots={2:[1]})
        receipt=aux['pages'][0]['slots'][0]['cell_crop']
        self.assertEqual(receipt['crop_geometry_mode'],DUAL_MODE)
        self.assertEqual(receipt['source_quad_pdf_points'],slot_quad(p,slot,2,3))
        out=observe_content_rows([self.data],document_page_count=3,secondary_observations=[aux])
        self.assertEqual(out['source_report_ledger']['logical_row_count'],1)
        changed=deepcopy(aux);changed['pages'][0]['slots'][0]['cell_crop']['source_quad_pdf_points'][0][0]+=1
        with self.assertRaisesRegex(ValueError,'quadrilateral'):observe_field_shapes([self.data],secondary_observations=[changed])


if __name__=='__main__':unittest.main()
