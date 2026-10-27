"""Source classification intersects existing financial and owner qualification."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from test_oge_classification_candidate import classification_candidate_inputs
from unison_snapshot.codec import encode
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.disclosure_status_oge import build_oge_status


class ClassificationStatusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='oge-classification-status-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def build(self, data, *, generic=False, base=None, cutoff=None):
        raw = data['native'] if generic else data['legacy']
        candidate, audit = build_oge_candidate(data['catalog'], [raw],
            data['base'] if base is None else base,
            data_cutoff_at=cutoff or data['cutoff'], evidence_root=data['evidence_root'],
            source_extractions_by_document={data['document_id']:raw},
            **({'legacy_extractions_by_document':{data['document_id']:data['legacy']}} if generic else {}))
        self.save(data, audit)
        return candidate, audit

    @staticmethod
    def save(data, audit):
        target = data['review_root']/'oge/qualifications/current.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encode(audit))

    def record(self, data, candidate):
        return next(row for scope in build_oge_status(data['evidence_root'], data['review_root'],
                    candidate['transactions'], []) for row in scope['reports']
                    if row['report_id'] == data['document_id'])

    def test_selected_reader_combination_and_native_id_repeat_remain_current(self):
        cases = [(False, 'Self', None), (True, None, None),
                 (False, 'Spouse', None), (False, None, 'Undisclosed')]
        for index, (generic, owner, prior_owner) in enumerate(cases):
            with self.subTest(generic=generic, owner=owner, prior_owner=prior_owner):
                data = classification_candidate_inputs(self.root/str(index), owner=owner, prior_owner=prior_owner)
                candidate, audit = self.build(data, generic=generic)
                record = self.record(data, candidate)
                identifier = data['prior']['id']
                self.assertEqual(len(candidate['transactions']), 1)
                self.assertEqual(candidate['transactions'][0]['id'], identifier)
                self.assertEqual(candidate['transactions'][0]['instrument_type'], 'Stock')
                self.assertEqual(record['qualified_transaction_count'], 1)
                self.assertEqual(record['native_classification']['current_ids'], [identifier])
                self.assertEqual(record['native_classification']['qualified_ids'], [identifier])
                self.assertEqual(record['source_interpretation_binding']['parser_version'],
                                 'oge-278t-pdf/v7' if generic else 'oge-278t-pdf/v2')
                next_candidate, _ = self.build(data, generic=generic, base=candidate)
                self.assertEqual(next_candidate['transactions'], candidate['transactions'])
                self.assertEqual(self.record(data, next_candidate)['qualified_transaction_count'], 1)

    def test_overlapping_unknown_owner_and_classification_masks_do_not_double_subtract(self):
        data = classification_candidate_inputs(self.root, prior_owner='Undisclosed', unknown=True, extra_row=True)
        candidate, audit = self.build(data)
        self.assertIn(data['prior'], candidate['transactions'])
        record = self.record(data, candidate)
        self.assertEqual(record['qualified_transaction_count'], 1)
        self.assertEqual(len(record['publication_transaction_ids']), 2)
        self.assertEqual(record['qualification_state'], 'partial')
        # Erasing a policy receipt cannot restore its physical old Bond row.
        stripped = deepcopy(audit); stripped['reports'][0].pop('native_classification')
        self.save(data, stripped)
        self.assertEqual(self.record(data, candidate)['qualified_transaction_count'], 1)
        forged = deepcopy(audit)
        receipt = forged['reports'][0]['native_classification']
        receipt['current_ids'] = receipt['mechanism_ids']
        receipt['deferred_ids'] = []
        receipt['qualified_ids'] = sorted(row['id'] for row in candidate['transactions'])
        self.save(data, forged)
        with self.assertRaisesRegex(ValueError, 'classification'):
            self.record(data, candidate)

    def test_missing_selection_with_only_default_cache_cannot_qualify_native_old_id(self):
        data = classification_candidate_inputs(self.root, owner='Spouse', unknown=True, extra_row=True)
        candidate, audit = self.build(data)
        self.assertEqual(self.record(data, candidate)['qualified_transaction_count'], 1)
        native_path = (data['review_root']/'oge/extractions'/data['document_id']/
                       data['native']['source_sha256']/'oge-278t-pdf-v7.json')
        native_path.unlink()
        report = audit['reports'][0]
        report.pop('native_classification'); report.pop('source_interpretation_binding')
        self.save(data, audit)
        record = self.record(data, candidate)
        self.assertIsNone(record['qualified_transaction_count'])
        self.assertEqual(record['qualification_state'], 'unknown')
        self.assertEqual(len(record['publication_transaction_ids']), 2)

    def test_document_blocker_is_not_removed_by_a_valid_classification_projection(self):
        data = classification_candidate_inputs(self.root, owner='Self')
        candidate, audit = self.build(data, cutoff='2026-06-02T00:00:00Z')
        self.assertEqual(candidate['transactions'], data['base']['transactions'])
        self.assertEqual(audit['current_qualified_transaction_count'], 0)
        record = self.record(data, candidate)
        self.assertEqual(record['qualified_transaction_count'], 0)
        self.assertNotEqual(record['qualification_state'], 'zero_transactions')
        self.assertEqual(len(record['publication_transaction_ids']), 1)


if __name__ == '__main__':
    unittest.main()
