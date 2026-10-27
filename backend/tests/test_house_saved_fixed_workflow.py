"""Exercise saved workflow scheduling with real House bridge/candidate CLIs.

Fixed-source qualification commands remain real commands. The small generated
input deliberately lacks their unrelated fixed originals, so the fresh-path
control must still hit the existing source guard, without weakening it.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

import test_house_paper_owner_correction as paper_controls
from test_house_candidate import BASE
from test_oge_annual_workflow import bash_binary
from unison_snapshot.codec import encode
from unison_snapshot.house_candidate import _person


ROOT=Path(__file__).resolve().parents[2]
WORKFLOW=ROOT/'.github/workflows/house-review.yml'
FIXED_STEPS=(
    ('Qualify fixed source reviewed House paper rows','qualify_house_paper_9116141.py'),
    ('Close fixed House paper prior-report relationship','complete_house_paper_9116141.py'),
    ('Qualify source-reviewed House paper 9115820','qualify_house_paper_9115820.py'),
)
CUTOFF='2026-10-28T00:00:00Z'


def step(name):
    block=WORKFLOW.read_text(encoding='utf-8').split('      - name: '+name+'\n',1)[1].split(
        '\n      - name:',1)[0]
    condition=re.search(r'^        if: (.+)$',block,re.M)
    body=textwrap.dedent(block.split('        run: |\n',1)[1])
    return condition.group(1) if condition else None,body


def scheduled_fixed_body(name):
    condition,body=step(name)
    if condition is None:return body
    if condition!='inputs.materialize_saved_paper != true':
        raise ValueError('Unsupported fixed-writer workflow condition in this bounded scheduler')
    # Evaluate the actual workflow selection for both dispatch values. A
    # missing condition really executes the unmodified writer and fails saved
    # mode, rather than merely failing a string-presence assertion.
    return 'if [ "$MATERIALIZE_SAVED_PAPER" != "true" ]; then\n'+body+'\nfi\n'


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(encode(value))


class HouseSavedFixedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.shell=bash_binary()
        if self.shell is None:self.skipTest('Bash required for actual workflow steps')
        temporary=tempfile.TemporaryDirectory(prefix='house-saved-');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.workspace=self.root/'workspace';self.workspace.mkdir()
        self.data=paper_controls.paper_owner_fixture(self.root/'source')
        self.evidence=self.data['evidence_root'];self.review=self.data['review_root']
        metadata=self.data['metadata']
        self.relative=Path(str(metadata['filing_year']))/metadata['document_id']/(metadata['sha256']+'.json')
        for folder,key in (('identities','identity'),('extractions','legacy_extraction'),
                ('qualifications','legacy_qualification'),('paper_observations','observation'),
                ('paper_amount_headers','amount_header'),('paper_key_field_rereads','field_reread')):
            write(self.review/'house_clerk'/folder/self.relative,self.data[key])
        base=deepcopy(BASE);base['meta']['data_cutoff_at']=CUTOFF
        base['people']=[_person(self.data['identity'])]
        base['transactions']=deepcopy(self.data['legacy_qualification']['transactions'])
        self.base=base;self.candidate=self.review/'candidates/sources/house_clerk-current.json'
        write(self.candidate,base)
        self.state=self.root/'state'
        write(self.state/'status/house_clerk.json',{'status':'ok','run_at':CUTOFF,
            'filing_year':metadata['filing_year'],'counts':{'archived':1,'pending':0,'failed':0}})
        self.original={p:p.read_bytes() for p in self.evidence.rglob('*') if p.is_file()}
        self.calls=self.root/'python-calls.jsonl';self.shim=self.root/'python-passthrough.py'
        self.shim.write_text(
            'import json,os,subprocess,sys\nfrom pathlib import Path\n'
            'args=sys.argv[1:]\n'
            'with Path(os.environ["TEST_CALL_LOG"]).open("a",encoding="utf-8") as f:\n'
            ' f.write(json.dumps(args)+"\\n")\n'
            'repo=Path(os.environ["TEST_REPO"])\n'
            'args=[str(repo/a) if a.startswith(("backend/scripts/","docs/")) else a for a in args]\n'
            'os.environ["PYTHONPATH"]=str(repo/"backend/src")\n'
            'result=subprocess.run([sys.executable,*args])\n'
            'raise SystemExit(result.returncode)\n',encoding='utf-8')

    def execute(self,body,*,saved):
        script=('set -euo pipefail\n'
                'python() { "$REAL_PYTHON" "$TEST_SHIM" "$@"; }\n'
                'git() { echo "No Git/network operation in this test" >&2; return 90; }\n'+body)
        env=dict(os.environ,REAL_PYTHON=sys.executable,TEST_SHIM=str(self.shim),TEST_REPO=str(ROOT),
            TEST_CALL_LOG=str(self.calls),EVIDENCE_ROOT=str(self.evidence),REVIEW_ROOT=str(self.review),
            STATE_ROOT=str(self.state),MATERIALIZE_SAVED_PAPER='true' if saved else 'false',
            PAPER_DOCUMENT_ID=self.data['metadata']['document_id'],PARSER_COMMIT='a'*40,
            GITHUB_STEP_SUMMARY=str(self.root/'step-summary.md'),MSYS_NO_PATHCONV='1',PYTHONUTF8='1')
        return subprocess.run([self.shell,'-c',script],cwd=self.workspace,env=env,
            capture_output=True,text=True,encoding='utf-8')

    def test_saved_dispatch_skips_fixed_writers_and_reaches_real_bridge_queue_and_candidate(self):
        selection=step('Select unprocessed evidence')[1]
        selected=self.execute(selection,saved=True)
        self.assertEqual(selected.returncode,0,selected.stdout+selected.stderr)
        self.assertEqual((self.workspace/'.local/parse-batch.txt').read_bytes(),b'')
        bridge=step('Bridge bounded House paper observations into qualification')[1]
        fixed='\n'.join(scheduled_fixed_body(name) for name,_ in FIXED_STEPS)
        queue=step('Build qualification queue status')[1]
        candidate=step('Build and validate frontend candidate')[1].split(
            '# Persist the validated House projection',1)[0]
        completed=self.execute(bridge+'\n'+fixed+'\n'+queue+'\n'+candidate,saved=True)
        self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)
        calls=[json.loads(line) for line in self.calls.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(any('bridge-house-paper-batch' in call for call in calls))
        self.assertTrue(any('build-house-candidate' in call for call in calls))
        self.assertFalse(any(script in arg for _,script in FIXED_STEPS for call in calls for arg in call))
        actual=json.loads(self.candidate.read_bytes())
        expected=sorted([{**row,'owner':'Undisclosed'} for row in self.base['transactions']],key=lambda row:row['id'])
        self.assertEqual(actual['transactions'],expected)
        self.assertEqual(actual['people'],self.base['people'])
        qualification=json.loads((self.review/'house_clerk/qualifications'/self.relative).read_bytes())
        self.assertIn('source_bound_paper_owner_correction',qualification)
        self.assertTrue(all(p.read_bytes()==raw for p,raw in self.original.items()))

    def test_fresh_dispatch_still_invokes_each_original_fixed_writer_and_preserves_source_guards(self):
        # Invoke the selected original steps independently: each intentionally
        # incomplete fixed-source input fails its real source check. This is
        # eligibility/guard evidence, not a claim that a failing full job would
        # continue past its first failure or that fixed visual review is redone.
        (self.workspace/'.local').mkdir()
        qualification=self.review/'house_clerk/qualifications'/self.relative
        before={p:p.read_bytes() for p in (qualification,self.candidate)}
        for name,script in FIXED_STEPS:
            with self.subTest(step=name):
                process=self.execute(scheduled_fixed_body(name),saved=False)
                self.assertNotEqual(process.returncode,0)
                self.assertRegex(process.stdout+process.stderr,'not uniquely archived|not unique')
                calls=[json.loads(line) for line in self.calls.read_text(encoding='utf-8').splitlines()]
                self.assertTrue(any(call and call[0]=='backend/scripts/'+script for call in calls))
                self.assertTrue(all(p.read_bytes()==raw for p,raw in before.items()))
                self.assertTrue(all(p.read_bytes()==raw for p,raw in self.original.items()))


if __name__=='__main__':unittest.main()
