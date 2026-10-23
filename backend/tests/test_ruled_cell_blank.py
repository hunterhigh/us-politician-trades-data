"""Independent print witnesses must not absorb target-cell writing."""
from copy import deepcopy
import hashlib
import importlib.util
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from unison_snapshot.ruled_cell_blank import inspect_ruled_cell_blank


@unittest.skipUnless(importlib.util.find_spec('PIL') and importlib.util.find_spec('numpy'),'optional raster dependencies')
class RuledCellBlankTests(unittest.TestCase):
    def scene(self,*,offset=0,width=1):
        from PIL import Image,ImageDraw
        image=Image.new('L',(600+offset,700+offset),255)
        box=[100+offset,300+offset,200+offset,350+offset]
        pen=ImageDraw.Draw(image)
        for x in (box[0],box[2]):pen.line((x,50,x,650+offset),fill=0,width=width)
        for y in (box[1],box[3]):pen.line((30,y,550+offset,y),fill=0,width=width)
        horizontal=[[220+offset,350+offset],[370+offset,500+offset]]
        vertical=[[100+offset,200+offset],[400+offset,500+offset]]
        return image,box,horizontal,vertical

    def observe(self,image,box,horizontal,vertical):
        return inspect_ruled_cell_blank(image,box,horizontal_witness_spans=horizontal,vertical_witness_spans=vertical)

    def test_unique_printing_and_all_light_cells_keep_full_cell_accounting(self):
        from PIL import Image
        for offset in (0,37):
            for width in (1,2,3):
                with self.subTest(offset=offset,width=width):
                    image,box,h,v=self.scene(offset=offset,width=width)
                    before=image.tobytes();out=self.observe(image,box,h,v)
                    self.assertEqual(out['status'],'blank',out)
                    self.assertEqual(out['unexplained_dark_pixel_count'],0)
                    self.assertEqual(out['observed_dark_pixel_count'],out['removed_rule_pixel_count'])
                    self.assertFalse(out['target_ink_used_to_estimate_rules'])
                    self.assertEqual(image.tobytes(),before)
        image=Image.new('L',(600,700),255)
        out=self.observe(image,[100,300,200,350],[],[])
        self.assertEqual(out['status'],'blank');self.assertEqual(out['removed_rule_pixel_count'],0)

    def test_every_corner_pixel_and_near_edge_stroke_remains_unexplained(self):
        from PIL import ImageDraw
        locations=[(101,301),(199,301),(101,349),(199,349),(150,325)]
        lines=[(70,307,215,307),(70,343,215,343),(107,270,107,370),(193,270,193,370)]
        clean=self.observe(*self.scene())['independent_rule_witnesses']
        for pixel in locations:
            with self.subTest(pixel=pixel):
                image,box,h,v=self.scene();image.putpixel(pixel,0)
                out=self.observe(image,box,h,v)
                self.assertEqual(out['status'],'unknown')
                self.assertEqual(out['unexplained_dark_pixel_count'],1)
                self.assertEqual(out['independent_rule_witnesses'],clean)
        for line in lines:
            with self.subTest(line=line):
                image,box,h,v=self.scene();ImageDraw.Draw(image).line(line,fill=0,width=1)
                out=self.observe(image,box,h,v)
                self.assertEqual(out['status'],'unknown')
                self.assertGreater(out['unexplained_dark_pixel_count'],0)
                self.assertEqual(out['independent_rule_witnesses'],clean)

    def test_missing_broken_ambiguous_or_thick_frames_do_not_prove_blank(self):
        from PIL import ImageDraw
        for case in ('broken','ambiguous','missing','thick','few_spans','overlap_spans','inside_spans'):
            with self.subTest(case=case):
                image,box,h,v=self.scene(width=9 if case=='thick' else 1)
                if case=='broken':image.putpixel((150,300),255)
                if case=='ambiguous':ImageDraw.Draw(image).line((30,306,550,306),fill=0,width=1)
                if case=='missing':ImageDraw.Draw(image).rectangle((220,295,550,305),fill=255)
                if case=='few_spans':h=h[:1]
                if case=='overlap_spans':h=[h[0],h[0]]
                if case=='inside_spans':h=[[110,190],[220,350]]
                self.assertEqual(self.observe(image,box,h,v)['status'],'unknown')

    def test_unwitnessed_curves_and_unavailable_source_windows_stay_unknown(self):
        from PIL import ImageDraw
        image,box,h,v=self.scene()
        ImageDraw.Draw(image).line((130,299,180,297),fill=0,width=1)
        # Ink wholly outside the calibrated cell does not change its contents.
        self.assertEqual(self.observe(image,box,h,v)['status'],'blank')
        ImageDraw.Draw(image).line((130,301,180,303),fill=0,width=1)
        self.assertEqual(self.observe(image,box,h,v)['status'],'unknown')
        self.assertEqual(self.observe(image,[2,300,200,350],h,v)['reason'],'source_cell_window_unsupported')

    @unittest.skipUnless(importlib.util.find_spec('pymupdf') and importlib.util.find_spec('pdfplumber'),
                         'optional PDF dependencies')
    def test_generated_pdf_is_recomputed_through_the_normal_blank_owner_consumer(self):
        import pymupdf
        import pdfplumber
        from PIL import ImageDraw
        from test_house_ptr_blank_owner import HouseBlankPaperOwnerTests
        from unison_snapshot.house_ptr_paper_qualification import _blank_paper_owner
        fixture=HouseBlankPaperOwnerTests();fixture.setUp();fixture.draw_rules()
        for marked in (False,True):
            image=fixture.image.copy()
            if marked:ImageDraw.Draw(image).line((80,407,220,407),fill=0,width=1)
            png=io.BytesIO();image.save(png,format='PNG')
            document=pymupdf.open();page=document.new_page(width=360,height=360)
            page.insert_image(page.rect,stream=png.getvalue());raw=document.tobytes();document.close()
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                actual=pdf.pages[0].to_image(resolution=200).original
            observation=deepcopy(fixture.f.observation);observation['source_sha256']=hashlib.sha256(raw).hexdigest()
            physical=observation['pages'][0]['rows'][0]
            encoded=io.BytesIO();actual.crop(tuple(physical['cells']['owner']['bbox_pixels'])).save(encoded,format='PNG')
            physical['cells']['owner']['crop_sha256']=hashlib.sha256(encoded.getvalue()).hexdigest()
            columns={'status':'located_shadow','owner':[.1,.2],'asset':[.2,.6],
                     'event':[.6,.7],'notification':[.7,.8]}
            with patch('unison_snapshot.house_ptr_region_shadow.locate_transaction_grid',return_value=fixture.grid),patch(
                    'unison_snapshot.house_ptr_cell_observation_shadow._column_boxes',return_value=columns):
                first=_blank_paper_owner(raw,observation,physical,1,{})
                second=_blank_paper_owner(raw,observation,physical,1,{})
            self.assertEqual(first,second)
            if marked:self.assertIsNone(first)
            else:
                self.assertEqual(first['owner'],'Undisclosed')
                self.assertEqual(first['source_sha256'],observation['source_sha256'])
                self.assertEqual(first['source_blank_observation']['status'],'blank')


if __name__=='__main__':unittest.main()
