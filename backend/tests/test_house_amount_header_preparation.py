"""Column preparation contracts with generated PDFs and fixed recognizer output."""
from copy import deepcopy
import hashlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_house_ptr_key_fields_shadow import report, amount_cells
from unison_snapshot.house_ptr_amount_header import (
    evaluate_amount_header_pages, interpret_amount_column)
from unison_snapshot.house_ptr_amount_header_preparation import (
    plan_amount_header_columns, prepare_amount_headers, missing_amount_header_columns)


class AmountHeaderPreparationTests(unittest.TestCase):
    def bridge_fixture(self):
        from test_house_paper_pipeline import HousePaperPipelineTests
        fixture = HousePaperPipelineTests('test_qualified_legacy_rows_are_preserved_without_ocr')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.observation = report()
        fixture.observation['source_sha256'] = fixture.sha
        fixture.observation['pages'][0].update(coordinate_frame='original_page', orientation_degrees=0)
        fixture.header = evaluate_amount_header_pages(fixture.sha, [
            {'page': 1, 'status': 'header_ocr_observed', 'raw_header_text': '$1,000-$15,000'}])
        return fixture

    def test_normal_bridge_prepares_selected_column_and_saves_it(self):
        from unison_snapshot.house_paper_pipeline import bridge_paper_report
        fixture = self.bridge_fixture()
        fresh = deepcopy(fixture.header)
        fresh.update(reading_mode='printed_columns/v1', column_readings=[{
            'page': 1, 'label': 'B', 'status': 'raw_column_observed', 'raw': '$15,001-$50,000',
            'crop_sha256': 'b' * 64, 'render_dpi': 300, 'image_rotation_degrees': 270,
            'coordinate_frame': 'original_page', 'bbox_pixels': [1, 2, 3, 4]}])
        fresh['pages'][0].update(status='selected_columns_observed', amount_column_count=11)
        with (patch('unison_snapshot.house_paper_pipeline.observe_house_ptr_pdf', return_value=fixture.observation),
              patch('unison_snapshot.house_paper_pipeline.observe_amount_header', return_value=fixture.header),
              patch('unison_snapshot.house_ptr_amount_header_preparation.observe_amount_header', return_value=fresh) as ocr,
              patch('unison_snapshot.house_paper_pipeline.qualify_paper_observation', return_value=fixture.paper)):
            outcome = bridge_paper_report(evidence_root=fixture.evidence, review_root=fixture.review,
                metadata_path=fixture.metadata_path, tesseract='fixed', observe_new=True)
        import json
        saved = json.loads((fixture.review / 'house_clerk/paper_amount_headers' / fixture.relative).read_text())
        self.assertEqual(outcome, 'observed_and_qualified')
        self.assertEqual(ocr.call_args.kwargs['column_labels'], ['B'])
        self.assertEqual(saved['column_readings'][0]['raw'], '$15,001-$50,000')
        self.assertEqual(saved['pages'], fixture.header['pages'])

    def test_saved_materialization_never_prepares_even_missing_columns(self):
        from unison_snapshot.house_paper_pipeline import bridge_paper_report
        fixture = self.bridge_fixture()
        fixture._write(fixture.review / 'house_clerk/paper_observations' / fixture.relative, fixture.observation)
        fixture._write(fixture.review / 'house_clerk/paper_amount_headers' / fixture.relative, fixture.header)
        with (patch('unison_snapshot.house_paper_pipeline.prepare_amount_headers') as prepare,
              patch('unison_snapshot.house_paper_pipeline.qualify_paper_observation', return_value=fixture.paper),
              patch('unison_snapshot.house_paper_pipeline.materialize_paper_observation',
                    side_effect=RuntimeError('saved materializer reached'))):
            with self.assertRaisesRegex(RuntimeError, 'saved materializer reached'):
                bridge_paper_report(evidence_root=fixture.evidence, review_root=fixture.review,
                    metadata_path=fixture.metadata_path, tesseract='absent', observe_new=True, materialize_saved=True)
        prepare.assert_not_called()

    def test_batch_only_explicit_cached_target_can_prepare_missing_columns(self):
        from unison_snapshot.house_paper_pipeline import bridge_paper_batch
        for explicit, saved in ((True, False), (False, False), (True, True)):
            with self.subTest(explicit=explicit, materialize_saved=saved):
                fixture = self.bridge_fixture()
                fixture._write(fixture.review / 'house_clerk/paper_observations' / fixture.relative, fixture.observation)
                fixture._write(fixture.review / 'house_clerk/paper_amount_headers' / fixture.relative, fixture.header)
                batch = fixture.review / 'batch.txt'
                batch.write_text('', encoding='utf-8')
                with (patch('unison_snapshot.house_paper_pipeline.prepare_amount_headers', return_value=fixture.header) as prepare,
                      patch('unison_snapshot.house_paper_pipeline.observe_house_ptr_pdf') as observe,
                      patch('unison_snapshot.house_paper_pipeline.observe_amount_header') as whole_header,
                      patch('unison_snapshot.house_paper_pipeline.qualify_paper_observation', return_value=fixture.paper),
                      patch('unison_snapshot.house_paper_pipeline.materialize_paper_observation',
                            side_effect=RuntimeError('saved materializer reached')) as materialize):
                    result = bridge_paper_batch(evidence_root=fixture.evidence, review_root=fixture.review,
                        parse_batch=batch, tesseract='fixed',
                        document_id=fixture.metadata['document_id'] if explicit else None,
                        materialize_saved=saved)
                observe.assert_not_called()
                whole_header.assert_not_called()
                if explicit and not saved:
                    prepare.assert_called_once()
                else:
                    prepare.assert_not_called()
                if saved:
                    materialize.assert_called_once()
                    self.assertEqual(result, {'paper_observation_failed': 1})
                else:
                    self.assertEqual(result, {'requalified_existing_observation': 1})

    def fixture(self, *, count=11):
        import pymupdf
        import pdfplumber.display  # Load renderer before patching recognizer subprocesses.
        doc = pymupdf.open()
        page = doc.new_page(width=600, height=800)
        for number in range(count + 1):
            x = 300 + number * 24
            page.draw_line((x, 80), (x, 640))
        for y in (80, 240, 320, 400, 480, 560, 640):
            page.draw_line((300, y), (300 + count * 24, y))
        page.insert_text((325, 190), '$15,001-$50,000', rotate=90)
        raw = doc.tobytes(); doc.close()
        observed = report()
        observed['source_sha256'] = hashlib.sha256(raw).hexdigest()
        observed['pages'][0].update(coordinate_frame='original_page', orientation_degrees=0)
        observed['pages'][0]['rows'][0]['cells']['amount'] = amount_cells()[:count]
        header = evaluate_amount_header_pages(observed['source_sha256'], [
            {'page': 1, 'status': 'header_ocr_observed', 'amount_column_count': count,
             'raw_header_text': '$1,000-$15,000'}])
        region = {'status': 'located_shadow', 'coordinate_frame': 'original_page',
                  'header_band': [.1, .3]}
        columns = {'status': 'located_shadow', 'amount': [.5 + i * .04 for i in range(count + 1)]}
        return raw, observed, header, region, columns

    def test_plans_only_unambiguous_selected_columns_without_dates(self):
        raw, observed, header, _, _ = self.fixture()
        row = observed['pages'][0]['rows'][0]
        row['cells']['event_date']['raw_text'] = 'unreadable'
        observed['pages'][0]['rows'].append(dict(deepcopy(row), physical_band_index=3))
        self.assertEqual(plan_amount_header_columns(observed)['requests'], [
            {'page': 1, 'label': 'B', 'amount_column_count': 11}])
        row['cells']['amount'][0]['interior_dark_fraction'] = .09
        observed['pages'][0]['rows'] = [row]
        self.assertEqual(plan_amount_header_columns(observed)['requests'], [])
        row['cells']['amount'] = amount_cells(primary=None, supplemental=.13)
        self.assertEqual(plan_amount_header_columns(observed)['requests'][0]['label'], 'K')
        row['cells']['amount'] = amount_cells(primary='B', supplemental=.13)
        self.assertEqual(plan_amount_header_columns(observed)['requests'], [])

    def test_ten_columns_and_uncertain_special_do_not_invent_a_selection(self):
        _, observed, _, _, _ = self.fixture(count=10)
        self.assertEqual(plan_amount_header_columns(observed)['requests'][0]['amount_column_count'], 10)
        observed['pages'][0]['rows'][0]['cells']['amount'] = amount_cells(supplemental=.05)
        self.assertEqual(plan_amount_header_columns(observed)['requests'], [])

    def test_selected_crop_reads_b_while_unrelated_whole_header_is_missing(self):
        raw, observed, header, region, columns = self.fixture()
        with (patch('unison_snapshot.house_ptr_table_region.locate_transaction_grid', return_value=region),
              patch('unison_snapshot.house_ptr_cell_observation._column_boxes', return_value=columns),
              patch('unison_snapshot.house_ptr_amount_header.subprocess.run',
                    return_value=SimpleNamespace(returncode=0, stdout=b'$15,001-$50,000')) as ocr):
            prepared = prepare_amount_headers(raw, observed, executable='fixed', existing_header=header)
        self.assertEqual(ocr.call_count, 1)
        self.assertEqual(prepared['status'], 'header_unresolved')
        self.assertIsNone(prepared['ranges'])
        self.assertEqual(prepared['pages'], header['pages'])
        reading = prepared['column_readings'][0]
        self.assertEqual((reading['page'], reading['label']), (1, 'B'))
        self.assertEqual(interpret_amount_column(reading)['low'], 15001)
        self.assertEqual(prepared['column_preparation']['missing_column_count'], 0)
        # Check actual rendered crop bytes supplied to the recognizer.
        self.assertEqual(hashlib.sha256(ocr.call_args.kwargs['input']).hexdigest(), reading['crop_sha256'])
        from unison_snapshot.house_ptr_paper_qualification import _bound_amount_column
        physical = {'band': [.4, .5], 'cells': {'amount': [
            {'label': 'B', 'bbox_normalized': [columns['amount'][1], .4, columns['amount'][2], .5]}]}}
        with (patch('unison_snapshot.house_ptr_table_region.locate_transaction_grid', return_value=region),
              patch('unison_snapshot.house_ptr_cell_observation._column_boxes', return_value=columns)):
            consumed = _bound_amount_column(raw, reading, physical, 1, {})
            self.assertEqual(consumed['low'], 15001)
            self.assertIsNone(_bound_amount_column(raw, dict(reading, crop_sha256='0' * 64), physical, 1, {}))
        with patch('unison_snapshot.house_ptr_amount_header_preparation.observe_amount_header') as no_ocr:
            replay = prepare_amount_headers(raw, observed, executable='fixed', existing_header=prepared)
        no_ocr.assert_not_called()
        self.assertEqual(replay, prepared)

    def test_budget_and_source_errors_precede_any_ocr(self):
        raw, observed, header, _, _ = self.fixture()
        row = deepcopy(observed['pages'][0]['rows'][0]); row['physical_band_index'] = 3
        row['cells']['amount'] = amount_cells(primary='A')
        observed['pages'][0]['rows'].append(row)
        observer = Mock()
        with self.assertRaisesRegex(ValueError, 'budget; OCR not started'):
            prepare_amount_headers(raw, observed, executable='fixed', existing_header=header,
                                   max_column_readings=1, observer=observer)
        with self.assertRaisesRegex(ValueError, 'source hash mismatch'):
            prepare_amount_headers(b'wrong', observed, executable='fixed', existing_header=header, observer=observer)
        observer.assert_not_called()

    def test_incremental_missing_columns_and_failed_attempt_cache(self):
        raw, observed, header, _, _ = self.fixture()
        base = {'page': 1, 'label': 'B', 'status': 'column_ocr_failed', 'raw': '',
                'render_dpi': 300, 'image_rotation_degrees': 270, 'coordinate_frame': 'original_page',
                'crop_sha256': 'b' * 64, 'bbox_pixels': [1, 2, 3, 4], 'amount_column_count': 11}
        header.update(reading_mode='printed_columns/v1', column_readings=[base])
        observer = Mock()
        prepare_amount_headers(raw, observed, executable='fixed', existing_header=header, observer=observer)
        observer.assert_not_called()
        row = deepcopy(observed['pages'][0]['rows'][0]); row['physical_band_index'] = 3
        row['cells']['amount'] = amount_cells(primary='A')
        observed['pages'][0]['rows'].append(row)
        fresh = deepcopy(header); fresh['column_readings'] = [dict(base, label='A')]
        fresh['pages'][0]['status'] = 'selected_columns_observed'
        observer.return_value = fresh
        result = prepare_amount_headers(raw, observed, executable='fixed', existing_header=header, observer=observer)
        self.assertEqual(observer.call_args.kwargs['column_labels'], ['A'])
        self.assertEqual([item['label'] for item in result['column_readings']], ['A', 'B'])
        self.assertEqual(result['column_readings'][1], base)
        unversioned = deepcopy(header); unversioned.pop('reading_mode')
        with self.assertRaisesRegex(ValueError, 'supported reading mode'):
            missing_amount_header_columns(plan_amount_header_columns(observed), unversioned)
        header['column_readings'].append(deepcopy(base))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            missing_amount_header_columns(plan_amount_header_columns(observed), header)

    def test_unresolved_geometry_has_no_invented_reading(self):
        raw, observed, header, _, _ = self.fixture()
        with patch('unison_snapshot.house_ptr_table_region.locate_transaction_grid', return_value={'status': 'unknown'}):
            result = prepare_amount_headers(raw, observed, executable='fixed', existing_header=header)
        self.assertNotIn('column_readings', result)
        self.assertEqual(result['column_preparation']['missing_column_count'], 1)

    def test_k_role_and_a_variants_remain_printed_evidence(self):
        for low in (1000, 1001):
            self.assertEqual(interpret_amount_column({'status': 'raw_column_observed', 'label': 'A',
                'raw': f'${low:,}-$15,000'})['low'], low)
        self.assertIsNone(interpret_amount_column({'status': 'raw_column_observed', 'label': 'K',
            'raw': 'Over $1,000,000'}))
        self.assertEqual(interpret_amount_column({'status': 'raw_column_observed', 'label': 'K',
            'raw': 'Spouse/DC Amount over $1,000,000'})['kind'], 'spouse_dc_special_open_ended')
        self.assertEqual(interpret_amount_column({'status': 'raw_column_observed', 'label': 'K',
            'raw': 'Spouse/DCAmountover$1,000,000'})['low'], 1000000)
