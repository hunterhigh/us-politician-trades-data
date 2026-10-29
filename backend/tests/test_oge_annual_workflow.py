"""Execute the workflow's bounded annual branch, including failure propagation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/oge.yml'


def bash_binary():
    found = shutil.which('bash')
    if found:
        return found
    git = shutil.which('git')
    if git:
        candidate = Path(git).resolve().parents[1] / 'bin/bash.exe'
        if candidate.is_file():
            return str(candidate)
    return None


class AnnualWorkflowTests(unittest.TestCase):
    def run_branch(self, enabled='false', document='', *, fail=False, scans='false', parser=''):
        shell = bash_binary()
        if shell is None:
            self.skipTest('Bash is required to execute the actual workflow branch')
        workflow = WORKFLOW.read_text(encoding='utf-8')
        block = textwrap.dedent(workflow.split(
            '          # BEGIN bounded saved annual preparation\n', 1)[1].split(
            '          # END bounded saved annual preparation', 1)[0])
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            record = folder / 'record.py'
            record.write_text("import json,os,sys\nfrom pathlib import Path\nargs=sys.argv[1:]\nif args[:2]==['-m','unison_snapshot']:args=args[2:]\np=Path(os.environ['TEST_CALL_LOG'])\nwith p.open('a',encoding='utf-8') as f: f.write(json.dumps(args)+'\\n')\nif os.environ.get('TEST_FAIL_PREPARE')=='1' and args[0]=='prepare-oge-annual-runtime': sys.exit(9)\n",
                encoding='utf-8')
            log = folder / 'calls.jsonl'
            script = ('set -euo pipefail\n'
                'python() { "$REAL_PYTHON" "$TEST_RECORDER" "$@"; }\n' + block +
                '\npython overlay-capture "${annual_args[@]}"\n')
            env = dict(os.environ, REAL_PYTHON=sys.executable, TEST_RECORDER=str(record),
                TEST_CALL_LOG=str(log), TEST_FAIL_PREPARE='1' if fail else '0',
                INCLUDE_SAVED_OGE_ANNUAL=enabled, OGE_ANNUAL_DOCUMENT_ID=document,
                INCLUDE_SAVED_OGE_ANNUAL_SCANS=scans,
                OGE_ANNUAL_PARSER_VERSION=parser,
                REVIEW_ROOT=str(folder / 'review root'), EVIDENCE_ROOT=str(folder / 'evidence root'),
                RUNNER_TEMP=str(folder / 'runtime output'), MSYS_NO_PATHCONV='1')
            result = subprocess.run([shell, '-c', script], cwd=ROOT, env=env,
                                    capture_output=True, text=True)
            calls = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()] if log.exists() else []
        return result, calls

    def test_default_and_explicit_selection_preserve_separate_argument_boundaries(self):
        result, calls = self.run_branch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [['overlay-capture']])
        result, calls = self.run_branch('true', 'a' * 32)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 2)
        prepare, overlay = calls
        self.assertEqual(prepare[0], 'prepare-oge-annual-runtime')
        self.assertEqual(prepare[-2:], ['--document-id', 'a' * 32])
        output = prepare[prepare.index('--output') + 1]
        evidence = prepare[prepare.index('--evidence-root') + 1]
        self.assertEqual(overlay, ['overlay-capture', '--oge-annual-input', output,
                                   '--oge-annual-runtime-root', evidence])
        self.assertIn('runtime output', output)

    def test_invalid_switch_or_selection_and_preparation_failure_stop_overlay(self):
        for enabled, document in [('false', 'a' * 32), ('true', '$(exit 0)'), ('yes', '')]:
            with self.subTest(enabled=enabled, document=document):
                result, calls = self.run_branch(enabled, document)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])
        result, calls = self.run_branch('true', fail=True)
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assertEqual(len(calls), 1)
        self.assertIn('prepare-oge-annual-runtime', calls[0][0])

    def test_scan_preparation_requires_its_explicit_switch_and_main_preparation(self):
        result, calls = self.run_branch('true', 'a'*32, scans='true')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--include-saved-scans', calls[0])
        self.assertEqual(calls[0][-3:], ['--include-saved-scans', '--document-id', 'a'*32])
        for enabled, scans in [('false', 'true'), ('true', 'yes'), ('true', '$(exit 0)')]:
            with self.subTest(enabled=enabled, scans=scans):
                result, calls = self.run_branch(enabled, scans=scans)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])
        result, calls = self.run_branch('true', scans='true', fail=True)
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assertEqual(len(calls), 1)

    def test_evidence_tree_and_opt_in_are_bound_in_the_real_workflow(self):
        content = WORKFLOW.read_text(encoding='utf-8')
        self.assertIn('include_saved_oge_annual:\n', content)
        self.assertIn('include_saved_oge_annual_scans:\n', content)
        self.assertIn('type: boolean\n        default: false', content)
        self.assertIn('uses: ./.github/actions/data-trees', content)
        self.assertIn('workspace: ${{ runner.temp }}/oge-source', content)
        self.assertIn('EVIDENCE_ROOT: ${{ steps.trees.outputs.evidence_root }}', content)
        overlay = content.split('python -m unison_snapshot overlay-whitehouse-annual-candidate', 1)[1].split(
            'test "$MARKET_DISTRIBUTION_AUTHORIZED"', 1)[0]
        self.assertIn('"${annual_args[@]}"', overlay)

    def test_native_parser_selection_is_explicit_and_cannot_enable_annual_preparation(self):
        selected = 'oge-278e-tables/v3'
        result, calls = self.run_branch('true', 'a'*32, parser=selected)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[0][-4:], ['--parser-version', selected, '--document-id', 'a'*32])
        result, calls = self.run_branch(parser=selected)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
