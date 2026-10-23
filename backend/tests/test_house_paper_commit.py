"""Recover saved artifact sets with real disk writes and process interruption."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_house_paper_retry as retry_fixtures
from unison_snapshot.house_paper_commit import (paper_review_access,
    recover_pending_paper_materializations, recover_paper_materialization)
from unison_snapshot.house_paper_pipeline import bridge_paper_report
import unison_snapshot.house_paper_commit as commits


class HousePaperCommitTests(unittest.TestCase):
    def fixture(self):
        f=retry_fixtures.HousePaperRetryTests();f.setUp();self.addCleanup(f.temp.cleanup)
        f.write(f.review/'house_clerk/extractions'/f.relative,{
            'source_sha256':f.metadata['sha256'],'source':f.metadata,
            'extraction_method':'tesseract_legacy_checkbox','transactions':[]})
        f.write(f.review/'house_clerk/qualifications'/f.relative,{
            'document_id':f.metadata['document_id'],'source_sha256':f.metadata['sha256'],'transactions':[]})
        f.write(f.review/'house_clerk/paper_amount_headers'/f.relative,f.header)
        return f

    def pending(self,f):return f.review/'house_clerk/paper_pending_materializations'/f.relative

    def artifacts(self,f):
        return {str(p.relative_to(f.review)):p.read_bytes() for p in f.review.rglob('*.json')}

    def interrupt(self,f,stage='extractions',*,after=False):
        replace=commits._replace_output
        def fault(path,content):
            selected=path.relative_to(f.review).parts[1]==stage
            if selected and not after:raise OSError('injected interruption')
            replace(path,content)
            if selected and after:raise OSError('injected interruption')
        with patch.object(commits,'_replace_output',side_effect=fault),self.assertRaisesRegex(OSError,'injected'):
            f.materialize_legacy()
        self.assertTrue(self.pending(f).is_file())

    def resume(self,f):
        # The archived bytes are deliberately not a renderable PDF. Success
        # therefore also proves that replay does not reparse source or OCR.
        with patch('unison_snapshot.house_paper_pipeline.materialize_paper_observation',
                   side_effect=AssertionError('prepared facts must not be reinterpreted')),patch(
                   'unison_snapshot.house_paper_pipeline.observe_house_ptr_pdf',
                   side_effect=AssertionError('recovery must not OCR')):
            return bridge_paper_report(evidence_root=f.evidence,review_root=f.review,
                metadata_path=f.evidence/'house_clerk/documents'/f.relative,
                tesseract='must-not-run',observe_new=False,materialize_saved=True)

    def assert_consistent(self,f):
        q=json.loads((f.review/'house_clerk/qualifications'/f.relative).read_bytes())
        self.assertEqual(json.loads((f.review/'house_clerk/extractions'/f.relative).read_bytes()),q['materialized_extraction'])
        self.assertEqual(q['transactions'],f.q['transactions'])
        reference=q['legacy_extraction_reference']
        self.assertEqual(json.loads((f.review/reference['path']).read_bytes())['extraction']['extraction_method'],
                         'tesseract_legacy_checkbox')
        self.assertFalse(self.pending(f).exists())
        self.assertTrue(f.failure.is_file())
        return q

    def test_every_replacement_boundary_can_resume_and_consumers_reject_pending_state(self):
        from unison_snapshot.house_candidate import build_house_candidate
        from unison_snapshot.disclosure_status_house import build_house_status
        for stage in ('paper_observations','paper_amount_headers','paper_legacy_extraction_references',
                      'extractions','qualifications'):
            for after in (False,True):
                with self.subTest(stage=stage,after=after):
                    f=self.fixture();self.interrupt(f,stage,after=after)
                    with self.assertRaisesRegex(ValueError,'incomplete'):build_house_candidate(f.review,{}, {})
                    with self.assertRaisesRegex(ValueError,'incomplete'):build_house_status(f.evidence,f.review,[],[])
                    self.assertEqual(self.resume(f),'recovered_saved_materialization')
                    self.assert_consistent(f)
                    before=self.artifacts(f)
                    self.assertEqual(self.resume(f),'legacy_qualified_preserved')
                    self.assertEqual(self.artifacts(f),before)

    def test_normal_selector_recovers_before_legacy_retry_without_erasing_old_failure(self):
        f=self.fixture();self.interrupt(f,'qualifications')
        result=f.select()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((f.root/'.local/parse-batch.txt').read_text(),'')
        self.assert_consistent(f)

    def test_batch_cli_recovers_saved_result_without_any_installed_recognizer(self):
        f=self.fixture();self.interrupt(f,'extractions',after=True)
        batch=f.root/'batch.txt';batch.write_text('')
        done=subprocess.run([sys.executable,'-m','unison_snapshot','bridge-house-paper-batch',
            '--evidence-root',str(f.evidence),'--review-root',str(f.review),'--parse-batch',str(batch),
            '--tesseract','NO_OCR_ALLOWED','--materialize-saved'],env=f.env,capture_output=True,text=True)
        self.assertEqual(done.returncode,0,done.stderr)
        self.assertEqual(json.loads(done.stdout),{'recovered_saved_materialization':1})
        self.assert_consistent(f)

    def test_recovered_result_reaches_normal_candidate_and_report_status(self):
        from test_house_candidate import BASE
        import test_disclosure_status_house as status_fixtures
        from unison_snapshot.house_candidate import build_house_candidate
        from unison_snapshot.disclosure_status_house import build_house_status

        f=self.fixture();self.interrupt(f,'extractions',after=True)
        self.resume(f)
        q=self.assert_consistent(f)
        f.write(f.review/'status/house_clerk.json',{
            'parser_commit':'a'*40,'pending_parse_count':0,'qualification_count':1,
            'qualified_transaction_count':len(q['transactions']),
            'quarantined_row_count':len(q['quarantined']),
            'evidence_count':1,'extracted_count':1,'failure_count':1})
        candidate=build_house_candidate(f.review,{
            'status':'ok','run_at':'2026-10-23T00:00:00Z',
            'counts':{'archived':1,'pending':0,'failed':0}},deepcopy(BASE))
        self.assertEqual(candidate['transactions'],q['transactions'])
        s=status_fixtures.HouseDisclosureStatusTests();s.setUp()
        self.addCleanup(s.temporary.cleanup)
        s.archive(f.metadata['document_id'],sha=f.metadata['sha256'])
        report=build_house_status(s.evidence,f.review,candidate['transactions'],[])[0]['reports'][0]
        self.assertEqual(report['qualified_transaction_count'],len(q['transactions']))
        self.assertIsNone(report['quarantined_row_count'])
        self.assertIn('historical_failure_receipt_present',report['reason_codes'])

    def test_process_exit_releases_lock_and_keeps_prepared_commit_recoverable(self):
        f=self.fixture();self.interrupt(f,'paper_observations')
        child='''
import os,sys
from pathlib import Path
import unison_snapshot.house_paper_commit as c
original=c._replace_output
def crash(path,content):
    original(path,content)
    if 'extractions' in path.parts:os._exit(73)
c._replace_output=crash
c.recover_pending_paper_materializations(Path(sys.argv[1]),Path(sys.argv[2]))
'''
        done=subprocess.run([sys.executable,'-c',child,str(f.evidence),str(f.review)],
                            env=f.env,capture_output=True,text=True)
        self.assertEqual(done.returncode,73,done.stderr)
        self.assertTrue(self.pending(f).is_file())
        self.assertEqual(recover_pending_paper_materializations(f.evidence,f.review),
                         [f.evidence/'house_clerk/documents'/f.relative])
        self.assert_consistent(f)

    def test_concurrent_consumers_and_writers_stop_without_touching_review_files(self):
        f=self.fixture();before=self.artifacts(f)
        child='''
import sys
from pathlib import Path
from unison_snapshot.house_paper_commit import paper_review_access
with paper_review_access(Path(sys.argv[1])):raise RuntimeError('unexpectedly entered locked review')
'''
        with paper_review_access(f.review):
            with paper_review_access(f.review):pass  # same-call nesting is supported
            done=subprocess.run([sys.executable,'-c',child,str(f.review/'..'/'review')],
                                env=f.env,capture_output=True,text=True)
            self.assertNotEqual(done.returncode,0)
            self.assertIn('review is busy',done.stderr)
        self.assertEqual(self.artifacts(f),before)
        self.assertFalse((f.review/'.local').exists())

    def test_external_destination_input_source_or_payload_changes_stop_before_any_replacement(self):
        for change in ('destination','identity','secondary','source','payload','metadata'):
            with self.subTest(change=change):
                f=self.fixture();self.interrupt(f,'extractions',after=True)
                metadata=deepcopy(f.metadata)
                if change=='destination':f.write(f.review/'house_clerk/paper_amount_headers'/f.relative,{'foreign_edit':True})
                if change=='identity':f.write(f.review/'house_clerk/identities'/f.relative,{'foreign_identity':True})
                if change=='secondary':f.write(f.review/'house_clerk/paper_key_field_rereads'/f.relative,{'foreign_cache':True})
                if change=='source':(f.evidence/f.metadata['archive_path']).write_bytes(b'changed original')
                if change=='payload':
                    value=json.loads(self.pending(f).read_bytes());value['payload']['outputs']['header']['foreign']=True
                    f.write(self.pending(f),value)
                if change=='metadata':metadata['filer_name']='Different, Person'
                before=self.artifacts(f)
                with self.assertRaises(ValueError):recover_paper_materialization(f.review,f.evidence,metadata)
                self.assertEqual(self.artifacts(f),before)
                self.assertTrue(self.pending(f).is_file())

    def test_failure_before_pending_record_does_not_replace_outputs_or_create_legacy_reference(self):
        f=self.fixture();before={folder:(f.review/'house_clerk'/folder/f.relative).read_bytes()
            for folder in ('extractions','qualifications','paper_observations','paper_amount_headers')}
        with patch.object(commits,'_atomic',side_effect=OSError('cannot stage journal')),self.assertRaises(OSError):
            f.materialize_legacy()
        for folder,value in before.items():self.assertEqual((f.review/'house_clerk'/folder/f.relative).read_bytes(),value)
        self.assertFalse(self.pending(f).exists())
        self.assertFalse((f.review/'house_clerk/paper_legacy_extraction_references').exists())


if __name__=='__main__':unittest.main()
