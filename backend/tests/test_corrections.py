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
                self.assertEqual(path.stem, correction['document_id'])
                self.assertEqual(path.parent.name, correction['source'])
                review = correction['source_review']
                self.assertEqual(review['source_sha256'], correction['source_sha256'])
                slots = {(a['row'], a.get('field')) for a in correction['actions']}
                self.assertEqual(len(slots), len(correction['actions']))
                self.assertTrue(all(a['row'] in correction['assertions']['row_inventory'] for a in correction['actions']))
                self.assertEqual(sum(a['field']=='account_title' for a in correction['actions']),
                    review['physical_counts']['data_candidate'])


class V1ContractTests(unittest.TestCase):
    def test_frontend_baseline_and_field_contract(self):
        import hashlib
        lock = json.loads((ROOT/'contract/v1-candidate.json').read_text(encoding='utf-8'))
        raw = (ROOT/lock['frontend_path']).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), lock['frontend_sha256'])
        # Required product-facing fields must survive importing the actual baseline.
        for field in (b'transactions', b'reported_holdings', b'source_health', b'amount_low', b'amount_high'):
            self.assertIn(field, raw)
