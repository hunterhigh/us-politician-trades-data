from copy import deepcopy
import json
from pathlib import Path
import unittest

from unison_snapshot.corrections import apply_correction, load_correction

ROOT = Path(__file__).resolve().parents[2]


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.document = {'source': 'example', 'document_id': 'report', 'source_sha256': 'a' * 64,
            'report': {'filed_at': None}, 'rows': {'p1#1': {'amount': None}}}
        self.correction = {k: self.document[k] for k in ('source', 'document_id', 'source_sha256')}
        self.correction['actions'] = [{'op': 'set_field', 'row': 'p1#1', 'field': 'amount',
            'expect': None, 'value': 100, 'evidence': 'page 1 cell'}]

    def test_edit_does_not_change_input_and_requires_old_value(self):
        result = apply_correction(self.document, self.correction)
        self.assertEqual(result['rows']['p1#1']['amount'], 100)
        self.assertIsNone(self.document['rows']['p1#1']['amount'])
        with self.assertRaisesRegex(ValueError, 'precondition'):
            apply_correction(result, self.correction)

    def test_wrong_source_missing_target_and_conflict_fail_atomically(self):
        for mutate in (lambda c: c.update(source_sha256='b'*64),
                lambda c: c['actions'][0].update(row='p2#9'),
                lambda c: c['actions'].append(deepcopy(c['actions'][0])),
                lambda c: c['actions'][0].pop('expect')):
            correction = deepcopy(self.correction); mutate(correction)
            original = deepcopy(self.document)
            with self.assertRaises(ValueError):
                apply_correction(self.document, correction)
            self.assertEqual(self.document, original)

    def test_report_add_drop_quarantine_and_relation_preconditions(self):
        actions = [
            {'op': 'set_report', 'field': 'filed_at', 'expect': None, 'value': '2026-01-01'},
            {'op': 'add_row', 'row': 'p1#2', 'expect_absent': True, 'value': {'amount': 200}},
            {'op': 'relate', 'row': 'p1#2', 'expect': {'amount': 200},
                'value': {'kind': 'duplicate', 'other_document_id': 'other', 'other_source_sha256': 'b'*64}},
            {'op': 'quarantine', 'row': 'p1#1', 'expect': {'amount': None}, 'reason': 'unreadable'},
            {'op': 'drop_row', 'row': 'p1#2', 'expect': {'amount': 200}}]
        for a in actions: a['evidence'] = 'source page'
        self.correction['actions'] = actions
        result = apply_correction(self.document, self.correction)
        self.assertEqual(result['rows'], {})
        self.assertEqual(len(result['relations']), 1)
        self.assertEqual(result['quarantined']['p1#1']['reason'], 'unreadable')

    def test_all_shipped_corrections_have_valid_targets_and_unique_actions(self):
        paths = list((ROOT/'backend/data/corrections').glob('*/*.json'))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=str(path)):
                correction = load_correction(path)
                self.assertEqual(path.stem, correction['document_id'].removeprefix('wh-url:'))
                self.assertEqual('oge' if correction['document_id'].startswith('wh-url:') else path.parent.name,
                    correction['source'])
                review = correction['source_review']
                if correction['source']=='house_clerk':
                    self.assertEqual(review['source_sha256'], correction['source_sha256'])
                slots = {(a.get('row','@report'), a.get('field',a['op'])) for a in correction['actions']}
                self.assertEqual(len(slots), len(correction['actions']))
                self.assertTrue(all(a['op']=='set_report' or a['row'] in correction['assertions']['row_inventory'] for a in correction['actions']))
                if correction['source']=='house_clerk':
                    self.assertEqual(sum(a['field']=='account_title' for a in correction['actions']),
                        review['physical_counts']['data_candidate'])
                elif correction['source']=='senate_efd':
                    self.assertEqual(len(correction['actions']),len(correction['assertions']['row_inventory']))
                elif correction['profile']=='reviewed-six-column-extraction/v1':
                    guards=correction['assertions']
                    self.assertFalse(set(guards['output_transactions']) & set(guards['output_quarantined']))
                    added={a['row'] for a in correction['actions'] if a['op']=='add_row'}
                    dropped={a['row'] for a in correction['actions'] if a['op']=='drop_row'}
                    self.assertEqual(set(guards['output_transactions']+guards['output_quarantined']),
                        (set(guards['input_row_inventory'])|added)-dropped)
                    self.assertEqual(correction['parser_version'],correction['parser_versions'][-1])
                    self.assertEqual(set(correction['parser_versions']),set(guards['legacy_versions']))
                elif correction['profile']=='reviewed-ocr-table/v1':
                    guards=correction['assertions']
                    self.assertEqual(sum(guards['page_row_counts'].values()),len(guards['row_inventory']))
                    self.assertEqual(correction['header']['parser_version'],correction['parser_version'])
                    for key in ('document_id','source_sha256','source_url'):
                        self.assertEqual(correction['source_profile'][key],correction[key])
                        self.assertEqual(correction['header'][key],correction[key])
                    self.assertTrue(guards['approved_artifact_sha256'])
                    self.assertTrue(correction['person']['id'].startswith('oge:'))
                elif correction['profile']=='reviewed-annual-extraction/v1':
                    from unison_snapshot.corrections import validate_correction
                    for application in [correction, *({**correction, **v} for v in correction['variants'].values())]:
                        validate_correction(application)
                        guards=application['assertions']
                        self.assertEqual(sum(guards['input_counts'].values()),sum(guards['output_counts'].values()))
                        for target in guards['row_inventory']:
                            kind,index=target.split('/')
                            self.assertGreaterEqual(int(index),0)
                            self.assertLess(int(index),guards['input_counts'][kind])
                        slots={(a.get('row','@report'),a['field']) for a in application['actions']}
                        self.assertEqual(len(slots),len(application['actions']))
                        for action in application['actions']:
                            self.assertIn('expect',action)
                            self.assertTrue(action['op']=='set_report' or action['row'] in guards['row_inventory'])
                for action in correction['actions']:
                    if action['op'] in {'set_field','set_report','drop_row','quarantine','relate'}:
                        self.assertIn('expect',action)
                    if action['op']=='add_row':self.assertTrue(action['expect_absent'])


class V1ContractTests(unittest.TestCase):
    def test_frontend_baseline_and_field_contract(self):
        import hashlib
        lock = json.loads((ROOT/'contract/v1-candidate.json').read_text(encoding='utf-8'))
        raw = (ROOT/lock['frontend_path']).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), lock['frontend_sha256'])
        # Required product-facing fields must survive importing the actual baseline.
        for field in (b'transactions', b'reported_holdings', b'source_health', b'amount_low', b'amount_high'):
            self.assertIn(field, raw)
