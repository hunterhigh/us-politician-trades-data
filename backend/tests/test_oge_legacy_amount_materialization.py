"""Saved-read compatibility contracts, not an OCR accuracy benchmark."""
from copy import deepcopy
import unittest
import json
from pathlib import Path
import tempfile

from test_oge_content_relations import saved
from test_oge_observed_candidate import original
from test_oge_candidate import base, catalog, direct, DOCUMENT_ID
from test_oge_generic_candidate import generic
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_reports import parse_table_rows
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.oge_transaction_adapter import materialize_oge_278t_observations
from unison_snapshot.oge_278t_field_shadow import (
    _amount_shape, LEGACY_AMOUNT_INTERPRETATION_MODE as MODE,
)
from unison_snapshot.disclosure_status_oge import _apply_audit

CUTOFF = '2025-06-04T00:00:00Z'
RAW = 'Yos $50,001-$100,000'
BLOCKER = 'legacy_amount_shape_current_unresolved'
STORED = {'status':'ocr_shape_only', 'values':[50001, 100000], 'reason':None}


def request(*, reverse=False, primary_known=True):
    rows = [['2', 'AFFECTED', 'purchase', '05/02/2025', 'No',
             '$50,001-$100,000' if primary_known else RAW],
            ['3', 'INDEPENDENT', 'purchase', '05/03/2025', 'No', '$1,001-$15,000']]
    if reverse:
        rows.reverse()
    observation = saved(rows)
    slots = []
    for slot, row in zip(observation['pages'][0]['slots'], rows):
        raw = RAW if row[1] == 'AFFECTED' else '$1,001-$15,000'
        top, bottom = slot['band_pdf_points']
        columns = slot['column_observation']
        slots.append({'slot_index':slot['slot_index'], 'band_pdf_points':[top, bottom],
            'page_raw_ocr_text':columns['notification']['raw_ocr_text'] + ' ' + columns['amount']['raw_ocr_text'],
            'column_raw_ocr_text':raw,
            'column_shape':deepcopy(STORED) if row[1] == 'AFFECTED' else _amount_shape(raw),
            'column_detections':[{'text':raw, 'bbox_pdf_points':[111, top + 2, 148, top + 8]}]})
    secondary = {'schema_version':'oge-278t-amount-column-observation/v2',
        'source_sha256':observation['source_sha256'], 'ocr_engine':'constructed-saved-secondary',
        'ocr_resolution_dpi':300, 'input_ocr_resolution_dpi':200,
        'summary':{'observed_slots':2}, 'pages':[{'page_number':2,
            'status':'amount_column_observed', 'slot_count':2, 'slots':slots}]}
    return {'observations':[observation], 'secondary_observations':[secondary],
            'document_page_count':3, 'field_interpretation_mode':MODE}


def consume(old=None, prior=None, inputs=None):
    return build_oge_candidate(catalog(), [old or original()], prior or base(),
        data_cutoff_at=CUTOFF, saved_observations_by_document={DOCUMENT_ID:inputs or request()})


def cached_affected():
    report = original()
    cells = request()['observations'][0]['pages'][0]['slots'][0]['column_observation']
    names = ('printed_number','description','type','transaction_date','notification','amount')
    raw = [cells[name]['raw_ocr_text'] for name in names]
    good, bad = parse_table_rows([(2, raw)], source_sha=report['source_sha256'])
    assert good and not bad
    report['source_rows'].append({'page_number':2, 'cells':raw})
    report['transactions'].extend(good)
    return report, good[0]['extraction_id']


class LegacyAmountMaterializationTests(unittest.TestCase):
    def test_both_orders_and_primary_routes_block_affected_keep_independent(self):
        for reverse in (False, True):
            for primary_known in (False, True):
                with self.subTest(reverse=reverse, primary_known=primary_known):
                    inputs = request(reverse=reverse, primary_known=primary_known)
                    before = deepcopy(inputs)
                    candidate, audit = consume(inputs=inputs)
                    self.assertEqual(inputs, before)
                    self.assertEqual({row['asset_name'] for row in candidate['transactions']},
                                     {'Example Inc.', 'INDEPENDENT'})
                    report = audit['reports'][0]
                    receipt = report['observation_receipt']
                    self.assertEqual(receipt['field_interpretation_mode'], MODE)
                    self.assertFalse(receipt['coverage_complete'])
                    affected = receipt['rows'][1 if reverse else 0]
                    self.assertEqual(affected['disposition'], 'unresolved')
                    self.assertEqual(affected['reasons'], [BLOCKER])
                    self.assertEqual(affected['interpretation_blockers'][0]['stored_shape'], STORED)
                    self.assertEqual(len(receipt['interpretation_conflicts']), 1)
                    self.assertTrue(any(BLOCKER in row['reasons'] for row in report['quarantined']))

    def test_default_strict_and_source_contracts_stay_strict(self):
        inputs = request()
        inputs.pop('field_interpretation_mode')
        with self.assertRaisesRegex(OgeCatalogError, 'shape differs'):
            consume(inputs=inputs)
        for kind in ('source', 'raw', 'band'):
            with self.subTest(kind=kind):
                inputs = request()
                secondary = inputs['secondary_observations'][0]
                slot = secondary['pages'][0]['slots'][0]
                if kind == 'source': secondary['source_sha256'] = 'b' * 64
                if kind == 'raw': slot['column_raw_ocr_text'] = 'other'
                if kind == 'band': slot['band_pdf_points'][0] += 1
                with self.assertRaises(OgeCatalogError):
                    consume(inputs=inputs)

    def test_eligible_parser_cache_is_not_a_published_predecessor(self):
        old, identifier = cached_affected()
        result, audit = consume(old=old)
        self.assertNotIn(identifier, {row['id'] for row in result['transactions']})
        self.assertIn('INDEPENDENT', {row['asset_name'] for row in result['transactions']})
        rejected = next(row for row in audit['reports'][0]['quarantined']
                        if row.get('extraction_id') == identifier)
        self.assertIn(BLOCKER, rejected['reasons'])
        self.assertEqual(rejected['cells'], old['transactions'][-1]['cells'])

    def test_unmatched_same_page_parser_cache_cannot_escape_affected_scope(self):
        old, identifier = cached_affected()
        # Same source/page, but a different raw spelling prevents the exact
        # physical-row proof. It is neither absence nor a safe second ID.
        old['transactions'][-1]['cells'][1] = 'AFFECTED alternate read'
        old['source_rows'][-1]['cells'][1] = 'AFFECTED alternate read'
        result, audit = consume(old)
        self.assertNotIn(identifier, {row['id'] for row in result['transactions']})
        self.assertIn('Example Inc.', {row['asset_name'] for row in result['transactions']})
        self.assertEqual(audit['reports'][0]['observation_receipt']['interpretation_unbound_parser_ids'],
                         [identifier])
        self.assertTrue(any('legacy_amount_source_correspondence_unresolved' in row['reasons']
                            for row in audit['reports'][0]['quarantined']))

    def test_actual_formal_full_object_kept_partial_and_repeat_exact(self):
        old, identifier = cached_affected()
        prior, _ = build_oge_candidate(catalog(), [old], base(), data_cutoff_at=CUTOFF)
        fact = next(row for row in prior['transactions'] if row['id'] == identifier)
        fact['source_optional_annotation'] = {'preserve':'entire predecessor'}
        before = deepcopy(prior)
        result, audit = consume(old, prior)
        self.assertEqual(prior, before)
        self.assertIn(fact, result['transactions'])
        self.assertEqual(len(result['transactions']), 3)
        self.assertNotIn(identifier, audit['reports'][0]['amount_recompute_current_qualified_ids'])
        repeated, repeat_audit = consume(old, result)
        self.assertEqual(result, repeated)
        record = {'reason_codes':[]}
        _apply_audit(record, repeat_audit['reports'][0])
        self.assertEqual(record['qualification_state'], 'partial')
        self.assertEqual(record['qualified_transaction_count'], 2)

    def test_unbound_formal_kept_without_guessing_new_id_correspondence(self):
        prior, _ = build_oge_candidate(catalog(), [original()], base(), data_cutoff_at=CUTOFF)
        unbound = deepcopy(prior['transactions'][0])
        unbound.update(id='oge-278t:' + 'e' * 24, asset_name='Unknown historical saved row')
        prior['transactions'].append(unbound)
        result, audit = consume(prior=prior)
        self.assertEqual(result['transactions'], sorted(prior['transactions'], key=lambda row:row['id']))
        self.assertTrue(any('legacy_formal_reference_unbound' in row['reasons']
                            for row in audit['reports'][0]['quarantined']))

    def test_legacy_reference_known_formal_and_saved_repeat_counts_stay_bound(self):
        old = original()
        del old['source_rows']
        prior, _ = build_oge_candidate(catalog(), [old], base(), data_cutoff_at=CUTOFF)
        result, audit = consume(old, prior)
        repeated, again = consume(old, result)
        self.assertEqual(result, repeated)
        self.assertEqual(audit['reports'][0]['amount_recompute_current_qualified_ids'],
                         again['reports'][0]['amount_recompute_current_qualified_ids'])
        self.assertEqual(len(again['reports'][0]['amount_recompute_current_qualified_ids']), 1)
        with tempfile.TemporaryDirectory() as folder:
            review = Path(folder)
            target = review / 'oge/extractions' / DOCUMENT_ID / old['source_sha256'] / 'old.json'
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(old), encoding='utf-8')
            record = {'reason_codes':[], 'source_versions':[old['source_sha256']],
                'report_id':DOCUMENT_ID, 'source_url':old['source_url']}
            _apply_audit(record, again['reports'][0], review=review)
            self.assertEqual(record['qualification_state'], 'partial')
            self.assertEqual(record['qualified_transaction_count'], 1)
            self.assertIsNone(record['quarantined_row_count'])
            self.assertEqual(record['observed_quarantined_slot_count'], 1)
            changed = deepcopy(again['reports'][0])
            changed['amount_recompute_current_qualified_ids'] = sorted(
                changed['amount_recompute_published_ids'])
            with self.assertRaisesRegex(ValueError, 'counts do not close'):
                _apply_audit({**record, 'reason_codes':[]}, changed, review=review)

    def test_same_id_different_full_projection_is_preserved_not_requalified(self):
        prior, _ = consume()
        good = next(row for row in prior['transactions'] if row['asset_name'] == 'INDEPENDENT')
        good['amount_low'] = 1002
        result, audit = consume(prior=prior)
        self.assertIn(good, result['transactions'])
        self.assertNotIn(good['id'], audit['reports'][0]['amount_recompute_current_qualified_ids'])
        self.assertTrue(any('legacy_amount_formal_projection_differs' in row['reasons']
                            for row in audit['reports'][0]['quarantined']))
        report = deepcopy(audit['reports'][0])
        report['amount_recompute_current_qualified_ids'] = sorted(
            [*report['amount_recompute_current_qualified_ids'], good['id']])
        with self.assertRaisesRegex(ValueError, 'counts do not close'):
            _apply_audit({'reason_codes':[]}, report)

    def test_saved_formal_nullable_omission_preserves_representation_and_current_qualification(self):
        prior, _ = consume()
        good = next(row for row in prior['transactions'] if row['asset_name'] == 'INDEPENDENT')
        for field in ('option_type', 'strike_price', 'expiration_date', 'ticker_mapping_basis'):
            self.assertIsNone(good.pop(field))
        before = deepcopy(good)
        result, audit = consume(prior=prior)
        self.assertIn(before, result['transactions'])
        self.assertIn(good['id'], audit['reports'][0]['amount_recompute_current_qualified_ids'])
        self.assertFalse(any('legacy_amount_formal_projection_differs' in row['reasons']
                             for row in audit['reports'][0]['quarantined']))
        record = {'reason_codes':[]}
        _apply_audit(record, audit['reports'][0])
        self.assertEqual(record['qualified_transaction_count'], 2)

    def test_legacy_generic_approved_owner_correction_keeps_existing_contract(self):
        old = original()
        prior, _ = build_oge_candidate(catalog(), [old], base(), data_cutoff_at=CUTOFF)
        prior['transactions'][0]['source_optional_annotation'] = {'preserve':'outside approved correction'}
        before = deepcopy(prior['transactions'])
        result, audit = build_oge_candidate(catalog(), [generic()], prior, data_cutoff_at=CUTOFF,
            legacy_extractions_by_document={DOCUMENT_ID:old},
            saved_observations_by_document={DOCUMENT_ID:request()})
        for row in before:
            self.assertIn({**row, 'owner':'Undisclosed'}, result['transactions'])
            self.assertNotIn(row['id'], audit['reports'][0]['amount_recompute_current_qualified_ids'])
        corrections = audit['reports'][0]['legacy_inheritance']['field_corrections']
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0]['rule'], 'approved_v2_default_self_to_undisclosed/v1')
        self.assertEqual(corrections[0]['source_reference']['basis'],
                         'whole_saved_source_input_replayed_with_two_defaults_no_explicit_owner')

    def test_status_cannot_count_preserved_conflict_as_current_or_erase_diagnostics(self):
        _, audit = consume()
        report = audit['reports'][0]
        record = {'reason_codes':[]}
        _apply_audit(record, report)
        self.assertEqual(record['qualified_transaction_count'], 2)
        for kind in ('count', 'mode', 'conflicts', 'rejection'):
            with self.subTest(kind=kind):
                changed = deepcopy(report)
                if kind == 'count': changed['promoted_count'] = 99
                if kind == 'mode': changed['observation_receipt']['field_interpretation_mode'] = 'other'
                if kind == 'conflicts': changed['observation_receipt']['interpretation_conflicts'] = []
                if kind == 'rejection':
                    changed['quarantined'] = []
                    changed['quarantined_count'] = 0
                with self.assertRaises(ValueError):
                    _apply_audit({'reason_codes':[]}, changed)

    def test_only_unresolved_amount_never_becomes_zero_transactions(self):
        old = original()
        old['transactions'], old['source_rows'] = [], []
        inputs = request()
        page = inputs['observations'][0]['pages'][0]
        page['slots'] = page['slots'][:1]
        page['physical_slot_count'] = 1
        secondary = inputs['secondary_observations'][0]
        secondary['pages'][0]['slots'] = secondary['pages'][0]['slots'][:1]
        secondary['pages'][0]['slot_count'] = 1
        secondary['summary']['observed_slots'] = 1
        result, audit = consume(old=old, inputs=inputs)
        self.assertEqual(result['transactions'], [])
        record = {'reason_codes':[]}
        _apply_audit(record, audit['reports'][0])
        self.assertEqual(record['qualified_transaction_count'], 0)
        self.assertEqual(record['qualification_state'], 'quarantined')
        self.assertFalse(record['source_observation_coverage']['coverage_complete'])


if __name__ == '__main__':
    unittest.main()
