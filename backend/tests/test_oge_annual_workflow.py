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
    def run_branch(self, enabled='false', document='', *, fail=False):
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
            record.write_text("import json,os,sys\nfrom pathlib import Path\n"
                "p=Path(os.environ['TEST_CALL_LOG'])\n"
                "with p.open('a',encoding='utf-8') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
                "if os.environ.get('TEST_FAIL_PREPARE')=='1' and 'prepare_oge_annual_runtime.py' in sys.argv[1]: sys.exit(9)\n",
                encoding='utf-8')
            log = folder / 'calls.jsonl'
            script = ('set -euo pipefail\n'
                'python() { "$REAL_PYTHON" "$TEST_RECORDER" "$@"; }\n' + block +
                '\npython overlay-capture "${annual_args[@]}"\n')
            env = dict(os.environ, REAL_PYTHON=sys.executable, TEST_RECORDER=str(record),
                TEST_CALL_LOG=str(log), TEST_FAIL_PREPARE='1' if fail else '0',
                INCLUDE_SAVED_OGE_ANNUAL=enabled, OGE_ANNUAL_DOCUMENT_ID=document,
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
        self.assertEqual(prepare[0], 'backend/scripts/prepare_oge_annual_runtime.py')
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
        self.assertIn('prepare_oge_annual_runtime.py', calls[0][0])

    def test_evidence_tree_and_opt_in_are_bound_in_the_real_workflow(self):
        content = WORKFLOW.read_text(encoding='utf-8')
        self.assertIn('include_saved_oge_annual:\n', content)
        self.assertIn('type: boolean\n        default: false', content)
        self.assertIn('evidence_root="$RUNNER_TEMP/oge-review-evidence"', content)
        self.assertIn('git worktree add --detach "$evidence_root" FETCH_HEAD', content)
        self.assertIn('EVIDENCE_ROOT: ${{ runner.temp }}/oge-review-evidence', content)
        overlay = content.split('python backend/scripts/overlay_whitehouse_annual_candidate.py', 1)[1].split(
            'test "$MARKET_DISTRIBUTION_AUTHORIZED"', 1)[0]
        self.assertIn('"${annual_args[@]}"', overlay)


if __name__ == '__main__':
    unittest.main()
