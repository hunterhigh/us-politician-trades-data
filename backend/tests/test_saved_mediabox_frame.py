"""Explicit saved MediaBox frames; synthetic image tests are not OCR accuracy."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_oge_dual_axis_geometry import image_page
from test_oge_278t_image_grid import header_words
from test_oge_annual import _Document
from unison_snapshot.ocr_geometry import OcrPage, mediabox_render_receipt, validate_mediabox_render_receipt
from unison_snapshot.oge_278t_raster_slots import observe_raster_slots, _dual_axis_image_grid, DUAL_MODE


class SavedMediaBoxFrameTests(unittest.TestCase):
    def fixture(self):
        page=image_page();page.cropbox=(1,2,791,610)
        words=header_words();sha=hashlib.sha256(b'synthetic source').hexdigest()
        receipt=mediabox_render_receipt(page,source_sha256=sha,page_number=1,resolution=300,words=words)
        return page,words,sha,receipt

    def test_recomputes_original_render_and_preserves_words(self):
        page,words,sha,receipt=self.fixture();before=deepcopy(words)
        saved=OcrPage(width=792,height=612,words=words,render_frame=receipt)
        validate_mediabox_render_receipt(page,saved,source_sha256=sha,page_number=1)
        self.assertEqual(saved.extract_words(),before)
        page.to_image.assert_called_with(resolution=300,antialias=True,force_mediabox=True)

    def test_wrong_source_page_boxes_size_hash_dpi_and_frame_are_rejected(self):
        page,words,sha,receipt=self.fixture()
        for key,value in [('source_sha256','f'*64),('page_number',2),('crop_box',[0,0,792,612]),
                ('media_box',[0,0,790,612]),('pixel_size',[1,1]),('pixel_sha256','0'*64),
                ('coordinate_frame','cropbox'),('resolution',True),('force_mediabox',False)]:
            with self.subTest(key=key):
                bad=deepcopy(receipt);bad[key]=value
                with self.assertRaises(ValueError):
                    validate_mediabox_render_receipt(page,OcrPage(width=792,height=612,words=words,render_frame=bad),source_sha256=sha,page_number=1)
        bad_words=deepcopy(words);bad_words[0]['x0']+=1
        with self.assertRaises(ValueError):
            validate_mediabox_render_receipt(page,OcrPage(width=792,height=612,words=bad_words,render_frame=receipt),source_sha256=sha,page_number=1)

    def test_changed_original_pixels_are_rejected(self):
        page,words,sha,receipt=self.fixture()
        page.to_image.return_value.original.putpixel((20,20),0)
        with self.assertRaises(ValueError):
            validate_mediabox_render_receipt(page,OcrPage(width=792,height=612,words=words,render_frame=receipt),source_sha256=sha,page_number=1)

    def test_cropped_saved_page_requires_frame_before_existing_geometry_consumer(self):
        page,words,sha,receipt=self.fixture()
        geometry=_dual_axis_image_grid(page)
        census={'results':[{'page_number':1,'status':'coverage_unknown','column_geometry_observation':geometry}]}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source.pdf';path.write_bytes(b'synthetic source')
            with patch('unison_snapshot.oge_278t_raster_slots.census_with_2024_fallback',return_value=(census,{})), patch('pdfplumber.open',return_value=_Document([page])), patch('unison_snapshot.oge_278t_raster_slots.ocr_pdf_pages',side_effect=AssertionError('new OCR forbidden')):
                with self.assertRaisesRegex(ValueError,'coordinate frame'):
                    observe_raster_slots(path,expected_sha256=sha,page_numbers=[1],saved_ocr_pages={1:OcrPage(width=792,height=612,words=words)},saved_ocr_engine='saved',geometry_mode=DUAL_MODE)
                result=observe_raster_slots(path,expected_sha256=sha,page_numbers=[1],saved_ocr_pages={1:OcrPage(width=792,height=612,words=words,render_frame=receipt)},saved_ocr_engine='saved',geometry_mode=DUAL_MODE)
        self.assertEqual(result['pages'][0]['render_frame'],receipt)
        self.assertTrue(result['pages'][0]['slots'])
        self.assertEqual(result['pages'][0]['column_geometry_observation']['header_role_evidence']['status'],'confirmed')


if __name__=='__main__':unittest.main()
