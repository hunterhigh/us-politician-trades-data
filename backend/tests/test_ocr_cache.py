import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image

from unison_snapshot.ocr_cache import cached_image_reader
from unison_snapshot.codec import digest


class SavedImageReadTests(unittest.TestCase):
    def test_existing_source_bound_read_is_adopted_without_first_time_reocr(self):
        with tempfile.TemporaryDirectory() as temporary:
            image=Image.new('L',(8,8),255)
            result={'date':'2026-01-02','status':'agreement'}
            seed={'binding':{'parser_version':'form/v1','mode':'L','size':[8,8],'pixel_sha256':digest(image.tobytes())},
                'engine_version':'unrecorded legacy engine','result':result}
            def forbidden(image):raise AssertionError('Unchanged original must not be re-OCRed')
            cached=cached_image_reader(temporary,parser_version='form/v1',engine_version='current engine',reader=forbidden,saved_reads=[seed])
            self.assertEqual(cached(image),result)
            self.assertEqual(cached(image),result)
            self.assertEqual(json.loads(next(Path(temporary).glob('*.json')).read_bytes())['engine_version'],'unrecorded legacy engine')
            other={**seed,'result':{'date':None,'status':'unreadable'}}
            with self.assertRaisesRegex(ValueError,'disagree'):
                cached_image_reader(temporary,parser_version='form/v1',engine_version='current',reader=forbidden,saved_reads=[seed,other])

    def test_unchanged_pixels_and_parser_reuse_without_repeating_ocr_or_correction_dependency(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls=[]
            def reader(image):calls.append(image.size);return {'date':'2026-01-02','status':'agreement'}
            image=Image.new('L',(8,8),255)
            first=cached_image_reader(temporary,parser_version='form/v1',engine_version='original engine',reader=reader)
            self.assertEqual(first(image),first(image))
            self.assertEqual(calls,[(8,8)])
            # A machine upgrade is not permission to overwrite an unchanged
            # source/parser cache. New reads still retain their engine version.
            second=cached_image_reader(temporary,parser_version='form/v1',engine_version='other engine',reader=reader)
            second(image);self.assertEqual(len(calls),1)
            image.putpixel((0,0),0);second(image);self.assertEqual(len(calls),2)
            newer=cached_image_reader(temporary,parser_version='form/v2',engine_version='other engine',reader=reader)
            newer(image);self.assertEqual(len(calls),3)

    def test_changed_cache_is_rejected_without_running_ocr(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls=[]
            def reader(image):calls.append(True);return {'date':None,'status':'unreadable'}
            cached=cached_image_reader(temporary,parser_version='form/v1',engine_version='engine',reader=reader)
            image=Image.new('L',(8,8),255);cached(image)
            path=next(Path(temporary).glob('*.json'));value=json.loads(path.read_bytes())
            value['result']['date']='2026-01-02';path.write_text(json.dumps(value),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'source binding'):cached(image)
            self.assertEqual(len(calls),1)
