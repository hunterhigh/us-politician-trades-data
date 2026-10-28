"""Source dispatch and real Git/CLI owner-correction publication contracts.

Generated native source documents exercise the product path, not recognition
accuracy on official disclosures. Mocked tests below isolate source dispatch.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.pipeline_qa.migration_inventory import InventoryError
from unison_snapshot.pipeline_qa.publication_delta import build_publication_delta

MODULE = 'unison_snapshot.pipeline_qa.publication_delta'
MAIN, REVIEW, EVIDENCE = 'a' * 40, 'b' * 40, 'c' * 40


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n').encode()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else encoded(value))


def sources_for(candidate):
    return {name: deepcopy(candidate) if name == 'oge' else
            {**deepcopy(candidate), 'people': [], 'transactions': [], 'reported_holdings': []}
            for name in ('house_clerk', 'oge', 'senate_efd')}


def dispatch_fixture():
    from test_pipeline_publication_delta import PublicationDeltaTests
    old, current, prepared, _ = PublicationDeltaTests().fixture()
    common = {'source_id': 'oge', 'verification_status': 'official_matched',
              'owner': 'Self', 'person_id': 'person', 'asset_name': 'Example',
              'instrument_type': 'Stock'}
    old['transactions'] = [
        {**common, 'id': 'direct', 'filing_id': '1' * 32,
         'source_url': 'https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/'
                       + '1' * 32 + '/$FILE/Example.pdf'},
        {**common, 'id': 'whitehouse', 'filing_id': 'wh-url:' + '2' * 24,
         'source_url': 'https://www.whitehouse.gov/wp-content/uploads/2026/06/Example.pdf'}]
    current['transactions'] = [{**row, 'owner': 'Undisclosed'} for row in old['transactions']]
    prepared['transactions'] = deepcopy(current['transactions'])
    prepared['meta'] = deepcopy(current['meta'])
    return old, current, prepared, sources_for(prepared)


def event(row):
    return {'entity': 'transactions', 'kind': 'corrected', 'old_id': row['id'], 'new_id': row['id'],
            'reason': 'source_identity_correction', 'evidence_url': row['source_url'],
            'source_sha256': 'd' * 64, 'changed_fields': ['owner']}


class OgeOwnerSourceDispatchTests(unittest.TestCase):
    def run_gate(self, values, decisions=None):
        previous, unified, prepared, sources = values
        transitions = None if decisions is None else {
            'schema_version': 'pipeline-fact-transitions/v1', 'from_main_commit': MAIN,
            'to_review_commit': REVIEW, 'evidence_commit': EVIDENCE, 'records': decisions}
        return build_publication_delta(previous_board=previous, unified=unified,
            prepared=prepared, sources=sources, transitions=transitions, repo=Path('source-contract'),
            main_commit=MAIN, review_commit=REVIEW, evidence_commit=EVIDENCE)

    def test_shared_source_id_uses_both_independent_verifiers_once(self):
        values = dispatch_fixture(); records = [event(row) for row in values[2]['transactions']]
        with patch(MODULE + '.verified_owner_corrections', return_value=[records[1]]) as wh, \
             patch(MODULE + '.verified_oge_owner_corrections', return_value=[records[0]]) as direct:
            first = self.run_gate(values)
            explicit = self.run_gate(values, deepcopy(records))
        self.assertEqual(first['source_verified_correction_count'], 2)
        self.assertEqual(explicit['source_verified_correction_count'], 2)
        self.assertEqual(len(explicit['changes']), 2)
        self.assertEqual({item['old_id'] for item in first['changes']}, {'direct', 'whitehouse'})
        self.assertEqual(first['counts']['transactions']['new'], 0)
        self.assertEqual(first['counts']['transactions']['corrected'], 2)
        self.assertIn('archived_oge_catalog_native_owner', first['correction_verification_level'])
        self.assertIn('archived_whitehouse_native_owner', first['correction_verification_level'])
        for call in direct.call_args_list:
            self.assertEqual(set(call.kwargs['old']), {'direct'})
            self.assertEqual(call.kwargs['previous_people'], values[0]['people'])
            self.assertEqual((call.kwargs['main_commit'], call.kwargs['review_commit'],
                              call.kwargs['evidence_commit']), (MAIN, REVIEW, EVIDENCE))
        for call in wh.call_args_list:
            self.assertNotIn('direct', call.kwargs['old'])

    def test_missing_direct_source_proof_cannot_be_supplied_as_explicit_decision(self):
        values = dispatch_fixture(); records = [event(row) for row in values[2]['transactions']]
        with patch(MODULE + '.verified_owner_corrections', return_value=[records[1]]), \
             patch(MODULE + '.verified_oge_owner_corrections', return_value=[]):
            with self.assertRaisesRegex(InventoryError, 'unexplained removal or field change'):
                self.run_gate(values)
            with self.assertRaisesRegex(InventoryError, 'matching source-specific evidence verifier'):
                self.run_gate(values, records)

    def test_direct_verifier_failure_is_not_retried_as_whitehouse(self):
        values = dispatch_fixture()
        with patch(MODULE + '.verified_owner_corrections', return_value=[]) as wh, \
             patch(MODULE + '.verified_oge_owner_corrections',
                   side_effect=InventoryError('direct source rejected')) as direct:
            with self.assertRaisesRegex(InventoryError, 'direct source rejected'):
                self.run_gate(values)
        self.assertEqual(direct.call_count, 1)
        self.assertEqual(wh.call_count, 1)
        self.assertNotIn('direct', wh.call_args.kwargs['old'])


class OgeOwnerPublicationCliTests(unittest.TestCase):
    def test_normal_candidate_fixed_git_publication_repeat_and_failed_output_preservation(self):
        from test_oge_owner_correction import fixture, write_files, CUTOFF
        old, expected, review, evidence, previous = fixture(
            asset='Example Common Stock (EXM)')
        self.assertEqual(old['instrument_type'], 'Stock')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); repo = root / 'repo'; repo.mkdir()
            env = dict(os.environ, GIT_NO_LAZY_FETCH='1', PYTHONIOENCODING='utf-8')

            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], env=env,
                    stderr=subprocess.STDOUT).decode().strip()

            git('init', '-q'); git('config', 'user.email', 'contract@example.invalid')
            git('config', 'user.name', 'Contract')
            board_raw = encoded(previous); digest = hashlib.sha256(board_raw).hexdigest()
            save(repo / 'manifest.json', {'is_demo': False, 'board': digest})
            save(repo / f'board/{digest}.json', board_raw)
            git('add', '.'); git('commit', '-qm', 'fixed official predecessor contract')
            main_commit = git('rev-parse', 'HEAD')
            write_files(repo, evidence)
            git('add', '.'); git('commit', '-qm', 'fixed original native source contract')
            evidence_commit = git('rev-parse', 'HEAD')
            write_files(repo, review)
            save(root / 'previous.json', previous)
            catalog = next(path for path in evidence if '/catalog/normalized/' in path)
            output, audit = root / 'candidate.json', root / 'audit.json'
            native = [sys.executable, '-m', 'unison_snapshot', 'build-oge-candidate',
                '--catalog', str(repo / catalog), '--extractions-dir', str(repo / 'oge/extractions'),
                '--legacy-extractions-dir', str(repo / 'oge/extractions'), '--generic',
                '--evidence-root', str(repo), '--base', str(root / 'previous.json'),
                '--data-cutoff-at', CUTOFF, '--output', str(output), '--audit-output', str(audit)]

            def run(command):
                return subprocess.run(command, env=env, capture_output=True, text=True)

            first = run(native); self.assertEqual(first.returncode, 0, first.stderr)
            candidate = json.loads(output.read_bytes())
            self.assertEqual(candidate['transactions'], [expected])
            self.assertEqual(candidate['people'], previous['people'])
            first_bytes = (output.read_bytes(), audit.read_bytes())
            repeated = run(native); self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual((output.read_bytes(), audit.read_bytes()), first_bytes)
            save(repo / 'oge/qualifications/current.json', audit.read_bytes())
            git('add', '.'); git('commit', '-qm', 'normal generic candidate review')
            review_commit = git('rev-parse', 'HEAD')
            status_path = root / 'status.json'
            status_result = run([sys.executable,
                str(Path(__file__).resolve().parents[1] / 'scripts/build_disclosure_status.py'),
                '--evidence-root', str(repo), '--review-root', str(repo), '--input', str(output),
                '--code-commit', 'f' * 40, '--previous-main-commit', main_commit,
                '--output', str(status_path)])
            self.assertEqual(status_result.returncode, 0, status_result.stderr)
            status = json.loads(status_path.read_bytes())
            record = next(record for scope in status['scopes'] for record in scope['reports']
                          if record['report_id'] == old['filing_id'])
            self.assertEqual(record['publication_transaction_ids'], [old['id']])
            from unison_snapshot.builder import build
            from unison_snapshot.disclosure_status import attach_disclosure_status
            from unison_snapshot.public_repo import PublicSnapshotRepository
            from test_public_repo import FakeTransport
            bundle = attach_disclosure_status(
                build(candidate, generated_at=CUTOFF, allow_production=True), status['scopes'],
                transactions=candidate['transactions'], provenance=status['provenance'])
            public = PublicSnapshotRepository('contract', 'data', transport=FakeTransport(bundle.files))
            self.assertEqual(public.fetch().snapshot['transactions'], [expected])
            self.assertEqual(public.fetch('person', expected['person_id']).snapshot['transactions'], [expected])
            self.assertTrue(public.disclosure_status(public.resolve(), include_records=True))
            source_root = root / 'sources'
            for name, value in sources_for(candidate).items():
                save(source_root / f'{name}-current.json', value)
            delta = root / 'delta.json'
            gate = [sys.executable, '-m', 'unison_snapshot.pipeline_qa.publication_delta',
                '--previous-board', str(root / 'previous.json'), '--unified', str(output),
                '--prepared', str(output), '--source-root', str(source_root), '--output', str(delta),
                '--repo', str(repo), '--main-commit', main_commit, '--evidence-commit', evidence_commit,
                '--review-commit', review_commit]
            accepted = run(gate); self.assertEqual(accepted.returncode, 0, accepted.stderr)
            report = json.loads(delta.read_bytes()); original_delta = delta.read_bytes()
            self.assertEqual(report['source_verified_correction_count'], 1)
            self.assertEqual(report['counts']['transactions'],
                {'previous': 1, 'prepared': 1, 'unchanged': 0, 'new': 0, 'corrected': 1})
            self.assertEqual(len(report['changes']), 1)
            proof = report['changes'][0]
            self.assertEqual(proof['changed_fields'], ['owner'])
            self.assertEqual(proof['old_id'], proof['new_id'])
            self.assertFalse(report['publication_pointer_moved'])
            self.assertEqual(run(gate).returncode, 0)
            self.assertEqual(delta.read_bytes(), original_delta)
            transition = {'schema_version': 'pipeline-fact-transitions/v1',
                'from_main_commit': main_commit, 'to_review_commit': review_commit,
                'evidence_commit': evidence_commit, 'records': [proof]}
            save(root / 'transitions.json', transition)
            explicit = gate + ['--transitions', str(root / 'transitions.json')]
            accepted = run(explicit); self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertEqual(delta.read_bytes(), original_delta)
            for bad in ({**proof, 'source_sha256': '0' * 64},
                        {**proof, 'changed_fields': ['owner', 'instrument_type']}):
                transition['records'] = [bad]; save(root / 'transitions.json', transition)
                delta.write_bytes(b'sentinel')
                self.assertNotEqual(run(explicit).returncode, 0)
                self.assertEqual(delta.read_bytes(), b'sentinel')
            # A consistently changed current/source candidate still has no
            # permission to migrate a financial or classification field.
            for field, value in (('amount_low', 1002), ('instrument_type', 'Bond'),
                                 ('unknown_optional', None)):
                altered = deepcopy(candidate); altered['transactions'][0][field] = value
                save(output, altered); save(source_root / 'oge-current.json', altered)
                delta.write_bytes(b'sentinel')
                self.assertNotEqual(run(gate).returncode, 0, field)
                self.assertEqual(delta.read_bytes(), b'sentinel')
            save(output, candidate); save(source_root / 'oge-current.json', candidate)
            # Wrong immutable review is refused. Restoring the original pinned
            # review succeeds despite the later commit and bad working bytes.
            path = next(path for path in review if path.endswith('v7.json'))
            value = json.loads(review[path]); value['transactions'][0]['asset_name'] = 'FORGED'
            save(repo / path, value); git('add', '.'); git('commit', '-qm', 'altered review cache')
            bad_review = git('rev-parse', 'HEAD')
            wrong_gate = gate[:]; wrong_gate[wrong_gate.index('--review-commit') + 1] = bad_review
            delta.write_bytes(b'sentinel')
            self.assertNotEqual(run(wrong_gate).returncode, 0)
            self.assertEqual(delta.read_bytes(), b'sentinel')
            accepted = run(gate); self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertEqual(delta.read_bytes(), original_delta)
            self.assertEqual(previous['transactions'], [old])


if __name__ == '__main__':
    unittest.main()
