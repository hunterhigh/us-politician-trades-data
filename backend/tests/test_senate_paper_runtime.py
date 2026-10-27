"""Existing saved source contracts transported without starting a recognizer."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.test_senate_account_correction import fixture
from unison_snapshot.senate import SenateEfdError
from unison_snapshot.senate_paper_runtime import prepare_senate_paper_inputs
from unison_snapshot.senate_reports import PARSER_VERSION, inspect_paper_entrypoint


class SenatePaperRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = fixture()  # Existing generated pixel/word source, not recognition gold.

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='spr-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.review, self.evidence = self.root/'r', self.root/'e'
        _, self.fact, review, evidence = deepcopy(self.source)
        for root, files in ((self.review, review), (self.evidence, evidence)):
            for relative, raw in files.items():
                path = root/relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        self.replay = next(self.review.glob('senate_efd/paper_grid_inputs/*.json'))
        self.saved = json.loads(self.replay.read_bytes())
        self.document = self.saved['report']['document_id']
        self.metadata = next(self.evidence.glob('senate_efd/reports/*/*.metadata.json'))
        metadata = json.loads(self.metadata.read_bytes())
        self.digest = metadata['sha256']
        self.viewer = self.metadata.with_name(self.digest+'.html')
        inspection = inspect_paper_entrypoint(metadata, self.viewer.read_bytes())
        self.inspection = self.review/f'senate_efd/paper_inspections/{self.document}/{self.digest}/{PARSER_VERSION}.json'
        self.write(self.inspection, inspection)
        self.extraction = next(self.review.glob('senate_efd/extractions/*/*/*.json'))

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding='utf-8')

    def prepare(self, **kwargs):
        return prepare_senate_paper_inputs(self.review, self.evidence,
            document_ids=kwargs.pop('document_ids', [self.document]), **kwargs)

    def fingerprints(self):
        return {(root.name, path.relative_to(root).as_posix()): hashlib.sha256(path.read_bytes()).hexdigest()
                for root in (self.review, self.evidence) for path in root.rglob('*') if path.is_file()}

    def cli(self, *arguments):
        backend = Path(__file__).resolve().parents[1]
        return subprocess.run([sys.executable, *map(str, arguments)], cwd=backend,
            env={**os.environ, 'PYTHONPATH':str(backend/'src'), 'PYTHONIOENCODING':'utf-8'}, capture_output=True)

    def test_existing_pointer_prepares_exact_portable_maps_without_ocr_or_writes(self):
        original = self.fingerprints()
        with patch('unison_snapshot.senate_paper.tesseract_words', side_effect=AssertionError('No OCR')):
            actual = self.prepare()
        self.assertEqual(actual['grid_observations'], {self.document:self.saved['observation']})
        self.assertEqual(actual['cached_word_pages'], {self.document:self.saved['cached_word_pages']})
        self.assertFalse(actual['receipt']['recognizer_started'])
        self.assertEqual(actual['receipt']['qualification_status'], 'deferred_to_existing_consumer')
        self.assertNotIn('eligible_transaction_count', actual['receipt'])
        self.assertEqual(actual['receipt']['total_bytes'], sum(item['bytes'] for item in actual['receipt']['files']))
        self.assertEqual(self.fingerprints(), original)
        moved = self.root/'moved'; moved.mkdir()
        for path in (self.review,self.evidence): shutil.move(str(path), str(moved/path.name))
        self.review,self.evidence = moved/'r',moved/'e'
        self.assertEqual(self.prepare(), actual)

    def test_prepare_cli_reaches_existing_paired_cli_and_preserves_complete_extraction(self):
        backend = Path(__file__).resolve().parents[1]
        grids,words,receipt,batch = [self.root/name for name in ('grids.json','words.json','receipt.json','batch.json')]
        original = self.fingerprints()
        result = self.cli(backend/'scripts/prepare_senate_paper_runtime.py',
            '--review-root',self.review,'--evidence-root',self.evidence,'--document-id',self.document,
            '--grid-output',grids,'--word-output',words,'--receipt-output',receipt)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))
        result = self.cli('-m','unison_snapshot','extract-senate-paper-pages',
            '--review-root',self.review,'--evidence-root',self.evidence,
            '--saved-grid-observations',grids,'--saved-word-pages',words,
            '--tesseract','__OCR_DISABLED__','--output',batch)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))
        value = json.loads(batch.read_bytes())
        self.assertEqual((value['report_count'],value['extraction_count'],value['failure_count']), (1,1,0))
        self.assertEqual(value['extractions'],[json.loads(self.extraction.read_bytes())])
        self.assertEqual(self.fingerprints(),original)

    def test_explicit_selection_is_bounded_and_does_not_scan_other_catalog_reports(self):
        for selection in ([],[self.document]*2,['unknown'],[f'{i:08x}-1111-4111-8111-111111111111' for i in range(13)]):
            with self.subTest(selection=selection),self.assertRaises(SenateEfdError):
                self.prepare(document_ids=selection)
        discovery = next(self.review.glob('senate_efd/discoveries/*.json'))
        value = json.loads(discovery.read_bytes())
        value['reports'].append({**value['reports'][0],'document_id':'22222222-2222-4222-8222-222222222222'})
        self.write(discovery,value)
        self.assertEqual(list(self.prepare()['grid_observations']),[self.document])

    def test_changed_viewer_metadata_manifest_and_image_cannot_be_self_signed(self):
        manifest = self.evidence/f'senate_efd/paper_pages/{self.document}/manifest.json'
        image = next(self.evidence.glob('senate_efd/paper_pages/*/2/*.gif'))
        for path in (self.viewer,self.metadata,manifest,image):
            original = path.read_bytes()
            if path.suffix == '.json':
                value = json.loads(original)
                value['filer_name' if path==self.metadata else 'manifest_sha256'] = 'wrong'
                self.write(path,value)
            else:
                path.write_bytes(original+b'changed')
            with self.subTest(path=path),self.assertRaises((SenateEfdError,ValueError)):
                self.prepare()
            path.write_bytes(original)

    def test_resigned_pointer_changed_words_and_forged_extraction_are_replayed(self):
        saved = deepcopy(self.saved)
        saved['cached_word_pages'][0]['words'][0]['text'] = 'Different source text'
        from unison_snapshot.senate_paper import _grid_json_bytes
        raw = _grid_json_bytes(saved); digest = hashlib.sha256(raw).hexdigest()
        target = self.replay.with_name(digest+'.json'); target.write_bytes(raw)
        original = self.extraction.read_bytes(); extraction = json.loads(original)
        extraction['grid_replay_input'] = {'path':target.relative_to(self.review).as_posix(),'sha256':digest}
        self.write(self.extraction,extraction)
        with self.assertRaises((SenateEfdError,ValueError)):
            self.prepare()
        extraction = json.loads(original); extraction['self_signed_eligible'] = True
        self.write(self.extraction,extraction)
        with self.assertRaisesRegex(SenateEfdError,'differ.*source replay'):
            self.prepare()

    def test_wrong_source_output_alias_and_budget_fail_before_output_replacement(self):
        backend = Path(__file__).resolve().parents[1]
        grids,words = self.root/'grids.json',self.root/'words.json'
        grids.write_bytes(b'grid sentinel');words.write_bytes(b'word sentinel')
        self.viewer.write_bytes(b'wrong source')
        result = self.cli(backend/'scripts/prepare_senate_paper_runtime.py',
            '--review-root',self.review,'--evidence-root',self.evidence,'--document-id',self.document,
            '--grid-output',grids,'--word-output',words)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(grids.read_bytes(),b'grid sentinel');self.assertEqual(words.read_bytes(),b'word sentinel')
        result = self.cli(backend/'scripts/prepare_senate_paper_runtime.py',
            '--review-root',self.review,'--evidence-root',self.evidence,'--document-id',self.document,
            '--grid-output',grids,'--word-output',grids)
        self.assertNotEqual(result.returncode,0)
        with patch('unison_snapshot.senate_paper_runtime.MAX_TOTAL_BYTES',1):
            with self.assertRaisesRegex(SenateEfdError,'total byte budget'):
                self.prepare()


if __name__=='__main__':unittest.main()
