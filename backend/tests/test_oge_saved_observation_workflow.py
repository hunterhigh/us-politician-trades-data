"""Execute the workflow's saved PTR selection and its actual candidate CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

from test_oge_annual_workflow import bash_binary
from test_oge_cover_consumption import fixture, write, DOCUMENT_ID, CUTOFF
from test_oge_content_relations import saved
from test_oge_saved_observation_writer import directory_alias


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/oge.yml'


def source_branch():
    content = WORKFLOW.read_text(encoding='utf-8')
    block = textwrap.dedent(content.split(
        '          # BEGIN bounded saved PTR selection\n', 1)[1].split(
        '          # END bounded saved PTR selection', 1)[0])
    call = textwrap.dedent(content.split(
        '          # END bounded saved PTR selection\n', 1)[1].split(
        '          PYTHONPATH=backend/src python -m unison_snapshot build-whitehouse-278t-candidate', 1)[0])
    return block + '\n' + call


class SavedOgeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.shell = bash_binary()
        if self.shell is None:
            self.skipTest('Bash is required to execute the real workflow branch')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.review = self.root/'review root'; self.review.mkdir()
        self.selected = self.review/'oge/saved_observations/a chosen input.json'
        write(self.selected, {})
        self.recorder = self.root/'recorder.py'
        self.log = self.root/'calls.jsonl'
        self.recorder.write_text('import json,os,sys\nfrom pathlib import Path\n'
            'with Path(os.environ["TEST_CALL_LOG"]).open("a",encoding="utf-8") as f:\n'
            ' f.write(json.dumps(sys.argv[1:])+"\\n")\n', encoding='utf-8')

    def execute(self, enabled='false', path='', *, real=False, evidence=None, cwd=None):
        function = ('python() { PYTHONPATH="$TEST_SOURCE_ROOT" "$REAL_PYTHON" "$@"; }\n' if real else
            'python() { if [[ "$1" == - ]]; then PYTHONPATH="$TEST_SOURCE_ROOT" "$REAL_PYTHON" "$@"; '
            'else "$REAL_PYTHON" "$TEST_RECORDER" "$@"; fi; }\n')
        script = 'set -euo pipefail\n' + function + 'cutoff="$TEST_CUTOFF"\n' + source_branch()
        env = dict(os.environ, REAL_PYTHON=sys.executable, TEST_SOURCE_ROOT=str(ROOT/'backend/src'),
            TEST_RECORDER=str(self.recorder), TEST_CALL_LOG=str(self.log), TEST_CUTOFF=CUTOFF,
            INCLUDE_SAVED_OGE_OBSERVATIONS=enabled, OGE_SAVED_OBSERVATIONS_PATH=path,
            REVIEW_ROOT=str(self.review), EVIDENCE_ROOT=str(evidence or self.root/'evidence root'),
            MSYS_NO_PATHCONV='1', PYTHONIOENCODING='utf-8')
        result = subprocess.run([self.shell, '-c', script], cwd=cwd or self.root,
                                env=env, capture_output=True, text=True)
        calls = [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines()] if self.log.exists() else []
        if self.log.exists():
            self.log.unlink()
        return result, calls

    def test_exact_bash_default_and_selected_arguments_keep_default_parser_and_roots(self):
        result, calls = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)
        self.assertNotIn('--saved-observations', calls[0])
        self.assertNotIn('--generic', calls[0])
        selected = self.selected.relative_to(self.review).as_posix()
        result, calls = self.execute('true', selected)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call[:3], ['-m', 'unison_snapshot', 'build-oge-candidate'])
        self.assertEqual(call[call.index('--saved-observations')+1], str(self.selected.resolve()))
        self.assertEqual(call[call.index('--evidence-root')+1], str(self.root/'evidence root'))
        self.assertNotIn('--generic', call)

    def test_invalid_or_missing_selection_stops_before_candidate_invocation(self):
        cases = [('false', self.selected.relative_to(self.review).as_posix()), ('yes', ''),
                 ('true', ''), ('true', '../outside.json'), ('true', '/outside.json'),
                 ('true', 'C:/outside.json'), ('true', 'oge\\wrong.json'),
                 ('true', 'oge//missing.json'), ('true', 'oge/./missing.json'),
                 ('true', 'missing.json'), ('true', 'oge/$(exit 0).json')]
        for enabled, path in cases:
            with self.subTest(enabled=enabled, path=path):
                result, calls = self.execute(enabled, path)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])

    def test_selected_symlink_cannot_escape_controlled_review_root(self):
        outside = self.root/'outside'/'saved.json'; write(outside, {})
        directory_alias(self.review/'escape', outside.parent)
        result, calls = self.execute('true', 'escape/saved.json')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
        self.assertEqual(outside.read_text(encoding='utf-8'), '{}')

    def test_actual_writer_and_workflow_cli_consume_new_fact_then_repeat_and_reject_bad_source(self):
        fixture_root = self.root/'source-fixture'; fixture_root.mkdir()
        data = fixture(fixture_root)
        self.review = data['review']
        observation = saved([data['old']['transactions'][0]['cells'],
            ['', 'Filer assets', '', '', '', ''],
            ['2', 'ALPHA', 'purchase', '05/02/2025', 'No', '$1,001 - $15,000']])
        observation['source_sha256'] = data['sha']
        primary = self.root/'primary.json'; write(primary, observation)
        ledger = self.root/'field-ledger.json'
        writer = subprocess.run([sys.executable, str(ROOT/'backend/src/unison_snapshot/commands/observe_oge_278t_fields.py'),
            '--observations', str(primary), '--review-root', str(self.review),
            '--evidence-root', str(data['evidence']), '--metadata', str(data['source'].with_suffix('.json')),
            '--output', str(ledger)], cwd=ROOT,
            env={**os.environ, 'PYTHONPATH': str(ROOT/'backend/src')}, capture_output=True, text=True)
        self.assertEqual(writer.returncode, 0, writer.stderr)
        selected = json.loads(writer.stdout)['saved_observations_path']
        write(self.root/'.local/oge-catalog.json', data['catalog'])
        write(self.root/'backend/data/empty-disclosure.json', data['formal'])
        result, _ = self.execute('true', selected, real=True, evidence=data['evidence'])
        self.assertEqual(result.returncode, 0, result.stderr)
        candidate_path = self.review/'candidates/sources/oge-current.json'
        audit_path = self.review/'oge/qualifications/current.json'
        candidate = json.loads(candidate_path.read_text(encoding='utf-8'))
        audit = json.loads(audit_path.read_text(encoding='utf-8'))
        self.assertEqual(len(candidate['transactions']), 2)
        self.assertIn(data['formal']['transactions'][0], candidate['transactions'])
        self.assertIn('ALPHA', [row['asset_name'] for row in candidate['transactions']])
        self.assertFalse(audit['reports'][0]['observation_receipt']['coverage_complete'])
        before = (candidate_path.read_bytes(), audit_path.read_bytes(), data['source'].read_bytes())
        result, _ = self.execute('true', selected, real=True, evidence=data['evidence'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, (candidate_path.read_bytes(), audit_path.read_bytes(), data['source'].read_bytes()))
        package = self.review/selected
        payload = json.loads(package.read_text(encoding='utf-8'))
        payload[DOCUMENT_ID]['observations'][0]['source_sha256'] = '0'*64
        write(package, payload)
        result, _ = self.execute('true', selected, real=True, evidence=data['evidence'])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, (candidate_path.read_bytes(), audit_path.read_bytes(), data['source'].read_bytes()))

    def test_opt_in_and_native_dependencies_exist_only_in_normal_publish_job(self):
        content = WORKFLOW.read_text(encoding='utf-8')
        option = content.split('      include_saved_oge_observations:\n', 1)[1].split('      oge_saved_observations_path:', 1)[0]
        self.assertIn('type: boolean\n        default: false', option)
        publish = content.split('  source:', 1)[1]
        install = publish.index("uses: ./.github/actions/setup-source")
        self.assertLess(install, publish.index('build-oge-candidate'))
        shadow = content.split('      - name: Publish normalized review data', 1)[0]
        self.assertNotIn('"${saved_observation_args[@]}"', shadow)
        self.assertNotIn('prepare_saved_oge_observations', content)


if __name__ == '__main__':
    unittest.main()
