"""Source-coordinate dual-axis math; synthetic fixtures are not OCR accuracy."""
from copy import deepcopy
import hashlib
import json
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
try:
    import numpy as np
    from PIL import Image
except ImportError:
    np=Image=None
from unison_snapshot.oge_278t_raster_slots import (DUAL_MODE,row_edges_at_x,slot_quad,point_in_slot,
    box_in_slot,box_overlaps_slot,slot_crop_bounds,table_crop_bounds,column_geometry_sha256,_dual_axis_image_grid,
    observe_raster_slots,DUAL_SCHEMA)


def image_page(row_slope=.006,column_slope=.004,kind=None):
    scale=3;pixels=np.full((612*scale,792*scale),255,np.uint8)
    edges=[104.,150.,473.,524.,581.,628.,686.];xref=395.;yref=300.
    for py in range(80*scale,596*scale):
        y=py/scale
        for edge in edges:
            px=int((edge+column_slope*(y-yref))*scale)
            pixels[py,max(0,px-1):px+2]=0
    for b in [85.,*map(float,range(116,582,15))]:
        B=b-row_slope*xref
        left=(edges[0]-column_slope*yref+column_slope*B)/(1-column_slope*row_slope)
        right=(edges[-1]-column_slope*yref+column_slope*B)/(1-column_slope*row_slope)
        for px in range(int(left*scale),int(right*scale)+1):
            py=int((b+row_slope*(px/scale-xref))*scale)
            pixels[max(0,py-1):py+2,px]=0
    if kind=='gap':
        y=300.;x=473+column_slope*(y-yref)
        pixels[int(y*scale):int((y+5)*scale),int((x-3)*scale):int((x+3)*scale)]=255
    elif kind=='extra_column':
        for py in range(80*scale,596*scale):
            px=int((300+column_slope*(py/scale-yref))*scale);pixels[py,px-1:px+2]=0
    elif kind=='extra_stroke':pixels[181*scale:181*scale+3,120*scale:140*scale]=0
    elif kind=='broken_row':
        for px in range(100*scale,690*scale):
            py=int((191+row_slope*(px/scale-xref))*scale);pixels[py-2:py+3,px]=255
        for py in range(185*scale,198*scale):
            for edge in edges:
                px=int((edge+column_slope*(py/scale-yref))*scale);pixels[py,px-1:px+2]=0
        for px in range(120*scale,140*scale):
            py=int((191+row_slope*(px/scale-xref))*scale);pixels[py-1:py+2,px]=0
    elif kind=='two_row_models':
        extra_slope=.014;extra_b=176+(row_slope-extra_slope)*(123.-xref)
        for px in range(104*scale,687*scale):
            py=int((extra_b+extra_slope*(px/scale-xref))*scale);pixels[py-1:py+2,px]=0
    page=SimpleNamespace(width=792,height=612,bbox=(0,0,792,612),rotation=0,
        lines=[],cropbox=(0,0,792,612),mediabox=(0,0,792,612))
    page.to_image=Mock(return_value=SimpleNamespace(original=Image.fromarray(pixels)))
    return page


def dual_saved_observation(source_sha=None, *, kind=None):
    """Actual image geometry + existing raw-word entry, PDF reader mocked only."""
    from test_oge_annual import _Document
    from test_oge_278t_image_grid import header_words
    from unison_snapshot.ocr_geometry import OcrPage
    page=image_page(kind=kind);geometry=_dual_axis_image_grid(page)
    words=header_words()
    cells=['1','ALPHA','purchase','06/01/2026','No','$1,001 - $15,000']
    boxes=[(110,125),(170,220),(480,518),(530,575),(590,620),(638,680)]
    for text,(left,right) in zip(cells,boxes):
        y=123.5+.006*((left+right)/2-395.)
        words.append({'text':text,'x0':left,'x1':right,'top':y-2,'bottom':y+2,'ocr_confidence':95,
                      'block_num':1,'par_num':1,'line_num':1})
    census={'results':[{'page_number':1,'status':'coverage_unknown','column_geometry_observation':geometry}]}
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'source.pdf';path.write_bytes(b'synthetic dual-axis source')
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        with patch('unison_snapshot.oge_278t_raster_slots.census_with_2024_fallback',return_value=(census,{})), \
             patch('pdfplumber.open',return_value=_Document([page])), \
             patch('unison_snapshot.oge_278t_raster_slots.ocr_pdf_pages',side_effect=AssertionError('new OCR forbidden')):
            data=observe_raster_slots(path,expected_sha256=sha,page_numbers=[1],saved_ocr_pages={1:OcrPage(width=792,height=612,words=words)},
                saved_ocr_engine='synthetic saved words',geometry_mode=DUAL_MODE)
    if source_sha:data['source_sha256']=source_sha
    return data,words


def dual_page(slope=.01):
    spans=[[12.,14.],[45.,47.],[85.,87.]];xs=[sum(s)/2 for s in spans];ref=sum(xs)/3
    intercepts=[20.,40.,60.]
    model={'schema_version':'oge-278t-row-lines/v1','coordinate_frame':'displayed_mediabox',
        'reference_x_pdf_points':ref,'valid_x_span_pdf_points':[10.,100.],
        'intercepts_y_pdf_points':intercepts,'slopes_dy_per_dx':[slope]*3,
        'line_observations':[{'samples':[{'x_span_pdf_points':s,'y_pdf_points':b+slope*(x-ref)}
            for s,x in zip(spans,xs)],'maximum_fit_residual_pdf_points':0.} for b in intercepts]}
    page={'geometry_mode':DUAL_MODE,'render_coordinate_frame':'displayed_mediabox',
          'column_edges_pdf_points':[10.,20.,40.,55.,70.,85.,100.],'row_line_model':model}
    slot={'row_boundary_refs':[0,1],'band_pdf_points':[20.,40.],
          'band_coordinate_basis':'row_lines_at_reference_x'}
    return page,slot


class DualAxisMathTests(unittest.TestCase):
    @unittest.skipUnless(np is not None and Image is not None,'optional image dependencies unavailable')
    def test_saved_entry_retains_source_quads_and_every_original_word(self):
        data,words=dual_saved_observation()
        self.assertEqual(data['schema_version'],DUAL_SCHEMA)
        page=data['pages'][0]
        self.assertEqual(page['known_physical_slot_count'],31)
        self.assertIsNone(page['physical_slot_count'])
        self.assertEqual(sum(len(s['words']) for s in page['slots'])+len(page['unbound_page_ocr_words']),len(words))
        first=page['slots'][0]
        self.assertEqual(first['quad_pdf_points'],slot_quad(page,first))
        self.assertEqual(first['column_observation']['description']['raw_ocr_text'],'ALPHA')
        self.assertEqual(first['column_observation']['type']['raw_ocr_text'],'purchase')
    @unittest.skipUnless(np is not None and Image is not None,'optional image dependencies unavailable')
    def test_same_source_image_fits_both_axes_without_threshold_changes(self):
        for r,c in ((.006,.004),(-.006,-.004),(.006,-.004),(-.006,.004)):
            with self.subTest(row=r,column=c):
                out=_dual_axis_image_grid(image_page(r,c))
                self.assertEqual(out['status'],'candidate',str(out)[:1200])
                self.assertEqual(len(out['row_line_model']['intercepts_y_pdf_points']),33)
                self.assertEqual(len(out['physical_row_bands']),31)
                for slope in out['row_line_model']['slopes_dy_per_dx']:self.assertLess(abs(slope-r),.001)
                self.assertLessEqual(out['row_matching_evidence']['fitted_proposal_count'],512)

    @unittest.skipUnless(np is not None and Image is not None,'optional image dependencies unavailable')
    def test_broken_or_multiple_column_grid_remains_unknown(self):
        for kind in ('gap','extra_column'):
            with self.subTest(kind=kind):self.assertEqual(_dual_axis_image_grid(image_page(kind=kind))['status'],'unknown')

    @unittest.skipUnless(np is not None and Image is not None,'optional image dependencies unavailable')
    def test_unmatched_strokes_and_broken_row_are_barriers_not_new_or_merged_cells(self):
        for kind in ('extra_stroke','broken_row'):
            out=_dual_axis_image_grid(image_page(kind=kind))
            self.assertEqual(out['status'],'candidate',str(out)[:1200])
            self.assertLess(len(out['physical_row_bands']),31)
            self.assertTrue(out['unresolved_regions'])
            self.assertTrue(out['row_matching_evidence']['boundary_barriers'])
            self.assertTrue(all(b-a<=22.5 for a,b in out['physical_row_bands']))

    @unittest.skipUnless(np is not None and Image is not None,'optional image dependencies unavailable')
    def test_equal_supported_row_models_are_not_chosen_by_count_or_greedy_match(self):
        out=_dual_axis_image_grid(image_page(kind='two_row_models'))
        self.assertEqual(out['status'],'unknown')
        self.assertTrue(any(f['reason']=='multiple_supported_row_models' for f in out['candidate_failures']))

    @unittest.skipUnless(np is not None and Image is not None,'optional image dependencies unavailable')
    def test_success_and_failure_geometry_are_json_stable_without_validator_coercion(self):
        for kind in (None,'gap','extra_column','extra_stroke','broken_row','two_row_models'):
            with self.subTest(kind=kind):
                out=_dual_axis_image_grid(image_page(kind=kind))
                self.assertEqual(out,json.loads(json.dumps(out,allow_nan=False)))
    def test_both_slope_directions_preserve_raw_coordinate_assignment(self):
        for slope in (-.01,.01):
            page,slot=dual_page(slope)
            quad=slot_quad(page,slot)
            self.assertEqual([p[0] for p in quad],[10.,100.,100.,10.])
            self.assertTrue(point_in_slot(page,slot,80,30))
            self.assertTrue(box_in_slot(page,slot,[75,25,80,35]))
            self.assertTrue(box_overlaps_slot(page,slot,[75,19,80,21]))
            self.assertFalse(box_in_slot(page,slot,[75,19,80,21]))
            self.assertFalse(box_overlaps_slot(page,slot,[110,20,120,30]))
            crop=slot_crop_bounds(page,slot,0,6)
            self.assertAlmostEqual(crop[0],10.6);self.assertAlmostEqual(crop[2],98.5)
            self.assertLess(crop[1],20.);self.assertGreater(crop[3],40.)
            self.assertEqual(crop,table_crop_bounds(page,[slot],0,6))

    def test_model_and_cache_binding_reject_changed_samples_or_slopes(self):
        page,slot=dual_page();before=column_geometry_sha256(page)
        for key in ('slope','sample','basis'):
            changed=deepcopy(page);s=deepcopy(slot)
            if key=='slope':changed['row_line_model']['slopes_dy_per_dx'][0]=.009
            if key=='sample':changed['row_line_model']['line_observations'][0]['samples'][1]['y_pdf_points']+=1
            if key=='basis':s.pop('band_coordinate_basis')
            with self.subTest(key=key),self.assertRaises(ValueError):slot_quad(changed,s)
        changed=deepcopy(page);changed['row_line_model']['line_observations'][0]['samples'][1]['y_pdf_points']+=1
        self.assertNotEqual(before,column_geometry_sha256(changed))

    def test_legacy_hash_and_flat_geometry_keep_original_contract(self):
        page={'column_edges_pdf_points':[10.,20.,40.,55.,70.,85.,100.]}
        slot={'band_pdf_points':[20.,40.]}
        expected=hashlib.sha256(json.dumps({'edges':page['column_edges_pdf_points'],'model':None,'frame':None},
            sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        self.assertEqual(column_geometry_sha256(page),expected)
        self.assertEqual(slot_quad(page,slot),[[10.,20.],[100.,20.],[100.,40.],[10.,40.]])


if __name__=='__main__':unittest.main()
