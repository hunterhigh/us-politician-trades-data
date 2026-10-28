"""The annual observation workflow produces the controlled runtime's layout."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

from test_oge_annual_workflow import bash_binary
import test_oge_annual_saved_scan_runtime as scan_fixtures
from test_oge_annual_candidate import base
from unison_snapshot.oge_annual_runtime import controlled_scan_output_path

ROOT=Path(__file__).resolve().parents[2]
WORKFLOW=ROOT/'.github/workflows/oge-annual-review.yml'
DOCUMENT='1462ab8c5542e5e7852589b0007899fc'
SOURCE='2d8f441b87c97812d2f7ab298657f834b636a14b78594b0cddffbefd8fa1de28'


def workflow_step(name):
    content=WORKFLOW.read_text(encoding='utf-8')
    step=content.split('      - name: '+name+'\n',1)[1].split('\n      - name:',1)[0]
    return textwrap.dedent(step.split('        run: |\n',1)[1])


class AnnualObservationWorkflowTests(unittest.TestCase):
    def workflow_branch(self, *, fail=False):
        shell=bash_binary()
        if shell is None:self.skipTest('Bash is required to execute the workflow steps')
        observe=workflow_step('Observe fixed Harris scanned annual sections without promotion')
        publish=workflow_step('Publish annual extraction audits').replace('${{ github.run_id }}','test-run')
        with tempfile.TemporaryDirectory()as directory:
            folder=Path(directory);runtime=folder/'runtime root';review=runtime/'oge-annual-review'
            (review/'oge/annual/section_observations').mkdir(parents=True)
            prior=review/'oge/annual/scan_section_observations/prior.json'
            prior.parent.mkdir();prior.write_bytes(b'prior saved observation')
            candidate=review/'candidate.json';candidate.write_bytes(b'prior candidate')
            record=folder/'record.py';log=folder/'calls.jsonl'
            record.write_text("import json,os,sys\nfrom pathlib import Path\n"
                "with Path(os.environ['TEST_CALL_LOG']).open('a',encoding='utf-8') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
                "if sys.argv[1]=='python' and os.environ['TEST_FAIL_WRITER']=='1': sys.exit(9)\n"
                "if sys.argv[1]=='git' and '--quiet' in sys.argv: sys.exit(1)\n",encoding='utf-8')
            script=('set -euo pipefail\n'
                'python() { "$REAL_PYTHON" "$TEST_RECORDER" python "$@"; }\n'
                'git() { "$REAL_PYTHON" "$TEST_RECORDER" git "$@"; }\n'+observe+'\n'+publish)
            env=dict(os.environ,REAL_PYTHON=sys.executable,TEST_RECORDER=str(record),
                TEST_CALL_LOG=str(log),TEST_FAIL_WRITER='1'if fail else'0',
                RUNNER_TEMP=runtime.as_posix(),GITHUB_RUN_ID='test-run',MSYS_NO_PATHCONV='1')
            result=subprocess.run([shell,'-c',script],cwd=ROOT,env=env,capture_output=True,text=True)
            calls=[json.loads(line)for line in log.read_text(encoding='utf-8').splitlines()]if log.exists()else[]
            self.assertEqual(prior.read_bytes(),b'prior saved observation')
            self.assertEqual(candidate.read_bytes(),b'prior candidate')
        return result,calls

    def test_actual_bash_uses_standard_writer_and_publishes_its_directory(self):
        result,calls=self.workflow_branch()
        self.assertEqual(result.returncode,0,result.stderr)
        writer=calls[0]
        self.assertEqual(writer[:2],['python','backend/src/unison_snapshot/commands/observe_oge_annual_scan_sections.py'])
        self.assertNotIn('--output',writer)
        self.assertEqual(writer[writer.index('--document-id')+1],DOCUMENT)
        self.assertEqual(writer[writer.index('--source-sha256')+1],SOURCE)
        review=writer[writer.index('--review-root')+1]
        self.assertIn('runtime root',review)
        self.assertTrue(review.endswith('/oge-annual-review'))
        pdf=writer[writer.index('--pdf')+1]
        self.assertTrue(pdf.endswith(f'/oge-annual-evidence/oge/annual/reports/{DOCUMENT}/{SOURCE}.pdf'))
        self.assertTrue(any(call[:4]==['git','-C',review,'add'] and
                            call[4:]==['oge/annual/section_observations']for call in calls))
        self.assertFalse(any('oge/annual/scan_section_observations'in call for call in calls))
        self.assertTrue(any('commit'in call for call in calls))

    def test_actual_bash_writer_failure_stops_before_any_git_publication(self):
        result,calls=self.workflow_branch(fail=True)
        self.assertEqual(result.returncode,9,result.stderr)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][:2],['python','backend/src/unison_snapshot/commands/observe_oge_annual_scan_sections.py'])

    def test_normal_writer_prepares_and_overlays_without_moving_legacy_observations(self):
        case=scan_fixtures.SavedScanRuntimeTests();case.setUp();self.addCleanup(case.doCleanups)
        case.seed()  # Generated PDF and a fixed prior cover observation; no OCR.
        standard=controlled_scan_output_path(case.review,'section_observation',scan_fixtures.DOC,case.sha)
        legacy=case.review/f'oge/annual/scan_section_observations/{scan_fixtures.DOC}/{case.sha}/sections-v1.json'
        arguments=['--pdf',case.pdf,'--source-sha256',case.sha,'--document-id',scan_fixtures.DOC]
        result=case.cli('observe-oge-annual-scan-sections',[*arguments,'--output',legacy])
        self.assertEqual(result.returncode,0,result.stderr)
        legacy_raw=legacy.read_bytes();self.assertEqual(legacy_raw,standard.read_bytes())
        standard.unlink();case.input.write_bytes(b'prior runtime input')
        result=case.prepare()
        self.assertNotEqual(result.returncode,0)
        self.assertIn(b'lacks its required cover or section inventory',result.stderr)
        self.assertEqual(case.input.read_bytes(),b'prior runtime input')
        result=case.cli('observe-oge-annual-scan-sections',[*arguments,'--review-root',case.review])
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(standard.read_bytes(),legacy_raw)
        result=case.prepare();self.assertEqual(result.returncode,0,result.stderr)
        package=json.loads(case.input.read_bytes())
        paths=[item['path']for item in package['controlled_annual_runtime']['files']]
        self.assertIn(standard.relative_to(case.review).as_posix(),paths)
        self.assertNotIn(legacy.relative_to(case.review).as_posix(),paths)
        case.write(case.candidate,base());result=case.overlay()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(case.candidate.read_bytes())['transactions'],base()['transactions'])
        self.assertEqual(legacy.read_bytes(),legacy_raw)
        before=standard.read_bytes()
        wrong=['--pdf',case.pdf,'--source-sha256','0'*64,'--document-id',scan_fixtures.DOC,
               '--output',standard]
        result=case.cli('observe-oge-annual-scan-sections',wrong)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(standard.read_bytes(),before)
        self.assertEqual(legacy.read_bytes(),legacy_raw)
