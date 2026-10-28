"""Execute only the local saved-replay Bash and real normal consumer CLIs.

Checkout, fetch, installation and publication steps are deliberately not run.
The generated source is an existing transport fixture, not recognition gold.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

from tests.test_oge_annual_workflow import bash_binary
from tests.test_senate_account_correction import fixture
from tests import test_senate_candidate as candidates
from unison_snapshot.senate_candidate import build_senate_candidate
from unison_snapshot.senate_reports import PARSER_VERSION, inspect_paper_entrypoint


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT/'.github/workflows/senate-paper-pages.yml'
SELECTED = '11111111-1111-4111-8111-111111111111'
UNSELECTED = '22222222-2222-4222-8222-222222222222'


class SenateSavedWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell = bash_binary()
        if cls.shell is None:
            raise unittest.SkipTest('Bash is needed to execute the actual workflow steps')
        cls.source = fixture()
        cls.code_temporary = tempfile.TemporaryDirectory(prefix='sw-code-')
        cls.addClassCleanup(cls.code_temporary.cleanup)
        cls.code = Path(cls.code_temporary.name)
        # Real modules and pinned public processors/renderers run in a short
        # workspace, never overwriting the checkout's ingest/current.json.
        for relative in ('backend/src', 'frontend-contract-v2', 'frontend-contract-v3',
                         'review-input/us-politician-trades-watch/scripts'):
            shutil.copytree(ROOT/relative, cls.code/relative, ignore=shutil.ignore_patterns('__pycache__'))
        target = cls.code/'backend/scripts/prepare_senate_paper_runtime.py'
        target.parent.mkdir(parents=True)
        shutil.copy2(ROOT/'backend/scripts/prepare_senate_paper_runtime.py', target)
        cls.write(cls.code/'ingest/current.json', candidates.BASE)

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding='utf-8')

    @staticmethod
    def block(name):
        content = WORKFLOW.read_text(encoding='utf-8')
        step = content.split('      - name: '+name+'\n', 1)[1]
        return textwrap.dedent(step.split('        run: |\n', 1)[1].split('\n      - ', 1)[0])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sw-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.review,self.evidence,self.state = [self.root/name for name in ('r','e','s')]
        self.runtime = self.root/'runtime'; self.runtime.mkdir()
        self.calls = self.root/'python-calls.txt'

    def run_blocks(self, names, *, documents=SELECTED):
        # The wrapper only selects the actual interpreter and records argv;
        # every Python script, heredoc and CLI executes without substitution.
        script = ('set -euo pipefail\n'
                  'python() { printf "%s\\n" "$*" >> "$PYTHON_CALLS"; "$REAL_PYTHON" "$@"; }\n' +
                  '\n'.join(self.block(name) for name in names))
        return subprocess.run([self.shell,'-c',script],cwd=self.code,
            env={**os.environ,'REAL_PYTHON':sys.executable,'PYTHON_CALLS':str(self.calls),
                 'PYTHONPATH':str(self.code/'backend/src'),'PYTHONIOENCODING':'utf-8',
                 'MSYS_NO_PATHCONV':'1','SAVED_PAPER_DOCUMENT_IDS':documents,
                 'REVIEW_ROOT':str(self.review),'EVIDENCE_ROOT':str(self.evidence),
                 'STATE_ROOT':str(self.state),'RUNNER_TEMP':str(self.runtime)},
            capture_output=True,text=True,encoding='utf-8')

    def ready_inputs(self):
        _,_,review,evidence = deepcopy(self.source)
        for root,files in ((self.review,review),(self.evidence,evidence)):
            for relative,raw in files.items():
                path=root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        metadata_path = next(self.evidence.glob('senate_efd/reports/*/*.metadata.json'))
        metadata = json.loads(metadata_path.read_bytes())
        viewer = metadata_path.with_name(metadata['sha256']+'.html')
        self.write(self.review/f'senate_efd/paper_inspections/{SELECTED}/{metadata["sha256"]}/{PARSER_VERSION}.json',
                   inspect_paper_entrypoint(metadata,viewer.read_bytes()))
        selected_path = next(self.review.glob('senate_efd/extractions/*/*/*.json'))
        selected = json.loads(selected_path.read_bytes())
        other = candidates.extraction(UNSELECTED,parser_version=PARSER_VERSION)
        # Existing electronic report is deliberately outside the saved subset.
        self.unselected_path = self.review/f'senate_efd/extractions/{UNSELECTED}/{other["source_sha256"]}/{PARSER_VERSION}.json'
        self.write(self.unselected_path,other)
        scratch = self.root/'accounting'
        source_state = candidates.SenateCandidateTests().fixture(scratch,
            [candidates.identity(SELECTED),candidates.identity(UNSELECTED)],[selected,other])
        status = json.loads((scratch/'status/senate_efd.json').read_bytes())
        status.update(json.loads((self.review/'status/senate_efd.json').read_bytes()))
        self.write(self.review/'status/senate_efd.json',status)
        catalog = status['catalog_sha256']
        source_state['catalog']['sha256'] = catalog
        self.write(self.state/'status/senate_efd.json',source_state)
        identities_path = next((self.review/'senate_efd/identities'/catalog).glob('*.json'))
        identities = json.loads(identities_path.read_bytes())
        identities.update(report_count=2,identities=[candidates.identity(SELECTED),candidates.identity(UNSELECTED)])
        self.write(identities_path,identities)
        discovery_path = self.review/f'senate_efd/discoveries/{catalog}.json'
        discovery = json.loads(discovery_path.read_bytes())
        discovery['reports'].append({'document_id':UNSELECTED,'source_id':'senate_efd',
            'access_method':'electronic_ptr','document_url':other['source_url'],
            'filer_name':candidates.identity(UNSELECTED)['filer_name']})
        self.write(discovery_path,discovery)
        return source_state

    def protected(self):
        return {(root.name,path.relative_to(root).as_posix()): hashlib.sha256(path.read_bytes()).hexdigest()
                for root in (self.review,self.evidence,self.state) for path in root.rglob('*') if path.is_file()}

    def assert_originals_unchanged(self, original):
        current = self.protected()
        self.assertEqual({key:current[key] for key in original},original)
        added = set(current)-set(original)
        self.assertTrue(all(root=='r' and (path.startswith('candidates/') or
            path.startswith('senate_efd/qualifications/') or path=='status/disclosure_cutoff.json')
            for root,path in added),added)

    def test_actual_saved_bash_reaches_both_candidates_and_keeps_unselected_inputs(self):
        state = self.ready_inputs()
        original = self.protected()
        expected,_ = build_senate_candidate(self.review,state,deepcopy(candidates.BASE),evidence_root=self.evidence)
        self.assertEqual(len(expected['transactions']),2)
        result = self.run_blocks(['Validate saved replay selection',
            'Prepare and consume selected saved paper inputs','Build candidates from existing source state'])
        self.assertEqual(result.returncode,0,result.stderr)
        source = json.loads((self.review/'candidates/sources/senate_efd-current.json').read_bytes())
        unified = json.loads((self.review/'candidates/disclosure-current.json').read_bytes())
        self.assertEqual(source['transactions'],expected['transactions'])
        self.assertEqual(source['people'],expected['people'])
        self.assertEqual(unified['transactions'],expected['transactions'])
        self.assertEqual(unified['reported_holdings'],expected['reported_holdings'])
        batch = json.loads((self.code/'.local/senate-saved-extraction-batch.json').read_bytes())
        # The extraction runner's catalog count includes paper reports only;
        # the second, electronic report stays in the full candidate unchanged.
        self.assertEqual((batch['report_count'],batch['catalog_report_count']),(1,1))
        self.assertEqual(batch['selected_document_ids'],[SELECTED])
        self.assert_originals_unchanged(original)
        calls = self.calls.read_text(encoding='utf-8')
        self.assertIn('prepare_senate_paper_runtime.py',calls)
        self.assertIn('--tesseract __OCR_DISABLED__',calls)
        self.assertIn('build-senate-candidate',calls)
        self.assertIn('build-disclosure-candidate',calls)
        self.assertIn('--harmonize-cutoffs',calls)

    def test_invalid_csv_and_disabled_selection_fail_before_any_reader(self):
        for value in ('',SELECTED+',',','+SELECTED,SELECTED+','+SELECTED,'AAAAAAAA-1111-4111-8111-111111111111',
                      '$(echo unsafe)',','.join(f'{index:08x}-1111-4111-8111-111111111111' for index in range(13))):
            with self.subTest(value=value):
                result = self.run_blocks(['Validate saved replay selection'],documents=value)
                self.assertNotEqual(result.returncode,0)
        result = self.run_blocks(['Validate fresh mode selection'],documents=SELECTED)
        self.assertEqual(result.returncode,2)
        result = self.run_blocks(['Validate fresh mode selection'],documents='')
        self.assertEqual(result.returncode,0,result.stderr)
        calls = self.calls.read_text(encoding='utf-8')
        self.assertNotIn('prepare_senate_paper_runtime.py',calls)
        self.assertNotIn('unison_snapshot',calls)

    def test_unready_existing_state_is_rejected_without_touching_candidates(self):
        self.ready_inputs()
        prepared = self.run_blocks(['Validate saved replay selection','Prepare and consume selected saved paper inputs'])
        self.assertEqual(prepared.returncode,0,prepared.stderr)
        path = self.state/'status/senate_efd.json'
        state = json.loads(path.read_bytes());state.update(collection_enabled=False,status='disabled')
        self.write(path,state)
        targets = [self.review/name for name in ('candidates/sources/senate_efd-current.json',
            'candidates/disclosure-current.json','senate_efd/qualifications/current.json')]
        for target in targets:
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'candidate sentinel')
        original = self.protected()
        result = self.run_blocks(['Build candidates from existing source state'])
        self.assertNotEqual(result.returncode,0)
        self.assertIn('not ready for a candidate',result.stderr)
        for target in targets:self.assertEqual(target.read_bytes(),b'candidate sentinel')
        self.assert_originals_unchanged(original)

    def test_default_and_saved_jobs_are_explicitly_exclusive(self):
        content = WORKFLOW.read_text(encoding='utf-8')
        fresh,saved = content.split('  archive-pages:\n',1)[1].split('  replay-saved:\n',1)
        self.assertIn('if: ${{ !inputs.replay_saved_paper }}',fresh)
        self.assertIn('if: ${{ inputs.replay_saved_paper }}',saved)
        self.assertIn('type: boolean\n        default: false',content)
        self.assertLess(fresh.index('Validate fresh mode selection'),fresh.index('actions/checkout'))
        self.assertLess(saved.index('Validate saved replay selection'),saved.index('actions/checkout'))
        self.assertIn('group: disclosure-source-writer',fresh)
        self.assertIn('group: disclosure-source-writer',saved)
        for name in ('Validate saved replay selection','Prepare and consume selected saved paper inputs',
                     'Build candidates from existing source state'):
            block = self.block(name)
            for forbidden in ('git fetch','git push','pip install','apt-get','archive-senate-paper-pages',
                              'correct-reviewed-senate-paper'):
                self.assertNotIn(forbidden,block)
        for forbidden in ('archive-senate-paper-pages','senate-efd-gate','apt-get','tesseract-ocr',
                          'correct-reviewed-senate-paper'):
            self.assertNotIn(forbidden,saved)


if __name__=='__main__':unittest.main()
