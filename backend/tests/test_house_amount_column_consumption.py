"""Selected header consumption, conflict isolation and exact crop scope."""
from copy import deepcopy
import hashlib
import io
import unittest
from unittest.mock import patch

import test_house_ptr_paper_qualification as paper_tests
from unison_snapshot.house_ptr_amount_header import LABELS, evaluate_amount_header_pages
from unison_snapshot.house_ptr_paper_qualification import _bound_amount_column, materialize_paper_observation


class AmountColumnConsumptionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = paper_tests.HousePaperQualificationTests()
        self.fixture.setUp()
        self.raw, self.header, self.reread, self.slots = self.fixture._source_bound_fixture()
        self.row = self.fixture.observation['pages'][0]['rows'][0]

    def consume(self, amount, *, header=None, copies=1):
        value = deepcopy(header or self.header)
        value.update(reading_mode='printed_columns/v1',
                     column_readings=[{'page': 1, 'label': amount['letter']}] * copies)
        # Isolate selection and conflicting-read policy. Exact source crop
        # verification is exercised separately below, without mocking it.
        with patch('unison_snapshot.house_ptr_fixed_source_core.check_source_cells', return_value=self.slots), \
             patch('unison_snapshot.house_ptr_paper_qualification._asset_reading_is_bound', return_value=True), \
             patch('unison_snapshot.house_ptr_paper_qualification._bound_amount_column', return_value=amount) as bound:
            result = materialize_paper_observation(self.raw, self.fixture.observation,
                self.fixture.metadata, self.fixture.identity, amount_header=value, field_reread=self.reread)
        return result, bound

    def test_equal_reads_preserve_fact_and_conflicting_a_bounds_isolate_it(self):
        a = {'letter': 'A', 'low': 1000, 'high': 15000, 'kind': 'range'}
        valid, bound = self.consume(a)
        self.assertEqual(valid['transactions'][0]['amount_low'], 1000)
        bound.assert_called_once()
        rejected, _ = self.consume({**a, 'low': 1001})
        self.assertEqual(rejected['transactions'], [])
        self.assertIn('amount_header_reading_conflict', rejected['quarantined'][0]['reasons'])

    def test_missing_unrelated_label_does_not_hide_same_page_a_conflict(self):
        partial = evaluate_amount_header_pages(self.fixture.sha, [{'page': 1,
            'status': 'header_ocr_observed', 'raw_header_text': '\n'.join([
                '$1,000-$15,000', *(text for key, text in LABELS.items() if key != 'G')])}])
        self.assertIsNone(partial['ranges'])
        valid, _ = self.consume({'letter': 'A', 'low': 1000, 'high': 15000, 'kind': 'range'}, header=partial)
        self.assertEqual(len(valid['transactions']), 1)
        rejected, _ = self.consume({'letter': 'A', 'low': 1001, 'high': 15000, 'kind': 'range'}, header=partial)
        self.assertEqual(rejected['transactions'], [])
        self.assertIn('amount_header_reading_conflict', rejected['quarantined'][0]['reasons'])

    def test_k_only_uses_supplemental_column_and_keeps_open_upper_bound(self):
        for cell in self.row['cells']['amount']:
            cell['interior_dark_fraction'] = .13 if cell['label'] == 'K' else .001
        header = evaluate_amount_header_pages(self.fixture.sha, [{'page': 1, 'status': 'header_not_selected'}])
        amount = {'letter': 'K', 'low': 1000000, 'high': None, 'low_exclusive': True,
                  'kind': 'spouse_dc_special_open_ended'}
        result, bound = self.consume(amount, header=header)
        bound.assert_called_once()
        fact = result['transactions'][0]
        self.assertEqual(fact['owner'], 'Spouse')
        self.assertEqual(fact['amount_low'], 1000000)
        self.assertIsNone(fact['amount_high'])
        self.assertIs(fact['amount_low_exclusive'], True)
        for label in ('A', 'B'):
            next(cell for cell in self.row['cells']['amount'] if cell['label'] == label)['interior_dark_fraction'] = .13
        rejected, bound = self.consume(amount, header=header)
        bound.assert_not_called()
        self.assertEqual(rejected['transactions'], [])

    def test_k_cannot_grant_a_joint_owner_the_spouse_special_amount(self):
        self.row['cells']['owner']['raw_text'] = 'JT'
        for cell in self.row['cells']['amount']:
            cell['interior_dark_fraction'] = .13 if cell['label'] == 'K' else .001
        header = evaluate_amount_header_pages(self.fixture.sha, [{'page': 1, 'status': 'header_not_selected'}])
        rejected, _ = self.consume({'letter': 'K', 'low': 1000000, 'high': None,
            'low_exclusive': True, 'kind': 'spouse_dc_special_open_ended'}, header=header)
        self.assertEqual(rejected['transactions'], [])
        self.assertIn('spouse_dc_open_ended_amount_not_representable', rejected['quarantined'][0]['reasons'])

    def test_duplicate_column_readings_cannot_outvote_a_complete_header(self):
        rejected, bound = self.consume({'letter': 'A', 'low': 1000, 'high': 15000, 'kind': 'range'}, copies=2)
        bound.assert_not_called()
        self.assertEqual(rejected['transactions'], [])
        self.assertIn('amount_column_reading_duplicated', rejected['quarantined'][0]['reasons'])

    def test_exact_header_band_is_required_even_for_same_column_and_pixels(self):
        from PIL import Image
        image = Image.new('L', (1000, 1000), 'white')
        region = {'status': 'located_shadow', 'coordinate_frame': 'original_page', 'header_band': [.1, .2]}
        columns = {'status': 'located_shadow', 'amount': [i / 10 for i in range(11)]}
        physical = {'band': [.3, .4], 'cells': {'amount': [
            {'label': 'B', 'bbox_normalized': [.1, .3, .2, .4]}]}}
        reading = {'page': 1, 'label': 'B', 'status': 'raw_column_observed', 'raw': '$15,001-$50,000',
            'render_dpi': 300, 'image_rotation_degrees': 270, 'coordinate_frame': 'original_page',
            'amount_column_count': 10}
        def bind(box, **changes):
            stream = io.BytesIO()
            image.crop(tuple(box)).rotate(270, expand=True).save(stream, format='PNG')
            return _bound_amount_column(b'unused', {**reading, 'bbox_pixels': box,
                'crop_sha256': hashlib.sha256(stream.getvalue()).hexdigest(), **changes}, physical, 1, {1: image})
        self.assertIsNone(bind([102, 105, 198, 195]))  # White pixels have no located header.
        with patch('unison_snapshot.house_ptr_region_shadow.locate_transaction_grid', return_value=region), \
             patch('unison_snapshot.house_ptr_cell_observation_shadow._column_boxes', return_value=columns):
            self.assertEqual(bind([102, 105, 198, 195])['low'], 15001)
            self.assertIsNone(bind([102, 210, 198, 290]))
            self.assertIsNone(bind([102, 105, 198, 195], page=2))
            self.assertIsNone(bind([102, 105, 198, 195], amount_column_count=11))


if __name__ == '__main__':
    unittest.main()
