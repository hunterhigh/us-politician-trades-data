"""Controlled native annual preparation through the normal offline CLIs."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_oge_annual_candidate import source, inputs, base, CONTENT, DOC, SHA
from unison_snapshot.oge_annual import PARSER_VERSION
from unison_snapshot.oge_annual_runtime import prepare_controlled_annual_input
from unison_snapshot.whitehouse_annual_candidate import overlay_oge_annual_candidate


class ControlledAnnualRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.review, self.evidence = self.root/'review', self.root/'evidence'
        self.review.mkdir(); self.evidence.mkdir()
        self.extraction = source()
        self.request = inputs(self.extraction)
        self.extraction_path = self.save_extraction(self.extraction)
        self.meta_path = self.evidence/f'oge/annual/reports/{DOC}/{SHA}.json'
        self.write(self.meta_path, self.request['archive_metadata_by_document'][DOC])
        self.pdf_path = self.meta_path.with_suffix('.pdf'); self.pdf_path.write_bytes(CONTENT)
        self.output, self.candidate, self.audit = self.root/'input.json', self.root/'candidate.json', self.root/'audit.json'
        self.repo = Path(__file__).resolve().parents[2]

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def save_extraction(self, value):
        path = self.review/f'oge/annual/extractions/{value["source_document_id"]}/{value["source_sha256"]}/{value["parser_version"].replace("/", "-")}.json'
        self.write(path, value)
        return path

    def run_cli(self, name, arguments):
        return subprocess.run([sys.executable, str(self.repo/'backend/scripts'/name), *arguments],
            env=dict(os.environ, PYTHONPATH=str(self.repo/'backend/src')), capture_output=True)

    def prepare(self, *extra):
        return self.run_cli('prepare_oge_annual_runtime.py', [
            '--review-root', str(self.review), '--evidence-root', str(self.evidence),
            '--output', str(self.output), '--code-commit', 'a'*40,
            '--started-at', '2026-10-23T00:00:00Z', *extra])

    def overlay(self, *, runtime=True, candidate=None, audit=None):
        return self.run_cli('overlay_whitehouse_annual_candidate.py', [
            '--review-root', str(self.review), '--evidence-root', str(self.evidence),
            '--candidate', str(candidate or self.candidate), '--audit-out', str(audit or self.audit),
            '--oge-annual-input', str(self.output),
            *(['--oge-annual-runtime-root', str(self.evidence)] if runtime else [])])

    def test_v3_prepare_move_and_normal_overlay_are_offline_and_repeatable(self):
        first = self.prepare('--document-id', DOC)
        self.assertEqual(first.returncode, 0, first.stderr)
        saved = self.output.read_bytes()
        prepared = json.loads(saved)
        self.assertEqual(prepared['extractions'], [self.extraction])
        self.assertEqual(len(prepared['controlled_annual_runtime']['files']), 3)
        self.assertIs(prepared['controlled_annual_runtime']['recognizer_started'], False)
        again = self.prepare('--document-id', DOC)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.output.read_bytes(), saved)
        # No machine paths in the package: move both trees and the input.
        relocated = self.root/'relocated'; relocated.mkdir()
        for path in (self.review, self.evidence, self.output):
            shutil.move(str(path), str(relocated/path.name))
        self.review, self.evidence, self.output = relocated/'review', relocated/'evidence', relocated/'input.json'
        self.write(self.candidate, base())
        result = self.overlay()
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = json.loads(self.candidate.read_bytes())
        self.assertEqual(len(actual['reported_holdings']), 2)
        self.assertEqual(json.loads(self.audit.read_bytes())['oge_annual']['added_holding_count'], 2)
        previous = self.candidate.read_bytes()
        repeated = self.overlay()
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(self.candidate.read_bytes(), previous)

    def test_legacy_saved_input_never_becomes_qualified_or_drops_prior_facts(self):
        self.extraction_path.unlink()
        legacy = dict(deepcopy(self.extraction), parser_version=PARSER_VERSION)
        legacy['filing_date'] = None
        legacy.pop('cover_date_evidence', None)
        legacy.pop('source_coverage_evidence', None)
        self.save_extraction(legacy)
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        prepared = json.loads(self.output.read_bytes())
        self.assertEqual(prepared['extractions'][0]['parser_version'], PARSER_VERSION)
        self.assertEqual(prepared['controlled_annual_runtime']['documents'][0]['input_status'], 'saved_legacy_parser_input')
        self.assertIs(prepared['controlled_annual_runtime']['documents'][0]['holding_coverage_parser_supported'], False)
        self.write(self.candidate, base())
        result = self.overlay()
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = json.loads(self.candidate.read_bytes())
        self.assertEqual(actual['reported_holdings'], [])
        self.assertEqual(actual['transactions'], base()['transactions'])
        previous, _ = overlay_oge_annual_candidate(base(), self.request)
        self.write(self.candidate, previous)
        result = self.overlay()
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = json.loads(self.candidate.read_bytes())
        self.assertEqual(actual['reported_holdings'], previous['reported_holdings'])
        self.assertEqual(actual['transactions'], previous['transactions'])

    def test_missing_wrong_source_and_wrong_metadata_fail_before_prepared_output(self):
        self.output.write_bytes(b'input sentinel')
        self.pdf_path.unlink()
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_bytes(), b'input sentinel')
        self.pdf_path.write_bytes(b'wrong source')
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_bytes(), b'input sentinel')
        self.pdf_path.write_bytes(CONTENT)
        metadata = self.request['archive_metadata_by_document'][DOC]
        self.write(self.meta_path, dict(metadata, filer_name='Another Person'))
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_bytes(), b'input sentinel')

    def test_ambiguous_versions_and_absent_explicit_document_fail(self):
        self.save_extraction(dict(deepcopy(self.extraction), parser_version=PARSER_VERSION))
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'ambiguous source/parser versions', result.stderr)
        self.assertFalse(self.output.exists())
        result = self.prepare('--document-id', 'b'*32)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_changed_dependencies_or_input_fail_before_candidate_and_audit_writes(self):
        result = self.prepare(); self.assertEqual(result.returncode, 0, result.stderr)
        original = self.output.read_bytes()
        self.candidate.write_bytes(b'candidate sentinel'); self.audit.write_bytes(b'audit sentinel')
        for change in ('pdf', 'review', 'input', 'version', 'no-root'):
            with self.subTest(change=change):
                self.pdf_path.write_bytes(CONTENT)
                self.write(self.extraction_path, self.extraction)
                self.output.write_bytes(original)
                if change == 'pdf': self.pdf_path.write_bytes(b'changed PDF')
                if change == 'review': self.write(self.extraction_path, dict(self.extraction, annual_year=2024))
                if change in ('input', 'version'):
                    value = json.loads(original)
                    if change == 'input': value['extractions'][0]['filer_name'] = 'Changed Name'
                    else: value['controlled_annual_runtime']['schema_version'] = 'unsupported'
                    self.write(self.output, value)
                result = self.overlay(runtime=change != 'no-root')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.candidate.read_bytes(), b'candidate sentinel')
                self.assertEqual(self.audit.read_bytes(), b'audit sentinel')

    def test_document_budget_and_unknown_versions_do_not_create_output(self):
        for index in range(13):
            value = dict(deepcopy(self.extraction), source_document_id=f'{index:032x}')
            self.save_extraction(value)
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'budget', result.stderr)
        self.assertFalse(self.output.exists())
        value = deepcopy(self.extraction); value['parser_version'] = 'oge-278e-tables/v999'
        self.extraction_path.unlink(); self.save_extraction(value)
        result = self.prepare('--document-id', DOC)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_overlapping_outputs_cannot_overwrite_inputs_sources_or_each_other(self):
        result = self.prepare(); self.assertEqual(result.returncode, 0, result.stderr)
        self.write(self.candidate, base()); self.audit.write_bytes(b'audit sentinel')
        unrelated = self.evidence/'unrelated-source.json'; unrelated.write_bytes(b'untouched evidence')
        originals = {path: path.read_bytes() for path in (
            self.output, self.candidate, self.audit, self.extraction_path, self.meta_path, self.pdf_path, unrelated)}
        cases = [(self.candidate, self.candidate), (self.output, self.audit),
                 (self.candidate, self.output), (self.extraction_path, self.audit),
                 (self.candidate, self.extraction_path), (self.candidate, self.meta_path),
                 (self.pdf_path, self.audit), (self.candidate, unrelated)]
        for candidate, audit in cases:
            with self.subTest(candidate=candidate.name, audit=audit.name):
                result = self.overlay(candidate=candidate, audit=audit)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b'Annual ', result.stderr)
                for path, raw in originals.items():
                    self.assertEqual(path.read_bytes(), raw)

    def test_output_aliases_are_resolved_before_any_write(self):
        result = self.prepare(); self.assertEqual(result.returncode, 0, result.stderr)
        self.write(self.candidate, base()); self.audit.write_bytes(b'audit sentinel')
        alias_root = self.root/'root-alias'
        try:
            alias_root.symlink_to(self.root, target_is_directory=True)
        except OSError as error:
            if os.name != 'nt':
                self.skipTest(f'Local OS does not allow creating symbolic links: {error}')
            # Windows junctions exercise the same resolved-directory alias
            # without requiring the elevated symbolic-link privilege.
            created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias_root), str(self.root)],
                                     capture_output=True)
            self.assertEqual(created.returncode, 0, created.stderr)
            self.addCleanup(alias_root.rmdir)
        originals = {path: path.read_bytes() for path in (
            self.output, self.candidate, self.audit, self.extraction_path, self.meta_path, self.pdf_path)}
        new_evidence = alias_root/'evidence/new-source.json'
        for candidate, audit in ((self.candidate, alias_root/self.output.name),
                                  (self.candidate, new_evidence), (self.candidate, alias_root/self.candidate.name)):
            with self.subTest(audit=audit.name):
                result = self.overlay(candidate=candidate, audit=audit)
                self.assertNotEqual(result.returncode, 0)
                for path, raw in originals.items(): self.assertEqual(path.read_bytes(), raw)
                self.assertFalse(new_evidence.exists())

    def test_normal_review_candidate_and_annual_audit_locations_are_allowed(self):
        result = self.prepare(); self.assertEqual(result.returncode, 0, result.stderr)
        candidate = self.review/'candidates/sources/oge-current.json'
        audit = self.review/'whitehouse/annual/candidate-current.json'
        self.write(candidate, base())
        result = self.overlay(candidate=candidate, audit=audit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(candidate.read_bytes())['reported_holdings']), 2)
        self.assertEqual(json.loads(audit.read_bytes())['oge_annual']['added_holding_count'], 2)


if __name__ == '__main__':
    unittest.main()
