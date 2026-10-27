"""Normal consumer composition; mocked source proofs are not font/paint positives.

Real Georgia/Form/graphics-state source recognition is exercised separately on
the retained local original. These generated PDFs test only the public merge,
source-replay and normal receiver contracts without committing official bytes.
"""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_house_native_contrast as contrast
from test_house_ptr import IDENTITY
from test_house_candidate import BASE
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_ptr import qualify_automatic


class HouseNativeFinancialTests(unittest.TestCase):
    def setUp(self):
        self.fixture = contrast.HouseNativeContrastTests()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.raw, self.base = self.fixture.source('white')
        self.base.pop('native_contrast_financial_profile', None)
        self.metadata = (self.root / self.base['source']['archive_path']).with_suffix('.json')

    def profile(self, *, observed=False):
        # Only this independent source-reader API is mocked. It deliberately
        # supplies a contract observation, never an original-font certificate.
        from unison_snapshot.house_native_financial import PROFILE_VERSION
        return {'status': 'observed',
            'profile_version': PROFILE_VERSION, 'source_sha256': self.raw['source_sha256'],
            'document_id': self.raw['source']['document_id'], 'raw_projection': deepcopy(self.raw),
            'rows': [{'raw_row_id': row['extraction_id'],
                      'status': 'observed' if observed else 'unknown',
                      'proof': {'method': 'private_test_source_contract'} if observed else {},
                      'reasons': [] if observed else ['private_test_source_unknown']}
                     for row in self.raw['transactions']],
            'scope': {'status': 'observed_scope', 'scope_complete': True,
                      'document_id': self.raw['source']['document_id'],
                      'source_sha256': self.raw['source_sha256'],
                      'method': 'private_test_contract_not_source_authority'},
            'reasons': [] if observed else ['private_test_source_unknown']}

    def source_reader(self, profile):
        return patch('unison_snapshot.house_revision_source_scope.replay_native_house_financial_source',
                     return_value=deepcopy(profile))

    def augment(self):
        from unison_snapshot.house_native_financial import augment_selected_native_contrast
        return augment_selected_native_contrast(self.root, self.metadata, self.base)

    def test_augmentation_keeps_raw_members_and_separates_new_projection_from_original_evidence(self):
        from unison_snapshot.house_native_financial import PROFILE_KEY
        original = deepcopy(self.base)
        profile = self.profile(observed=True)
        with self.source_reader(profile):
            report = self.augment()
        self.assertEqual(report[PROFILE_KEY]['source'], profile)
        self.assertEqual(report[PROFILE_KEY]['original_base_dispositions'], original['native_contrast_dispositions'])
        excluded = {PROFILE_KEY, 'native_contrast_dispositions'}
        self.assertEqual({key: value for key, value in report.items() if key not in excluded},
                         {key: value for key, value in original.items() if key not in excluded})
        projected = report['native_contrast_dispositions'][0]['current_projected']
        self.assertEqual(projected, self.raw['transactions'][0])
        self.assertIn('extraction_id', projected)
        self.assertIn('filing_status', projected)
        self.assertEqual(projected['verification_status'], 'awaiting_automatic_qualification')
        self.assertEqual(self.base, original)
        from unison_snapshot.house_native_financial import requires_source_financial_upgrade
        with self.source_reader(profile) as reader:
            self.assertFalse(requires_source_financial_upgrade(self.root, self.metadata, report))
            reader.assert_not_called()

    def test_unknown_supplement_does_not_remove_independently_qualified_original_row(self):
        original = qualify_automatic(self.base, IDENTITY, evidence_root=self.root)
        with self.source_reader(self.profile()):
            report = self.augment()
            qualification = qualify_automatic(report, IDENTITY, evidence_root=self.root)
        self.assertEqual(report, self.base)
        self.assertNotIn('native_contrast_financial_profile', report)
        self.assertEqual(qualification['transactions'], original['transactions'])
        self.assertEqual(len(qualification['transactions']), 1)
        self.assertEqual(qualification['transactions'][0]['asset_name'], 'Other Inc.')
        self.assertEqual(len(qualification['quarantined']), 1)
        self.assertFalse(qualification['coverage_complete'])
        self.assertEqual(qualification['qualification']['zero_transaction_document_count'], 0)
        from unison_snapshot.house_native_financial import requires_source_financial_upgrade
        root, _, complete = self.fixture.source('white_dark')
        complete.pop('native_contrast_financial_profile', None)
        self.assertTrue(all(row['status'] == 'current_projected'
                            for row in complete['native_contrast_dispositions']))
        metadata = (root / complete['source']['archive_path']).with_suffix('.json')
        with self.source_reader(self.profile()) as reader:
            self.assertFalse(requires_source_financial_upgrade(root, metadata, complete))
            reader.assert_not_called()

    def test_source_contract_can_fill_only_original_unresolved_id_once(self):
        # This proves the merge/normal financial kernel, not the real source
        # reader's ability to certify the white glyph in this generated PDF.
        expected = {row['id']: row for row in qualify_automatic(self.raw, IDENTITY)['transactions']}
        with self.source_reader(self.profile(observed=True)):
            report = self.augment()
            qualification = qualify_automatic(report, IDENTITY, evidence_root=self.root)
            repeated = qualify_automatic(deepcopy(report), IDENTITY, evidence_root=self.root)
        self.assertEqual({row['id']: row for row in qualification['transactions']}, expected)
        self.assertEqual(len(qualification['transactions']), len(expected))
        self.assertEqual(repeated, qualification)
        self.assertFalse(qualification['coverage_complete'])

    def test_saved_profile_and_original_rows_cannot_be_resigned_into_qualification(self):
        from unison_snapshot.house_native_financial import PROFILE_KEY
        profile = self.profile(observed=True)
        with self.source_reader(profile):
            report = self.augment()
            for mutate in (
                    lambda value: value[PROFILE_KEY]['source']['rows'][0]['proof'].update(approved=True),
                    lambda value: value[PROFILE_KEY]['source'].update(source_sha256='0' * 64),
                    lambda value: value['transactions'][0].update(asset_name='Changed source row')):
                with self.subTest(mutation=mutate):
                    changed = deepcopy(report)
                    mutate(changed)
                    with self.assertRaises((HouseIndexError, ValueError)):
                        qualify_automatic(changed, IDENTITY, evidence_root=self.root)

    def test_profile_without_original_root_is_not_authority(self):
        with self.source_reader(self.profile(observed=True)):
            report = self.augment()
        with self.assertRaises((HouseIndexError, ValueError)):
            qualify_automatic(report, IDENTITY)

    def test_unknown_profile_flows_through_normal_candidate_and_report_status(self):
        from unison_snapshot.house_candidate import build_house_candidate
        from unison_snapshot.disclosure_status_house import build_house_status
        review = self.root / 'review'

        def write(relative, value):
            path = review / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding='utf-8')

        with self.source_reader(self.profile()):
            report = self.augment()
            qualification = qualify_automatic(report, IDENTITY, evidence_root=self.root)
            relative = f'2026/20000001/{report["source_sha256"]}.json'
            write('house_clerk/extractions/' + relative, report)
            write('house_clerk/qualifications/' + relative, qualification)
            write('status/summary.json', {'parser_commit': 'a' * 40, 'pending_parse_count': 0,
                'qualification_count': 1, 'qualified_transaction_count': len(qualification['transactions']),
                'quarantined_row_count': len(qualification['quarantined']), 'evidence_count': 1,
                'extracted_count': 1, 'failure_count': 0})
            state = {'status': 'ok', 'run_at': '2026-09-18T12:00:00Z',
                     'counts': {'archived': 1, 'pending': 0, 'failed': 0}}
            candidate = build_house_candidate(review, state, deepcopy(BASE), evidence_root=self.root)
            scopes = build_house_status(self.root, review, candidate['transactions'], [])
        self.assertEqual(candidate['transactions'], qualification['transactions'])
        report_status = next(row for scope in scopes for row in scope['reports']
                             if row.get('document_id') == '20000001')
        self.assertEqual(report_status['qualified_transaction_count'], 1)
        self.assertEqual(report_status['qualification_state'], 'partial')
        self.assertEqual(report_status['publication_transaction_ids'],
                         [row['id'] for row in qualification['transactions']])

    def test_verified_supersession_is_not_revived_by_contrast_conservation(self):
        # This mock stands only for an already authenticated resolver return.
        # It certifies no PDF, amendment chain or official identifier. The real
        # retained-program New/Amended chain supplies that separate evidence.
        from unison_snapshot.house_candidate import build_house_candidate, _person
        report = deepcopy(self.base)
        qualification = qualify_automatic(report, IDENTITY, evidence_root=self.root)
        historical = qualify_automatic(self.raw, IDENTITY)['transactions']
        predecessor = next(row for row in historical if row['id'] not in
                           {value['id'] for value in qualification['transactions']})
        predecessor['source_note'] = 'retain the complete historical object absent a verified mapping'
        base = deepcopy(BASE)
        base['people'] = [_person(IDENTITY)]
        base['transactions'] = historical
        review = self.root / 'supersession-review'

        def write(relative, value):
            path = review / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding='utf-8')

        relative = f'2026/20000001/{report["source_sha256"]}.json'
        write('house_clerk/extractions/' + relative, report)
        write('house_clerk/qualifications/' + relative, qualification)
        write('status/summary.json', {'parser_commit': 'a' * 40, 'pending_parse_count': 0,
            'qualification_count': 1, 'qualified_transaction_count': 1, 'quarantined_row_count': 1,
            'evidence_count': 1, 'extracted_count': 1, 'failure_count': 0})
        state = {'status': 'ok', 'run_at': '2026-09-18T12:00:00Z',
                 'counts': {'archived': 1, 'pending': 0, 'failed': 0}}
        without_mapping = build_house_candidate(review, state, deepcopy(base), evidence_root=self.root)
        self.assertIn(predecessor, without_mapping['transactions'])
        successor = qualification['transactions'][0]
        authenticated = {'transactions': deepcopy(qualification['transactions']),
            'qualifications': {'20000001': deepcopy(qualification)}, 'identities': {},
            'dispositions': {}, 'replacements': [{
                'prior_transaction_id': predecessor['id'], 'transaction_id': successor['id']}]}
        with patch('unison_snapshot.house_source_revisions.resolve_source_bound_house_revisions',
                   return_value=authenticated):
            candidate = build_house_candidate(review, state, deepcopy(base), evidence_root=self.root)
        self.assertNotIn(predecessor['id'], {row['id'] for row in candidate['transactions']})
        self.assertEqual(candidate['transactions'], qualification['transactions'])

    def test_verified_supersession_state_keeps_incomplete_contrast_coverage_separate(self):
        # Only the consuming status contract is isolated here. Authentication
        # of a real New/Amended mapping remains the retained-program test's job.
        from unison_snapshot.disclosure_status_house import build_house_status
        root, _, report = self.fixture.source('white_dark')
        report.pop('native_contrast_financial_profile', None)
        qualification = qualify_automatic(report, IDENTITY, evidence_root=root)
        self.assertEqual(len(qualification['transactions']), 2)
        self.assertFalse(qualification['quarantined'])
        review = root / 'superseded-status-review'

        def write(relative, value):
            path = review / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding='utf-8')

        relative = f'2026/20000001/{report["source_sha256"]}.json'
        write('house_clerk/extractions/' + relative, report)
        write('house_clerk/qualifications/' + relative, qualification)
        mappings = [{'prior_transaction_id': row['id'],
                     'transaction_id': 'private-contract-successor-' + str(index)}
                    for index, row in enumerate(qualification['transactions'])]
        authenticated = {'transactions': [], 'qualifications': {'20000001': qualification},
                         'identities': {}, 'dispositions': {}, 'replacements': mappings}
        with patch('unison_snapshot.house_source_revisions.resolve_source_bound_house_revisions',
                   return_value=authenticated):
            scopes = build_house_status(root, review, [], [])
        status = next(row for scope in scopes if scope['scope_id'] == 'house_ptr'
                      for row in scope['reports'] if row['document_id'] == '20000001')
        self.assertEqual(status['qualification_state'], 'superseded')
        self.assertEqual(status['qualified_transaction_count'], 0)
        self.assertFalse(status['publication_transaction_ids'])
        self.assertEqual(status['superseded_transaction_ids'],
                         sorted(row['id'] for row in qualification['transactions']))
        self.assertFalse(status['source_observation_coverage']['coverage_complete'])
        self.assertIn('source_row_coverage_incomplete', status['reason_codes'])


if __name__ == '__main__':
    unittest.main()
