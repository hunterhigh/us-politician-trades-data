"""Synthetic scanned-grid boundaries; not independent official OCR accuracy."""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

try:
    import numpy as np
    from PIL import Image
except ImportError:
    np = Image = None

from unison_snapshot.oge_278t_raster_slots import (
    _oge_2024_image_grid, _header_role_evidence, _image_rule_witness, observe_raster_slots)
from unison_snapshot.oge_278t_page_shadow import probe_raster_row_bands, probe_oge_2024_alternate_bands
from unison_snapshot.ocr_geometry import ocr_pdf_pages

EDGES = [104., 150., 473., 524., 581., 628., 686.]


def grid_page(scale=3, kind=None):
    pixels=np.full((612*scale,792*scale),255,np.uint8)
    for x in EDGES:
        pixels[80*scale:582*scale,int(x*scale)]=0
    for y in range(116,582,15):
        pixels[y*scale,int(EDGES[0]*scale):int(EDGES[-1]*scale)+1]=0
    if kind=='missing':
        pixels[:,int(EDGES[3]*scale)-3:int(EDGES[3]*scale)+4]=255
    elif kind=='extra':
        pixels[80*scale:582*scale,300*scale]=0
    elif kind=='block':
        pixels[:,450*scale:490*scale]=0
    elif kind=='gap':
        pixels[300*scale:305*scale,int(EDGES[2]*scale)-10:int(EDGES[2]*scale)+11]=255
    elif kind=='left_only':
        # Keep all vertical rules, but remove horizontal rules to their right.
        for y in range(116,582,15):
            pixels[y*scale,480*scale:686*scale]=255
            for x in EDGES[3:]:pixels[y*scale,int(x*scale)]=0
    elif kind=='header_only':
        pixels[200*scale:,:]=255
    page=SimpleNamespace(width=792,height=612,bbox=(0,0,792,612),rotation=90,
                         lines=[],cropbox=(2,3,790,609))
    page.to_image=Mock(return_value=SimpleNamespace(original=Image.fromarray(pixels)))
    return page


def header_words():
    words=[]
    for i,label in enumerate(('Description','Type','Date','Notification','Amount'),1):
        words.append({'text':label,'x0':EDGES[i]+3,'x1':EDGES[i]+30,'top':105.,'bottom':113.,
                      'ocr_confidence':90.})
    for i,label in enumerate(('OGE','Form','278-T','2024')):
        words.append({'text':label,'x0':104+i*30,'x1':130+i*30,'top':19.,'bottom':26.,
                      'ocr_confidence':90.})
    return words


@unittest.skipUnless(np is not None and Image is not None, "optional raster dependencies unavailable")
class ImageGridTests(unittest.TestCase):
    def test_blind_grid_keeps_candidates_pending_header_and_does_not_use_33(self):
        page=grid_page();out=_oge_2024_image_grid(page)
        self.assertEqual(out['status'],'candidate')
        self.assertEqual(len(out['physical_row_bands']),31)
        self.assertEqual(out['render_coordinate_frame'],'displayed_mediabox')
        self.assertEqual(out['render_rotation'],90)
        page.to_image.assert_called_once_with(resolution=216,force_mediabox=True)
        self.assertEqual(_header_role_evidence(header_words(),out)['status'],'confirmed')

    def test_actual_media_pixel_scale_is_used_for_both_axes(self):
        a=_oge_2024_image_grid(grid_page(scale=2));b=_oge_2024_image_grid(grid_page(scale=3))
        self.assertEqual(a['status'],b['status'])
        self.assertEqual(a['physical_row_bands'],b['physical_row_bands'])
        for x,y in zip(a['edges_pdf_points'],b['edges_pdf_points']):self.assertLess(abs(x-y),.3)

    def test_missing_extra_broken_block_or_local_only_grid_remains_unknown(self):
        for kind in ('missing','extra','gap','block','left_only','header_only'):
            with self.subTest(kind=kind):self.assertEqual(_oge_2024_image_grid(grid_page(kind=kind))['status'],'unknown')

    def test_wrong_or_missing_template_and_misplaced_or_duplicate_roles_rejected(self):
        geometry=_oge_2024_image_grid(grid_page())
        for kind in ('annual','no_title','missing','wrong_column','duplicate','body','wide_box'):
            words=header_words()
            if kind=='annual':words[7]['text']='278e'
            if kind=='no_title':words=words[:5]
            if kind=='missing':words.pop(0)
            if kind=='wrong_column':words[0].update(x0=530,x1=550)
            if kind=='duplicate':words.append(deepcopy(words[0]))
            if kind=='body':words[0].update(top=125,bottom=132)
            if kind=='wide_box':words[0].update(x0=0,x1=600)
            with self.subTest(kind=kind):self.assertEqual(_header_role_evidence(words,geometry)['status'],'unknown')

    def test_small_header_label_errors_are_diagnostic_and_raw_words_survive(self):
        geometry=_oge_2024_image_grid(grid_page());words=header_words()
        words[0]['text']='Descriotion';words[2]['text']='Dato';words[7]['text']='278-7'
        before=deepcopy(words);out=_header_role_evidence(words,geometry)
        self.assertEqual(out['status'],'confirmed');self.assertEqual(words,before)
        self.assertEqual(out['observed_words'][0]['text'],'Descriotion')

    def test_nonzero_page_origin_is_not_stretched_to_media_frame(self):
        page=grid_page();page.bbox=(2,3,794,615)
        self.assertEqual(_oge_2024_image_grid(page)['status'],'unknown')
        page.to_image.assert_not_called()

    def test_clipped_rule_window_cannot_count_as_complete(self):
        image=np.full((100,40),255,np.uint8);image[:,20]=0
        for top,bottom in ((0,300),(-10,90)):
            out=_image_rule_witness(image,x=20,top=top,bottom=bottom,scale_x=1,scale_y=1)
            self.assertEqual(out["status"],"unknown")
            self.assertEqual(out["reason"],"pixel_window_outside_page")

    def test_census_and_existing_audit_do_not_confirm_pending_image_template(self):
        from unison_snapshot.oge_278t_raster_slots import census_with_2024_fallback
        from unison_snapshot.oge_278t_format_audit import audit_278t_format
        page=grid_page();opened=Mock();opened.__enter__=Mock(return_value=SimpleNamespace(pages=[page]));opened.__exit__=Mock()
        initial={"results":[{"page_number":1,"status":"coverage_unknown","physical_row_bands":[]}]}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"source";path.write_bytes(b"synthetic");digest=hashlib.sha256(b"synthetic").hexdigest()
            with patch("pdfplumber.open",return_value=opened),patch(
                    "unison_snapshot.oge_278t_raster_slots.probe_raster_row_bands",return_value=initial):
                census,edges=census_with_2024_fallback(path,expected_sha256=digest,page_numbers=[1])
            self.assertIsNone(edges[1]);self.assertEqual(census["results"][0]["status"],"coverage_unknown")
            self.assertEqual(census["results"][0]["column_geometry_observation"]["status"],"candidate")
            shadow={"page_number":1,"text_mode":"raster","row_basis":"unknown","candidate_row_bands":[],
                    "coverage":"coverage_unknown_raster","unresolved_row_bands":[],"unresolved_row_regions":[],
                    "number_sequence_warnings":[],"row_coverage":"unknown"}
            with patch("unison_snapshot.oge_278t_format_audit.inspect_pdf",return_value={"page_count":1,"pages":[shadow]}),patch(
                    "unison_snapshot.oge_278t_format_audit.census_with_2024_fallback",return_value=(census,edges)):
                out=audit_278t_format(path,extraction={"source_sha256":digest,"source_rows":[],"transactions":[],"quarantined":[]},raster_census=True)
            self.assertEqual(out["confirmed_physical_slot_count"],0)
            self.assertEqual(out["pages"][0]["raster_column_geometry"],"unknown")

    def test_short_scan_gap_is_bounded_and_text_relay_cannot_connect(self):
        image=np.full((600,40),255,np.uint8);image[:,20]=0;image[250:252,:]=255
        kwargs=dict(x=10,top=0,bottom=300,scale_x=2,scale_y=2)
        # .75pt at this resolution permits one blank scanline, not two.
        self.assertEqual(_image_rule_witness(image,**kwargs)['status'],'unknown')
        image[251,20]=0
        self.assertEqual(_image_rule_witness(image,**kwargs)['status'],'confirmed')
        image[:200,:]=255;image[:200,16]=0
        self.assertEqual(_image_rule_witness(image,**kwargs)['status'],'unknown')


@unittest.skipUnless(np is not None and Image is not None, "optional raster dependencies unavailable")
class MediaCoordinateTests(unittest.TestCase):
    def test_both_row_censuses_render_the_actual_media_not_crop(self):
        for fn in (probe_raster_row_bands,probe_oge_2024_alternate_bands):
            page=grid_page();opened=Mock();opened.__enter__=Mock(return_value=SimpleNamespace(pages=[page]));opened.__exit__=Mock()
            with tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'source';path.write_bytes(b'synthetic')
                with patch('pdfplumber.open',return_value=opened):
                    result=fn(path,expected_sha256=hashlib.sha256(b'synthetic').hexdigest(),page_numbers=[1])
            page.to_image.assert_called_once_with(resolution=216,force_mediabox=True)
            self.assertTrue(result['results'])

    def test_real_cropbox_and_rotations_keep_media_lines_in_pdf_coordinates(self):
        import pdfplumber
        from io import BytesIO
        for rotation in (0,90,180,270):
            stream=b"0 G 1 w 200 100 m 200 500 l S"
            objects=[b"<< /Type /Catalog /Pages 2 0 R >>",b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                     ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 792 612] /CropBox [2 3 790 609] /Rotate %d /Resources << >> /Contents 4 0 R >>"%rotation).encode(),
                     b"<< /Length "+str(len(stream)).encode()+b" >>\nstream\n"+stream+b"\nendstream"]
            data=b"%PDF-1.4\n";offsets=[0]
            for i,obj in enumerate(objects,1):
                offsets.append(len(data));data+=str(i).encode()+b" 0 obj\n"+obj+b"\nendobj\n"
            start=len(data);data+=b"xref\n0 5\n0000000000 65535 f \n"
            for offset in offsets[1:]:data+=("%010d 00000 n \n"%offset).encode()
            data+=("trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"%start).encode()
            with pdfplumber.open(BytesIO(data)) as document:
                page=document.pages[0];image=page.to_image(resolution=216,force_mediabox=True).original.convert("L")
                gray=np.asarray(image);line=page.lines[0]
                x=(line["x0"]+line["x1"])/2;y=(line["top"]+line["bottom"])/2
                px=int(x*gray.shape[1]/page.width);py=int(y*gray.shape[0]/page.height)
                with self.subTest(rotation=rotation):
                    self.assertLess(int(gray[py-2:py+3,px-2:px+3].min()),160)
                    self.assertAlmostEqual(image.width/page.width,3,delta=.02)
                    self.assertAlmostEqual(image.height/page.height,3,delta=.02)

    def test_page_ocr_media_render_is_explicit_and_default_stays_compatible(self):
        for force in (False,True):
            page=grid_page(scale=1)
            tsv=b'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
            with patch('unison_snapshot.ocr_geometry._engine',return_value=('synthetic','synthetic')),patch(
                    'unison_snapshot.ocr_geometry.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=tsv)):
                ocr_pdf_pages(SimpleNamespace(pages=[page]),force_mediabox=force,page_numbers=[1])
            kwargs={'resolution':200,'antialias':True}
            if force:kwargs['force_mediabox']=True
            page.to_image.assert_called_once_with(**kwargs)

    def test_confirmed_header_updates_basis_without_qualifying_content(self):
        grid=_oge_2024_image_grid(grid_page());words=header_words()
        census={"results":[{"page_number":1,"status":"coverage_unknown","physical_row_bands":[],
                  "row_detection_basis":"same_page_image_grid_pending_header","column_geometry_observation":grid}]}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"source";path.write_bytes(b"synthetic")
            with patch("unison_snapshot.oge_278t_raster_slots.census_with_2024_fallback",return_value=(census,{1:None})),patch(
                    "pdfplumber.open"),patch("unison_snapshot.oge_278t_raster_slots.ocr_pdf_pages",return_value=(
                        [SimpleNamespace(extract_words=lambda:words)],"synthetic")) as ocr:
                out=observe_raster_slots(path,expected_sha256=hashlib.sha256(b"synthetic").hexdigest(),page_numbers=[1])
            self.assertTrue(ocr.call_args.kwargs["force_mediabox"])
        self.assertEqual(out["confirmed_physical_slot_count"],31)
        self.assertEqual(out["pages"][0]["row_detection_basis"],"same_page_image_grid_header_confirmed")
        self.assertEqual(out["pages"][0]["column_geometry_observation"]["status"],"confirmed")
        self.assertFalse(out["production_mutation_authorized"])

    def test_wrong_header_does_not_produce_slots_and_retains_unbound_ocr(self):
        grid=_oge_2024_image_grid(grid_page());words=header_words();words[7]['text']='278e'
        census={'results':[{'page_number':1,'status':'coverage_unknown',
                 'physical_row_bands':[],'column_geometry_observation':grid}]}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source';path.write_bytes(b'synthetic')
            with patch('unison_snapshot.oge_278t_raster_slots.census_with_2024_fallback',return_value=(census,{1:EDGES})),patch(
                    'pdfplumber.open'),patch('unison_snapshot.oge_278t_raster_slots.ocr_pdf_pages',return_value=(
                        [SimpleNamespace(extract_words=lambda:words)],'synthetic')):
                out=observe_raster_slots(path,expected_sha256=hashlib.sha256(b'synthetic').hexdigest(),page_numbers=[1])
        self.assertEqual(out['confirmed_physical_slot_count'],0)
        self.assertIsNone(out['pages'][0]['physical_slot_count'])
        self.assertEqual(out['pages'][0]['unbound_page_ocr_words'],words)
        self.assertEqual(out['pages'][0]['slots'],[])
        self.assertFalse(out['production_mutation_authorized'])


if __name__=='__main__':unittest.main()
