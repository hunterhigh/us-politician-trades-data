"""Run the annual job's real Bash candidate branch against mixed saved inputs."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap
import tomllib
import unittest

import test_house_native_contrast_consumption as native
import test_house_partial_holdings as annual_a
import test_annual_transaction_candidate_relations as annual_b
import test_oge_annual_workflow as shell_fixture
from unison_snapshot.house_holdings import qualify_financial_report
from unison_snapshot.disclosure_status_house import build_house_status

ROOT=Path(__file__).resolve().parents[2]
WORKFLOW=ROOT/'.github/workflows/house-holdings.yml'


class HouseHoldingsSourceRootWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.native=native.HouseNativeContrastConsumptionTests();self.native.setUp()
        self.addCleanup(self.native.doCleanups)

    def write(self,path,value):
        self.native.write(path,value)

    def prepare(self):
        data=self.native.prepare('white',prior=True)
        self.assertEqual(data['q']['parser_version'],'house-ptr-native-bichrome/v1')
        root=data['root'];evidence=root/'evidence source';review=root/'mixed review'
        shutil.copytree(data['evidence'],evidence);shutil.copytree(data['review'],review)
        data.update(evidence=evidence,review=review)
        # Reuse the existing approved partial-A source contract without any new
        # parser or OCR. B is an independent annual report under its own ID.
        extraction,observation=annual_a.partial_input()
        aq=qualify_financial_report(extraction,annual_a.IDENTITY)
        self.assertTrue(aq['production_eligible'])
        self.assertFalse(aq['holding_coverage']['complete_total_assets'])
        b=annual_b.house_input('10000002',raw=b'existing annual B saved fixture')
        for x,o,identity,q in [(extraction,observation,annual_a.IDENTITY,aq),
                               (b['extraction'],b['observation'],b['identity'],None)]:
            source=x['source'];sha=x['source_sha256']
            rel=Path(str(source['filing_year']))/source['document_id']/(sha+'.json')
            for directory,value in [('holding_extractions',x),('holding_section_observations',o),
                                     ('holding_identities',identity)]:
                self.write(review/'house_clerk'/directory/rel,value)
            if q is not None:self.write(review/'house_clerk/holding_qualifications'/rel,q)
            self.write(evidence/'house_clerk/financial-reports'/rel,{**source,'sha256':sha,
                'retrieved_at':'2026-09-18T00:00:00Z',
                'archive_path':f'house_clerk/financial-reports/{source["filing_year"]}/{source["document_id"]}/{sha}.pdf'})
        self.write(review/'status/house_holdings.json',{'qualification_count':1,
            'qualified_holding_count':len(aq['holdings']),'quarantined_row_count':len(aq['quarantined'])})
        state=root/'state root';self.write(state/'status/house_clerk.json',json.loads((root/'state.json').read_bytes()))
        data['state']=state;data['annual_q']=aq
        # The job normally starts from its previous complete candidate. Obtain
        # it through the existing normal CLI with explicit roots, then perform
        # its established annual normalization before testing the workflow.
        self.native.candidate(data)
        expected=self.native.candidate(data,prior_path=root/'candidate.json')
        expected_bytes=(root/'candidate.json').read_bytes()
        current=review/'candidates/sources/house_clerk-current.json'
        current.parent.mkdir(parents=True,exist_ok=True);current.write_bytes(expected_bytes)
        alias=review/'candidates/house-current.json';alias.write_bytes(b'existing downstream candidate')
        data.update(current=current,alias=alias,expected=expected,expected_bytes=expected_bytes)
        self.assertEqual(len(expected['transactions']),3)
        self.assertEqual(len(expected['reported_holdings']),1)
        self.assertEqual(expected['reported_holdings'],aq['holdings'])
        self.assertEqual(expected['meta']['holding_report_coverage'],[aq['holding_coverage']])
        self.assertEqual({r['id']:r for r in expected['transactions'] if r['id'].startswith('house-ptr:')},
                         {r['id']:r for r in data['base']['transactions']})
        return data

    def candidate_branch(self):
        text=WORKFLOW.read_text(encoding='utf-8')
        start=text.index('          mkdir -p "$REVIEW_ROOT/candidates/sources"')
        end=text.index('          source_args=',start)
        return textwrap.dedent(text[start:end])

    def run_branch(self,data,*,historical_without_root=False,evidence=None,without_dependency=False):
        bash=shell_fixture.bash_binary()
        if bash is None:self.skipTest('Bash required for the real annual workflow branch')
        branch=self.candidate_branch()
        if historical_without_root:
            branch=branch.replace(' --evidence-root "$EVIDENCE_ROOT"','')
        after=data['root']/'after-build'
        if after.exists():after.unlink()
        guard=data['root']/'dependency guard';guard.mkdir(exist_ok=True)
        blocked=data['root']/'blocked-import.log'
        (guard/'sitecustomize.py').write_text(
            'import sys,os\nfrom pathlib import Path\n'
            'class NoMuPDF:\n'
            ' def find_spec(self,fullname,path=None,target=None):\n'
            '  if fullname.split(".")[0] in {"pymupdf","fitz"}:\n'
            '   Path(os.environ["BLOCKED_IMPORT_LOG"]).write_text(fullname)\n'
            '   raise ModuleNotFoundError("native-source-proof dependency intentionally unavailable")\n'
            'sys.meta_path.insert(0,NoMuPDF())\n',encoding='utf-8')
        script=('set -euo pipefail\npython() {\n'
            ' if [ "$TEST_WITHOUT_DEPENDENCY" = "true" ]; then\n'
            '  PYTHONPATH="$TEST_GUARD$TEST_PATH_SEPARATOR$PYTHONPATH" "$REAL_PYTHON" "$@"\n'
            ' else "$REAL_PYTHON" "$@"; fi\n}\n'+branch+
            '\nprintf completed > "$TEST_AFTER_BUILD"\n')
        env={**os.environ,'REAL_PYTHON':sys.executable,'PYTHONUTF8':'1','GIT_NO_LAZY_FETCH':'1',
            'MSYS_NO_PATHCONV':'1','EVIDENCE_ROOT':str(evidence or data['evidence']),
            'REVIEW_ROOT':str(data['review']),'STATE_ROOT':str(data['state']),
            'TEST_AFTER_BUILD':str(after),'TEST_WITHOUT_DEPENDENCY':'true' if without_dependency else 'false',
            'TEST_GUARD':str(guard),'TEST_PATH_SEPARATOR':os.pathsep,'BLOCKED_IMPORT_LOG':str(blocked)}
        result=subprocess.run([bash,'-c',script],cwd=ROOT,env=env,capture_output=True,
                              text=True,encoding='utf-8',timeout=60)
        return result,after,blocked

    def unchanged_inputs(self,data):
        return {p:p.read_bytes() for root in (data['evidence'],data['review']) for p in root.rglob('*.json')
                if p not in {data['current'],data['alias']}}

    def test_real_job_branch_missing_root_fails_then_bound_root_preserves_all_facts(self):
        data=self.prepare();inputs=self.unchanged_inputs(data)
        before=data['current'].read_bytes();alias=data['alias'].read_bytes()
        failed,after,_=self.run_branch(data,historical_without_root=True)
        self.assertNotEqual(failed.returncode,0,failed.stdout)
        self.assertIn('requires an evidence root',failed.stderr+failed.stdout)
        self.assertFalse(after.exists());self.assertEqual(data['current'].read_bytes(),before)
        self.assertEqual(data['alias'].read_bytes(),alias)
        done,after,_=self.run_branch(data)
        self.assertEqual(done.returncode,0,done.stderr+done.stdout)
        self.assertTrue(after.exists())
        self.assertEqual(data['current'].read_bytes(),before)
        self.assertEqual(data['alias'].read_bytes(),before)
        self.assertEqual(json.loads(before),data['expected'])
        again,_,_=self.run_branch(data)
        self.assertEqual(again.returncode,0,again.stderr+again.stdout)
        self.assertEqual(data['current'].read_bytes(),before)
        self.assertEqual(inputs,self.unchanged_inputs(data))
        scopes=build_house_status(data['evidence'],data['review'],data['expected']['transactions'],data['expected']['reported_holdings'])
        annual=next(s for s in scopes if s['scope_id']=='house_annual')
        holding=next(r for r in annual['reports'] if r['document_id']=='10000001')
        self.assertEqual(holding['holding_coverage_status'],'partial_official_value_unknown')
        self.assertEqual(holding['official_value_unknown_holding_count'],1)
        self.assertFalse(holding['holding_coverage']['complete_total_assets'])
        self.assertEqual(holding['qualification_state'],'partial')

    def test_wrong_root_and_missing_native_proof_dependency_cannot_write_or_continue(self):
        data=self.prepare();inputs=self.unchanged_inputs(data)
        before=data['current'].read_bytes();alias=data['alias'].read_bytes()
        wrong=data['root']/'wrong evidence';wrong.mkdir()
        for kwargs in ({'evidence':wrong},{'without_dependency':True}):
            with self.subTest(kwargs=kwargs):
                done,after,blocked=self.run_branch(data,**kwargs)
                self.assertNotEqual(done.returncode,0,done.stdout)
                self.assertFalse(after.exists());self.assertEqual(data['current'].read_bytes(),before)
                self.assertEqual(data['alias'].read_bytes(),alias)
                self.assertEqual(inputs,self.unchanged_inputs(data))
                if kwargs.get('without_dependency'):
                    self.assertTrue(blocked.exists(),'real CLI did not attempt the missing proof dependency')
                    self.assertIn(blocked.read_text(),{'pymupdf','fitz'})
        done,_,_=self.run_branch(data)
        self.assertEqual(done.returncode,0,done.stderr+done.stdout)
        self.assertEqual(data['current'].read_bytes(),before)

    def test_job_installs_existing_pinned_source_proof_extra(self):
        content=WORKFLOW.read_text(encoding='utf-8')
        extras=set(re.search(r'python -m pip install -e "backend\[([^]]+)\]"',content).group(1).split(','))
        self.assertIn('house-ingest',extras)
        self.assertIn('native-source-proof',extras)
        metadata=tomllib.loads((ROOT/'backend/pyproject.toml').read_text(encoding='utf-8'))
        deps=metadata['project']['optional-dependencies']
        self.assertNotIn('PyMuPDF',','.join(deps['house-ingest']))
        self.assertTrue(any(re.fullmatch(r'PyMuPDF==[0-9.]+',x) for x in deps['native-source-proof']))
