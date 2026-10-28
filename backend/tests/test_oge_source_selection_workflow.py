"""Real Bash -> canonical preparation -> candidate/status source selection.

The PDFs are generated native contract inputs. No source/CLI is mocked, no OCR
is run, and explicit selection does not establish latest-version or revision
authority. Default single-version workflow coverage is reused from its tests.
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
from test_oge_source_selection import make_versioned_inputs
from unison_snapshot.codec import encode
from unison_snapshot.disclosure_status_oge import build_oge_status


ROOT = Path(__file__).resolve().parents[2]


class OgeSourceSelectionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.shell = bash_binary()
        if self.shell is None:
            self.skipTest('Bash is required to execute the actual source-selection workflow')
        temporary = tempfile.TemporaryDirectory(prefix='oge-source-workflow-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = make_versioned_inputs(self.root)
        self.review = self.data['review_root']
        for doc, sha in ((self.data['a'], self.data['sha_a']),
                         (self.data['a'], self.data['sha_a2']),
                         (self.data['b'], self.data['sha_b'])):
            self.cache(doc, sha).unlink()
        self.write(self.root / '.local/oge-catalog.json', json.loads(self.data['catalog_path'].read_bytes()))
        self.write(self.root / 'backend/data/empty-disclosure.json', json.loads(self.data['base_path'].read_bytes()))
        script = self.root / 'backend/src/unison_snapshot/commands/prepare_oge_ptr_generic.py'
        script.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'backend/src/unison_snapshot/commands/prepare_oge_ptr_generic.py', script)
        self.output = self.review / 'candidates/sources/oge-current.json'
        self.audit = self.review / 'oge/qualifications/current.json'
        self.env = dict(os.environ, REAL_PYTHON=sys.executable,
            TEST_SOURCE_ROOT=os.pathsep.join((str(ROOT / 'backend/src'), os.environ.get('PYTHONPATH', ''))),
            TEST_CUTOFF=self.data['cutoff'], REVIEW_ROOT=str(self.review),
            EVIDENCE_ROOT=str(self.data['evidence_root']),
            INCLUDE_SAVED_OGE_OBSERVATIONS='false', OGE_SAVED_OBSERVATIONS_PATH='',
            PYTHONIOENCODING='utf-8', MSYS_NO_PATHCONV='1')

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encode(value))

    def cache(self, document, sha):
        return self.data['extractions_root'] / document / sha / 'oge-278t-pdf-v7.json'

    def execute(self, *, generic='', generic_configured='', source='', sources_configured=''):
        env = {**self.env, 'OGE_GENERIC_DOCUMENT_ID': generic,
            'OGE_GENERIC_DOCUMENT_IDS': generic_configured, 'OGE_PTR_DOCUMENT_SOURCE': source,
            'OGE_PTR_DOCUMENT_SOURCES': sources_configured}
        script = ('set -euo pipefail\n'
            'python() { PYTHONPATH="$TEST_SOURCE_ROOT" "$REAL_PYTHON" "$@"; }\n'
            'cutoff="$TEST_CUTOFF"\n' + source_branch())
        return subprocess.run([self.shell, '-c', script], cwd=self.root,
                              env=env, capture_output=True, text=True)

    def preparation_receipts(self, result):
        values = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        return [value for value in values if value.get('schema_version') == 'oge-native-ptr-preparation/v1']

    def assert_selected(self):
        candidate, audit = json.loads(self.output.read_bytes()), json.loads(self.audit.read_bytes())
        facts = {row['filing_id']: row for row in candidate['transactions']}
        self.assertEqual(facts[self.data['a']], {**self.data['old_a'], 'owner': 'Undisclosed'})
        self.assertEqual(facts[self.data['b']], self.data['old_b'])
        reports = {row['document_id']: row for row in audit['reports']}
        binding = reports[self.data['a']]['source_interpretation_binding']
        self.assertEqual(binding['source_sha256'], self.data['sha_a'])
        self.assertEqual(binding['parser_version'], 'oge-278t-pdf/v7')
        self.assertEqual(reports[self.data['b']]['source_interpretation_binding']['parser_version'], 'oge-278t-pdf/v2')
        scopes = build_oge_status(self.data['evidence_root'], self.review, candidate['transactions'], [])
        status = {record['report_id']: record for scope in scopes if scope['scope_id'] == 'oge_direct_278t'
                  for record in scope['reports']}
        self.assertEqual(status[self.data['a']]['source_versions'], sorted([self.data['sha_a'], self.data['sha_a2']]))
        self.assertIn('multiple_archive_versions', status[self.data['a']]['reason_codes'])
        self.assertEqual(status[self.data['a']]['source_interpretation_binding'], binding)
        self.assertEqual([status[doc]['qualified_transaction_count'] for doc in (self.data['a'], self.data['b'])], [1, 1])
        return candidate, audit

    def test_real_branch_prepares_exact_missing_version_deduplicates_and_repeats_next_base(self):
        a, sha = self.data['a'], self.data['sha_a']
        pin = f'{a}:{sha}'
        protected = {path: path.read_bytes() for root in (self.data['evidence_root'], self.data['extractions_root'])
                     for path in root.rglob('*') if path.is_file()}
        self.assertFalse(self.cache(a, sha).exists())
        result = self.execute(generic=a, source=pin)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipts = self.preparation_receipts(result)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]['document_id'], a)
        self.assertEqual(receipts[0]['source_sha256'], sha)
        self.assertEqual(receipts[0]['source_selection'], 'explicit_archived_input')
        self.assertEqual(receipts[0]['created_paths'], [self.cache(a, sha).relative_to(self.review).as_posix()])
        candidate, _ = self.assert_selected()
        self.assertFalse(self.cache(a, self.data['sha_a2']).exists())
        self.assertFalse(self.cache(self.data['b'], self.data['sha_b']).exists())
        first = self.output.read_bytes(), self.audit.read_bytes()
        # Same pair in configuration and dispatch is one selection/preparation.
        result = self.execute(generic=a, generic_configured=a, source=pin, sources_configured=pin)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.preparation_receipts(result)), 1)
        self.assertEqual(self.preparation_receipts(result)[0]['created_paths'], [])
        self.assertEqual((self.output.read_bytes(), self.audit.read_bytes()), first)
        self.write(self.root / 'backend/data/empty-disclosure.json', candidate)
        result = self.execute(generic_configured=a, sources_configured=pin)
        self.assertEqual(result.returncode, 0, result.stderr)
        repeated, _ = self.assert_selected()
        self.assertEqual(repeated['transactions'], candidate['transactions'])
        self.assertEqual(repeated['people'], candidate['people'])
        self.assertTrue(all(path.read_bytes() == raw for path, raw in protected.items()))

    def test_conflicting_unprepared_or_invalid_policy_and_source_failure_do_not_write(self):
        a, b, sha = self.data['a'], self.data['b'], self.data['sha_a']
        pin = f'{a}:{sha}'
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.audit.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_bytes(b'candidate sentinel')
        self.audit.write_bytes(b'audit sentinel')
        caches = {path: path.read_bytes() for path in self.data['extractions_root'].rglob('*') if path.is_file()}
        cases = [
            {'source': pin},
            {'generic': a, 'source': f'{b}:{self.data["sha_b"]}'},
            {'generic': a, 'source': pin, 'sources_configured': f'{a}:{self.data["sha_a2"]}'},
            {'generic': a, 'source': pin.upper()},
            {'generic': a, 'sources_configured': pin + ','},
            {'generic': a, 'source': '$(exit 0)'},
            {'generic': a, 'sources_configured': ','.join(f'{n:032x}:{n:064x}' for n in range(33))},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                result = self.execute(**kwargs)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.output.read_bytes(), b'candidate sentinel')
                self.assertEqual(self.audit.read_bytes(), b'audit sentinel')
                self.assertEqual({path: path.read_bytes() for path in self.data['extractions_root'].rglob('*') if path.is_file()}, caches)
        source_path = self.data['evidence_root'] / f'oge/reports/{a}/{sha}.pdf'
        original = source_path.read_bytes()
        source_path.write_bytes(original + b' mismatch')
        result = self.execute(generic=a, source=pin)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_bytes(), b'candidate sentinel')
        self.assertEqual(self.audit.read_bytes(), b'audit sentinel')
        self.assertFalse(self.cache(a, sha).exists())
        source_path.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
