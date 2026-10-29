"""Pure source-shape controls; synthetic glyphs do not claim original accuracy."""
from copy import deepcopy
from PIL import Image,ImageDraw
import numpy as np
import unittest
from unison_snapshot.pdf_source_date_sequences import glyph_sequence_shape,compare_auxiliary_date


def masks():
    one=Image.new('L',(70,120));d=ImageDraw.Draw(one);d.line([(30,15),(40,10),(40,110)],fill=255,width=9);d.line([(25,110),(53,110)],fill=255,width=9)
    slash=Image.new('L',(70,120));ImageDraw.Draw(slash).line([(50,10),(20,110)],fill=255,width=9)
    nine=Image.new('L',(70,120));d=ImageDraw.Draw(nine);d.ellipse([10,10,60,65],outline=255,width=10);d.line([(55,45),(55,100),(35,110)],fill=255,width=10)
    return [np.flipud(np.asarray(x)) for x in (one,slash,nine)]


class SourceDateSequenceTests(unittest.TestCase):
    def test_original_one_is_not_slash_and_one_plus_nine_is_not_atomic_nine(self):
        one,slash,nine=masks()
        self.assertFalse(glyph_sequence_shape(one)['supports_single_slash'])
        self.assertTrue(glyph_sequence_shape(slash)['supports_single_slash'])
        self.assertFalse(glyph_sequence_shape(slash)['supports_single_nine'])
        self.assertTrue(glyph_sequence_shape(nine)['supports_single_nine'])
        composite=np.concatenate([one,np.zeros((120,5),dtype='uint8'),nine],axis=1)
        self.assertFalse(glyph_sequence_shape(composite)['supports_single_nine'])
        self.assertFalse(glyph_sequence_shape(np.zeros((20,20),dtype='uint8'))['supports_single_slash'])

    def test_only_proved_whole_source_nine_and_single_extra_day_one_is_explained(self):
        record={'status':'source_slash_nine_supported','raw_text':'7/9/2025 ',
            'source_object_index':1,'slash_nine_glyph_indices':[1,2]}
        actual=compare_auxiliary_date(record,'7/19/2025')
        self.assertEqual(actual['status'],'auxiliary_extra_day_one_contradicted_by_source_glyph_sequence')
        self.assertEqual(actual['raw_source_text'],'7/9/2025 ')
        for raw in ['7/9/2025','7/29/2025','7/119/2025','7/19/2026','7/19/2025 comment','19','7/1/2025']:
            with self.subTest(raw=raw):self.assertEqual(compare_auxiliary_date(record,raw)['status'],'not_resolved')
        for source in ['7/19/2025','7/11/2025','7/12/2025']:
            with self.subTest(source=source):
                bound=deepcopy(record);bound['raw_text']=source
                self.assertEqual(compare_auxiliary_date(bound,source)['status'],'not_resolved')
                self.assertEqual(compare_auxiliary_date(bound,'7/9/2025')['status'],'not_resolved')
        record['status']='unsupported'
        self.assertEqual(compare_auxiliary_date(record,'7/19/2025')['status'],'not_resolved')

    def test_producer_missing_ambiguous_and_transformed_source_content_stays_unknown(self):
        import hashlib
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from unison_snapshot.pdf_source_date_sequences import observe_source_date_sequences
        with TemporaryDirectory() as folder:
            source=Path(folder)/'source.pdf';source.write_bytes(b'synthetic source');sha=hashlib.sha256(source.read_bytes()).hexdigest()
            obj={'source_object_index':1,'raw_text':'7/9/2025','bbox_displayed_mediabox':[80,102,98,108],
                'matrix_pdf_bottom_left':[1,0,0,1,80,40],'font_base_name':'mock','font_size':6,
                'exclusion_reasons':[],'observation_issues':[]}
            glyphs=[{'cid':i,'raw_unicode':c,'source_matrix':[1,0,0,1,80+i,40],'advance_pdf_points':1} for i,c in enumerate('7/9/2025')]
            row={'font':'mock','raw_text':'7/9/2025','matrix':[1,0,0,1,80,40],'font_size':6,'ctm':[1,0,0,1,0,0],'linematrix':[0,0],'rise':0,'glyphs':glyphs}
            fonts={'mock':[{'data':b'not a font','font_resource':'F1','font_program_sha256':'f'*64,'to_unicode_sha256':'e'*64}]}
            inventory={'objects':[obj],'text_object_manifest_sha256':'a'*64}
            for kind in ('missing','multiple_content','multiple_font','transform','charset'):
                rows=[deepcopy(row)];f=deepcopy(fonts)
                if kind=='missing':rows=[]
                if kind=='multiple_content':rows*=2
                if kind=='multiple_font':f['mock']*=2
                if kind=='transform':rows[0]['ctm']=[2,0,0,2,0,0]
                with self.subTest(kind=kind),patch('unison_snapshot.pdf_source_date_sequences.observe_pdf_text_objects',return_value=inventory),patch('unison_snapshot.pdf_source_date_sequences._content_and_fonts',return_value=(rows,f)):
                    result=observe_source_date_sequences(source,expected_sha256=sha,page_number=1)
                self.assertEqual(result['observations'][0]['status'],'unsupported')

if __name__=='__main__':unittest.main()
