"""Printed checkbox geometry controls; no OCR or official-source bytes required."""
import hashlib
import importlib.util
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from unison_snapshot.house_ptr_cell_observation_shadow import _printed_checkbox
from unison_snapshot.house_ptr_key_fields_shadow import _single_mark, _amount_marks
from unison_snapshot.house_ptr_paper_qualification import materialize_paper_observation


@unittest.skipUnless(importlib.util.find_spec('PIL') and importlib.util.find_spec('numpy'),
                     'raster libraries are optional in contract CI')
class HousePrintedCheckboxTests(unittest.TestCase):
    def image(self, marked=False):
        from PIL import Image, ImageDraw
        image = Image.new('RGB', (64, 60), 'white')
        pen = ImageDraw.Draw(image)
        pen.rectangle((9, 8, 55, 52), outline='black', width=3)
        if marked:
            pen.line((15, 15, 49, 46), fill='black', width=3)
            pen.line((49, 15, 15, 46), fill='black', width=3)
        return image

    def cell(self, label, marked=False):
        box = _printed_checkbox(self.image(marked))
        self.assertIsNotNone(box)
        return {'label': label, 'interior_dark_fraction': .2, 'printed_checkbox': box}

    def test_frame_ink_is_not_a_mark(self):
        empty = self.cell('sale')
        self.assertEqual(empty['printed_checkbox']['interior_dark_fraction'], 0)
        self.assertEqual(_single_mark([empty]), (None, 'no_strong_mark'))

    def test_purchase_and_partial_sale_roles_stay_distinct(self):
        labels = ['purchase', 'sale', 'partial_sale', 'exchange']
        cells = [self.cell(label, label == 'partial_sale') for label in labels]
        self.assertEqual(_single_mark(cells)[0], 'partial_sale')
        cells[0] = self.cell('purchase', True)
        self.assertEqual(_single_mark(cells), (None, 'multiple_possible_marks'))

    def test_eleven_amount_columns_do_not_invent_k_from_frame(self):
        cells = [self.cell(chr(65+i), i == 0) for i in range(11)]
        amount, _, spouse, _ = _amount_marks(cells)
        self.assertEqual(amount, 'A')
        self.assertIs(spouse, False)

    def test_unboxed_mark_keeps_existing_measurement(self):
        from PIL import ImageDraw
        image = self.image()
        ImageDraw.Draw(image).rectangle((45, 0, 63, 59), fill='white')
        self.assertIsNone(_printed_checkbox(image))
        self.assertEqual(_single_mark([{'label':'sale','interior_dark_fraction':.15}])[0], 'sale')

    def test_partially_located_box_family_is_not_mixed_with_old_frame_scores(self):
        cells = [self.cell('purchase', True), {'label':'sale','interior_dark_fraction':.2}]
        self.assertEqual(_single_mark(cells), (None, 'printed_box_geometry_incomplete'))

    def test_forged_printed_box_score_is_rejected_against_source_pixels(self):
        source = b'synthetic-source'
        sha = hashlib.sha256(source).hexdigest()
        cell = self.cell('purchase')
        cell['bbox_pixels'] = [0, 0, 64, 60]
        cell['printed_checkbox']['interior_dark_fraction'] = .99
        pdf = MagicMock()
        pdf.__enter__.return_value.pages = [SimpleNamespace(
            to_image=lambda **kwargs:SimpleNamespace(original=self.image()))]
        metadata = {'sha256':sha,'document_id':'9115808','filed_date':'2026-05-08',
                    'filing_year':2026,'filing_type':'P','source_id':'house_clerk',
                    'source_url':'https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/9115808.pdf',
                    'filer_name':'Rep. Hal Rogers','state_district':'KY05'}
        with patch('unison_snapshot.house_ptr_paper_qualification.qualify_paper_observation', return_value={}), patch(
                'unison_snapshot.house_ptr_fixed_source_core.check_source_cells',
                return_value={(1,2):{'cells':{'direction':[cell]}}}), patch('pdfplumber.open',return_value=pdf):
            with self.assertRaisesRegex(ValueError, 'printed checkbox differs'):
                materialize_paper_observation(source, {'source_sha256':sha,'pages':[]}, metadata,
                                             {'document_id':'9115808'})


if __name__ == '__main__':
    unittest.main()
