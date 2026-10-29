"""Layout-domain regressions; generated grids are geometry contracts, not OCR gold."""
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

try:
    import numpy as np
    from PIL import Image
except ImportError:
    np = Image = None

from unison_snapshot.oge_278t_page_shadow import _raster_rule_centers
from unison_snapshot.oge_278t_raster_slots import (
    DUAL_MODE, _dual_axis_image_grid, _dual_header_geometry, observe_raster_slots,census_with_2024_fallback)


def scene(*, row_slope=.006, column_slope=.004, height=612, offset=0, broken=False, missing_header=False):
    width=792+offset;scale=3;xref=395+offset;yref=300
    header=55 if height==612 else 100
    body=list(range(116 if height==612 else 140,582 if height==612 else 701,15))
    bounds=[header,*body]
    edges=np.array([104,150,473,524,581,628,686],float)+offset
    pixels=np.full((height*scale,width*scale),255,np.uint8)
    for py in range((header-5)*scale,(bounds[-1]+5)*scale):
        for edge in edges:
            x=int((edge+column_slope*(py/scale-yref))*scale)
            pixels[py,x-1:x+2]=0
    for boundary in bounds:
        if missing_header and boundary==header:
            continue
        b=boundary-row_slope*xref
        left=(edges[0]-column_slope*yref+column_slope*b)/(1-column_slope*row_slope)
        right=(edges[-1]-column_slope*yref+column_slope*b)/(1-column_slope*row_slope)
        for px in range(int(left*scale),int(right*scale)+1):
            py=int((boundary+row_slope*(px/scale-xref))*scale)
            pixels[py-1:py+2,px]=0
    # Full-width page decorations have no seven-column continuation.
    pixels[20*scale:20*scale+3,:]=0
    pixels[35*scale:35*scale+3,:]=0
    if broken:
        y=300;x=edges[2]+column_slope*(y-yref)
        pixels[y*scale:(y+6)*scale,int((x-3)*scale):int((x+3)*scale)]=255
    page=SimpleNamespace(width=width,height=height,bbox=(0,0,width,height),rotation=0,
        cropbox=(0,0,width,height),mediabox=(0,0,width,height),lines=[])
    page.to_image=Mock(return_value=SimpleNamespace(original=Image.fromarray(pixels)))
    return page,bounds


@unittest.skipUnless(np is not None and Image is not None,"optional image dependencies unavailable")
class GridSearchDomainTests(unittest.TestCase):
    def test_explicit_vertical_domain_retains_legacy_default(self):
        gray=np.full((700,30),255,np.uint8)
        gray[[40,100,620],:]=0
        self.assertEqual(_raster_rule_centers(gray,points_per_pixel=1,x0=2,x1=20),[100.])
        self.assertEqual(_raster_rule_centers(gray,points_per_pixel=1,x0=2,x1=20,y_span=(0,700)),[40.,100.,620.])
        for span in ((100,50),(-1,500),(0,float('nan'))):
            with self.assertRaises(ValueError):
                _raster_rule_centers(gray,points_per_pixel=1,x0=2,x1=20,y_span=span)

    def test_header_outside_old_window_and_page_decorations_are_separated(self):
        for row,col in ((.006,.004),(-.006,-.004),(.006,-.004),(-.006,.004)):
            with self.subTest(row=row,column=col):
                page,bounds=scene(row_slope=row,column_slope=col)
                result=_dual_axis_image_grid(page)
                self.assertEqual(result['status'],'candidate',str(result)[:1000])
                actual=result['row_line_model']['intercepts_y_pdf_points']
                self.assertEqual(len(actual),len(bounds))
                self.assertTrue(all(abs(a-b)<.6 for a,b in zip(actual,bounds)))
                proof=result['row_matching_evidence']['column_connected_component']
                self.assertEqual(proof['supported_component_count'],1)
                self.assertTrue(any(not c['contains_column_regions'] for c in proof['components']))
                self.assertLess(result['column_line_model']['valid_y_span_pdf_points'][0],80)
                self.assertTrue(all(w['status']=='confirmed' for w in result['weak_rule_observations']))

    def test_shift_and_larger_page_have_observed_instead_of_template_bounds(self):
        for kwargs in ({'offset':40},{'height':720}):
            with self.subTest(**kwargs):
                page,bounds=scene(**kwargs);out=_dual_axis_image_grid(page)
                self.assertEqual(out['status'],'candidate',str(out)[:1000])
                actual=out['row_line_model']['intercepts_y_pdf_points']
                self.assertEqual(len(actual),len(bounds))
                self.assertAlmostEqual(actual[-1],bounds[-1],delta=.6)
                self.assertEqual(out['row_matching_evidence']['search_y_span_pdf_points'],[0.,page.height])

    def test_broken_column_cannot_be_joined_by_page_decorations(self):
        page,_=scene(broken=True)
        out=_dual_axis_image_grid(page)
        self.assertEqual(out['status'],'unknown')
        self.assertTrue(any(f.get('evidence',{}).get('column_connected_component',{}).get(
            'supported_component_count')==0 for f in out['candidate_failures']))

    def test_missing_header_words_are_diagnosed_after_geometric_boundaries(self):
        page,_=scene();geometry=_dual_axis_image_grid(page)
        before=deepcopy(geometry)
        _,header=_dual_header_geometry([],geometry)
        self.assertEqual(geometry,before)
        self.assertEqual(header['failure_stage'],'header_role')
        self.assertEqual(header['best_unique_label_count'],0)
        self.assertEqual(header['witnessed_boundary_count'],len(geometry['row_line_model']['intercepts_y_pdf_points']))

    def test_header_boundary_cannot_be_invented_from_label_positions(self):
        from test_oge_278t_image_grid import header_words
        page,_=scene(missing_header=True)
        geometry=_dual_axis_image_grid(page)
        self.assertEqual(geometry['status'],'candidate')
        self.assertGreater(geometry['row_line_model']['intercepts_y_pdf_points'][0],100)
        _,header=_dual_header_geometry(header_words(),geometry)
        self.assertEqual(header['status'],'unknown')
        self.assertEqual(header['failure_stage'],'header_role')

    def test_unsupported_origin_or_excessive_slant_stays_unknown(self):
        page,_=scene();page.bbox=(10,20,802,632)
        self.assertEqual(_dual_axis_image_grid(page)['reason'],'unsupported_raster_coordinate_frame')
        page,_=scene(row_slope=.025,column_slope=.025)
        self.assertEqual(_dual_axis_image_grid(page)['status'],'unknown')

    @unittest.skipUnless(importlib.util.find_spec('pdfplumber'),'optional PDF dependency unavailable')
    def test_dual_route_does_not_run_legacy_fixed_strip_census(self):
        from test_oge_annual import _Document
        page,_=scene()
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.pdf';source.write_bytes(b'fixture source')
            digest=hashlib.sha256(source.read_bytes()).hexdigest()
            with patch('pdfplumber.open',return_value=_Document([page])),patch(
                    'unison_snapshot.oge_278t_raster_slots.probe_raster_row_bands',side_effect=AssertionError('legacy census forbidden')):
                result,edges=census_with_2024_fallback(source,expected_sha256=digest,page_numbers=[1],geometry_mode=DUAL_MODE)
                self.assertEqual(result['results'][0]['column_geometry_observation']['status'],'candidate')
                self.assertNotIn('rule_positions',result['results'][0])
                self.assertEqual(edges,{1:None})
                for numbers in ([0],[1,1],[True],list(range(1,10)),[2]):
                    with self.assertRaises(ValueError):
                        census_with_2024_fallback(source,expected_sha256=digest,page_numbers=numbers,geometry_mode=DUAL_MODE)



if __name__=='__main__':unittest.main()
