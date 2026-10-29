"""Fixed-year processing retains facts and never visits another year's caches."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import test_house_candidate as fixtures
from test_house_candidate import BASE,TRANSACTION,IDENTITY
from unison_snapshot.house_candidate import build_house_candidate
from unison_snapshot.house_archive_queue import project,write
from unison_snapshot.house import HouseIndexError


class FixedYearProjectionTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.review=self.root/'review';self.evidence=self.root/'evidence'
        self.evidence.mkdir();fixtures.HouseCandidateTests().fixture(self.review)
        self.state={'status':'ok','run_at':'2026-09-18T12:00:00Z','counts':{'archived':1,'pending':0,'failed':0}}
        self.prior=build_house_candidate(self.review,self.state,deepcopy(BASE))
        # A stale unrelated cache must not be parsed or silently requalified.
        (self.review/'house_clerk/qualifications/2026/20000001/a.json').write_text('unreadable old cache')
        self.row={**TRANSACTION,'id':'house-ptr:new','filing_id':'20000002',
            'transaction_date':'2025-09-01','filed_at':'2025-09-10T00:00:00Z'}
        self.path=self.review/'house_clerk/qualifications/2025/20000002/b.json'
        write(self.path,{'schema_version':'house-ptr-qualification/v1','identity':IDENTITY,
                        'transactions':[self.row],'quarantined':[]})

    def test_selected_year_uses_normal_builder_and_retains_prior_facts_and_watermark(self):
        before=deepcopy(self.prior)
        value=project(self.review,self.evidence,self.state,self.prior,2025)
        self.assertEqual(value['transactions'],sorted([TRANSACTION,self.row],key=lambda r:r['id']))
        for name in ('people','reported_holdings','source_health','meta'):
            self.assertEqual(value[name],self.prior[name])
        self.assertEqual(self.prior,before)
        self.assertEqual(project(self.review,self.evidence,self.state,value,2025),value)

    def test_existing_fact_conflict_blocks_instead_of_overwriting(self):
        prior=deepcopy(self.prior);prior['transactions'].append({**self.row,'amount_high':50000})
        with self.assertRaisesRegex(HouseIndexError,'changes an existing qualified fact'):
            project(self.review,self.evidence,self.state,prior,2025)

    def test_quarantined_report_does_not_remove_old_fact_or_invent_new_rows(self):
        write(self.path,{'schema_version':'house-ptr-qualification/v1','identity':IDENTITY,
                        'transactions':[],'quarantined':[{'reasons':['amount_unreadable']}]})
        value=project(self.review,self.evidence,self.state,self.prior,2025)
        self.assertEqual(value,self.prior)


from test_data_trees import SourceTreeFixture
from unittest.mock import patch
from unison_snapshot.commands import data_trees
from unison_snapshot.house_archive_queue import consume
from unison_snapshot.house_ptr import PARSER_RETRY_VERSION


class FixedArchiveQueueTests(SourceTreeFixture,unittest.TestCase):
    def test_bound_inventory_is_saved_per_report_and_resumed_without_reparsing(self):
        import hashlib
        control=self.prepare();evidence,review,state=(Path(control['trees'][r]['root']) for r in ('evidence','review','state'))
        raw=b'bound archived fixture; parser is mocked';sha=hashlib.sha256(raw).hexdigest()
        pdf=evidence/f'house_clerk/documents/2025/20000002/{sha}.pdf';pdf.parent.mkdir(parents=True);pdf.write_bytes(raw)
        metadata={'filing_year':2025,'document_id':'20000002','sha256':sha,
                  'archive_path':pdf.relative_to(evidence).as_posix(),
                  'document_url':'https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/20000002.pdf'}
        write(pdf.with_suffix('.json'),metadata);write(evidence/'house_clerk/members/roster.roster.json',{})
        write(state/'checkpoints/house/2025.json',{'filing_year':2025,'queue':[],'counts':{'failed':0},
                                               'documents':{'20000002':{}},'index_sha256':'a'*64})
        write(state/'status/house_backfill_2025.json',{'counts':{'archived':1}})
        write(review/'candidates/sources/house_clerk-current.json',{'kept':'all prior facts'})
        data_trees.publish(self.workspace,data_trees.ROLES,'source fixtures')
        poison=review/'house_clerk/extractions/2026/poison.json';write(poison,{'invalid':'must not visit'})
        extraction={'source':{'document_id':'20000002'},'transactions':[]}
        qualification={'transactions':[],'quarantined':[{'reasons':['unsupported_format']}],'qualification':{}}
        with patch('unison_snapshot.house_ptr.parse_archived_pdf',return_value=extraction) as parser,\
             patch('unison_snapshot.house_members.suggest_identity',return_value={}),\
             patch('unison_snapshot.house_ptr.qualify_automatic',return_value=qualification),\
             patch('unison_snapshot.house_archive_queue.project',side_effect=lambda r,e,s,p,y:p),\
             patch('unison_snapshot.commands.assemble_source.assemble'):
            value=consume(self.workspace,year=2025)
            self.assertEqual((value['report_count'],value['queue_count'],value['quarantined_row_count']),(1,0,1))
            self.assertEqual(value['reports'][0]['reasons'],['unsupported_format'])
            self.assertIsNone(value['reports'][0]['person_id'])
            self.assertEqual(consume(self.workspace,year=2025),value)
            self.assertEqual(parser.call_count,1)
        self.assertEqual(json.loads(poison.read_bytes()),{'invalid':'must not visit'})
        saved=json.loads(data_trees.git(self.repo,'--git-dir',self.remote,'show','review:status/house_backfill_2025.json'))
        self.assertEqual(saved,value)
