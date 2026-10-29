"""Run the real default selector with retained failures and saved paper facts."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import textwrap
import unittest
from unittest.mock import patch

import test_house_ptr_paper_qualification as fixtures
from unison_snapshot.house_paper_pipeline import bridge_paper_batch
from unison_snapshot.house_ptr_paper_qualification import materialize_paper_observation


class HousePaperRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence, self.review = self.root/'evidence', self.root/'review'
        f = fixtures.HousePaperQualificationTests(); f.setUp()
        raw, header, reread, slots = f._source_bound_fixture()
        f.metadata['archive_path']='house_clerk/test-source.pdf'
        self.raw, self.header = raw, header
        pdf=self.evidence/f.metadata['archive_path'];pdf.parent.mkdir(parents=True,exist_ok=True);pdf.write_bytes(raw)
        with patch('unison_snapshot.house_ptr_fixed_source_core.check_source_cells', return_value=slots), patch(
                'unison_snapshot.house_ptr_paper_qualification._asset_reading_is_bound', return_value=True):
            self.q = materialize_paper_observation(raw, f.observation, f.metadata, f.identity,
                amount_header=header, field_reread=reread)
        self.metadata, self.observation = f.metadata, f.observation
        self.relative = Path('2026/9116141') / (f.sha+'.json')
        self.write(self.evidence/'house_clerk/documents'/self.relative, self.metadata)
        self.write(self.review/'house_clerk/extractions'/self.relative, self.q['materialized_extraction'])
        self.write(self.review/'house_clerk/qualifications'/self.relative, self.q)
        self.write(self.review/'house_clerk/paper_observations'/self.relative, self.observation)
        self.write(self.review/'house_clerk/identities'/self.relative, f.identity)
        self.failure = self.review/'house_clerk/failures'/self.relative
        self.write(self.failure, {'error':'preserved old parser failure'})
        repo = Path(__file__).resolve().parents[2]
        workflow = (repo/'.github/workflows/house.yml').read_text(encoding='utf-8')
        selector = workflow.split('- name: Select unprocessed evidence',1)[1].split('- name:',1)[0]
        self.assertIn('python -m unison_snapshot house-review select', selector)
        self.script = 'import sys; from unison_snapshot.commands.house_review import main; sys.argv=["house-review","select"]; main()'
        self.env = {**os.environ, 'PYTHONPATH':str(repo/'backend/src'),
            'EVIDENCE_ROOT':str(self.evidence), 'REVIEW_ROOT':str(self.review),
            'PARSER_COMMIT':'frozen', 'LEGACY_PARSER_VERSION':'legacy',
            'PARSER_RETRY_VERSION':'retry', 'BATCH_SIZE':'25'}

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value),encoding='utf-8')

    def select(self):
        import sys
        return subprocess.run([sys.executable,'-c',self.script],cwd=self.root,
            env=self.env,capture_output=True,text=True)

    def test_default_repeat_preserves_saved_qualification_and_failure(self):
        before = {p:p.read_bytes() for p in self.review.rglob('*.json')}
        result = self.select()
        self.assertEqual(result.returncode,0,result.stderr)
        batch = self.root/'.local/parse-batch.txt'
        self.assertEqual(batch.read_text(), '')
        self.assertEqual(bridge_paper_batch(evidence_root=self.evidence,review_root=self.review,
            parse_batch=batch,tesseract='must-not-run'), {'not_legacy_paper':1})
        self.assertEqual({p:p.read_bytes() for p in before},before)
        self.assertTrue(self.failure.is_file())

    def test_plain_legacy_failure_still_selected_for_retry(self):
        self.write(self.review/'house_clerk/extractions'/self.relative,
            {'extraction_method':'tesseract_legacy_checkbox','parser_version':'legacy'})
        result=self.select()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('9116141',(self.root/'.local/parse-batch.txt').read_text())

    def test_corrupt_legacy_extraction_keeps_existing_retry_behavior(self):
        (self.review/'house_clerk/extractions'/self.relative).write_text('{broken',encoding='utf-8')
        result=self.select()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('9116141',(self.root/'.local/parse-batch.txt').read_text())

    def test_label_or_source_receipt_changes_stop_before_destructive_retry(self):
        for kind in ['label_only','source','document','parser','qualification','observation']:
            with self.subTest(kind=kind):
                x=deepcopy(self.q['materialized_extraction']);q=deepcopy(self.q);o=deepcopy(self.observation)
                if kind=='label_only':x={'extraction_method':'source_bound_paper_cells'}
                if kind=='source':x['source_sha256']='f'*64
                if kind=='document':x['source']['document_id']='wrong'
                if kind=='parser':x['parser_version']='unknown-version'
                if kind=='qualification':q['materialized_extraction']['transactions']=[]
                if kind=='observation':o['changed']=True
                self.write(self.review/'house_clerk/extractions'/self.relative,x)
                self.write(self.review/'house_clerk/qualifications'/self.relative,q)
                self.write(self.review/'house_clerk/paper_observations'/self.relative,o)
                before={p:p.read_bytes() for p in self.review.rglob('*.json')}
                result=self.select()
                self.assertNotEqual(result.returncode,0)
                self.assertEqual({p:p.read_bytes() for p in before},before)

    def materialize_legacy(self, *, bad=False):
        from unison_snapshot.house_paper_pipeline import bridge_paper_report
        from unison_snapshot.house_ptr_paper_temporal import PARSER_VERSION
        result=deepcopy(self.q)
        if bad: result['materialized_extraction']['source_sha256']='f'*64
        header_path=self.review/'house_clerk/paper_amount_headers'/self.relative
        if not header_path.is_file(): self.write(header_path,self.header)
        with patch('unison_snapshot.house_paper_pipeline.materialize_paper_observation',return_value=result),patch(
                'unison_snapshot.house_paper_pipeline.qualify_paper_observation',return_value={
                    'parser_version':PARSER_VERSION,'transactions':[],'quarantined':[]}):
            return bridge_paper_report(evidence_root=self.evidence,review_root=self.review,
                metadata_path=self.evidence/'house_clerk/documents'/self.relative,
                tesseract='must-not-run',observe_new=False,materialize_saved=True)

    def test_existing_legacy_is_referenced_and_matching_new_extraction_reaches_status(self):
        old={'source_sha256':self.metadata['sha256'],'source':self.metadata,
             'extraction_method':'tesseract_legacy_checkbox','transactions':[{'old_reading':'retained'}]}
        self.write(self.review/'house_clerk/extractions'/self.relative,old)
        self.write(self.review/'house_clerk/qualifications'/self.relative,{
            'document_id':self.metadata['document_id'],'source_sha256':self.metadata['sha256'],'transactions':[]})
        self.assertEqual(self.materialize_legacy(),'materialized_existing_observation')
        path=self.review/'house_clerk/qualifications'/self.relative
        q=json.loads(path.read_text())
        self.assertEqual(json.loads((self.review/'house_clerk/extractions'/self.relative).read_text()),q['materialized_extraction'])
        reference=q['legacy_extraction_reference']
        saved=json.loads((self.review/reference['path']).read_text())
        self.assertEqual(saved['extraction'],old)
        import test_disclosure_status_house as status_fixture
        from unison_snapshot.disclosure_status_house import build_house_status
        s=status_fixture.HouseDisclosureStatusTests();s.setUp();self.addCleanup(s.temporary.cleanup)
        s.archive(self.metadata['document_id'],sha=self.metadata['sha256'])
        report=build_house_status(s.evidence,self.review,q['transactions'],[])[0]['reports'][0]
        self.assertEqual(report['qualified_transaction_count'],1)
        self.assertIsNone(report['quarantined_row_count'])
        before={p:p.read_bytes() for p in self.review.rglob('*.json')}
        self.assertEqual(self.materialize_legacy(),'legacy_qualified_preserved')
        self.assertEqual({p:p.read_bytes() for p in before},before)
        self.assertEqual(len(list((self.review/'house_clerk/paper_legacy_extraction_references').rglob('*.json'))),1)

    def test_bad_materialized_result_cannot_replace_legacy(self):
        old={'extraction_method':'tesseract_legacy_checkbox'}
        self.write(self.review/'house_clerk/extractions'/self.relative,old)
        prior={'document_id':self.metadata['document_id'],'source_sha256':self.metadata['sha256'],'transactions':[]}
        self.write(self.review/'house_clerk/qualifications'/self.relative,prior)
        with self.assertRaises(ValueError): self.materialize_legacy(bad=True)
        self.assertEqual(json.loads((self.review/'house_clerk/extractions'/self.relative).read_text()),old)
        self.assertEqual(json.loads((self.review/'house_clerk/qualifications'/self.relative).read_text()),prior)
        self.assertFalse((self.review/'house_clerk/paper_legacy_extraction_references').exists())

    def test_zero_formal_replay_keeps_one_verified_legacy_reference(self):
        tx=self.q['transactions'].pop()
        self.q['quarantined'].append({'extraction_id':tx['id'],'reasons':['still_unresolved']})
        self.q['qualification'].update(qualified_count=0,quarantined_count=2,production_eligible=False)
        self.write(self.review/'house_clerk/extractions'/self.relative,{'extraction_method':'tesseract_legacy_checkbox'})
        self.write(self.review/'house_clerk/qualifications'/self.relative,{
            'document_id':self.metadata['document_id'],'source_sha256':self.metadata['sha256'],'transactions':[]})
        self.materialize_legacy()
        before={p:p.read_bytes() for p in self.review.rglob('*.json')}
        self.assertEqual(self.materialize_legacy(),'materialized_existing_observation')
        self.assertEqual({p:p.read_bytes() for p in before},before)
        refs=list((self.review/'house_clerk/paper_legacy_extraction_references').rglob('*.json'))
        self.assertEqual(len(refs),1)
        saved=json.loads(refs[0].read_text());saved['extraction']['tampered']=True
        self.write(refs[0],saved)
        with self.assertRaisesRegex(ValueError,'reference binding'):self.materialize_legacy()


if __name__=='__main__': unittest.main()
