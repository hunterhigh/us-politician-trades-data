"""Actual workflow preparation and mixed selection through existing consumers.

Generated native sources exercise the contract, not official recognition.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_oge_annual_workflow import bash_binary
from test_oge_saved_observation_workflow import source_branch
from test_oge_scoped_generic_cli import make_mixed_inputs
from unison_snapshot.codec import encode


ROOT = Path(__file__).resolve().parents[2]


class ScopedGenericWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.shell = bash_binary()
        if self.shell is None:
            self.skipTest('Bash is required to execute the actual workflow branch')
        temporary = tempfile.TemporaryDirectory(prefix='oge-scoped-workflow-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = make_mixed_inputs(self.root)
        self.review = self.data['review_root']
        for doc, sha in ((self.data['a'], self.data['sha_a']),
                         (self.data['b'], self.data['sha_b'])):
            (self.data['extractions_root']/doc/sha/'oge-278t-pdf-v7.json').unlink()
        self.write(self.root/'.local/oge-catalog.json',
                   json.loads(self.data['catalog_path'].read_bytes()))
        self.original = json.loads(self.data['base_path'].read_bytes())
        self.write(self.root/'ingest/current.json', self.original)
        # The temporary workflow checkout contains the exact production script.
        script = self.root/'backend/scripts/prepare_oge_ptr_generic.py'
        script.parent.mkdir(parents=True)
        shutil.copyfile(ROOT/'backend/scripts/prepare_oge_ptr_generic.py', script)
        self.output = self.review/'candidates/sources/oge-current.json'
        self.audit = self.review/'oge/qualifications/current.json'
        self.env = dict(os.environ, REAL_PYTHON=sys.executable,
            TEST_SOURCE_ROOT=os.pathsep.join((str(ROOT/'backend/src'),
                                             os.environ.get('PYTHONPATH', ''))),
            TEST_CUTOFF=self.data['cutoff'], REVIEW_ROOT=str(self.review),
            EVIDENCE_ROOT=str(self.data['evidence_root']),
            INCLUDE_SAVED_OGE_OBSERVATIONS='false', OGE_SAVED_OBSERVATIONS_PATH='',
            PYTHONIOENCODING='utf-8', MSYS_NO_PATHCONV='1')

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encode(value))

    def execute(self, dispatched='', configured=''):
        env = {**self.env, 'OGE_GENERIC_DOCUMENT_ID': dispatched,
               'OGE_GENERIC_DOCUMENT_IDS': configured}
        script = ('set -euo pipefail\n'
            'python() { PYTHONPATH="$TEST_SOURCE_ROOT" "$REAL_PYTHON" "$@"; }\n'
            'cutoff="$TEST_CUTOFF"\n' + source_branch())
        return subprocess.run([self.shell, '-c', script], cwd=self.root,
                              env=env, capture_output=True, text=True)

    def result(self):
        return json.loads(self.output.read_bytes()), json.loads(self.audit.read_bytes())

    def git_commit(self, root):
        def git(*args):
            return subprocess.check_output(['git', '-C', str(root), *args],
                stderr=subprocess.STDOUT, text=True).strip()
        if not (root/'.git').exists():
            git('init', '-q')
            git('config', 'user.name', 'Scoped Native Contract')
            git('config', 'user.email', 'scoped@example.invalid')
        git('add', '.')
        if subprocess.run(['git', '-C', str(root), 'diff', '--cached', '--quiet']).returncode:
            git('commit', '-qm', 'fixed native workflow inputs')
        return git('rev-parse', 'HEAD')

    def public_status(self, candidate):
        self.git_commit(self.data['evidence_root'])
        self.git_commit(self.review)
        status_path = self.root/'status.json'
        result = subprocess.run([sys.executable,
            str(ROOT/'backend/scripts/build_disclosure_status.py'),
            '--evidence-root', str(self.data['evidence_root']),
            '--review-root', str(self.review), '--input', str(self.output),
            '--code-commit', 'f'*40, '--previous-main-commit', 'e'*40,
            '--output', str(status_path)], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(status_path.read_bytes())
        records = {row['report_id']: row for scope in status['scopes']
                   for row in scope['reports']}
        for document in (self.data['a'], self.data['b']):
            self.assertEqual(records[document]['qualified_transaction_count'], 1)
            self.assertEqual(len(records[document]['publication_transaction_ids']), 1)
        from unison_snapshot.builder import build
        from unison_snapshot.disclosure_status import attach_disclosure_status
        from unison_snapshot.public_repo import PublicSnapshotRepository
        from test_public_repo import FakeTransport
        bundle = attach_disclosure_status(
            build(candidate, generated_at=self.data['cutoff'], allow_production=True),
            status['scopes'], transactions=candidate['transactions'],
            provenance=status['provenance'])
        public = PublicSnapshotRepository('contract', 'data', transport=FakeTransport(bundle.files))
        facts = candidate['transactions']
        self.assertEqual(public.fetch().snapshot['transactions'], facts)
        person = candidate['people'][0]['id']
        self.assertEqual(public.fetch('person', person).snapshot['transactions'], facts)
        self.assertTrue(public.disclosure_status(public.resolve(), include_records=True))

    def test_dispatch_prepares_one_pair_and_configured_next_run_preserves_mixed_facts(self):
        originals = {path: path.read_bytes() for path in self.data['evidence_root'].rglob('*')
                     if path.is_file()}
        result = self.execute(dispatched=self.data['a'])
        self.assertEqual(result.returncode, 0, result.stderr)
        candidate, audit = self.result()
        expected = [{**self.data['old_a'], 'owner': 'Undisclosed'}, self.data['old_b']]
        self.assertEqual(candidate['transactions'], sorted(expected, key=lambda row: row['id']))
        selected = {row['document_id']: row['source_interpretation_binding']['parser_version']
                    for row in audit['reports']}
        self.assertEqual(selected, {self.data['a']: 'oge-278t-pdf/v7',
                                   self.data['b']: 'oge-278t-pdf/v2'})
        self.assertFalse((self.data['extractions_root']/self.data['b']/self.data['sha_b']/
                          'oge-278t-pdf-v7.json').exists())
        first = self.output.read_bytes(), self.audit.read_bytes()
        result = self.execute(configured=self.data['a'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output.read_bytes(), self.audit.read_bytes()), first)
        self.assertTrue(all(path.read_bytes() == content for path, content in originals.items()))
        self.public_status(candidate)
        # The actual previous output becomes the next base. Configuration
        # explicitly carries the interpretation choice into a scheduled run.
        self.write(self.root/'ingest/current.json', candidate)
        result = self.execute(configured=self.data['a'])
        self.assertEqual(result.returncode, 0, result.stderr)
        next_candidate, _ = self.result()
        self.assertEqual(next_candidate['transactions'], candidate['transactions'])
        self.public_status(next_candidate)

    def test_configured_and_dispatch_selection_union_uses_existing_receivers(self):
        result = self.execute(dispatched=self.data['b'], configured=self.data['a'])
        self.assertEqual(result.returncode, 0, result.stderr)
        candidate, audit = self.result()
        self.assertEqual(candidate['transactions'], sorted([
            {**self.data['old_a'], 'owner': 'Undisclosed'},
            {**self.data['old_b'], 'owner': 'Undisclosed'}], key=lambda row: row['id']))
        self.assertEqual({row['source_interpretation_binding']['parser_version']
                          for row in audit['reports']}, {'oge-278t-pdf/v7'})
        self.assertEqual(len(candidate['transactions']), 2)

    def test_invalid_policy_and_source_failure_preserve_existing_outputs(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.audit.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_bytes(b'candidate sentinel'); self.audit.write_bytes(b'audit sentinel')
        for dispatched, configured in (('$(exit 0)', ''), ('', self.data['a']+','),
                                       ('', self.data['a'].upper())):
            with self.subTest(dispatched=dispatched, configured=configured):
                result = self.execute(dispatched, configured)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.output.read_bytes(), b'candidate sentinel')
                self.assertEqual(self.audit.read_bytes(), b'audit sentinel')
        cache = self.data['extractions_root']/self.data['a']/self.data['sha_a']/'oge-278t-pdf-v2.json'
        original = cache.read_bytes()
        wrong = json.loads(original); wrong['self_signed_selection'] = True
        cache.write_bytes(encode(wrong))
        result = self.execute(configured=self.data['a'])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_bytes(), b'candidate sentinel')
        self.assertEqual(self.audit.read_bytes(), b'audit sentinel')
        self.assertEqual(json.loads(cache.read_bytes()), wrong)
        self.assertFalse((cache.parent/'oge-278t-pdf-v7.json').exists())


if __name__ == '__main__':
    unittest.main()
