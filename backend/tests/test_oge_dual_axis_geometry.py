"""Source-coordinate dual-axis math; synthetic fixtures are not OCR accuracy."""
from copy import deepcopy
import hashlib
import json
import unittest
from unison_snapshot.oge_278t_raster_slots import (DUAL_MODE,row_edges_at_x,slot_quad,point_in_slot,
    box_in_slot,box_overlaps_slot,slot_crop_bounds,table_crop_bounds,column_geometry_sha256)


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
