"""Approved explicit Undetermined values remain gaps, never zero holdings."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import importlib.util
import sys
from unittest.mock import patch

from test_house_holdings import page, word, extraction, IDENTITY, SHA
import test_house_candidate as house_candidate_fixture
from test_house_candidate import BASE, TRANSACTION
from test_public_repo import FakeTransport
from unison_snapshot.house_holdings import extract_schedule_a_pages, qualify_financial_report
from unison_snapshot.house_annual_sections import observe_annual_sections
from unison_snapshot.house_candidate import build_house_candidate
from unison_snapshot.house import HouseIndexError
from unison_snapshot.builder import build
from unison_snapshot.legacy import load, processor_version_for
from unison_snapshot.public_repo import PublicSnapshotRepository
from unison_snapshot.disclosure_candidate import build_disclosure_candidate, DisclosureCandidateError


def partial_input():
    pages=[page(),page(bad_value=True)]
    for p in pages:
        p['words'] += [word('Schedule',25,80),word('A',80,80)]
    x=extraction(extract_schedule_a_pages(pages,source_sha256=SHA)[0])
    obs=observe_annual_sections(pages,x)
    x['source_text_coverage']=obs['source_text_coverage']
    return x,obs


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value),encoding='utf-8')


def candidate_fixture(root,*,older=False):
    house_candidate_fixture.HouseCandidateTests().fixture(root)
    x,obs=partial_input();q=qualify_financial_report(x,IDENTITY)
    relative=f"2025/{x['source']['document_id']}/{SHA}.json"
    for kind,value in [('holding_extractions',x),('holding_section_observations',obs),('holding_qualifications',q)]:
        write(root/'house_clerk'/kind/relative,value)
    total=len(q['holdings'])
    if older:
        old=deepcopy(q);old.pop('holding_coverage');old['source']['document_id']='old-report'
        old['source']['filed_date']='2025-05-01';old['report_period_end']='2024-12-31';old['quarantined']=[]
        for row in old['holdings']:
            row.update(id='old-holding',filing_id='old-report',report_period_end='2024-12-31',filed_at='2025-05-01T00:00:00Z')
        write(root/'house_clerk/holding_qualifications/2024/old-report/old.json',old)
        total+=len(old['holdings'])
    write(root/'status/house_holdings.json',{'qualification_count':2 if older else 1,
          'qualified_holding_count':total,'quarantined_row_count':len(q['quarantined'])})
    state={'status':'ok','run_at':'2026-09-18T12:00:00Z','counts':{'archived':1,'pending':0,'failed':0}}
    return build_house_candidate(root,state,deepcopy(BASE)),q,state,relative


class PartialHoldingTests(unittest.TestCase):
    def test_generic_explicit_value_gap_preserves_safe_rows_and_partition(self):
        x,_=partial_input();before=deepcopy(x);q=qualify_financial_report(x,IDENTITY)
        self.assertTrue(q['production_eligible'])
        self.assertEqual((len(q['holdings']),len(q['excluded']),len(q['quarantined'])),(1,2,1))
        c=q['holding_coverage']
        self.assertEqual((c['finite_value_low'],c['finite_value_high']),(1001,15000))
        self.assertEqual(c['unresolved_rows'][0]['value_raw'],'Undetermined')
        self.assertIs(c['complete_total_assets'],False)
        self.assertEqual(x,before)

    def test_other_missing_or_conflicting_evidence_does_not_allow_partial(self):
        for mutation in ['literal','asset','identity','coverage','document','empty','bounds']:
            with self.subTest(mutation=mutation):
                x,_=partial_input();identity=deepcopy(IDENTITY)
                if mutation=='literal':x['rows'][2]['value_raw']='Unreadable'
                if mutation=='asset':x['rows'][0]['review_reasons']=['asset_name_unresolved']
                if mutation=='identity':identity['status']='unmatched'
                if mutation=='coverage':x['source_text_coverage']['unresolved_financial_text_line_count']=1
                if mutation=='document':x['document_reasons']=['source_page_unread']
                if mutation=='empty':x['rows']=[x['rows'][2]]
                if mutation=='bounds':x['rows'][2]['value_low']=0
                q=qualify_financial_report(x,identity)
                self.assertFalse(q['production_eligible'])
                self.assertNotIn('holding_coverage',q)
                self.assertEqual(q['holdings'],[])

    def test_actual_candidate_reader_v3_and_builder_do_not_mix_old_year(self):
        with tempfile.TemporaryDirectory() as d:
            c,q,_,_=candidate_fixture(Path(d),older=True)
            self.assertEqual(c['transactions'],[TRANSACTION])
            self.assertEqual(c['reported_holdings'],q['holdings'])
            self.assertEqual(c['meta']['holding_report_coverage'],[q['holding_coverage']])
            bundle=build(c,generated_at='2026-09-18T12:00:00Z',allow_production=True)
            self.assertEqual(bundle.manifest['holding_report_coverage'],[q['holding_coverage']])
            repo=PublicSnapshotRepository('example','data',transport=FakeTransport(bundle.files))
            for mode,key in [('dashboard',None),('search',None),('person',IDENTITY['person_id']),('ticker','EXM')]:
                snapshot=repo.fetch(mode,key).snapshot
                self.assertEqual(processor_version_for(snapshot),'v3')
                processed=load('process_snapshot',version='v3').build_snapshot(snapshot)
                expected=[] if mode=='ticker' else [q['holding_coverage']]
                self.assertEqual(processed['meta']['holding_report_coverage'],expected)
            html=load('render_dashboard',version='v3').render_html(processed)
            self.assertIn('data',html)

    def test_candidate_recomputes_partial_from_saved_pages_not_labels(self):
        for mutation in ['count','value','pages','qualification']:
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as d:
                root=Path(d);_,q,state,rel=candidate_fixture(root)
                if mutation=='count':q['holding_coverage']['observed_row_count']=999
                if mutation=='qualification':q['holdings'][0]['value_high']=0
                if mutation in {'count','qualification'}:write(root/'house_clerk/holding_qualifications'/rel,q)
                if mutation=='value':
                    path=root/'house_clerk/holding_extractions'/rel
                    x=json.loads(path.read_text());x['rows'][2]['value_raw']='Unreadable';write(path,x)
                if mutation=='pages':
                    path=root/'house_clerk/holding_section_observations'/rel
                    obs=json.loads(path.read_text());obs.pop('source_word_pages');write(path,obs)
                with self.assertRaises(HouseIndexError):build_house_candidate(root,state,deepcopy(BASE))

    def test_processor_rejects_wrong_ids_period_totals_or_unknown_literal(self):
        with tempfile.TemporaryDirectory() as d:
            c,_,_,_=candidate_fixture(Path(d));c['meta']['snapshot_id']='test-partial'
            for mutation in ['ids','period','total','literal','count']:
                changed=deepcopy(c);coverage=changed['meta']['holding_report_coverage'][0]
                if mutation=='ids':coverage['published_holding_ids']=[]
                if mutation=='period':coverage['report_period_end']='2024-12-31'
                if mutation=='total':coverage['finite_value_high']=0
                if mutation=='literal':coverage['unresolved_rows'][0]['value_raw']='Unknown'
                if mutation=='count':coverage['observed_row_count']=999
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                    load('process_snapshot',version='v3').build_snapshot(changed)

    def test_four_reader_modes_keep_complete_context_and_validate_filtered_loaded_rows(self):
        with tempfile.TemporaryDirectory() as d:
            c,_,_,_=candidate_fixture(Path(d))
            second=deepcopy(c['reported_holdings'][0]);second.update(id='second-synthetic-holding',ticker='OTHER',asset_name='Other')
            c['reported_holdings'].append(second)
            coverage=c['meta']['holding_report_coverage'][0]
            coverage['published_holding_ids'].append(second['id']);coverage['observed_row_count']+=1
            coverage['finite_value_low']+=second['value_low'];coverage['finite_value_high']+=second['value_high']
            bundle=build(c,generated_at='2026-09-18T12:00:00Z',allow_production=True)
            repo=PublicSnapshotRepository('example','data',transport=FakeTransport(bundle.files))
            for mode,key in [('dashboard',None),('search',None),('person',IDENTITY['person_id']),('ticker','EXM')]:
                snapshot=repo.fetch(mode,key).snapshot
                processor=load('process_snapshot',version='v3')
                processed=processor.build_snapshot(snapshot)
                self.assertEqual(len(processed['reported_holdings']),1 if mode=='ticker' else 2)
                self.assertEqual(len(processed['meta']['holding_report_coverage']),0 if mode=='ticker' else 1)
                changed=deepcopy(snapshot)
                changed['reported_holdings'][0]['report_period_end']='2024-12-31'
                with self.subTest(mode=mode),self.assertRaises(ValueError):processor.build_snapshot(changed)

    def test_existing_fetch_command_selects_v3_for_partial_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            c,q,_,_=candidate_fixture(Path(d))
            bundle=build(c,generated_at='2026-09-18T12:00:00Z',allow_production=True)
            selection=PublicSnapshotRepository('example','data',transport=FakeTransport(bundle.files)).fetch('person',IDENTITY['person_id'])
            client=Path(__file__).resolve().parents[2]/'client'
            spec=importlib.util.spec_from_file_location('_partial_fetch_cli',client/'fetch_snapshot.py')
            module=importlib.util.module_from_spec(spec)
            with patch.object(sys,'path',[str(client),*sys.path]):spec.loader.exec_module(module)
            output=Path(d)/'client.json'
            with patch.object(module,'PublicSnapshotRepository') as repository,patch.object(sys,'argv',[
                    'fetch_snapshot','person','--value',IDENTITY['person_id'],'--owner','example','--repo','data','--output',str(output)]):
                repository.return_value.fetch.return_value=selection
                self.assertEqual(module.main(),0)
            processed=json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(processed['meta']['holding_report_coverage'],[q['holding_coverage']])

    def test_existing_source_assembly_preserves_coverage_and_rejects_wrong_binding(self):
        with tempfile.TemporaryDirectory() as d:
            c,q,_,_=candidate_fixture(Path(d))
            combined=build_disclosure_candidate(deepcopy(BASE),{'house_clerk':c})
            self.assertEqual(combined['reported_holdings'],q['holdings'])
            self.assertEqual(combined['meta']['holding_report_coverage'],[q['holding_coverage']])
            bundle=build(combined,generated_at='2026-09-18T12:00:00Z',allow_production=True)
            snapshot=PublicSnapshotRepository('example','data',transport=FakeTransport(bundle.files)).fetch('person',IDENTITY['person_id']).snapshot
            self.assertEqual(load('process_snapshot',version='v3').build_snapshot(snapshot)['meta']['holding_report_coverage'],[q['holding_coverage']])
            c['meta']['holding_report_coverage'][0]['published_holding_ids']=[]
            with self.assertRaises(DisclosureCandidateError):build_disclosure_candidate(deepcopy(BASE),{'house_clerk':c})


if __name__=='__main__':unittest.main()
