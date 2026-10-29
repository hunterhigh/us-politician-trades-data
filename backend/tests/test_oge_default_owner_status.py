"""Ordinary v2 selection conserves published owner aliases without false q."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_oge_owner_correction import fixture, write_files, encoded, DOCUMENT, CUTOFF
from test_oge_owner_scope_status import EXPLICIT_NEW, unknown_external_text
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.disclosure_status_oge import build_oge_status


class DefaultOwnerStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.closed = fixture(heading=' ', extra_rows=EXPLICIT_NEW)
        cls.unknown = fixture(heading=' ', extra_rows=EXPLICIT_NEW,
                              pdf_mutator=unknown_external_text)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='oge-default-owner-status-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.evidence, self.review = self.root/'evidence', self.root/'review'

    def inputs(self, *, unknown=False):
        old, _, review, evidence, prior = deepcopy(self.unknown if unknown else self.closed)
        write_files(self.evidence, evidence); write_files(self.review, review)
        catalog = json.loads(next(raw for name,raw in evidence.items() if '/catalog/normalized/' in name))
        legacy = json.loads(next(raw for name,raw in review.items() if name.endswith('pdf-v2.json')))
        native = json.loads(next(raw for name,raw in review.items() if name.endswith('pdf-v7.json')))
        return old, prior, catalog, legacy, native

    def build(self, values, prior, *, generic=False):
        _, _, catalog, legacy, native = values
        result, audit = build_oge_candidate(catalog, [native if generic else legacy], prior,
            data_cutoff_at=CUTOFF, evidence_root=self.evidence,
            **({'legacy_extractions_by_document':{DOCUMENT:legacy}} if generic else {}))
        self.save(audit)
        return result, audit

    def save(self, audit):
        write_files(self.review, {'oge/qualifications/current.json':encoded(audit)})

    def status(self, result):
        return next(report for scope in build_oge_status(self.evidence,self.review,
            result['transactions'],[]) for report in scope['reports'] if report['report_id'] == DOCUMENT)

    def test_actual_generic_output_as_default_next_base_keeps_owner_and_requalifies(self):
        values = self.inputs()
        generic, _ = self.build(values, values[1], generic=True)
        expected = next(row for row in generic['transactions'] if row['id'] == values[0]['id'])
        self.assertEqual(expected['owner'],'Undisclosed')
        result, audit = self.build(values, generic)
        self.assertEqual(result['transactions'],generic['transactions'])
        report = self.status(result)
        self.assertEqual(report['source_interpretation_binding']['parser_version'],'oge-278t-pdf/v2')
        self.assertEqual(report['qualified_transaction_count'],2)
        self.assertEqual(report['qualification_state'],'qualified')
        self.assertEqual(report['default_owner_conservation']['current_ids'],[expected['id']])
        repeated, _ = self.build(values,result)
        self.assertEqual(repeated['transactions'],result['transactions'])
        self.assertEqual(self.status(repeated)['qualified_transaction_count'],2)

    def test_unknown_scope_keeps_whole_prior_and_explicit_new_row_is_current(self):
        values = self.inputs(unknown=True)
        prior = deepcopy(values[1]); prior['transactions'][0]['owner'] = 'Undisclosed'
        prior['transactions'][0]['optional_source_annotation'] = None
        result, audit = self.build(values,prior)
        self.assertIn(prior['transactions'][0],result['transactions'])
        report = self.status(result)
        self.assertEqual(report['qualified_transaction_count'],1)
        self.assertEqual(report['qualification_state'],'partial')
        self.assertEqual(len(report['publication_transaction_ids']),2)
        self.assertEqual(report['default_owner_conservation']['current_ids'],[])
        self.assertEqual(report['default_owner_conservation']['retained_ids'],[values[0]['id']])
        # Deleting the new receipt must not revert to the old promoted count.
        audit['reports'][0].pop('default_owner_conservation')
        self.save(audit)
        self.assertEqual(self.status(result)['qualified_transaction_count'],1)

    def test_unsupported_full_formal_stays_protected_and_never_falls_back_to_promoted(self):
        values = self.inputs()
        prior = deepcopy(values[1]); prior['transactions'][0].update(owner='Undisclosed',amount_low=1002)
        result,audit = self.build(values,prior)
        self.assertIn(prior['transactions'][0],result['transactions'])
        self.assertEqual(self.status(result)['qualified_transaction_count'],1)
        audit['reports'][0].pop('default_owner_conservation'); self.save(audit)
        record = self.status(result)
        self.assertEqual(record['qualified_transaction_count'],1)
        self.assertEqual(record['default_owner_conservation']['deferred_ids'],[values[0]['id']])

    def test_receipt_cannot_lift_current_or_hide_physical_protection(self):
        values = self.inputs(unknown=True)
        prior = deepcopy(values[1]); prior['transactions'][0]['owner'] = 'Undisclosed'
        result,audit = self.build(values,prior)
        for key,value in (('current_ids',[values[0]['id']]), ('retained_ids',[]),
                          ('deferred_ids',[]), ('binding',{})):
            changed = deepcopy(audit)
            changed['reports'][0]['default_owner_conservation'][key] = value
            self.save(changed)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError,'conservation differs'):
                self.status(result)
        # Even when the audit omits the receipt altogether, complete source
        # replay remains mandatory for the selected raw2 cache.
        changed = deepcopy(audit); changed['reports'][0].pop('default_owner_conservation')
        changed['reports'][0].pop('source_interpretation_binding'); self.save(changed)
        self.assertIsNone(self.status(result)['qualified_transaction_count'])

    def test_explicit_row_and_document_blockers_cannot_be_erased_by_owner_alias(self):
        values = self.inputs()
        generic,_ = self.build(values,values[1],generic=True)
        result,audit = self.build(values,generic)
        changed = deepcopy(audit)
        report = changed['reports'][0]
        report['quarantined'] = [{'extraction_id':values[0]['id'],'reasons':['row_conflict']}]
        report['quarantined_count'] = 1
        report['default_owner_conservation']['current_ids'] = []
        report['default_owner_conservation']['deferred_ids'] = [values[0]['id']]
        self.save(changed)
        self.assertEqual(self.status(result)['qualified_transaction_count'],1)
        # Keeping the originally eligible receipt alongside a real blocker is
        # inconsistent, regardless of its former source proof.
        report['default_owner_conservation']['current_ids'] = [values[0]['id']]
        report['default_owner_conservation']['deferred_ids'] = []
        self.save(changed)
        with self.assertRaisesRegex(ValueError,'conservation differs'):
            self.status(result)

    def test_independent_nonordinary_receivers_do_not_enter_default_mask(self):
        values = self.inputs()
        result,audit = self.build(values,values[1])
        audit['reports'][0]['observation_receipt'] = {'coverage_complete':False}
        self.save(audit)
        with patch('unison_snapshot.disclosure_status_oge._apply_default_owner_conservation',
                   side_effect=AssertionError('saved financial qualification is independent')):
            record = self.status(result)
        self.assertEqual(record['qualified_transaction_count'],2)
        self.assertEqual(record['qualification_state'],'partial')

    def test_only_preserved_unknown_owner_is_not_a_zero_transaction_attestation(self):
        data = fixture(heading=' ', pdf_mutator=unknown_external_text)
        _, _, review, evidence, prior = deepcopy(data)
        write_files(self.evidence,evidence); write_files(self.review,review)
        catalog = json.loads(next(raw for name,raw in evidence.items() if '/catalog/normalized/' in name))
        legacy = json.loads(next(raw for name,raw in review.items() if name.endswith('pdf-v2.json')))
        prior['transactions'][0]['owner'] = 'Undisclosed'
        result,audit = build_oge_candidate(catalog,[legacy],prior,data_cutoff_at=CUTOFF,
                                           evidence_root=self.evidence)
        self.save(audit)
        record = self.status(result)
        self.assertEqual(record['qualified_transaction_count'],0)
        self.assertEqual(record['qualification_state'],'unknown')
        self.assertEqual(record['publication_transaction_ids'],[prior['transactions'][0]['id']])

    def test_missing_both_bindings_with_only_v2_cache_cannot_lift_preserved_owner(self):
        values = self.inputs(unknown=True)
        prior = deepcopy(values[1]); prior['transactions'][0]['owner'] = 'Undisclosed'
        result,audit = self.build(values,prior)
        self.assertEqual(self.status(result)['qualified_transaction_count'],1)
        (self.review/f'oge/extractions/{DOCUMENT}/{values[3]["source_sha256"]}/oge-278t-pdf-v7.json').unlink()
        report = audit['reports'][0]
        report.pop('default_owner_conservation')
        report.pop('source_interpretation_binding')
        self.save(audit)
        record = self.status(result)
        self.assertIsNone(record['qualified_transaction_count'])
        self.assertEqual(record['qualification_state'],'unknown')
        self.assertEqual(len(record['publication_transaction_ids']),2)
        self.assertIn('source_interpretation_selection_unknown',record['reason_codes'])

    def test_unselected_old_cache_does_not_replay_source_to_withhold_explicit_owner(self):
        from test_oge_default_explicit_owner_status import explicit_owner_inputs
        data = explicit_owner_inputs(self.root)
        result, audit = build_oge_candidate(data['catalog'], [data['legacy']], data['generic'],
            data_cutoff_at=CUTOFF, evidence_root=self.evidence)
        self.assertEqual(result['transactions'][0]['owner'], 'Spouse')
        sha = data['legacy']['source_sha256']
        (self.review/f'oge/extractions/{DOCUMENT}/{sha}/oge-278t-pdf-v7.json').unlink()
        report = audit['reports'][0]
        report.pop('source_interpretation_binding')
        for field in ('owner_scope_binding', 'owner_scope_view', 'owner_scope_decisions',
                      'owner_scope_deferred_ids', 'native_classification', 'legacy_inheritance',
                      'default_owner_conservation'):
            report.pop(field, None)
        self.save(audit)
        with patch('unison_snapshot.oge_default_owner_conservation.prepare_default_owner_conservation',
                   side_effect=AssertionError('unselected caches cannot establish qualification')):
            record = self.status(result)
        self.assertIsNone(record['qualified_transaction_count'])
        self.assertEqual(record['qualification_state'], 'unknown')
        self.assertEqual(len(record['publication_transaction_ids']), len(result['transactions']))


if __name__ == '__main__':
    unittest.main()
