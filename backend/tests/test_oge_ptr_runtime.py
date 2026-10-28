"""Offline native PTR preparation authenticates originals before immutable writes."""
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

import pymupdf

from test_oge_owner_correction import fixture, write_files, encoded, DOCUMENT
from test_oge_saved_observation_writer import directory_alias
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_ptr_runtime import prepare_oge_ptr_generic

ROOT = Path(__file__).resolve().parents[2]


class OgePtrRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = fixture()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='oge-ptr-prepare-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.evidence, self.review = self.root/'evidence', self.root/'review'
        _, _, review, evidence, _ = deepcopy(self.data)
        write_files(self.evidence, evidence)
        write_files(self.review, review)
        self.metadata = next(self.evidence.glob(f'oge/reports/{DOCUMENT}/*.json'))
        self.meta = json.loads(self.metadata.read_text())
        self.pdf = self.evidence/self.meta['archive_path']
        self.sha = self.meta['sha256']
        catalog = next(self.evidence.glob('oge/catalog/normalized/*.json'))
        self.catalog = self.root/'catalog.json'
        self.catalog.write_bytes(catalog.read_bytes())
        self.targets = [self.review/f'oge/extractions/{DOCUMENT}/{self.sha}/oge-278t-pdf-v{v}.json'
                        for v in (2, 7)]

    def prepare(self, **kwargs):
        return prepare_oge_ptr_generic(document_id=DOCUMENT, evidence_root=self.evidence,
            review_root=self.review, catalog=self.catalog, **kwargs)

    def files(self):
        return {str(path.relative_to(self.root)):path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()}

    def rejected_unchanged(self, **kwargs):
        before = self.files()
        with self.assertRaises((OgeCatalogError, OSError, ValueError)):
            self.prepare(**kwargs)
        self.assertEqual(self.files(), before)

    def test_source_replay_creates_both_existing_normal_versions_and_repeat_is_immutable(self):
        expected = [json.loads(path.read_text()) for path in self.targets]
        for path in self.targets:
            path.unlink()
        source = self.pdf.read_bytes()
        receipt = self.prepare()
        self.assertEqual(receipt['parser_versions'], ['oge-278t-pdf/v2', 'oge-278t-pdf/v7'])
        self.assertEqual(len(receipt['created_paths']), 2)
        self.assertEqual([json.loads(path.read_text()) for path in self.targets], expected)
        self.assertEqual(receipt['source_sha256'], hashlib.sha256(source).hexdigest())
        # Semantically identical preexisting JSON retains its own byte encoding.
        self.targets[0].write_bytes(encoded(expected[0]))
        before = self.files()
        self.assertEqual(self.prepare()['created_paths'], [])
        self.assertEqual(self.files(), before)

    def test_both_targets_are_checked_before_creating_either_and_full_object_matters(self):
        self.targets[0].unlink()
        value = json.loads(self.targets[1].read_text())
        value['transactions'][0]['asset_name'] = 'Changed cache, same binding'
        self.targets[1].write_bytes(encoded(value))
        self.rejected_unchanged()
        self.assertFalse(self.targets[0].exists())

    def test_metadata_catalog_and_source_bytes_cannot_be_resigned_into_other_bindings(self):
        original = self.metadata.read_bytes()
        changes = {'document_id':'a'*32, 'document_url':'https://example.invalid/wrong.pdf',
            'filer_name':'Different Person', 'agency':'Other Agency', 'position_title':'Other Title',
            'catalog_added_date':'2026-07-01', 'amended_label':'Amended',
            'pending_final_oge_disposition':True, 'byte_length':self.meta['byte_length']+1,
            'sha256':'b'*64, 'archive_path':'../outside.pdf'}
        for key, value in changes.items():
            with self.subTest(field=key):
                self.metadata.write_bytes(encoded({**self.meta,key:value}))
                self.rejected_unchanged()
                self.metadata.write_bytes(original)
        content = self.pdf.read_bytes()
        self.pdf.write_bytes(content+b'\nchanged bytes')
        self.rejected_unchanged()

    def test_missing_multiple_or_orphan_source_versions_are_not_selected_by_order(self):
        raw = self.pdf.read_bytes()
        self.pdf.unlink()
        self.rejected_unchanged()
        self.pdf.write_bytes(raw)
        other = self.metadata.with_name('f'*64+'.json')
        other.write_bytes(self.metadata.read_bytes())
        self.rejected_unchanged()
        other.unlink()
        self.metadata.with_name('f'*64+'.pdf').write_bytes(raw)
        self.rejected_unchanged()

    def test_current_and_history_context_must_agree_and_history_only_target_is_supported(self):
        history = self.review/'oge/discoveries'
        history.mkdir(parents=True)
        historical = history/'existing.json'
        historical.write_bytes(self.catalog.read_bytes())
        current = json.loads(self.catalog.read_text())
        current['transactions'] = []
        self.catalog.write_bytes(encoded(current))
        self.assertEqual(self.prepare(catalog_history_dir=history)['document_id'], DOCUMENT)
        incompatible = json.loads(historical.read_text())
        incompatible['transactions'][0]['agency'] = 'Other identity'
        (history/'conflicting.json').write_bytes(encoded(incompatible))
        self.rejected_unchanged(catalog_history_dir=history)
        (history/'conflicting.json').unlink()
        historical.unlink()
        self.rejected_unchanged(catalog_history_dir=history)

    def test_controlled_output_escape_and_dependency_hardlink_are_rejected(self):
        for target in self.targets:
            target.unlink()
        folder = self.targets[0].parent
        folder.rmdir()
        outside = self.root/'outside'; outside.mkdir()
        directory_alias(folder, outside)
        self.rejected_unchanged()

    def test_target_alias_to_catalog_or_other_target_is_rejected_without_replacement(self):
        for target in self.targets:
            target.unlink()
        os.link(self.catalog, self.targets[0])
        self.rejected_unchanged()
        self.targets[0].unlink()
        self.targets[0].write_bytes(encoded(json.loads(self.data[2][
            next(key for key in self.data[2] if key.endswith('pdf-v2.json'))])))
        os.link(self.targets[0], self.targets[1])
        self.rejected_unchanged()

    def test_negative_raw_interpretation_is_prepared_without_granting_eligibility(self):
        # Complete raw source replay may honestly report unknown fields. The
        # preparation step must not add a new financial/date eligibility gate.
        document = pymupdf.open(stream=self.pdf.read_bytes(), filetype='pdf')
        document.delete_page(0)
        raw = document.tobytes(); document.close()
        self.pdf.unlink(); self.metadata.unlink()
        sha = hashlib.sha256(raw).hexdigest()
        self.pdf = self.pdf.with_name(sha+'.pdf'); self.pdf.write_bytes(raw)
        self.metadata = self.metadata.with_name(sha+'.json')
        self.metadata.write_bytes(encoded({**self.meta,'sha256':sha,'byte_length':len(raw),
            'archive_path':self.pdf.relative_to(self.evidence).as_posix()}))
        receipt = self.prepare()
        self.assertEqual(len(receipt['created_paths']), 2)
        for item in receipt['extractions']:
            raw = json.loads((self.review/item['path']).read_text())
            self.assertIsNone(raw['filed_at'])
            self.assertFalse(raw['evidence_complete'])
            self.assertTrue(raw['document_reasons'])
        with patch('unison_snapshot.oge_ptr_runtime.selected_parser_version', return_value='oge-278t-pdf/v6'):
            self.rejected_unchanged()

    def test_identity_roots_and_explicit_budget_boundaries_fail_before_writes(self):
        for value in ('',DOCUMENT.upper(),'../'+DOCUMENT,DOCUMENT+' ', 'f'*31):
            with self.subTest(value=value), self.assertRaises(OgeCatalogError):
                prepare_oge_ptr_generic(document_id=value,evidence_root=self.evidence,
                    review_root=self.review,catalog=self.catalog)
        with self.assertRaises(OgeCatalogError):
            prepare_oge_ptr_generic(document_id=DOCUMENT,evidence_root=self.evidence,
                review_root=self.evidence,catalog=self.catalog)
        with patch('unison_snapshot.oge_ptr_runtime.MAX_JSON_BYTES',1):
            self.rejected_unchanged()
        with patch('unison_snapshot.oge_ptr_runtime.MAX_CATALOG_BYTES',1):
            self.rejected_unchanged()
        with patch('unison_snapshot.oge_ptr_runtime.MAX_PDF_BYTES',1):
            self.rejected_unchanged()

    def test_actual_source_selected_visibility_family_is_not_prepared_as_ordinary(self):
        document = pymupdf.open(stream=self.pdf.read_bytes(), filetype='pdf')
        document[1].insert_text((30, 80), 'Invisible note', render_mode=3)
        raw = document.tobytes(); document.close()
        self.pdf.unlink(); self.metadata.unlink()
        sha = hashlib.sha256(raw).hexdigest()
        self.pdf = self.pdf.with_name(sha+'.pdf'); self.pdf.write_bytes(raw)
        self.metadata = self.metadata.with_name(sha+'.json')
        self.metadata.write_bytes(encoded({**self.meta,'sha256':sha,'byte_length':len(raw),
            'archive_path':self.pdf.relative_to(self.evidence).as_posix()}))
        self.rejected_unchanged()

    def test_actual_script_receipt_success_and_failure_keeps_all_inputs(self):
        args = [sys.executable,str(ROOT/'backend/src/unison_snapshot/commands/prepare_oge_ptr_generic.py'),
            '--document-id',DOCUMENT,'--evidence-root',str(self.evidence),
            '--review-root',str(self.review),'--catalog',str(self.catalog)]
        env = dict(os.environ,PYTHONPATH=str(ROOT/'backend/src'))
        result = subprocess.run(args,env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['created_paths'],[])
        value = json.loads(self.targets[0].read_text()); value['unexpected'] = None
        self.targets[0].write_bytes(encoded(value))
        before = self.files()
        result = subprocess.run(args,env=env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.files(),before)

    def additional_archived_version(self):
        # A second real, parseable immutable PDF version, not a renamed fake
        # SHA. Only a trailing comment changes; its source bytes have a new hash.
        raw = self.pdf.read_bytes() + b'\n% second archived input for explicit selection\n'
        sha = hashlib.sha256(raw).hexdigest()
        pdf = self.pdf.with_name(sha + '.pdf')
        pdf.write_bytes(raw)
        metadata = pdf.with_suffix('.json')
        metadata.write_bytes(encoded({**self.meta, 'sha256': sha, 'byte_length': len(raw),
            'archive_path': pdf.relative_to(self.evidence).as_posix(),
            'retrieved_at': '2026-06-21T00:00:00Z'}))
        return sha, pdf, metadata

    def test_explicit_archived_sha_prepares_only_that_pair_and_preserves_other_versions(self):
        sha, _, _ = self.additional_archived_version()
        original = {path:path.read_bytes() for path in [*self.evidence.rglob('*'), *self.targets]
                    if path.is_file()}
        self.rejected_unchanged()  # No explicit choice retains the old contract.
        receipt = self.prepare(source_sha256=sha)
        self.assertEqual(receipt['source_sha256'], sha)
        self.assertEqual(receipt['source_selection'], 'explicit_archived_input')
        self.assertEqual(len(receipt['created_paths']), 2)
        self.assertTrue(all('/' + sha + '/' in path for path in receipt['created_paths']))
        for item in receipt['extractions']:
            value = json.loads((self.review / item['path']).read_bytes())
            self.assertEqual(value['source_sha256'], sha)
            self.assertEqual(value['parser_version'], item['parser_version'])
        self.assertEqual({path:path.read_bytes() for path in original}, original)
        before = self.files()
        self.assertEqual(self.prepare(source_sha256=sha)['created_paths'], [])
        self.assertEqual(self.prepare(source_sha256=self.sha)['created_paths'], [])
        self.assertEqual(self.files(), before)

    def test_explicit_selection_rejects_invalid_missing_or_unpaired_source_before_writes(self):
        sha, pdf, _ = self.additional_archived_version()
        for invalid in ('', 'F' * 64, '0' * 64, sha + ' ', '../' + sha, 7):
            with self.subTest(source_sha256=invalid):
                self.rejected_unchanged(source_sha256=invalid)
        content = pdf.read_bytes()
        pdf.unlink()
        self.rejected_unchanged(source_sha256=self.sha)
        pdf.write_bytes(content)
        extra = pdf.with_name('notes.txt'); extra.write_bytes(b'not a source version')
        self.rejected_unchanged(source_sha256=self.sha)
        extra.unlink()
        with patch('unison_snapshot.oge_ptr_runtime.MAX_ARCHIVE_VERSIONS', 1):
            self.rejected_unchanged(source_sha256=self.sha)

    def test_selected_original_and_all_selected_caches_remain_strictly_immutable(self):
        sha, pdf, metadata = self.additional_archived_version()
        original = metadata.read_bytes()
        changed = json.loads(original); changed['agency'] = 'Different source identity'
        metadata.write_bytes(encoded(changed))
        self.rejected_unchanged(source_sha256=sha)
        metadata.write_bytes(original)
        raw = pdf.read_bytes(); pdf.write_bytes(raw + b'\n% changed source')
        self.rejected_unchanged(source_sha256=sha)
        pdf.write_bytes(raw)
        self.prepare(source_sha256=sha)
        selected = self.review / f'oge/extractions/{DOCUMENT}/{sha}'
        default = selected / 'oge-278t-pdf-v2.json'
        generic = selected / 'oge-278t-pdf-v7.json'
        default.unlink()
        value = json.loads(generic.read_bytes()); value['transactions'][0]['asset_name'] = 'Altered cache'
        generic.write_bytes(encoded(value))
        self.rejected_unchanged(source_sha256=sha)
        self.assertFalse(default.exists())

    def test_prepare_script_accepts_explicit_input_without_changing_default_multisha_behavior(self):
        sha, _, _ = self.additional_archived_version()
        args = [sys.executable, str(ROOT/'backend/src/unison_snapshot/commands/prepare_oge_ptr_generic.py'),
            '--document-id', DOCUMENT, '--evidence-root', str(self.evidence),
            '--review-root', str(self.review), '--catalog', str(self.catalog)]
        env = dict(os.environ, PYTHONPATH=str(ROOT/'backend/src'))
        before = self.files()
        missing = subprocess.run(args, env=env, capture_output=True, text=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(self.files(), before)
        chosen = subprocess.run([*args, '--source-sha256', sha], env=env, capture_output=True, text=True)
        self.assertEqual(chosen.returncode, 0, chosen.stderr)
        self.assertEqual(json.loads(chosen.stdout)['source_sha256'], sha)


if __name__ == '__main__':
    unittest.main()
