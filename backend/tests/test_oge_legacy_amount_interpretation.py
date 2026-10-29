"""Constructed interpretation contracts; no OCR/source accuracy assertion."""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from test_oge_content_relations import saved
from unison_snapshot.oge_278t_amount_column_shadow import observe_amount_column
from unison_snapshot.oge_278t_field_shadow import (
    _amount_shape, observe_field_shapes, observe_content_rows,
    LEGACY_AMOUNT_INTERPRETATION_MODE as MODE, AMOUNT_SHAPE_RULES_VERSION,
)


RAW = 'Yos $50,001-$100,000'
STORED = {'status': 'ocr_shape_only', 'values': [50001, 100000], 'reason': None}
BLOCKER = 'legacy_amount_shape_current_unresolved'


def inputs(*, primary_known=True, reverse=False, structural=None):
    affected = (['', structural, '', '', '', ''] if structural is not None else
        ['1', 'AFFECTED', 'purchase', '01/02/2026', 'No', '$50,001-$100,000' if primary_known else '?'])
    rows = [affected, ['2', 'INDEPENDENT', 'purchase', '01/03/2026', 'No', '$1,001-$15,000']]
    if reverse:
        rows.reverse()
    primary = saved(rows)
    index = 1 if reverse else 0
    slots = []
    for n, slot in enumerate(primary['pages'][0]['slots']):
        raw = RAW if n == index else '$1,001-$15,000'
        top, bottom = slot['band_pdf_points']
        columns = slot['column_observation']
        slots.append({'slot_index': slot['slot_index'], 'band_pdf_points': [top, bottom],
            'page_raw_ocr_text': columns['notification']['raw_ocr_text'] + ' ' + columns['amount']['raw_ocr_text'],
            'column_raw_ocr_text': raw, 'column_shape': deepcopy(STORED) if n == index else _amount_shape(raw),
            'column_detections': [{'text': raw, 'bbox_pdf_points': [111, top + 2, 148, top + 8]}]})
    secondary = {'schema_version': 'oge-278t-amount-column-observation/v2',
        'source_sha256': primary['source_sha256'], 'ocr_engine': 'constructed-secondary',
        'ocr_resolution_dpi': 300, 'input_ocr_resolution_dpi': 200,
        'summary': {'observed_slots': 2}, 'pages': [{'page_number': 2,
            'status': 'amount_column_observed', 'slot_count': 2, 'slots': slots}]}
    return primary, secondary, index


class LegacyAmountInterpretationTests(unittest.TestCase):
    def content(self, primary, secondary, **options):
        return observe_content_rows([primary], document_page_count=4,
            secondary_observations=[secondary], **options)

    def test_strict_default_still_rejects_and_explicit_mode_preserves_all_raw_inputs(self):
        primary, secondary, index = inputs()
        before = deepcopy((primary, secondary))
        with self.assertRaisesRegex(ValueError, 'shape differs'):
            self.content(primary, secondary)
        result = self.content(primary, secondary, field_interpretation_mode=MODE)
        self.assertEqual((primary, secondary), before)
        self.assertEqual(result['field_interpretation_mode'], MODE)
        fields = result['field_observations']
        self.assertEqual(fields['field_interpretation_mode'], MODE)
        slot = fields['pages'][0]['slots'][index]
        self.assertEqual(slot['interpretation_blockers'], [{
            'reason': BLOCKER, 'source_ref': 'secondary_observations/0/pages/0/slots/0',
            'stored_shape': STORED, 'current_shape': _amount_shape(RAW)}])
        reading = slot['field_readings']['amount']['observations'][1]
        self.assertEqual(reading['raw_ocr_text'], RAW)
        self.assertEqual(reading['shape'], _amount_shape(RAW))
        self.assertEqual(reading['detections'], secondary['pages'][0]['slots'][index]['column_detections'])
        self.assertEqual(result['source_report_ledger']['adapter_version'],
                         'oge-278t-content-rows/v8-legacy-amount-recompute')

    def test_both_slot_orders_block_primary_fallback_and_keep_independent_logical_row(self):
        for reverse in (False, True):
            for primary_known in (False, True):
                with self.subTest(reverse=reverse, primary_known=primary_known):
                    primary, secondary, index = inputs(primary_known=primary_known, reverse=reverse)
                    result = self.content(primary, secondary, field_interpretation_mode=MODE)
                    bad, good = result['slot_content'][index], result['slot_content'][1-index]
                    self.assertEqual(bad['disposition'], 'data_candidate')
                    self.assertEqual(bad['relation_evidence']['status'], 'unresolved')
                    self.assertIn(BLOCKER, bad['relation_evidence']['blocking_reasons'])
                    self.assertEqual(good['relation_evidence']['status'], 'supported_observation_relation')
                    self.assertEqual(result['source_report_ledger']['logical_row_count'], 1)
                    self.assertEqual(result['source_report_ledger']['logical_rows'][0]['slot_refs'],
                                     [{'page_number': 2, 'slot_key': str(2-index)}])

    def test_current_unknown_is_not_itself_a_legacy_blocker_or_a_new_strict_rule(self):
        primary, secondary, index = inputs()
        secondary['pages'][0]['slots'][index]['column_shape'] = _amount_shape(RAW)
        result = self.content(primary, secondary)
        self.assertNotIn('field_interpretation_mode', result)
        self.assertNotIn('interpretation_blockers', result['slot_content'][index])
        self.assertEqual(result['slot_content'][index]['relation_evidence']['status'], 'supported_observation_relation')
        explicit = self.content(primary, secondary, field_interpretation_mode=MODE)
        self.assertNotIn('interpretation_blockers', explicit['slot_content'][index])

    def test_affected_owner_account_and_note_labels_cannot_claim_structural_exemption(self):
        for label in ('Filer assets', 'Investment account #7', 'Intentionally left blank'):
            with self.subTest(label=label):
                primary, secondary, index = inputs(structural=label)
                result = self.content(primary, secondary, field_interpretation_mode=MODE)
                row = result['slot_content'][index]
                self.assertEqual(row['disposition'], 'unknown')
                self.assertEqual(row['reason'], BLOCKER)
                self.assertTrue(row['interpretation_blockers'])
                self.assertNotEqual(row['account_context']['status'], 'observed')

    def test_legacy_claim_must_be_exact_positive_and_not_a_self_signed_money_change(self):
        for claim in ({**STORED, 'values': [50000, 100000]},
                      {**STORED, 'reason': 'invented'},
                      {**STORED, 'extra': None},
                      {'status': 'unknown', 'values': None, 'reason': None}):
            with self.subTest(claim=claim):
                primary, secondary, index = inputs()
                secondary['pages'][0]['slots'][index]['column_shape'] = claim
                with self.assertRaisesRegex(ValueError, 'shape differs'):
                    self.content(primary, secondary, field_interpretation_mode=MODE)
        primary, secondary, index = inputs()
        slot = secondary['pages'][0]['slots'][index]
        slot.update(column_raw_ocr_text='Junk $1-$15,000',
                    column_shape={'status': 'ocr_shape_only', 'values': [True, 15000], 'reason': None})
        slot['column_detections'][0]['text'] = slot['column_raw_ocr_text']
        with self.assertRaisesRegex(ValueError, 'shape differs'):
            self.content(primary, secondary, field_interpretation_mode=MODE)

    def test_source_band_raw_detection_duplicate_slot_and_summary_contracts_still_raise(self):
        for kind in ('source', 'band', 'raw', 'box', 'duplicate', 'count'):
            with self.subTest(kind=kind):
                primary, secondary, index = inputs()
                slot = secondary['pages'][0]['slots'][index]
                if kind == 'source': secondary['source_sha256'] = 'b' * 64
                elif kind == 'band': slot['band_pdf_points'][0] += 1
                elif kind == 'raw': slot['column_raw_ocr_text'] += ' changed'
                elif kind == 'box': slot['column_detections'][0]['bbox_pdf_points'] = [20, 102, 40, 108]
                elif kind == 'duplicate': secondary['pages'][0]['slots'].append(deepcopy(slot))
                else: secondary['summary']['observed_slots'] = 1
                with self.assertRaises(ValueError):
                    self.content(primary, secondary, field_interpretation_mode=MODE)

    def test_mode_is_narrow_and_cannot_mix_source_quad_glyph_or_date_modes(self):
        for kind in ('unknown_mode', 'v1', 'tagged_v2', 'quad', 'glyph', 'sequence'):
            with self.subTest(kind=kind):
                primary, secondary, _ = inputs()
                options = {'field_interpretation_mode': MODE}
                if kind == 'unknown_mode': options['field_interpretation_mode'] = 'invented'
                elif kind == 'v1': secondary['schema_version'] = 'oge-278t-amount-column-observation/v1'
                elif kind == 'tagged_v2': secondary['shape_rule_version'] = AMOUNT_SHAPE_RULES_VERSION
                elif kind == 'quad': secondary['read_mode'] = 'source_quad_cells/v1'
                elif kind == 'glyph': options['source_glyph_mode'] = 'invented'
                else: options['source_date_sequence_mode'] = 'invented'
                with self.assertRaises(ValueError):
                    self.content(primary, secondary, **options)

    def test_strict_equal_v1_v3_and_tagged_v2_can_mix_without_receiving_legacy_exception(self):
        for version, tagged in (('v1', False), ('v2', True), ('v3', True)):
            with self.subTest(version=version):
                primary, legacy, index = inputs()
                strict = deepcopy(legacy)
                strict['schema_version'] = 'oge-278t-amount-column-observation/' + version
                strict['ocr_engine'] = 'independent-strict-read'
                if tagged:
                    strict['shape_rule_version'] = AMOUNT_SHAPE_RULES_VERSION
                for slot in strict['pages'][0]['slots']:
                    slot['column_shape'] = _amount_shape(slot['column_raw_ocr_text'])
                for reads in ([legacy, strict], [strict, legacy]):
                    result = observe_content_rows([primary], document_page_count=4,
                        secondary_observations=reads, field_interpretation_mode=MODE)
                    self.assertEqual(result['source_report_ledger']['logical_row_count'], 1)
                    self.assertEqual(len(result['slot_content'][index]['interpretation_blockers']), 1)
                strict['pages'][0]['slots'][index]['column_shape'] = deepcopy(STORED)
                with self.assertRaisesRegex(ValueError, 'shape differs'):
                    observe_content_rows([primary], document_page_count=4,
                        secondary_observations=[legacy, strict], field_interpretation_mode=MODE)

    def test_whole_read_v1_rule_tag_has_explicit_amount_contract(self):
        self.assertEqual(AMOUNT_SHAPE_RULES_VERSION, 'oge-278t-amount-shape/whole-read-v1')
        for raw, expected in (
                ('$15,001 - $50,000', [15001, 50000]),
                ('No $15,001 - $50,000.', [15001, 50000]),
                ('Yes | $50,001–$100,000', [50001, 100000]),
                (RAW, None), ('$100 001 - $200,000', None),
                ('$15.001 - $50,000', None), ('$50,000 - $15,001', None),
                ('$15,001 - $50,000 extra', None)):
            with self.subTest(raw=raw):
                self.assertEqual(_amount_shape(raw)['values'], expected)

    def test_amount_v3_rules_are_required_exact_and_do_not_enable_legacy_claims(self):
        primary, secondary, index = inputs()
        secondary['schema_version'] = 'oge-278t-amount-column-observation/v3'
        for tag in (None, 'different'):
            if tag is not None: secondary['shape_rule_version'] = tag
            with self.assertRaisesRegex(ValueError, 'v3 requires'):
                self.content(primary, secondary)
        secondary['shape_rule_version'] = AMOUNT_SHAPE_RULES_VERSION
        with self.assertRaisesRegex(ValueError, 'shape differs'):
            self.content(primary, secondary)
        secondary['pages'][0]['slots'][index]['column_shape'] = _amount_shape(RAW)
        result = self.content(primary, secondary)
        self.assertNotIn('interpretation_blockers', result['slot_content'][index])
        secondary['read_mode'] = 'source_quad_cells/v1'
        with self.assertRaisesRegex(ValueError, 'v3 requires'):
            self.content(primary, secondary)

    def test_fresh_producer_emits_v3_current_rules_using_fixed_saved_rapid_read(self):
        primary, secondary, _ = inputs()
        page = secondary['pages'][0]
        rapid = {'source_sha256': primary['source_sha256'], 'ocr_engine': secondary['ocr_engine'],
            'ocr_resolution_dpi': 300, 'input_ocr_resolution_dpi': 200, 'pages': [{
                'page_number': 2, 'status': 'columns_observed', 'detections_outside_slots': [],
                'slots': [{'slot_index': row['slot_index'], 'band_pdf_points': row['band_pdf_points'],
                           'raw_ocr_text': row['column_raw_ocr_text'], 'detections': row['column_detections']}
                          for row in page['slots']]}]}
        before = deepcopy((primary, rapid))
        with patch('unison_snapshot.oge_278t_amount_column_shadow.observe_rapid_column', return_value=rapid):
            fresh = observe_amount_column(Path('unused-no-OCR.pdf'), slots_observation=primary)
        self.assertEqual((primary, rapid), before)
        self.assertEqual(fresh['schema_version'], 'oge-278t-amount-column-observation/v3')
        self.assertEqual(fresh['shape_rule_version'], AMOUNT_SHAPE_RULES_VERSION)
        self.assertEqual(fresh['pages'][0]['slots'][0]['column_shape'], _amount_shape(RAW))
        observe_field_shapes([primary], secondary_observations=[fresh])


if __name__ == '__main__':
    unittest.main()
