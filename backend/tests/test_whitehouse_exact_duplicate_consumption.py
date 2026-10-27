"""Normal exact-copy consumption and fixed-Git duplicate-resolution gate.

Both old IDs come from actual existing parsers over a generated native PDF.
This tests a bounded duplicate policy, not arbitrary rekey/withdrawal admission.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from whitehouse_exact_duplicate_fixture import make_exact_duplicate_inputs, save
from test_public_repo import FakeTransport
from unison_snapshot.builder import build
from unison_snapshot.disclosure_status import attach_disclosure_status
from unison_snapshot.disclosure_status_oge import build_oge_status
from unison_snapshot.public_repo import PublicSnapshotRepository


ROOT = Path(__file__).resolve().parents[2]


class WhiteHouseExactDuplicateConsumptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = tempfile.TemporaryDirectory(prefix='wh-duplicate-source-')
        cls.addClassCleanup(cls.seed.cleanup)
        cls.data = make_exact_duplicate_inputs(Path(cls.seed.name), include_unrelated=True)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='wh-duplicate-consumer-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.install_fixture(self.data)

    def install_fixture(self, data):
        self.root.mkdir(parents=True, exist_ok=True)
        self.data = data
        self.evidence, self.review = self.root / 'evidence', self.root / 'review'
        shutil.copytree(self.data['evidence_root'], self.evidence)
        shutil.copytree(self.data['review_root'], self.review)
        self.previous = deepcopy(self.data['base'])
        self.base = self.root / 'base.json'
        save(self.base, self.previous)
        self.candidate = self.root / 'candidate.json'
        self.audit = self.review / 'whitehouse/qualifications/candidate-current.json'
        self.binding = self.review / 'whitehouse/exact-source-bindings-current.json'
        self.env = {**os.environ, 'PYTHONPATH': str(ROOT / 'backend/src'),
                    'PYTHONIOENCODING': 'utf-8', 'GIT_NO_LAZY_FETCH': '1'}

    def command(self, *arguments):
        return subprocess.run([sys.executable, *map(str, arguments)], cwd=ROOT,
            env=self.env, capture_output=True, text=True, encoding='utf-8')

    def normal(self, *, next_base=False, exhibits=None):
        arguments = [ROOT / 'backend/scripts/build_whitehouse_278t_candidate.py',
            '--oge-candidate', self.candidate if next_base else self.base,
            '--review-root', self.review, '--evidence-root', self.evidence,
            '--candidate-out', self.candidate, '--audit-out', self.audit,
            '--expected-report-count', '1', '--exact-source-copies',
            '--exact-source-bindings', self.binding]
        if exhibits is not None:
            arguments += ['--saved-exhibit-observations', exhibits]
        return self.command(*arguments)

    def fixed_snapshots(self):
        """Commit actual old board, source bytes and final normal review separately."""
        repo = self.root / 'fixed-repo'
        repo.mkdir()

        def git(*arguments):
            return subprocess.check_output(['git', '-C', str(repo), *arguments],
                env=self.env, stderr=subprocess.STDOUT).decode().strip()

        git('init', '-q')
        git('config', 'user.name', 'Source Contract')
        git('config', 'user.email', 'contract@example.invalid')
        from test_whitehouse_owner_correction import encoded
        board = encoded(self.previous)
        digest = hashlib.sha256(board).hexdigest()
        save(repo / 'manifest.json', {'is_demo': False, 'board': digest})
        save(repo / f'board/{digest}.json', board)
        git('add', '.')
        git('commit', '-qm', 'complete official predecessor source control')
        main = git('rev-parse', 'HEAD')
        for path in self.evidence.rglob('*'):
            if path.is_file():
                save(repo / path.relative_to(self.evidence), path.read_bytes())
        git('add', '.')
        git('commit', '-qm', 'fixed original bytes and archive metadata')
        evidence = git('rev-parse', 'HEAD')
        for path in self.review.rglob('*'):
            if path.is_file():
                save(repo / path.relative_to(self.review), path.read_bytes())
        git('add', '.')
        git('commit', '-qm', 'actual selected raw caches and normal candidate audit')
        review = git('rev-parse', 'HEAD')
        return repo, main, evidence, review

    def gate(self, refs, *, transitions=None):
        repo, main, evidence, review = refs
        args = ['-m', 'unison_snapshot.pipeline_qa.publication_delta',
            '--previous-board', self.base, '--unified', self.candidate,
            '--prepared', self.candidate, '--source-root', self.root / 'sources',
            '--output', self.root / 'delta.json', '--repo', repo,
            '--main-commit', main, '--evidence-commit', evidence, '--review-commit', review]
        if transitions is not None:
            args += ['--transitions', transitions]
        return self.command(*args)

    def test_normal_cli_fixed_git_automatic_explicit_repeat_public_reader_and_next_base(self):
        self.assertEqual(len(self.previous['transactions']), 3)
        canonical = self.data['canonical_fact']
        duplicate = self.data['duplicate_fact']
        unrelated = self.data['unrelated_fact']
        self.assertNotEqual(canonical['id'], duplicate['id'])
        result = self.normal()
        self.assertEqual(result.returncode, 0, result.stderr)
        candidate = json.loads(self.candidate.read_bytes())
        expected = sorted([canonical, unrelated], key=lambda row: row['id'])
        self.assertEqual(candidate['transactions'], expected)
        self.assertEqual(candidate['people'], self.previous['people'])
        audit = json.loads(self.audit.read_bytes())
        receipts = audit['removed_duplicate_formal_rows']
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]['old_transaction_id'], duplicate['id'])
        self.assertEqual(receipts[0]['canonical_transaction_id'], canonical['id'])
        scopes = build_oge_status(self.evidence, self.review, candidate['transactions'],
                                  candidate['reported_holdings'])
        records = [record for scope in scopes for record in scope['reports']]
        direct = next(record for record in records
                      if record['report_id'] == self.data['oge_archive']['document_id'])
        self.assertEqual(direct['publication_transaction_ids'], [canonical['id']])
        self.assertEqual(direct['qualified_transaction_count'], 1)
        all_publication_ids = [row_id for record in records
                               for row_id in record['publication_transaction_ids']]
        self.assertEqual(all_publication_ids.count(canonical['id']), 1)
        self.assertNotIn(duplicate['id'], all_publication_ids)
        unrelated_status = next(record for record in records
                                if record['source_url'] == unrelated['source_url'])
        self.assertEqual(unrelated_status['publication_transaction_ids'], [unrelated['id']])
        self.assertIsNone(unrelated_status['qualified_transaction_count'])
        for scope in scopes:
            self.assertFalse(scope['scope']['universe_complete'])
        for record in records:
            if record['qualification_state'] in {'unknown', 'partial'}:
                self.assertIsNot(record.get('source_observation_coverage', {}).get('coverage_complete'), True)

        self.assert_publication_cycle(candidate, [{
            'entity': 'transactions', 'kind': 'duplicate',
            'old_id': duplicate['id'], 'new_id': canonical['id'],
            'reason': 'duplicate_resolution', 'evidence_url': canonical['source_url'],
            'source_sha256': self.data['source_sha256'],
        }], scopes, check_native=True)

    def assert_publication_cycle(self, candidate, expected_proofs, scopes, *, check_native=False):
        expected = candidate['transactions']
        before = {path: path.read_bytes() for path in (
            self.evidence / self.data['oge_archive']['archive_path'],
            self.evidence / self.data['whitehouse_archive']['archive_path'])}
        for name in ('oge', 'house_clerk', 'senate_efd'):
            save(self.root / 'sources' / f'{name}-current.json', candidate if name == 'oge' else
                 {**candidate, 'people': [], 'transactions': [], 'reported_holdings': []})
        refs = self.fixed_snapshots()
        result = self.gate(refs)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.root / 'delta.json'
        automatic = output.read_bytes()
        delta = json.loads(automatic)
        proofs = delta['changes']
        self.assertEqual(proofs, sorted(expected_proofs, key=lambda proof: proof['old_id']))
        self.assertEqual(delta['source_verified_exact_duplicate_count'], len(expected_proofs))
        self.assertEqual(delta['counts']['transactions']['duplicate'], len(expected_proofs))
        self.assertEqual(delta['counts']['transactions']['new'], 0)
        old = {row['id']: row for row in self.previous['transactions']}
        final = {row['id']: row for row in candidate['transactions']}
        for proof in proofs:
            self.assertEqual(old[proof['new_id']], final[proof['new_id']])
        result = self.gate(refs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_bytes(), automatic)
        _, main, evidence, review = refs
        transitions = {'schema_version': 'pipeline-fact-transitions/v1', 'from_main_commit': main,
            'to_review_commit': review, 'evidence_commit': evidence, 'records': deepcopy(proofs)}
        selected = self.root / 'transitions.json'
        save(selected, transitions)
        result = self.gate(refs, transitions=selected)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_bytes(), automatic)
        transitions['records'][0]['reason'] = 'source_bound_rekey'
        save(selected, transitions)
        output.write_bytes(b'prior publication audit')
        result = self.gate(refs, transitions=selected)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_bytes(), b'prior publication audit')

        bundle = build(candidate, generated_at=candidate['meta']['data_cutoff_at'], allow_production=True)
        bundle = attach_disclosure_status(bundle, scopes, transactions=candidate['transactions'],
            provenance={'code_commit': 'f' * 40, 'previous_main_commit': main,
                        'evidence_commit': evidence, 'review_commit': review})
        public = PublicSnapshotRepository('contract', 'data', transport=FakeTransport(bundle.files))
        self.assertEqual(public.fetch().snapshot['transactions'], candidate['transactions'])
        self.assertEqual(public.fetch().snapshot['people'], candidate['people'])
        self.assertTrue(public.disclosure_status(public.resolve(), include_records=True))
        result = self.normal(next_base=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.candidate.read_bytes())['transactions'], expected)
        self.assertEqual(json.loads(self.candidate.read_bytes())['people'], self.previous['people'])
        repeated = self.candidate.read_bytes(), self.audit.read_bytes()
        result = self.normal(next_base=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(repeated, (self.candidate.read_bytes(), self.audit.read_bytes()))
        self.assertTrue(all(path.read_bytes() == raw for path, raw in before.items()))
        if check_native:
            # A valid empty saved selection enters the real native-context path
            # without adding facts. Its early return must bind the same exact-copy
            # policy and dependencies as the ordinary exact-source branch.
            exhibits = self.root / 'empty-exhibits.json'
            save(exhibits, {})
            result = self.normal(next_base=True, exhibits=exhibits)
            self.assertEqual(result.returncode, 0, result.stderr)
            native_pair = self.candidate.read_bytes(), self.audit.read_bytes()
            result = self.normal(next_base=True, exhibits=exhibits)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['idempotent'])
            self.assertEqual(native_pair, (self.candidate.read_bytes(), self.audit.read_bytes()))

    def test_two_identical_financial_rows_resolve_by_physical_row_not_semantic_uniqueness(self):
        data = make_exact_duplicate_inputs(self.root / 'two-row-source', identical_rows=True)
        self.root = self.root / 'two-row-consumer'
        self.install_fixture(data)
        self.assertEqual(len(self.previous['transactions']), 4)
        self.assertEqual([pair['row_number'] for pair in data['pairs']], [1, 2])
        expected = sorted(data['canonical_facts'], key=lambda row: row['id'])
        first, second = expected
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual({key: value for key, value in first.items() if key != 'id'},
                         {key: value for key, value in second.items() if key != 'id'})
        result = self.normal()
        self.assertEqual(result.returncode, 0, result.stderr)
        candidate = json.loads(self.candidate.read_bytes())
        self.assertEqual(candidate['transactions'], expected)
        self.assertEqual(candidate['people'], self.previous['people'])
        receipts = json.loads(self.audit.read_bytes())['removed_duplicate_formal_rows']
        expected_pairs = {(pair['duplicate_fact']['id'], pair['canonical_fact']['id'])
                          for pair in data['pairs']}
        self.assertEqual({(row['old_transaction_id'], row['canonical_transaction_id'])
                          for row in receipts}, expected_pairs)
        self.assertEqual(len(receipts), 2)
        for pair in data['pairs']:
            record = next(row for row in receipts
                          if row['old_transaction_id'] == pair['duplicate_fact']['id'])
            self.assertEqual((record['page_number'], record['row_number']),
                             (pair['page_number'], pair['row_number']))
        scopes = build_oge_status(self.evidence, self.review, candidate['transactions'], [])
        records = [record for scope in scopes for record in scope['reports']]
        direct = next(record for record in records
                      if record['report_id'] == data['oge_archive']['document_id'])
        self.assertEqual(direct['qualified_transaction_count'], 2)
        self.assertEqual(set(direct['publication_transaction_ids']), {row['id'] for row in expected})
        all_publication_ids = [row_id for record in records for row_id in record['publication_transaction_ids']]
        self.assertTrue(all(all_publication_ids.count(row['id']) == 1 for row in expected))
        self.assertTrue(all(row['id'] not in all_publication_ids for row in data['duplicate_facts']))
        proofs = [{'entity': 'transactions', 'kind': 'duplicate',
            'old_id': pair['duplicate_fact']['id'], 'new_id': pair['canonical_fact']['id'],
            'reason': 'duplicate_resolution', 'evidence_url': pair['canonical_fact']['source_url'],
            'source_sha256': data['source_sha256']} for pair in data['pairs']]
        self.assert_publication_cycle(candidate, proofs, scopes)

    def assert_preserved_or_refused(self, original):
        self.candidate.write_bytes(b'prior candidate')
        self.audit.parent.mkdir(parents=True, exist_ok=True)
        self.audit.write_bytes(b'prior candidate audit')
        before = self.base.read_bytes()
        result = self.normal()
        if result.returncode:
            self.assertEqual(self.candidate.read_bytes(), b'prior candidate')
            self.assertEqual(self.audit.read_bytes(), b'prior candidate audit')
        else:
            candidate = json.loads(self.candidate.read_bytes())
            self.assertEqual(candidate['transactions'], sorted(original['transactions'], key=lambda row: row['id']))
            self.assertEqual(candidate['people'], original['people'])
            self.assertFalse(json.loads(self.audit.read_bytes()).get('removed_duplicate_formal_rows'))
        self.assertEqual(self.base.read_bytes(), before)

    def test_missing_original_cannot_authorize_removing_a_previous_formal_fact(self):
        path = self.evidence / self.data['oge_archive']['archive_path']
        raw = path.read_bytes()
        path.unlink()
        try:
            self.assert_preserved_or_refused(self.previous)
        finally:
            path.write_bytes(raw)

    def test_known_null_omission_on_both_old_objects_keeps_canonical_representation(self):
        target_ids = {self.data['canonical_fact']['id'], self.data['duplicate_fact']['id']}
        for row in self.previous['transactions']:
            if row['id'] in target_ids:
                self.assertIsNone(row.pop('option_type'))
        save(self.base, self.previous)
        original = deepcopy(self.previous)
        result = self.normal()
        self.assertEqual(result.returncode, 0, result.stderr)
        candidate = json.loads(self.candidate.read_bytes())
        canonical = next(row for row in original['transactions']
                         if row['id'] == self.data['canonical_fact']['id'])
        expected = sorted([canonical, self.data['unrelated_fact']], key=lambda row: row['id'])
        self.assertEqual(candidate['transactions'], expected)
        self.assertNotIn('option_type', next(row for row in candidate['transactions']
                                            if row['id'] == canonical['id']))
        self.assertEqual(candidate['people'], original['people'])
        for name in ('oge', 'house_clerk', 'senate_efd'):
            save(self.root / 'sources' / f'{name}-current.json', candidate if name == 'oge' else
                 {**candidate, 'people': [], 'transactions': [], 'reported_holdings': []})
        result = self.gate(self.fixed_snapshots())
        self.assertEqual(result.returncode, 0, result.stderr)
        proof, = json.loads((self.root / 'delta.json').read_bytes())['changes']
        self.assertEqual((proof['kind'], proof['new_id']), ('duplicate', canonical['id']))

    def test_additional_full_fact_instrument_or_amount_difference_is_not_a_duplicate(self):
        for field, value in (('instrument_type', 'Bond'), ('amount_low', 1002)):
            with self.subTest(field=field):
                original = deepcopy(self.previous)
                target = next(row for row in original['transactions']
                              if row['id'] == self.data['duplicate_fact']['id'])
                target[field] = value
                save(self.base, original)
                self.assert_preserved_or_refused(original)


if __name__ == '__main__':
    unittest.main()
