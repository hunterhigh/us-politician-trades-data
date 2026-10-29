"""Real native PDF -> ordinary candidate -> fixed Git publication contracts."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from whitehouse_account_display_fixture import fixture, encoded, write_files
from unison_snapshot.commands import build_whitehouse_278t_candidate as candidate_cli


class AccountDisplayCorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = fixture()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.review, self.evidence = self.root/'review', self.root/'evidence'
        self.old, self.expected, review, evidence, self.base = deepcopy(self.data)
        write_files(self.review, review); write_files(self.evidence, evidence)
        self.candidate, self.audit = self.root/'candidate.json', self.root/'audit.json'
        self.candidate.write_bytes(encoded(self.base))
        self.project = Path(__file__).resolve().parents[2]
        self.env = dict(os.environ, PYTHONPATH=str(self.project/'backend/src'), GIT_NO_LAZY_FETCH='1')

    def run_candidate(self, **options):
        arguments = dict(oge_candidate=self.candidate, candidate_out=self.candidate, audit_out=self.audit,
            review_root=self.review, evidence_root=self.evidence, expected_report_count=1)
        arguments.update(options)
        return candidate_cli.run(**arguments)

    def current(self):
        return json.loads(self.candidate.read_bytes())

    def test_normal_candidate_and_publication_clis_both_predecessors_and_repeat(self):
        for owner in ('Self', 'Undisclosed'):
            with self.subTest(owner=owner), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); repo = root/'repo'; repo.mkdir()
                old, expected, review, evidence, base = deepcopy(self.data)
                old['owner'] = owner; base['transactions'] = [old]
                write_files(repo, {**review, **evidence})
                board = encoded(base); digest = hashlib.sha256(board).hexdigest()
                write_files(repo, {'manifest.json':encoded({'is_demo':False, 'board':digest}),
                    f'board/{digest}.json':board})
                def git(*args):
                    return subprocess.check_output(['git', '-C', str(repo), *args],
                        env=self.env, stderr=subprocess.STDOUT).decode().strip()
                git('init', '-q'); git('config', 'user.name', 'Contract')
                git('config', 'user.email', 'contract@example.invalid')
                git('add', '.'); git('commit', '-qm', 'generated native source contract')
                commit = git('rev-parse', 'HEAD')
                write_files(root, {'base.json':board})
                path, audit_path = root/'candidate.json', root/'audit.json'
                command = [sys.executable, str(self.project/'backend/src/unison_snapshot/commands/build_whitehouse_278t_candidate.py'),
                    '--oge-candidate', str(root/'base.json'), '--candidate-out', str(path),
                    '--audit-out', str(audit_path), '--review-root', str(repo), '--evidence-root', str(repo),
                    '--expected-report-count', '1']
                def run(arguments):
                    result = subprocess.run(arguments, env=self.env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    return result
                run(command)
                current = json.loads(path.read_bytes())
                self.assertEqual(current['transactions'], [expected])
                self.assertEqual(old['id'], expected['id'])
                for field in ('people', 'reported_holdings', 'security_market_data'):
                    self.assertEqual(current[field], base[field])
                snapshot = path.read_bytes(), audit_path.read_bytes()
                repeated = command[:]; repeated[repeated.index('--oge-candidate')+1] = str(path)
                self.assertTrue(json.loads(run(repeated).stdout)['idempotent'])
                self.assertEqual(snapshot, (path.read_bytes(), audit_path.read_bytes()))
                sources = root/'sources'
                for name in ('house_clerk', 'senate_efd', 'oge'):
                    value = current if name == 'oge' else {**current, 'people':[], 'transactions':[], 'reported_holdings':[]}
                    write_files(sources, {f'{name}-current.json':encoded(value)})
                output = root/'delta.json'
                gate = [sys.executable, '-m', 'unison_snapshot.fact_policy.publication_delta',
                    '--previous-board', str(root/'base.json'), '--unified', str(path), '--prepared', str(path),
                    '--source-root', str(sources), '--output', str(output), '--repo', str(repo),
                    '--main-commit', commit, '--review-commit', commit, '--evidence-commit', commit]
                run(gate); original = output.read_bytes(); delta = json.loads(original)
                self.assertEqual(delta['counts']['transactions']['corrected'], 1)
                proof = delta['changes'][0]
                self.assertEqual(proof['changed_fields'], ['asset_name', 'owner'] if owner == 'Self' else ['asset_name'])
                self.assertIn('whitehouse_native_account_display', delta['correction_verification_level'])
                run(gate); self.assertEqual(output.read_bytes(), original)
                transition = dict(schema_version='pipeline-fact-transitions/v1', from_main_commit=commit,
                    to_review_commit=commit, evidence_commit=commit, records=[proof])
                write_files(root, {'transitions.json':encoded(transition)})
                explicit = gate + ['--transitions', str(root/'transitions.json')]
                run(explicit); self.assertEqual(output.read_bytes(), original)
                transition['records'] = [{**proof, 'changed_fields':['asset_name', 'owner', 'amount_low']}]
                write_files(root, {'transitions.json':encoded(transition)})
                failed = subprocess.run(explicit, env=self.env, capture_output=True, text=True)
                self.assertNotEqual(failed.returncode, 0)
                self.assertEqual(output.read_bytes(), original)
                self.assertEqual({key: (repo/key).read_bytes() for key in evidence}, evidence)
                from unison_snapshot.builder import build
                from unison_snapshot.public_repo import PublicSnapshotRepository
                from test_public_repo import FakeTransport
                files = build(current, generated_at='2026-06-20T00:01:00Z', allow_production=True).files
                public = PublicSnapshotRepository('contract', 'data', transport=FakeTransport(files)).fetch().snapshot
                self.assertEqual(public['transactions'], [expected])
                self.assertEqual(expected['asset_name'].count(' — '), 1)

    def test_exact_mode_corrects_single_source_but_does_not_invent_alias_resolution(self):
        self.run_candidate(exact_source_copies=True)
        self.assertEqual(self.current()['transactions'], [self.expected])
        before = self.candidate.read_bytes(), self.audit.read_bytes()
        self.assertTrue(self.run_candidate(exact_source_copies=True)['idempotent'])
        self.assertEqual(before, (self.candidate.read_bytes(), self.audit.read_bytes()))
        self.candidate.write_bytes(encoded(self.base)); self.audit.unlink()
        from test_whitehouse_classification_consumption import NativeClassificationConsumptionTests
        NativeClassificationConsumptionTests.add_native_alias(self)
        self.run_candidate(exact_source_copies=True, expected_report_count=2)
        self.assertEqual(self.current()['transactions'], [self.old])
        audit = json.loads(self.audit.read_bytes())
        self.assertEqual(audit['exact_source_aliases'], [])
        self.assertEqual(audit['quarantined_row_count'], 2)
        before = self.candidate.read_bytes(), self.audit.read_bytes()
        self.assertTrue(self.run_candidate(exact_source_copies=True, expected_report_count=2)['idempotent'])
        self.assertEqual(before, (self.candidate.read_bytes(), self.audit.read_bytes()))

    def unrelated_fact(self):
        return {**self.old, 'id':'oge-278t:'+'f'*24, 'filing_id':'wh-url:'+'e'*24,
            'source_url':'https://www.whitehouse.gov/wp-content/uploads/2026/06/Prior-PTR.pdf',
            'asset_name':'Unrelated Prior Corporation'}

    def test_fresh_without_legacy_retains_first_and_repeated_account_display(self):
        _, _, review, evidence, base = fixture(inherit_legacy=False)
        for path in self.review.rglob('*.json'):
            path.unlink()
        write_files(self.review, review); write_files(self.evidence, evidence)
        baseline = self.unrelated_fact(); base['transactions'] = [baseline]
        self.candidate.write_bytes(encoded(base))
        self.run_candidate()
        current = self.current()
        self.assertEqual(len(current['transactions']), 2)
        self.assertIn(baseline, current['transactions'])
        fresh = next(row for row in current['transactions'] if row['id'] != baseline['id'])
        self.assertEqual(fresh['asset_name'], self.expected['asset_name'])
        self.assertNotEqual(fresh['id'], self.old['id'])
        before = self.candidate.read_bytes(), self.audit.read_bytes()
        self.assertTrue(self.run_candidate()['idempotent'])
        self.assertEqual(before, (self.candidate.read_bytes(), self.audit.read_bytes()))

    def test_unrelated_isolation_does_not_change_source_account_projection(self):
        old, expected, review, evidence, base = fixture(extra='unrelated')
        for path in (self.review/'whitehouse/extractions').rglob('*.json'):
            path.unlink()
        write_files(self.review, review); write_files(self.evidence, evidence)
        self.candidate.write_bytes(encoded(base))
        self.run_candidate()
        self.assertEqual(self.current()['transactions'], [expected])
        self.assertEqual(old['instrument_type'], expected['instrument_type'])
        self.assertEqual(json.loads(self.audit.read_bytes())['quarantined_row_count'], 1)

    def test_existing_exhibit_application_and_nested_replay_keep_real_account_source(self):
        from unittest.mock import patch
        import pdfplumber
        from test_oge_whitehouse_replacements import cross_fixture, DOCUMENT_ID
        from test_oge_annual import _Document, _Page
        from unison_snapshot import oge_cross_source_replacements as cross
        extractions, annual, coverage, directory, people, *_ = candidate_cli._verify_review(
            self.review, expected_report_count=1)
        d = cross_fixture(self.root/'exhibit')
        for source, destination in ((d['evidence'], self.evidence), (d['review'], self.review)):
            for path in source.rglob('*'):
                if path.is_file():
                    target = destination/path.relative_to(source)
                    if not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(path.read_bytes())
        inputs = dict(base=self.base, extractions=[*extractions, d['wh']],
            oge_whitehouse_coverage=coverage, directory_index=directory,
            annual_extractions=annual, annual_identity_people=people)
        native_sha = extractions[0]['source_sha256']
        real_open = pdfplumber.open
        def opened(path, *args, **kwargs):
            # Only the pre-existing unrelated OGE EXHIBIT fixture uses saved
            # table readings. The new account proof always parses real bytes.
            if Path(path).stem == native_sha:
                return real_open(path, *args, **kwargs)
            return _Document([_Page('', [d['raw']])])
        with patch('pdfplumber.open', side_effect=opened), \
                patch('unison_snapshot.oge_278t_page_shadow.observe_native_exhibit_page', side_effect=d['native']), \
                patch('unison_snapshot.oge_reports._extract_borderless_transaction_tables', return_value=[]):
            candidate, _, _ = cross._bound_whitehouse_candidate(inputs, self.evidence, self.review)
            result, proof = cross.apply_cross_source_native_replacements(candidate,
                {DOCUMENT_ID:d['inputs']}, inputs, self.evidence, self.review)
            self.assertEqual(len(proof['applications']), 1)
            self.assertIn(self.expected, result['transactions'])
            self.assertEqual(proof['context']['wh_inputs']['base'], self.base)
            self.assertEqual(cross.verify_cross_source_native_applications(proof,
                self.evidence, self.review), (result, proof))
            self.assertEqual(cross.replay_cross_source_context(proof['context'],
                self.evidence, self.review), (result, proof))
            self.assertNotIn('native_account_display_policy_version', proof['context']['wh_inputs'])


if __name__ == '__main__':
    unittest.main()
