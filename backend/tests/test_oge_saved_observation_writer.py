"""Portable saved-input preparation reuses real PDF and existing saved fixtures."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_oge_cover_consumption import fixture, write, DOCUMENT_ID
from test_oge_content_relations import column_read
from unison_snapshot.oge_278t_field_shadow import observe_content_rows, observe_field_shapes
from unison_snapshot.oge_saved_observations import MAX_JSON_BYTES, load_saved_oge_observations


ROOT = Path(__file__).resolve().parents[2]
WRITER = ROOT / 'backend/src/unison_snapshot/commands/observe_oge_278t_fields.py'


def directory_alias(link, target):
    if os.name == 'nt':
        subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(link), str(target)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


class SavedOgeObservationWriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = fixture(self.root)
        self.primary = self.root / 'primary.json'
        write(self.primary, self.data['request']['observations'][0])
        self.ledger = self.root / 'field-ledger.json'
        self.package = (self.data['review'] / 'oge/saved_observations' /
                        DOCUMENT_ID / self.data['sha'] / 'saved-observations.json')

    def command(self, *, prepare=True, output=None):
        result = [sys.executable, str(WRITER), '--observations', str(self.primary),
                  '--output', str(output or self.ledger)]
        if prepare:
            result += ['--review-root', str(self.data['review']),
                       '--evidence-root', str(self.data['evidence']),
                       '--metadata', str(self.data['source'].with_suffix('.json'))]
        return result

    def run_writer(self, command):
        return subprocess.run(command, cwd=ROOT,
            env={**os.environ, 'PYTHONPATH': str(ROOT / 'backend/src'),
                 'PYTHONIOENCODING': 'utf-8'}, capture_output=True, text=True)

    def sentinels(self):
        self.package.parent.mkdir(parents=True, exist_ok=True)
        self.package.write_bytes(b'prior saved package')
        self.ledger.write_bytes(b'prior field ledger')
        return self.package.read_bytes(), self.ledger.read_bytes(), self.data['source'].read_bytes()

    def assert_unchanged(self, before):
        self.assertEqual(before, (self.package.read_bytes(), self.ledger.read_bytes(),
                                  self.data['source'].read_bytes()))

    def test_actual_writer_creates_portable_input_and_exact_existing_field_ledger(self):
        secondary = column_read(self.data['request']['observations'][0], 'type', 'purchase')
        secondary_path = self.root / 'type.json'; write(secondary_path, secondary)
        command = self.command() + ['--secondary-observations', str(secondary_path)]
        result = self.run_writer(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(self.package.read_text(encoding='utf-8'))
        self.assertEqual(set(payload), {DOCUMENT_ID})
        self.assertEqual(set(payload[DOCUMENT_ID]),
                         {'observations', 'document_page_count', 'secondary_observations'})
        self.assertEqual(payload[DOCUMENT_ID]['document_page_count'], 2)
        self.assertEqual(payload[DOCUMENT_ID]['observations'], self.data['request']['observations'])
        self.assertEqual(payload[DOCUMENT_ID]['secondary_observations'], [secondary])
        content = observe_content_rows(**payload[DOCUMENT_ID])
        fields = content.pop('field_observations'); fields.update(content)
        self.assertEqual(json.loads(self.ledger.read_text(encoding='utf-8')), fields)
        # The portable bytes can be bound by the normal loader at a new path;
        # no caller-supplied absolute PDF pointer is carried into review.
        loaded = load_saved_oge_observations(self.package, evidence_root=self.data['evidence'],
                                              extractions=[self.data['old']])
        self.assertEqual(Path(loaded[DOCUMENT_ID]['archived_pdf_path']), self.data['source'].resolve())
        before = self.package.read_bytes(), self.ledger.read_bytes()
        repeated = self.run_writer(command)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(before, (self.package.read_bytes(), self.ledger.read_bytes()))
        self.assertEqual(json.loads(result.stdout)['saved_observations_path'],
                         self.package.relative_to(self.data['review']).as_posix())

    def test_old_field_only_invocation_keeps_its_existing_schema_and_no_package(self):
        result = self.run_writer(self.command(prepare=False))
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = observe_field_shapes(self.data['request']['observations'])
        self.assertEqual(json.loads(self.ledger.read_text(encoding='utf-8')), expected)
        self.assertFalse(self.package.exists())

    def test_partial_flag_group_or_any_ocr_option_fails_before_outputs(self):
        before = self.sentinels()
        cases = [self.command(prepare=False) + ['--review-root', str(self.data['review'])],
                 self.command() + ['--description-executable', 'not-an-executable'],
                 self.command() + ['--read-description-pdf', str(self.data['source']),
                                   '--description-output', str(self.root/'must-not-write.json')]]
        for command in cases:
            with self.subTest(command=command):
                result = self.run_writer(command)
                self.assertNotEqual(result.returncode, 0)
                self.assert_unchanged(before)
        self.assertFalse((self.root/'must-not-write.json').exists())

    def test_wrong_source_count_and_bad_secondary_are_rejected_before_either_write(self):
        before = self.sentinels()
        result = self.run_writer(self.command() + ['--document-page-count', '1'])
        self.assertNotEqual(result.returncode, 0); self.assert_unchanged(before)
        observation = deepcopy(self.data['request']['observations'][0]); observation['source_sha256'] = '0'*64
        write(self.primary, observation)
        result = self.run_writer(self.command())
        self.assertNotEqual(result.returncode, 0); self.assert_unchanged(before)
        write(self.primary, self.data['request']['observations'][0])
        secondary = column_read(self.data['request']['observations'][0], 'type', 'purchase')
        secondary['source_sha256'] = '1'*64
        path = self.root/'wrong-secondary.json'; write(path, secondary)
        result = self.run_writer(self.command() + ['--secondary-observations', str(path)])
        self.assertNotEqual(result.returncode, 0); self.assert_unchanged(before)

    def test_output_aliases_source_tree_inputs_and_other_output_fail_before_writes(self):
        before = self.sentinels()
        primary_before = self.primary.read_bytes()
        for output in (self.primary, self.data['source'], self.data['source'].with_suffix('.json'),
                       self.package, self.data['evidence']/'new-output.json', self.package.parent):
            with self.subTest(output=output):
                result = self.run_writer(self.command(output=output))
                self.assertNotEqual(result.returncode, 0)
                self.assert_unchanged(before)
                self.assertEqual(self.primary.read_bytes(), primary_before)
        self.assertFalse((self.data['evidence']/'new-output.json').exists())

    def test_symlink_output_and_portable_directory_cannot_escape_or_cover_inputs(self):
        before = self.sentinels()
        link = self.root/'ledger-link.json'
        if os.name == 'nt':
            os.link(self.primary, link)
        else:
            link.symlink_to(self.primary)
        primary_before = self.primary.read_bytes()
        result = self.run_writer(self.command(output=link))
        self.assertNotEqual(result.returncode, 0); self.assert_unchanged(before)
        self.assertEqual(self.primary.read_bytes(), primary_before)
        alternate_review = self.root/'review-with-escape'; alternate_review.mkdir()
        directory_alias(alternate_review/'oge', self.data['evidence'])
        command = self.command(); command[command.index('--review-root')+1] = str(alternate_review)
        result = self.run_writer(command)
        self.assertNotEqual(result.returncode, 0); self.assert_unchanged(before)

    def test_external_hardlinked_ledger_or_package_cannot_truncate_canonical_source(self):
        before = self.sentinels()
        for original in (self.data['source'], self.data['source'].with_suffix('.json')):
            with self.subTest(original=original):
                alias = self.root / ('external-' + original.suffix.lstrip('.') + '.json')
                os.link(original, alias)
                source_bytes = original.read_bytes()
                result = self.run_writer(self.command(output=alias))
                self.assertNotEqual(result.returncode, 0)
                self.assert_unchanged(before)
                self.assertEqual(alias.read_bytes(), source_bytes)
        self.package.unlink()
        os.link(self.data['source'], self.package)
        before = self.package.read_bytes(), self.ledger.read_bytes(), self.data['source'].read_bytes()
        result = self.run_writer(self.command())
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged(before)

    def test_oversized_saved_input_is_bounded_before_parsing_and_outputs(self):
        before = self.sentinels()
        self.primary.write_bytes(b' ' * (MAX_JSON_BYTES+1))
        result = self.run_writer(self.command())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('byte budget', result.stderr)
        self.assert_unchanged(before)


if __name__ == '__main__':
    unittest.main()
