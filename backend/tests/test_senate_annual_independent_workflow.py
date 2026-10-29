"""Execute the workflow's real archived annual build/overlay Bash only.

The collection, checkout, install, commit and push steps are never executed.
Generated archived HTML supplies contract controls, not recognition gold.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

import test_senate_annual_independent as annual_controls
import test_senate_members as member_controls
from test_oge_annual_workflow import bash_binary
from unison_snapshot.senate_annual import DISCOVERY_SCHEMA, _json
from unison_snapshot.senate_members import build_roster


ROOT=Path(__file__).resolve().parents[2]
WORKFLOW=ROOT/'.github/workflows/senate.yml'
MODE='independent_part4b/v1'


def workflow_build_body():
    from test_senate_production_recovery import step_script
    body=step_script('senate.yml','Extract and qualify latest annual holdings')
    return body.split('# Keep the validated Senate source current',1)[0]



def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(_json(value))


def archived_fixture(root):
    evidence=root/'senate-annual-evidence';review=root/'senate-annual-review'
    roster_raw=member_controls.xml(member_controls.ADA_CA);roster=build_roster(roster_raw)
    roster_sha=roster['metadata']['sha256'];members=evidence/'senate_efd/members'
    members.mkdir(parents=True);(members/f'{roster_sha}.xml').write_bytes(roster_raw)
    roster_path=members/f'{roster_sha}.roster.json';write(roster_path,roster)
    raw=annual_controls.original();sha=hashlib.sha256(raw).hexdigest()
    document='11111111-1111-4111-8111-000000000001'
    report={'document_id':document,
        'document_url':f'https://efdsearch.senate.gov/search/view/annual/{document}/',
        'filer_name':'Ada Example','office':'Example, Ada (Senator)','report_year':2025,
        'amendment_number':0,'portal_listed_date':'2026-05-01','catalog_index':0}
    metadata={**report,'schema_version':'senate-efd-annual-archive/v1',
              'source_sha256':sha,'byte_length':len(raw)}
    folder=evidence/'senate_efd/annual/reports'/document;folder.mkdir(parents=True)
    (folder/f'{sha}.html').write_bytes(raw);write(folder/f'{sha}.metadata.json',metadata)
    write(evidence/'senate_efd/annual/catalog/current.json',{'schema_version':DISCOVERY_SCHEMA,
        'source_id':'senate_efd','catalog_record_count':1,'annual_report_count':1,
        'nonannual_count':0,'reports':[report]})
    write(review/'status/senate_efd.json',{'identity_roster_sha256':roster_sha})
    candidate=review/'candidates/sources/senate_efd-current.json';write(candidate,annual_controls.base())
    return {'root':root,'evidence':evidence,'review':review,'roster_path':roster_path,'candidate':candidate,
        'current':review/'senate_efd/annual/current.json',
        'audit':review/'senate_efd/annual/candidate-audit-current.json'}


class SenateAnnualIndependentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.shell=bash_binary()
        if self.shell is None:self.skipTest('Bash required for the actual workflow body')
        temporary=tempfile.TemporaryDirectory(prefix='annual-mode-');self.addCleanup(temporary.cleanup)
        self.data=archived_fixture(Path(temporary.name))
        self.original={p:p.read_bytes() for p in self.data['evidence'].rglob('*') if p.is_file()}

    def execute(self, *, independent=False, structured=False):
        data=self.data
        body=workflow_build_body()
        self.assertNotIn('git -C',body);self.assertNotIn(' collect ',body)
        script=('set -euo pipefail\n'
                'python() { "$REAL_PYTHON" "$@"; }\n'
                'git() { echo "Git publication is outside this test" >&2; return 90; }\n'+body)
        env=dict(os.environ,REAL_PYTHON=sys.executable,PYTHONPATH=str(ROOT/'backend/src'),
            RUNNER_TEMP=str(data['root']),ROSTER=str(data['roster_path']),
            EVIDENCE_ROOT=str(data['evidence']),REVIEW_ROOT=str(data['review']),
            STRUCTURED_ASSET_LABELS='true' if structured else 'false',
            INDEPENDENT_TRANSACTIONS='true' if independent else 'false',
            MSYS_NO_PATHCONV='1',PYTHONIOENCODING='utf-8')
        return subprocess.run([self.shell,'-c',script],cwd=ROOT,env=env,text=True,capture_output=True)

    def succeeded(self,**kwargs):
        process=self.execute(**kwargs)
        self.assertEqual(process.returncode,0,process.stdout+process.stderr)
        self.assertTrue(all(path.read_bytes()==raw for path,raw in self.original.items()))
        return (json.loads(self.data['candidate'].read_bytes()),json.loads(self.data['current'].read_bytes()))

    def test_default_first_activation_and_sticky_replay_use_existing_cli(self):
        content=WORKFLOW.read_text(encoding='utf-8')
        declaration=content.split('      independent_transactions:\n',1)[1].split('\npermissions:',1)[0]
        self.assertIn('type: boolean',declaration);self.assertIn('default: false',declaration)
        self.assertIn('INDEPENDENT_TRANSACTIONS: ${{ inputs.independent_transactions }}',content)
        for step in ('Discover and archive latest annual versions','Publish official originals and catalog'):
            block=content.split('      - name: '+step+'\n',1)[1].split('\n      - name:',1)[0]
            self.assertIn('if: inputs.collect_annual == true && inputs.structured_asset_labels != true && inputs.independent_transactions != true',block)
        default,current=self.succeeded()
        self.assertNotIn('transaction_mode',current)
        self.assertEqual(default['transactions'],[])
        self.assertTrue(list((self.data['review']/'senate_efd/annual/part4b').rglob('observation-v2.json')))
        activated,current=self.succeeded(independent=True)
        self.assertEqual(current['transaction_mode'],MODE)
        self.assertEqual(len(activated['transactions']),1)
        self.assertTrue(activated['transactions'][0]['id'].startswith('senate-annual-part4b:'))
        self.assertTrue(list((self.data['review']/'senate_efd/annual/part4b').rglob('observation-v3.json')))
        repeated,current=self.succeeded()
        self.assertEqual(current['transaction_mode'],MODE)
        self.assertEqual(repeated,activated)

    def test_structured_and_independent_first_start_and_sticky_modes_compose(self):
        candidate,current=self.succeeded(independent=True,structured=True)
        self.assertEqual(current['transaction_mode'],MODE)
        self.assertEqual(current['asset_label_mode'],'structured_v4')
        self.assertEqual(len(candidate['transactions']),1)
        self.assertTrue(candidate['reported_holdings'])
        repeated,current=self.succeeded()
        self.assertEqual(current['transaction_mode'],MODE)
        self.assertEqual(current['asset_label_mode'],'structured_v4')
        self.assertEqual(repeated,candidate)

    def test_invalid_saved_mode_stops_before_candidate_or_audit_changes(self):
        data=self.data;write(data['audit'],{'prior':'audit must remain byte-identical'})
        original={p:p.read_bytes() for p in (data['candidate'],data['audit'])}
        for mode in ({'asset_label_mode':'unsupported'}, {'transaction_mode':'unsupported'}):
            with self.subTest(mode=mode):
                write(data['current'],deepcopy(mode));before=data['current'].read_bytes()
                process=self.execute(independent=True,structured=True)
                self.assertNotEqual(process.returncode,0)
                self.assertIn('selection is unsupported',process.stdout+process.stderr)
                self.assertEqual(data['current'].read_bytes(),before)
                self.assertTrue(all(p.read_bytes()==raw for p,raw in original.items()))
                self.assertTrue(all(p.read_bytes()==raw for p,raw in self.original.items()))


if __name__=='__main__':unittest.main()
