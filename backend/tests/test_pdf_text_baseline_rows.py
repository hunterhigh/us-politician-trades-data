"""Source baseline row proof is not a text repair or relaxed field boundary."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import hashlib
import unittest
from test_pdf_text_objects import source_pdf
from test_oge_dual_axis_geometry import image_page
from test_oge_278t_image_grid import header_words
from test_oge_annual import _Document
from unison_snapshot.pdf_text_object_words import observe_pdf_text_objects
from unison_snapshot.pdf_text_baselines import MODE,_assignment,source_text_baseline_bindings
from unison_snapshot.ocr_geometry import OcrPage
from unison_snapshot.oge_278t_raster_slots import _dual_axis_image_grid,observe_raster_slots
from unison_snapshot.oge_278t_field_shadow import observe_field_shapes,observe_content_rows


def flat_page():
    xs=[2.,99.,196.];spans=[[x-1,x+1] for x in xs];lines=[40.,60.,80.,100.]
    model={'schema_version':'oge-278t-row-lines/v1','coordinate_frame':'displayed_mediabox',
        'reference_x_pdf_points':99.,'valid_x_span_pdf_points':[0.,200.],
        'intercepts_y_pdf_points':lines,'slopes_dy_per_dx':[0.]*4,
        'line_observations':[{'samples':[{'x_span_pdf_points':span,'y_pdf_points':b} for span in spans],
            'maximum_fit_residual_pdf_points':0.} for b in lines]}
    return {'geometry_mode':'source_pdf_dual_axis/v1','render_coordinate_frame':'displayed_mediabox',
        'column_edges_pdf_points':[0.,20.,140.,155.,170.,185.,200.],'row_line_model':model,
        'slots':[{'slot_index':i+1,'row_boundary_refs':[i,i+1],'band_pdf_points':[lines[i],lines[i+1]],
            'band_coordinate_basis':'row_lines_at_reference_x'} for i in range(3)]}


class SourceBaselineRowsTests(unittest.TestCase):
    def test_original_descender_baseline_and_center_prove_same_unique_row(self):
        with TemporaryDirectory() as folder:
            source=Path(folder)/'source.pdf';sha=source_pdf(source,extra='BT /F1 14 Tf 25 42 Td (gy) Tj ET')
            native=observe_pdf_text_objects(source,expected_sha256=sha,page_number=1)
            proof=source_text_baseline_bindings(source,expected_sha256=sha,page_number=1,page=flat_page(),words=native['words'])
            idx=next(i for i,w in enumerate(native['words']) if w['text']=='gy')
            self.assertGreater(native['words'][idx]['bottom'],80.)
            self.assertEqual(proof['bindings'][str(idx)]['slot_index'],2)
            altered=deepcopy(native['words']);altered[idx]['x1']+=.01
            proof=source_text_baseline_bindings(source,expected_sha256=sha,page_number=1,page=flat_page(),words=altered)
            self.assertNotIn(str(idx),proof['bindings'])

    def test_hidden_white_covered_and_clipped_source_text_gets_no_rescue(self):
        for extra in ('BT /F1 14 Tf 3 Tr 25 42 Td (gy) Tj ET',
            '1 1 1 rg BT /F1 14 Tf 25 42 Td (gy) Tj ET',
            'BT /F1 14 Tf 25 42 Td (gy) Tj ET 1 1 1 rg 24 38 30 20 re f',
            'q 0 0 200 120 re W n BT /F1 14 Tf 25 42 Td (gy) Tj ET Q'):
            with self.subTest(extra=extra),TemporaryDirectory() as folder:
                source=Path(folder)/'source.pdf';sha=source_pdf(source,extra=extra)
                native=observe_pdf_text_objects(source,expected_sha256=sha,page_number=1)
                proof=source_text_baseline_bindings(source,expected_sha256=sha,page_number=1,page=flat_page(),words=native['words'])
                gy=[i for i,w in enumerate(native['words']) if w['text']=='gy']
                self.assertTrue(all(str(i) not in proof['bindings'] for i in gy))

    def test_cross_field_multiline_and_baseline_disagreement_are_not_rescued(self):
        record={'exclusion_reasons':[],'observation_issues':[],'text_render_mode':0,'raw_text':'gy',
            'matrix_pdf_bottom_left':[1,0,0,1,0,42],'bbox_displayed_mediabox':[25,68,38,81],
            'source_mediabox_pdf_bottom_left':[0,0,200,120]}
        self.assertEqual(_assignment(record,flat_page())['slot_index'],2)
        for changes in ({'raw_text':'g\ny'},{'bbox_displayed_mediabox':[15,68,38,81]},
            {'bbox_displayed_mediabox':[25,35,38,101]}, {'matrix_pdf_bottom_left':[1,0,0,1,0,62]}):
            with self.subTest(changes=changes):self.assertIsNone(_assignment({**record,**changes},flat_page()))

    def test_two_known_rows_cannot_hide_an_unobserved_row_between_them(self):
        page=flat_page();model=page['row_line_model']
        model['intercepts_y_pdf_points'].insert(3,85.)
        model['slopes_dy_per_dx'].append(0.)
        item=deepcopy(model['line_observations'][2])
        for sample in item['samples']:sample['y_pdf_points']=85.
        model['line_observations'].insert(3,item)
        page['slots'][2].update(row_boundary_refs=[3,4],band_pdf_points=[85.,100.])
        record={'exclusion_reasons':[],'observation_issues':[],'text_render_mode':0,'raw_text':'gy',
            'matrix_pdf_bottom_left':[1,0,0,1,0,42],'bbox_displayed_mediabox':[25,66,38,88],
            'source_mediabox_pdf_bottom_left':[0,0,200,120]}
        self.assertIsNone(_assignment(record,page))

    def observer_fixture(self,folder,mode,cross_field=False):
        page=image_page();geometry=_dual_axis_image_grid(page);words=header_words()+[
            {'text':'gy','x0':200.,'top':124.,'x1':480. if cross_field else 240.,'bottom':132.,'ocr_confidence':0}]
        source=Path(folder)/'source.pdf';source.write_bytes(b'synthetic observer source');sha=hashlib.sha256(source.read_bytes()).hexdigest()
        proof={'schema_version':'source-text-baseline-row-bindings/v1','source_sha256':sha,'page_number':1,
            'bindings':{str(len(words)-1):{'slot_index':1,'column_index':1,'source_object_index':42,
                'matrix_pdf_bottom_left':[1,0,0,1,0,483],'basis':'synthetic source contract'}},'qualification':'not_assessed'}
        census={'results':[{'page_number':1,'status':'coverage_unknown','column_geometry_observation':geometry}]}
        with patch('unison_snapshot.oge_278t_raster_slots.census_with_2024_fallback',return_value=(census,{})),patch('pdfplumber.open',return_value=_Document([page])),patch('unison_snapshot.pdf_text_baselines.source_text_baseline_bindings',return_value=proof):
            result=observe_raster_slots(source,expected_sha256=sha,page_numbers=[1],saved_ocr_pages={1:OcrPage(width=792,height=612,words=words)},saved_ocr_engine='synthetic original words',geometry_mode='source_pdf_dual_axis/v1',**({'native_baseline_mode':MODE} if mode else {}))
        return source,result,proof

    def test_normal_observer_preserves_raw_crossing_and_recomputes_proof_in_consumers(self):
        with TemporaryDirectory() as folder:
            source,old,_=self.observer_fixture(folder,False)
            source,new,proof=self.observer_fixture(folder,True)
            oldslots=old['pages'][0]['slots'];slots=new['pages'][0]['slots']
            self.assertEqual([s['column_observation'] for s in oldslots],[s['column_observation'] for s in slots])
            self.assertGreater(sum(len(s['raw_row_crossing_word_indices']) for s in slots),0)
            self.assertEqual(sum(len(s['unresolved_region_crossing_word_indices']) for s in slots),0)
            with patch('unison_snapshot.pdf_text_baselines.source_text_baseline_bindings',return_value=proof):
                out=observe_content_rows([new],document_page_count=1,source_pdf_path=source)
                evidence=out['field_observations']['pages'][0]['slots'][0]['word_evidence']
                self.assertTrue(evidence['raw_row_boundary_crossing_words'])
                self.assertFalse(evidence['row_boundary_crossing_words'])
                with self.assertRaisesRegex(ValueError,'controlled original'):
                    observe_field_shapes([new])
                for mutation in ('matrix','raw_cross','local','mode'):
                    bad=deepcopy(new)
                    if mutation=='matrix':bad['pages'][0]['native_baseline_bindings']['bindings'][next(iter(proof['bindings']))]['matrix_pdf_bottom_left'][5]+=1
                    if mutation=='raw_cross':bad['pages'][0]['slots'][0]['raw_row_crossing_word_indices']=[]
                    if mutation=='local':bad['pages'][0]['slots'][0]['words'][0]['source_page_word_index']+=1
                    if mutation=='mode':bad.pop('native_baseline_mode')
                    with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                        observe_content_rows([bad],document_page_count=1,source_pdf_path=source)

    def test_row_ownership_never_clears_real_horizontal_field_crossing(self):
        with TemporaryDirectory() as folder:
            source,new,proof=self.observer_fixture(folder,True,cross_field=True)
            with patch('unison_snapshot.pdf_text_baselines.source_text_baseline_bindings',return_value=proof):
                out=observe_field_shapes([new],source_pdf_path=source)
            row=out['pages'][0]['slots'][0]
            self.assertTrue(row['word_evidence']['crossing_words'])
            self.assertTrue(any(w['scope']=='field_boundary' for f in row['field_readings'].values() for w in f['geometry_warnings']))

if __name__=='__main__':unittest.main()
