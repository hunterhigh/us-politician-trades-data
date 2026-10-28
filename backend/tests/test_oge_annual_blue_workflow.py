"""Execute the opt-in annual Bash branch; record, never install, model extras.

The recorder verifies workflow ordering/arguments, not model accuracy or source
qualification. Controlled runtime/source proof is exercised by its own tests.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from test_oge_annual_workflow import bash_binary


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/oge.yml'
DOCUMENT = 'd' * 32
from unison_snapshot.commands import prepare_oge_annual_runtime as cli


class AnnualBlueWorkflowTests(unittest.TestCase):
    def execute(self, *, main='true', scans='true', blue='', model='', blank='false',
                cover='false', tesseract_present=True, fail=''):
        shell = bash_binary()
        if shell is None:
            self.skipTest('Bash is required to execute the actual workflow branch')
        content = WORKFLOW.read_text(encoding='utf-8')
        block = textwrap.dedent(content.split(
            '          # BEGIN bounded saved annual preparation\n', 1)[1].split(
            '          # END bounded saved annual preparation', 1)[0])
        overlay = textwrap.dedent(content.split(
            '          # END bounded saved annual preparation\n', 1)[1].split(
            '          test "$MARKET_DISTRIBUTION_AUTHORIZED"', 1)[0])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = root / 'record.py'
            log = root / 'calls.jsonl'
            record.write_text(
                'import json,os,sys\nfrom pathlib import Path\nargs=sys.argv[1:]\nif args[:2]==["-m","unison_snapshot"]:args=args[2:]\nwith Path(os.environ["TEST_CALL_LOG"]).open("a",encoding="utf-8") as f:\n f.write(json.dumps(args)+"\\n")\nfailure=os.environ["TEST_FAILURE"]\nif failure=="prepare" and "prepare-oge-annual-runtime" in args[0]:sys.exit(9)\nif failure=="pip" and args[:2]==["-m","pip"]:sys.exit(8)\n',
                encoding='utf-8')
            with record.open('a', encoding='utf-8') as handle:
                handle.write('if failure=="sudo" and args[0]=="sudo":sys.exit(7)\n')
            script = ('set -euo pipefail\n'
                'python() { if [[ "$1" == - ]]; then "$REAL_PYTHON" "$@"; '
                'else "$REAL_PYTHON" "$TEST_RECORDER" "$@"; fi; }\n'
                'sudo() { "$REAL_PYTHON" "$TEST_RECORDER" sudo "$@"; }\n'
                'command() { if [[ "$1" == -v && "${2:-}" == tesseract ]]; then '
                '[[ "$TEST_TESSERACT_PRESENT" == 1 ]]; else builtin command "$@"; fi; }\n'
                + block + '\n' + overlay)
            env = dict(os.environ, REAL_PYTHON=sys.executable, TEST_RECORDER=str(record),
                TEST_CALL_LOG=str(log), TEST_FAILURE=fail, MSYS_NO_PATHCONV='1',
                INCLUDE_SAVED_OGE_ANNUAL=main, INCLUDE_SAVED_OGE_ANNUAL_SCANS=scans,
                OGE_ANNUAL_DOCUMENT_ID='', OGE_ANNUAL_PARSER_VERSION='',
                OGE_ANNUAL_BLUE_DATE_DOCUMENT_ID=blue,
                OGE_ANNUAL_BLUE_DATE_MODEL_DIRECTORY=model,
                VERIFY_OGE_ANNUAL_BLANK_SLOTS=blank,
                REPLAY_OGE_ANNUAL_COVER_OCR=cover,
                TEST_TESSERACT_PRESENT='1' if tesseract_present else '0',
                REVIEW_ROOT=str(root / 'review root'), EVIDENCE_ROOT=str(root / 'evidence root'),
                RUNNER_TEMP=str(root / 'runtime output'))
            result = subprocess.run([shell, '-c', script], cwd=ROOT, env=env,
                                    capture_output=True, text=True)
            calls = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()] if log.exists() else []
        return result, calls

    def test_explicit_blue_prepares_before_pinned_extra_then_real_overlay_call(self):
        model = 'models/existing blue date'
        result, calls = self.execute(blue=DOCUMENT, model=model, blank='true')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 3)
        prepare, install, overlay = calls
        self.assertEqual(prepare[0], 'prepare-oge-annual-runtime')
        self.assertEqual(prepare[prepare.index('--blue-date-document-id') + 1], DOCUMENT)
        self.assertEqual(prepare[prepare.index('--blue-date-model-directory') + 1], model)
        self.assertIn('--verify-transaction-blank-slots', prepare)
        self.assertIn('--include-saved-scans', prepare)
        self.assertEqual(install, ['-m', 'pip', 'install', '-e', 'backend[oge-handwritten-date]'])
        self.assertEqual(overlay[0], 'overlay-whitehouse-annual-candidate')
        self.assertEqual(overlay[overlay.index('--oge-annual-input') + 1],
                         prepare[prepare.index('--output') + 1])
        self.assertEqual(overlay[overlay.index('--oge-annual-runtime-root') + 1],
                         prepare[prepare.index('--evidence-root') + 1])

    def test_defaults_and_blank_only_never_install_handwritten_dependencies(self):
        for kwargs in ({'main': 'false', 'scans': 'false'}, {}, {'blank': 'true'}):
            with self.subTest(kwargs=kwargs):
                result, calls = self.execute(**kwargs)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(any(call[:2] == ['-m', 'pip'] for call in calls))
                self.assertFalse(any(call[0] == 'sudo' for call in calls))
                self.assertFalse(any('--blue-date-document-id' in call for call in calls))
                self.assertFalse(any('--replay-cover-ocr' in call for call in calls))
                if kwargs.get('blank') == 'true':
                    self.assertIn('--verify-transaction-blank-slots', calls[0])
                elif kwargs.get('main') == 'false':
                    self.assertEqual(len(calls), 1)
                    self.assertNotIn('--oge-annual-input', calls[0])

    def test_unpaired_unsafe_or_disabled_options_stop_before_prepare(self):
        cases = [
            {'blue': DOCUMENT}, {'model': 'models/existing'},
            {'blue': 'D' * 32, 'model': 'models/existing'},
            {'blue': '$(exit 0)', 'model': 'models/existing'},
            {'blue': DOCUMENT, 'model': 'models/existing', 'main': 'false'},
            {'blue': DOCUMENT, 'model': 'models/existing', 'scans': 'false'},
            {'blank': 'true', 'main': 'false'}, {'blank': 'true', 'scans': 'false'},
            {'blank': 'yes'},
            {'cover': 'true', 'main': 'false'}, {'cover': 'true', 'scans': 'false'},
            {'cover': 'yes'},
            *({'blue': DOCUMENT, 'model': value} for value in (
                '../model', '/model', 'models//x', 'models/./x', 'models/../x',
                'C:/models', 'models\\x', 'models/', 'models\nx', 'x' * 513)),
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                result, calls = self.execute(**kwargs)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])

    def test_prepare_or_dependency_failure_prevents_later_operations(self):
        for failure, expected, count in [('prepare', 9, 1), ('pip', 8, 2)]:
            with self.subTest(failure=failure):
                result, calls = self.execute(blue=DOCUMENT, model='models/existing', fail=failure)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(len(calls), count)
                self.assertFalse(any('overlay-whitehouse-annual-candidate' in call[0] for call in calls))

    def test_cover_replay_existing_engine_skips_install_and_missing_engine_installs_before_prepare(self):
        for present in (True, False):
            with self.subTest(tesseract_present=present):
                result, calls = self.execute(cover='true', tesseract_present=present)
                self.assertEqual(result.returncode, 0, result.stderr)
                if present:
                    self.assertEqual(len(calls), 2)
                else:
                    self.assertEqual(calls[:2], [['sudo', 'apt-get', 'update'],
                                               ['sudo', 'apt-get', 'install', '-y', 'tesseract-ocr']])
                    calls = calls[2:]
                self.assertEqual(calls[0][0], 'prepare-oge-annual-runtime')
                self.assertIn('--replay-cover-ocr', calls[0])
                self.assertEqual(calls[1][0], 'overlay-whitehouse-annual-candidate')
                self.assertFalse(any(call[:2] == ['-m', 'pip'] for call in calls))

    def test_cover_dependency_and_prepare_failures_stop_model_extra_and_overlay(self):
        for failure, code in [('sudo', 7), ('prepare', 9)]:
            with self.subTest(failure=failure):
                result, calls = self.execute(cover='true', tesseract_present=False,
                    blue=DOCUMENT, model='models/existing', fail=failure)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertFalse(any(call[:2] == ['-m', 'pip'] for call in calls))
                self.assertFalse(any('overlay-whitehouse-annual-candidate' in call[0] for call in calls))
                self.assertEqual(calls[0], ['sudo', 'apt-get', 'update'])
                if failure == 'sudo':
                    self.assertEqual(len(calls), 1)
                else:
                    self.assertEqual(calls[2][0], 'prepare-oge-annual-runtime')
        result, calls = self.execute(cover='true', tesseract_present=False,
                                    blue=DOCUMENT, model='models/existing')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[0] for call in calls], ['sudo', 'sudo',
            'prepare-oge-annual-runtime', '-m',
            'overlay-whitehouse-annual-candidate'])

    def test_cli_forwards_repeated_selection_without_executing_a_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'output.json'
            args = ['prepare', '--review-root', str(root / 'review'),
                '--evidence-root', str(root / 'evidence'), '--output', str(output),
                '--code-commit', 'a' * 40, '--started-at', '2026-10-27T00:00:00Z',
                '--include-saved-scans', '--blue-date-document-id', DOCUMENT,
                '--blue-date-document-id', 'e' * 32, '--blue-date-model-directory', 'models/existing',
                '--verify-transaction-blank-slots', '--replay-cover-ocr']
            value = {'controlled_annual_runtime': {'recognizer_started': False}}
            with patch.object(sys, 'argv', args), patch.object(cli, 'prepare_controlled_annual_input', return_value=value) as prepare:
                cli.main()
            self.assertEqual(json.loads(output.read_bytes()), value)
            kwargs = prepare.call_args.kwargs
            self.assertEqual(kwargs['blue_date_document_ids'], [DOCUMENT, 'e' * 32])
            self.assertEqual(kwargs['blue_date_model_directory'], 'models/existing')
            self.assertTrue(kwargs['include_saved_scans'])
            self.assertTrue(kwargs['verify_transaction_blank_slots'])
            self.assertTrue(kwargs['replay_cover_ocr'])
            output.write_bytes(b'prior input')
            with patch.object(sys, 'argv', args), patch.object(cli, 'prepare_controlled_annual_input', side_effect=ValueError('source mismatch')):
                with self.assertRaisesRegex(ValueError, 'source mismatch'):
                    cli.main()
            self.assertEqual(output.read_bytes(), b'prior input')

    def test_workflow_inputs_are_explicit_default_closed_and_not_repo_configuration(self):
        content = WORKFLOW.read_text(encoding='utf-8')
        inputs = content.split('    inputs:\n', 1)[1].split('permissions:', 1)[0]
        self.assertEqual(sum(line.startswith('      ') and not line.startswith('       ') and line.endswith(':')
                             for line in inputs.splitlines()), 12)
        for name in ('oge_annual_blue_date_document_id', 'oge_annual_blue_date_model_directory',
                     'verify_oge_annual_blank_slots', 'replay_oge_annual_cover_ocr'):
            self.assertIn('      ' + name + ':\n', inputs)
            self.assertIn('inputs.' + name, content)
            self.assertNotIn('vars.' + name.upper(), content)
        blank = inputs.split('      verify_oge_annual_blank_slots:', 1)[1]
        self.assertIn('type: boolean\n        default: false', blank)


if __name__ == '__main__':
    unittest.main()
