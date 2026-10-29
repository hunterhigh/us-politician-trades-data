"""Version-bound annual report accounting through status CLI and public reader."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from unison_snapshot.disclosure_status_house import build_house_status
from unison_snapshot.house_annual_sections import consume_schedule_b_inputs
from unison_snapshot.house_holdings import qualify_financial_report
from unison_snapshot.disclosure_status import attach_disclosure_status
from unison_snapshot.builder import build
from unison_snapshot.public_repo import PublicSnapshotRepository
from tests.test_annual_transaction_candidate_relations import house_input
from test_house_partial_holdings import partial_input
from test_house_holdings import IDENTITY
from test_disclosure_status import NOW
from test_public_repo import production_payload, FakeTransport, COMMIT


class HouseAnnualStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.evidence, self.review = self.root/'evidence', self.root/'review'
        self.evidence.mkdir(); self.review.mkdir()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def fixture(self, *, holding=False):
        payload = house_input()
        if holding:
            extraction, observation = partial_input()
            payload = {'extraction': extraction, 'observation': observation, 'identity': IDENTITY}
        x = payload['extraction']; source=x['source']; sha=x['source_sha256']
        self.relative = f"2025/{source['document_id']}/{sha}.json"
        self.meta = dict(source, sha256=sha, retrieved_at='2026-10-15T00:00:00Z',
                         archive_path=f"house_clerk/financial-reports/2025/{source['document_id']}/{sha}.pdf")
        self.write(self.evidence/'house_clerk/financial-reports'/self.relative, self.meta)
        for kind, value in [('holding_extractions', x), ('holding_section_observations',payload['observation']),
                            ('holding_identities',payload['identity'])]:
            self.write(self.review/'house_clerk'/kind/self.relative, value)
        if holding:
            q = qualify_financial_report(x, IDENTITY)
            self.write(self.review/'house_clerk/holding_qualifications'/self.relative, q)
            return q['holdings']
        return consume_schedule_b_inputs([payload],transactions=[],reported_holdings=[])['transactions']

    def status(self, tx=(), hs=()):
        scopes=build_house_status(self.evidence,self.review,list(tx),list(hs))
        return next(s for s in scopes if s['scope_id']=='house_annual')

    def add_version(self, document='10000001', raw=b'new annual source bytes'):
        payload=house_input(document,raw=raw)
        x=payload['extraction'];sha=x['source_sha256'];source=x['source']
        relative=f"2025/{document}/{sha}.json"
        meta=dict(source,sha256=sha,retrieved_at='2026-10-16T00:00:00Z',
                  archive_path=f'house_clerk/financial-reports/2025/{document}/{sha}.pdf')
        self.write(self.evidence/'house_clerk/financial-reports'/relative,meta)
        for kind,value in [('holding_extractions',x),('holding_section_observations',payload['observation']),
                           ('holding_identities',payload['identity'])]:
            self.write(self.review/'house_clerk'/kind/relative,value)
        return consume_schedule_b_inputs([payload],transactions=[],reported_holdings=[])['transactions']

    def test_unpublished_old_cache_cannot_block_selected_version(self):
        old=self.fixture()
        new=self.add_version()
        self.assertNotEqual(old[0]['id'],new[0]['id'])
        report=self.status(new)['reports'][0]
        self.assertEqual(report['qualified_transaction_count'],1)
        self.assertEqual(report['publication_transaction_ids'],[new[0]['id']])
        self.assertNotIn('existing_annual_source_replacement_unresolved',report['reason_codes'])

    def test_real_old_publication_cannot_be_silently_replaced(self):
        old=self.fixture();new=self.add_version()
        for publication in (old,old+new):
            with self.subTest(ids=[row['id'] for row in publication]):
                report=self.status(publication)['reports'][0]
                self.assertEqual(report['qualified_transaction_count'],0)
                self.assertIn('existing_annual_source_replacement_unresolved',report['reason_codes'])
                self.assertIn(old[0]['id'],report['publication_transaction_ids'])

    def test_distinct_reports_for_same_person_and_year_do_not_contaminate(self):
        first=self.fixture()
        second=self.add_version(document='10000002')
        reports=self.status(first+second)['reports']
        self.assertEqual(len(reports),2)
        self.assertEqual([row['qualified_transaction_count'] for row in reports],[1,1])
        self.assertEqual([len(row['publication_transaction_ids']) for row in reports],[1,1])

    def test_reused_document_id_in_other_year_is_not_a_replacement_predecessor(self):
        current=self.fixture()
        from unison_snapshot.house_annual_sections import observe_annual_sections
        payload=house_input(raw=b'older year official source')
        x=payload['extraction'];source=x['source'];sha=x['source_sha256']
        source.update(filing_year=2024,filed_date='2025-05-01',
                      source_url=source['source_url'].replace('/2025/','/2024/'))
        x['report_period_end']='2024-12-31'
        for word in payload['pages'][0]['words']:
            if word['text']=='02/18/2025':word['text']='02/18/2024'
        payload['observation']=observe_annual_sections(payload['pages'],x)
        prior=consume_schedule_b_inputs([payload],transactions=[],reported_holdings=[])['transactions']
        self.assertEqual(len(prior),1)
        self.write(self.evidence/f'house_clerk/financial-reports/2024/10000001/{sha}.json',
            dict(source,sha256=sha,retrieved_at='2025-10-15T00:00:00Z',
                 archive_path=f'house_clerk/financial-reports/2024/10000001/{sha}.pdf'))
        reports=self.status(prior+current)['reports']
        self.assertEqual(reports[1]['qualified_transaction_count'],1)
        self.assertNotIn('existing_annual_source_replacement_unresolved',reports[1]['reason_codes'])
        self.assertEqual(reports[0]['publication_transaction_ids'],[prior[0]['id']])

    def test_annual_transactions_separate_from_ptr_and_replay_auditable_rows(self):
        tx=self.fixture()
        scopes=build_house_status(self.evidence,self.review,tx,[])
        self.assertEqual(scopes[0]['unmatched_publication_transaction_ids'],[])
        report=scopes[1]['reports'][0]
        self.assertEqual(report['qualified_transaction_count'],1)
        self.assertEqual(report['publication_transaction_ids'],[tx[0]['id']])
        self.assertIsNone(report['qualified_holding_count'])
        self.assertEqual(report['qualification_state'],'partial')
        self.assertIsNone(report['quarantined_row_count'])

    def test_partial_official_unknown_holdings_not_total_or_zero_transactions(self):
        hs=self.fixture(holding=True)
        report=self.status(hs=hs)['reports'][0]
        self.assertEqual(report['qualified_holding_count'],1)
        self.assertEqual(report['publication_holding_ids'],[hs[0]['id']])
        self.assertEqual(report['holding_coverage_status'],'partial_official_value_unknown')
        self.assertEqual(report['official_value_unknown_holding_count'],1)
        self.assertIsNone(report['qualified_transaction_count'])
        self.assertFalse(report['holding_coverage']['complete_total_assets'])
        self.assertEqual(report['qualification_state'],'partial')
        self.assertNotEqual(report['qualification_state'],'zero_transactions')

    def test_latest_version_does_not_borrow_old_qualification(self):
        tx=self.fixture(); meta=dict(self.meta,sha256='b'*64,retrieved_at='2026-10-16T00:00:00Z')
        meta['archive_path']=meta['archive_path'].replace(self.meta['sha256'],'b'*64)
        self.write(self.evidence/'house_clerk/financial-reports'/self.relative.replace(self.meta['sha256'],'b'*64),meta)
        report=self.status(tx)['reports'][0]
        self.assertEqual(report['selected_source_version'],'b'*64)
        self.assertIsNone(report['qualified_transaction_count'])
        self.assertEqual(report['qualification_state'],'not_evaluated')
        self.assertIn('publication_not_qualified_at_selected_version',report['reason_codes'])
        meta['retrieved_at']=self.meta['retrieved_at']
        self.write(self.evidence/'house_clerk/financial-reports'/self.relative.replace(self.meta['sha256'],'b'*64),meta)
        self.assertIsNone(self.status()['reports'][0]['selected_source_version'])

    def test_missing_extraction_or_identity_cannot_qualify(self):
        self.fixture()
        (self.review/'house_clerk/holding_identities'/self.relative).unlink()
        report=self.status()['reports'][0]
        self.assertEqual(report['qualified_transaction_count'],0)
        self.assertNotEqual(report['qualification_state'],'zero_transactions')
        self.assertIn('annual_identity_not_deterministic',report['reason_codes'])
        (self.review/'house_clerk/holding_extractions'/self.relative).unlink()
        self.assertEqual(self.status()['reports'][0]['extraction_state'],'not_attempted')

    def test_changed_source_pages_and_formal_facts_rejected(self):
        tx=self.fixture()
        bad=deepcopy(tx);bad[0]['asset_name']='changed'
        with self.assertRaisesRegex(ValueError,'published transaction differs'):
            self.status(bad)
        path=self.review/'house_clerk/holding_section_observations'/self.relative
        obs=json.loads(path.read_text());obs['source_word_pages'][0]['words'][0]['text']='changed'
        self.write(path,obs)
        with self.assertRaisesRegex(ValueError,'pages digest differs'):
            self.status()

    def test_tampered_holding_qualification_and_duplicate_ids_rejected(self):
        hs=self.fixture(holding=True)
        with self.assertRaisesRegex(ValueError,'holding ID missing or duplicated'):
            self.status(hs=hs+hs)
        path=self.review/'house_clerk/holding_qualifications'/self.relative
        q=json.loads(path.read_text());q['holdings'][0]['asset_name']='changed';self.write(path,q)
        with self.assertRaisesRegex(ValueError,'qualification differs'):
            self.status()

    def test_combined_sections_keep_two_count_units_and_no_quarantine_sum(self):
        self.fixture(holding=True)
        from unison_snapshot.house_annual_sections import observe_annual_sections
        path=self.review/'house_clerk/holding_extractions'/self.relative
        x=json.loads(path.read_text())
        obs=json.loads((self.review/'house_clerk/holding_section_observations'/self.relative).read_text())
        pages=obs['source_word_pages']+house_input()['pages']
        obs=observe_annual_sections(pages,x)
        x['source_text_coverage']=obs['source_text_coverage']
        q=qualify_financial_report(x,IDENTITY)
        for kind,value in [('holding_extractions',x),('holding_section_observations',obs),('holding_qualifications',q)]:
            self.write(self.review/'house_clerk'/kind/self.relative,value)
        audit=consume_schedule_b_inputs([dict(extraction=x,observation=obs,identity=IDENTITY,pages=pages)],
                                       transactions=[],reported_holdings=q['holdings'])
        report=self.status(audit['transactions'],q['holdings'])['reports'][0]
        self.assertEqual((report['qualified_transaction_count'],report['qualified_holding_count']),(1,1))
        self.assertEqual((len(report['publication_transaction_ids']),len(report['publication_holding_ids'])),(1,1))
        self.assertIsNone(report['quarantined_row_count'])

    def test_non_annual_archive_not_relabelled(self):
        self.fixture()
        path=self.evidence/'house_clerk/financial-reports'/self.relative
        self.write(path,dict(self.meta,report_type='Termination Report'))
        with self.assertRaisesRegex(ValueError,'archive receipt binding'):
            self.status()

    def test_no_qualification_is_unknown_count(self):
        self.fixture()
        for base in self.review.glob('house_clerk/*'):
            for path in base.glob('*/*/*.json'):path.unlink()
        report=self.status()['reports'][0]
        self.assertIsNone(report['qualified_transaction_count'])
        self.assertIsNone(report['qualified_holding_count'])
        self.assertEqual(report['qualification_state'],'not_evaluated')

    def test_review_without_archive_does_not_claim_qualified_or_zero(self):
        self.fixture()
        (self.evidence/'house_clerk/financial-reports'/self.relative).unlink()
        report=self.status()['reports'][0]
        self.assertEqual(report['qualification_state'],'unknown')
        self.assertEqual(report['acquisition_state'],'unknown')
        self.assertIsNone(report['qualified_transaction_count'])

    def test_frozen_git_tree_cli_attach_builder_and_public_reader(self):
        tx=self.fixture()
        from unison_snapshot.house_candidate import _person
        payload=production_payload()
        payload['people']=[_person(house_input()['identity'])]
        payload['transactions']=tx
        payload['reported_holdings']=[]
        payload['meta']['transaction_disclosure_relations']=consume_schedule_b_inputs(
            [house_input()], transactions=[], reported_holdings=[])['transaction_disclosure_relations']
        input_path=self.root/'candidate.json';self.write(input_path,payload)
        for tree in (self.evidence,self.review):
            for args in (['init','-q'],['add','.'],['-c','user.name=Test','-c','user.email=test@example.com',
                                                  'commit','-qm','fixture','--allow-empty']):
                subprocess.run(['git','-C',str(tree),*args],check=True,capture_output=True)
        out=self.root/'status.json'
        source=Path(__file__).resolve().parents[1]/'src'
        env=dict(os.environ,PYTHONPATH=str(source))
        result=subprocess.run([sys.executable,'-m','unison_snapshot','build-disclosure-status','--evidence-root',str(self.evidence),
            '--review-root',str(self.review),'--input',str(input_path),'--code-commit','1'*40,
            '--previous-main-commit','4'*40,'--output',str(out)],capture_output=True,text=True,env=env)
        self.assertEqual(result.returncode,0,result.stderr)
        status=json.loads(out.read_text())
        bundle=build(payload,generated_at=NOW,allow_production=True)
        attached=attach_disclosure_status(bundle,status['scopes'],transactions=tx,provenance=status['provenance'])
        reader=PublicSnapshotRepository('owner','repo',transport=FakeTransport(attached.files))
        public=reader.disclosure_status(COMMIT,include_records=True)
        annual=next(s for s in public['sources'] if s['scope_id']=='house_annual')
        self.assertEqual(annual['records'][0]['publication_transaction_count'],1)
        self.assertEqual(annual['counts']['publication_transaction_count'],1)
        self.assertEqual(annual['records'][0]['qualified_transaction_count'],1)
        self.assertEqual(attached.manifest['snapshot_id'],bundle.manifest['snapshot_id'])
