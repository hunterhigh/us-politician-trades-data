"""Execute the rebuild's first candidate command with its declared proof deps."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap
import tomllib
import unittest
from unittest.mock import patch

import test_native_bichrome_consumption as bichrome
import test_native_partition_consumption as normal
import test_oge_annual_workflow as shell_fixture

ROOT=Path(__file__).resolve().parents[2]
WORKFLOW=ROOT/'.github/workflows/whitehouse-candidate-rebuild.yml'


class WhiteHouseRebuildSourceDependencyWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.normal=normal.NativePartitionConsumptionTests();self.normal.setUp()
        self.addCleanup(self.normal.doCleanups)

    def prepare(self,generic=False):
        content=bichrome.source_pdf('white_on_white')
        with patch.object(normal,'normal_pdf',return_value=content):
            root,metadata,base=self.normal.wh_inputs('white_on_white')
        self.normal.wh_extract_coverage(root,metadata,generic)
        extraction=json.loads(next((root/'review/whitehouse/extractions').rglob('*.json')).read_bytes())
        self.assertIn('native-bichrome',extraction['parser_version'])
        expected,_=self.normal.wh_candidate(root)
        self.assertEqual(len(expected['transactions']),2)
        self.assertEqual(next(r for r in expected['transactions'] if r['ticker']=='PRIOR'),base['transactions'][0])
        current=root/'review/candidates/sources/oge-current.json'
        current.parent.mkdir(parents=True,exist_ok=True);current.write_bytes((root/'base.json').read_bytes())
        disclosure=root/'review/candidates/disclosure-current.json'
        disclosure.write_bytes((root/'base.json').read_bytes())
        audit=root/'review/whitehouse/qualifications/candidate-current.json'
        audit.parent.mkdir(parents=True,exist_ok=True);audit.write_bytes(b'{"existing":"audit"}\n')
        runner=root/'runner temporary';runner.mkdir()
        return {'root':root,'metadata':metadata,'base':base,'expected':expected,'current':current,
                'disclosure':disclosure,'audit':audit,'runner':runner}

    def branch(self):
        content=WORKFLOW.read_text(encoding='utf-8')
        section=content.split('      - name: Rebuild complete OGE and unified review candidates',1)[1]
        run=section.split('        run: |\n',1)[1]
        return textwrap.dedent(run.split('          python -m unison_snapshot overlay-whitehouse-annual-candidate',1)[0])

    def declared_dependencies(self):
        content=WORKFLOW.read_text(encoding='utf-8')
        install=re.search(r'run: python -m pip install -e (?:"?backend(?:\[([^]]+)\])?"?)',content)
        self.assertIsNotNone(install)
        extras=(install.group(1) or '').split(',')
        metadata=tomllib.loads((ROOT/'backend/pyproject.toml').read_text(encoding='utf-8'))['project']
        requirements=list(metadata['dependencies'])
        for extra in filter(None,extras):requirements+=metadata['optional-dependencies'][extra]
        packages={re.match(r'[A-Za-z0-9_.-]+',requirement).group(0).casefold() for requirement in requirements}
        return packages,extras,requirements

    def run_branch(self,data,*,blocked=(),evidence=None):
        bash=shell_fixture.bash_binary()
        if bash is None:self.skipTest('Bash required for the actual rebuild branch')
        root=data['root'];guard=root/'dependency guard';guard.mkdir(exist_ok=True)
        log=root/'blocked-import.log'
        if log.exists():log.unlink()
        after=root/'after-first-candidate'
        if after.exists():after.unlink()
        (guard/'sitecustomize.py').write_text(
            'import sys,os\nfrom pathlib import Path\n'
            'class MissingWorkflowDependency:\n'
            ' def find_spec(self,fullname,path=None,target=None):\n'
            '  if fullname.split(".")[0] in set(filter(None,os.environ["BLOCKED_MODULES"].split(","))):\n'
            '   Path(os.environ["BLOCKED_IMPORT_LOG"]).write_text(fullname)\n'
            '   raise ModuleNotFoundError("workflow dependency intentionally unavailable: "+fullname)\n'
            'sys.meta_path.insert(0,MissingWorkflowDependency())\n',encoding='utf-8')
        script=('set -euo pipefail\n'
            'python() { PYTHONPATH="$TEST_GUARD$TEST_PATH_SEPARATOR$PYTHONPATH" "$REAL_PYTHON" "$@"; }\n'+
            self.branch()+'\nprintf completed > "$TEST_AFTER_FIRST"\n')
        env={**os.environ,'PYTHONPATH':str(ROOT/'backend/src'),'PYTHONUTF8':'1','GIT_NO_LAZY_FETCH':'1',
            'MSYS_NO_PATHCONV':'1','REAL_PYTHON':sys.executable,'TEST_GUARD':str(guard),
            'TEST_PATH_SEPARATOR':os.pathsep,'BLOCKED_MODULES':','.join(blocked),
            'BLOCKED_IMPORT_LOG':str(log),'TEST_AFTER_FIRST':str(after),
            'EVIDENCE_ROOT':str(evidence or root/'evidence'),'REVIEW_ROOT':str(root/'review'),
            'RUNNER_TEMP':str(data['runner'])}
        result=subprocess.run([bash,'-c',script],cwd=ROOT,env=env,capture_output=True,
                              text=True,encoding='utf-8',timeout=60)
        return result,after,log

    def outputs(self,data):
        return tuple(data[key].read_bytes() for key in ('current','audit','disclosure'))

    def assert_backup_and_input_conservation(self,data,before):
        self.assertEqual((data['runner']/'oge-candidate-before.json').read_bytes(),before[0])
        self.assertEqual((data['runner']/'disclosure-candidate-before.json').read_bytes(),before[2])
        self.assertEqual(data['disclosure'].read_bytes(),before[2])

    def test_declared_runner_deps_support_real_first_candidate_default_and_generic(self):
        packages,_,_=self.declared_dependencies()
        missing=[module for package,module in [('pdfplumber','pdfplumber'),('pymupdf','pymupdf')]
                 if package not in packages]
        for generic in (False,True):
            with self.subTest(generic=generic):
                data=self.prepare(generic);before=self.outputs(data)
                done,after,_=self.run_branch(data,blocked=missing)
                self.assertEqual(done.returncode,0,done.stderr+done.stdout)
                self.assertTrue(after.exists());self.assert_backup_and_input_conservation(data,before)
                candidate=json.loads(data['current'].read_bytes())
                self.assertEqual(candidate,data['expected'])
                self.normal.assert_reader(candidate)
                stable=self.outputs(data)
                again,_,_=self.run_branch(data,blocked=missing)
                self.assertEqual(again.returncode,0,again.stderr+again.stdout)
                self.assertTrue(json.loads(again.stdout)['idempotent'])
                self.assertEqual(self.outputs(data),stable)

    def test_missing_each_source_dependency_or_wrong_root_fails_before_writing_or_continuing(self):
        data=self.prepare();initial=self.outputs(data)
        # Test actual fresh replay first: a root-bound saved source cannot be
        # trusted merely because its package is absent in the job environment.
        for dependency in ('pdfplumber','pymupdf'):
            with self.subTest(dependency=dependency):
                done,after,log=self.run_branch(data,blocked=[dependency])
                self.assertNotEqual(done.returncode,0,done.stdout)
                self.assertFalse(after.exists());self.assertEqual(self.outputs(data),initial)
                self.assertTrue(log.exists());self.assertEqual(log.read_text().split('.')[0],dependency)
                self.assert_backup_and_input_conservation(data,initial)
        done,_,_=self.run_branch(data)
        self.assertEqual(done.returncode,0,done.stderr+done.stdout)
        stable=self.outputs(data)
        wrong=data['root']/'wrong source root';wrong.mkdir()
        for kwargs in ({'blocked':['pdfplumber']},{'blocked':['pymupdf']},{'evidence':wrong}):
            with self.subTest(kwargs=kwargs):
                done,after,_=self.run_branch(data,**kwargs)
                self.assertNotEqual(done.returncode,0,done.stdout)
                self.assertFalse(after.exists());self.assertEqual(self.outputs(data),stable)
                self.assert_backup_and_input_conservation(data,stable)

    def test_existing_extra_pins_and_later_fixed_release_gates_are_preserved(self):
        packages,extras,requirements=self.declared_dependencies()
        self.assertIn('native-source-proof',extras)
        self.assertTrue({'pdfplumber','pymupdf'}<=packages)
        self.assertTrue(any(re.fullmatch(r'PyMuPDF==[0-9.]+',r) for r in requirements))
        self.assertTrue(any(re.fullmatch(r'pdfplumber==[0-9.]+',r) for r in requirements))
        content=WORKFLOW.read_text(encoding='utf-8')
        self.assertIn("audit['promoted_transaction_count']",content)
        self.assertIn('verify-first-launch',content)
        self.assertNotIn('audit_trump_',content)
        self.assertNotIn('overlay-whitehouse-annual-candidate',self.branch())
