"""OCR segmentation transport and consumers; not an OCR accuracy benchmark."""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_oge_dual_axis_reading import wide_description
from unison_snapshot.oge_ocr_line_order import prepare_line_source, MODE
from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns, observe_raster_slots, DUAL_MODE
from unison_snapshot.oge_278t_field_shadow import observe_field_shapes


def bind(data, labels=None, extra=None):
    """Freeze an OCR producer fixture in the same order as normal slot export."""
    page = data['pages'][0]
    data['description_line_mode'] = MODE
    raw = []
    for slot in page['slots']:
        for word in slot['words']:
            x0, top, x1, bottom = word['bbox_pdf_points']
            group = (labels or {}).get(word['text'], (9, 1, 1))
            word['ocr_line_word_index'] = len(raw)
            word['confidence'] = 95.
            raw.append(dict(text=word['text'], x0=x0, top=top, x1=x1, bottom=bottom,
                ocr_confidence=95., **dict(zip(('block_num', 'par_num', 'line_num'), group))))
        slot['words'].sort(key=lambda word: (word['bbox_pdf_points'][1], word['bbox_pdf_points'][0]))
        slot.update(_observe_slot_columns(slot['words'], page['column_edges_pdf_points'], page=page))
    raw.extend(extra or [])
    page['ocr_line_source'] = prepare_line_source(raw, source_sha256=data['source_sha256'],
                                                page_number=page['page_number'])
    return data


LABELS = {'A': (1, 1, 1), 'B': (1, 1, 1), 'C': (1, 1, 2), 'D': (1, 1, 2)}


class OgeOcrLineOrderTests(unittest.TestCase):
    def row(self, data):
        return observe_field_shapes([data])['pages'][0]['slots'][0]

    def test_source_lines_resolve_height_and_slope_order_without_changing_raw_words(self):
        for slope in (-.01, .01):
            data = wide_description(slope, two_lines=True, tall=True)
            if slope < 0:
                self.assertIsNone(self.row(data)['description_reading']['ordered_ocr_text'])
            bind(data, LABELS)
            before = deepcopy(data)
            row = self.row(data)
            reading = row['description_reading']
            self.assertEqual(reading['ordered_ocr_text'], 'A B C D')
            self.assertEqual(len(reading['ocr_line_references']), 2)
            self.assertEqual([[data['pages'][0]['slots'][0]['words'][i]['text'] for i in line]
                              for line in reading['line_word_indices']], [['A', 'B'], ['C', 'D']])
            self.assertEqual(row['qualification'], 'not_assessed')
            self.assertEqual(data, before)
            self.assertEqual(self.row(json.loads(json.dumps(data))), row)

    def test_line_labels_define_membership_and_never_supply_reading_order(self):
        labels = {'A': (3, 2, 9), 'B': (3, 2, 9), 'C': (1, 1, 9), 'D': (1, 1, 9)}
        data = bind(wide_description(-.01, two_lines=True, tall=True), labels)
        reading = self.row(data)['description_reading']
        self.assertEqual(reading['ordered_ocr_text'], 'A B C D')
        self.assertEqual([ref['line_reference']['block_num'] for ref in reading['ocr_line_references']], [3, 1])

    def test_line_transport_does_not_change_date_or_amount_interpretation(self):
        data = bind(wide_description(-.01, two_lines=True, tall=True), LABELS)
        legacy = deepcopy(data)
        del legacy['pages'][0]['ocr_line_source']
        for word in legacy['pages'][0]['slots'][0]['words']: del word['ocr_line_word_index']
        before = self.row(legacy)
        after = self.row(data)
        for key in ('transaction_date_reading', 'amount_reading'):
            self.assertEqual(after[key], before[key])
        for key in ('type', 'transaction_date', 'amount', 'printed_number'):
            self.assertEqual(after['field_shapes'][key], before['field_shapes'][key])

    def test_a_producer_line_spanning_another_physical_row_stays_unknown(self):
        extra = dict(text='foreign row', x0=30., x1=80., top=130., bottom=132.,
                     ocr_confidence=95., block_num=1, par_num=1, line_num=1)
        data = bind(wide_description(-.01), LABELS, extra=[extra])
        reading = self.row(data)['description_reading']
        self.assertIsNone(reading['ordered_ocr_text'])
        self.assertEqual(reading['ocr_line_rejection'], 'producer_line_crosses_physical_row')

    def test_overlapping_words_and_interleaved_lines_are_not_silently_ordered(self):
        data = wide_description(-.01)
        for word in data['pages'][0]['slots'][0]['words']:
            if word['text'] == 'B':
                word['bbox_pdf_points'][0::2] = [19., 25.]
        reading = self.row(bind(data, LABELS))['description_reading']
        self.assertEqual(reading['ocr_line_rejection'], 'producer_line_word_boxes_overlap')
        interleaved = {'A': (1, 1, 1), 'D': (1, 1, 1), 'B': (1, 1, 2), 'C': (1, 1, 2)}
        reading = self.row(bind(wide_description(-.01, two_lines=True), interleaved))['description_reading']
        self.assertIsNone(reading['ordered_ocr_text'])

    def test_line_labels_cannot_merge_separate_text_bands_or_split_overlapping_ones(self):
        data = wide_description(-.01)
        for word in data['pages'][0]['slots'][0]['words']:
            if word['text'] == 'A': word['bbox_pdf_points'][1::2] = [104., 106.]
            if word['text'] == 'B': word['bbox_pdf_points'][1::2] = [111., 113.]
        reading = self.row(bind(data, LABELS))['description_reading']
        self.assertEqual(reading['ocr_line_rejection'], 'producer_line_has_no_supported_text_band')
        split = {'A': (1, 1, 1), 'B': (1, 1, 2)}
        data = bind(wide_description(.01, horizontal=True), split)
        reading = self.row(data)['description_reading']
        self.assertIsNone(reading['ordered_ocr_text'])
        self.assertEqual(reading['ocr_line_rejection'], 'producer_line_order_geometry_ambiguous')

    def test_broken_source_or_word_references_stop_before_field_consumption(self):
        for change in ('source', 'page', 'digest', 'text', 'box', 'confidence', 'reference',
                       'missing_word', 'missing_source', 'line_label', 'native', 'render_words', 'mode', 'missing_mode'):
            with self.subTest(change=change):
                data = bind(wide_description(-.01), LABELS)
                page = data['pages'][0]; source = page['ocr_line_source']; word = page['slots'][0]['words'][0]
                if change == 'source': source['source_sha256'] = 'b'*64
                if change == 'page': source['page_number'] += 1
                if change == 'digest': source['words_sha256'] = 'b'*64
                if change == 'text': word['text'] += ' EDIT'
                if change == 'box': word['bbox_pdf_points'][0] += .1
                if change == 'confidence': word['confidence'] = 30.
                if change == 'reference': word['ocr_line_word_index'] = True
                if change == 'missing_word': page['slots'][0]['words'].pop()
                if change == 'missing_source': del page['ocr_line_source']
                if change == 'line_label': source['words'][0]['line_num'] = 0
                if change == 'native': source['words'][0]['source_object_index'] = 1
                if change == 'render_words': page['render_frame'] = {'words_sha256': 'b'*64}
                if change == 'mode': data['description_line_mode'] = 'unknown'
                if change == 'missing_mode': del data['description_line_mode']
                with self.assertRaises(ValueError): self.row(data)

    def test_native_and_old_inputs_keep_existing_line_behavior(self):
        original = wide_description(-.01)
        before = self.row(original)
        raw = [{'text': 'native run', 'source_object_index': 0, 'block_num': 1}]
        self.assertIsNone(prepare_line_source(raw, source_sha256='a'*64, page_number=1))
        self.assertIsNone(prepare_line_source([{'text': 'old word'}], source_sha256='a'*64, page_number=1))
        self.assertEqual(self.row(original), before)
        self.assertIsNone(prepare_line_source([{'text': 'partial', 'block_num': 1}], source_sha256='a'*64, page_number=1))
        with self.assertRaisesRegex(ValueError, 'line labels'):
            prepare_line_source([{'text': 'invalid', 'block_num': 1, 'par_num': 1, 'line_num': 0}],
                                source_sha256='a'*64, page_number=1)

    def test_normal_candidate_and_builder_consume_order_with_stable_prior_facts(self):
        from test_oge_observed_candidate import candidate, original
        from test_oge_candidate import base, catalog
        from unison_snapshot.oge_candidate import build_oge_candidate
        from unison_snapshot.builder import build
        data = wide_description(-.01, two_lines=True, tall=True)
        for word in data['pages'][0]['slots'][0]['words']:
            if word['text'] == '01/02/2026': word['text'] = '05/02/2025'
        bind(data, LABELS)
        inputs = {'observations': [data], 'document_page_count': 3}
        before = deepcopy(inputs); old = original()
        baseline, _ = build_oge_candidate(catalog(), [old], base(), data_cutoff_at='2025-06-04T00:00:00Z')
        result, audit = candidate(inputs, old)
        prior = {row['id']: row for row in baseline['transactions']}
        self.assertEqual({row['id']: row for row in result['transactions'] if row['id'] in prior}, prior)
        self.assertEqual([row['asset_name'] for row in result['transactions'] if row['id'] not in prior], ['A B C D'])
        self.assertEqual(candidate(inputs, old), (result, audit))
        self.assertEqual(inputs, before)
        self.assertTrue(build(result, generated_at='2025-06-04T00:00:00Z', allow_production=True).manifest['snapshot_id'])

    def test_a_new_order_does_not_rewrite_or_duplicate_the_same_legacy_source_row(self):
        from test_oge_observed_candidate import candidate, original
        from test_oge_candidate import base, catalog
        from unison_snapshot.oge_candidate import build_oge_candidate
        data = wide_description(-.01, two_lines=True, tall=True)
        for word in data['pages'][0]['slots'][0]['words']:
            if word['text'] == '01/02/2026': word['text'] = '05/02/2025'
        bind(data, LABELS)
        columns = data['pages'][0]['slots'][0]['column_observation']
        cells = [columns[key]['raw_ocr_text'] for key in (
            'printed_number', 'description', 'type', 'transaction_date', 'notification', 'amount')]
        old = original()
        old['transactions'][0].update(page_number=2, cells=cells, asset_name=cells[1],
                                      ticker=None, transaction_date='2025-05-02')
        old['source_rows'] = [{'page_number': 2, 'cells': cells}]
        baseline, _ = build_oge_candidate(catalog(), [old], base(), data_cutoff_at='2025-06-04T00:00:00Z')
        result, audit = candidate({'observations': [data], 'document_page_count': 3}, old)
        self.assertEqual(result['transactions'], baseline['transactions'])
        self.assertEqual(audit['reports'][0]['observation_receipt']['rows'][0]['disposition'], 'retained_legacy')

    def test_whitehouse_saved_input_selects_and_replays_the_explicit_line_version(self):
        import test_whitehouse_278t_saved_ocr as saved_fixtures
        from unison_snapshot.whitehouse_278t import materialize_saved_whitehouse_278t, validate_generic_whitehouse_report
        from unison_snapshot.oge import OgeCatalogError

        case = saved_fixtures.SavedWhiteHouseTests()
        with tempfile.TemporaryDirectory() as folder:
            inputs, _ = case.fixture(Path(folder))
            inputs.update(geometry_mode=DUAL_MODE, description_line_mode=MODE)
            metadata = json.loads(Path(inputs['archive_metadata_path']).read_bytes())
            data = wide_description(-.01, two_lines=True, tall=True)
            data['source_sha256'] = metadata['sha256']
            data['pages'][0]['page_number'] = 2
            data['selected_page_numbers'] = [2]
            bind(data, LABELS)
            with case.context(data), patch('unison_snapshot.oge_278t_raster_slots.observe_raster_slots', return_value=data) as observer:
                report = materialize_saved_whitehouse_278t(inputs)
                self.assertEqual(observer.call_args.kwargs['description_line_mode'], MODE)
                self.assertEqual(report['transactions'][0]['asset_name'], 'A B C D')
                self.assertEqual(report['observation_receipt']['description_line_mode'], MODE)
                validate_generic_whitehouse_report(report)
                self.assertEqual(materialize_saved_whitehouse_278t(inputs), report)
                changed = deepcopy(report)
                del changed['saved_ocr_input']['description_line_mode']
                with self.assertRaisesRegex(OgeCatalogError, 'replay'):
                    validate_generic_whitehouse_report(changed)
            for change in ({'description_line_mode': 'unknown'}, {'geometry_mode': None}):
                with self.assertRaises(OgeCatalogError):
                    materialize_saved_whitehouse_278t({**inputs, **change})



if __name__ == '__main__': unittest.main()
