"""Synthetic negative controls; real 9115711 is replayed only from fixed local evidence."""
from copy import deepcopy
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from unison_snapshot.house_ptr_cell_observation_shadow import _cell
from unison_snapshot.house_ptr_paper_qualification import _source_bound_no_transactions


@unittest.skipUnless(importlib.util.find_spec("PIL") and importlib.util.find_spec("numpy"),
                     "raster libraries are optional in contract CI")
class HousePaperNoTransactionsTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.image = Image.new('RGB', (1000, 1000), 'white')
        self.page = SimpleNamespace(width=360, height=360)
        self.rows = []
        for n, (state, text) in enumerate([
                ('title_legend', 'Example Mega Corp Common Stock'),
                ('unknown', 'Nothing to report for February 2026'),
                ('blank', '')], 1):
            top, bottom = .1*n, .1*n+.08
            def cell(label, left, right):
                return _cell(self.image, [], self.page, label=label,
                             x0=left, x1=right, y0=top, y1=bottom)
            cells = {'asset': cell('asset', .1, .5), 'owner': cell('owner', .02, .08),
                     'event_date': cell('event_date', .51, .61),
                     'notification_date': cell('notification_date', .62, .72),
                     'direction': [cell('purchase', .73, .8)],
                     'amount': [cell('A', .81, .9)]}
            cells['asset']['raw_text'] = text
            self.rows.append({'physical_band_index': n, 'disposition': state,
                              'band': [top, bottom], 'cells': cells})
        self.obs = {'source_sha256': 'a'*64, 'render_dpi': 200, 'pages': [{
            'page': 1, 'region_status': 'located_shadow', 'coordinate_frame': 'original_page',
            'cell_status': 'raw_observed_unverified', 'rows': self.rows}]}
        self.grid = {'physical_row_bands': [{'band': r['band'], 'disposition': r['disposition']}
                                          for r in self.rows]}

    def evaluate(self):
        pdf = MagicMock()
        pdf.__enter__.return_value.pages = [SimpleNamespace(
            to_image=lambda **kw: SimpleNamespace(original=self.image))]
        with patch('pdfplumber.open', return_value=pdf), patch(
                'unison_snapshot.house_ptr_region_shadow.locate_transaction_grid', return_value=self.grid), patch(
                'unison_snapshot.house_ptr_cell_observation_shadow._column_boxes', return_value={
                    'status':'located_shadow','asset':[.1,.5],'owner':[.02,.08],
                    'event':[.51,.61],'notification':[.62,.72],'action':[.73,.8],'amount':[.81,.9]}), patch(
                'unison_snapshot.house_ptr_fixed_source_core.check_source_cells') as check:
            result = _source_bound_no_transactions(b'synthetic', self.obs)
            if (self.obs['pages'][0]['cell_status'] == 'raw_observed_unverified' and
                    any(row.get('cells', {}).get('asset', {}).get('raw_text') ==
                        'Nothing to report for February 2026' for row in self.obs['pages'][0]['rows'])):
                check.assert_called_once_with(b'synthetic', self.obs, page_count=1,
                    allow_unobserved=False, verify_crop_hashes=True)
            return result

    def test_explicit_statement_example_and_blank_have_no_logical_trade(self):
        result = self.evaluate()
        self.assertEqual(result['ledger']['logical_row_count'], 0)
        self.assertEqual(result['ledger']['disposition_counts'], {'blank': 1, 'note': 2})
        self.assertEqual(result['declaration']['status'], 'explicit_no_transactions')

    def test_missing_ocr_or_empty_text_never_proves_zero(self):
        self.rows[1]['cells']['asset']['raw_text'] = ''
        self.assertIsNone(self.evaluate())

    def test_example_label_is_required_not_merely_short_first_band(self):
        self.rows[0]['cells']['asset']['raw_text'] = 'Real company stock'
        self.assertIsNone(self.evaluate())

    def test_transaction_date_or_mark_cannot_coexist_with_zero(self):
        from PIL import ImageDraw
        self.rows[1]['cells']['event_date']['raw_text'] = '2/1/2026'
        self.assertIsNone(self.evaluate())
        self.rows[1]['cells']['event_date']['raw_text'] = ''
        ImageDraw.Draw(self.image).rectangle((750, 230, 770, 250), fill='black')
        # Do not trust a saved zero ink score over the actual source pixels.
        self.assertEqual(self.rows[1]['cells']['direction'][0]['interior_dark_fraction'], 0)
        self.assertIsNone(self.evaluate())

    def test_omitted_mark_column_or_moved_crop_cannot_prove_zero(self):
        original = self.rows[1]['cells']['amount']
        self.rows[1]['cells']['amount'] = []
        self.assertIsNone(self.evaluate())
        self.rows[1]['cells']['amount'] = original
        self.rows[1]['cells']['event_date']['bbox_pixels'][0] += 1
        self.assertIsNone(self.evaluate())

    def test_omitted_physical_band_is_not_complete_coverage(self):
        self.obs['pages'][0]['rows'] = self.rows[:-1]
        self.assertIsNone(self.evaluate())

    def test_unobserved_page_never_proves_report_zero(self):
        self.obs['pages'][0]['cell_status'] = 'unobserved_not_selected'
        self.assertIsNone(self.evaluate())

    def test_modified_geometry_is_not_complete_coverage(self):
        self.obs['pages'][0]['rows'] = deepcopy(self.rows)
        self.obs['pages'][0]['rows'][1]['band'][0] += .001
        self.assertIsNone(self.evaluate())


if __name__ == '__main__':
    unittest.main()
