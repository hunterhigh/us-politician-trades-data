"""One House run keeps collection, review and historical waterlines separate."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_oge_annual_workflow import bash_binary
from test_house_saved_fixed_workflow import step

ROOT=Path(__file__).resolve().parents[2]


class HouseSourceWorkflowTests(unittest.TestCase):
    def test_one_locked_workflow_persists_evidence_before_state_and_qualification(self):
        text=(ROOT/'.github/workflows/house.yml').read_text(encoding='utf-8')
        self.assertIn('group: disclosure-source-writer',text)
        self.assertIn('uses: ./.github/actions/data-trees',text)
        names=('Publish immutable evidence first','Save archive checkpoint and collection state',
               'Persist collection state after evidence','Select unprocessed evidence',
               'Build and validate frontend candidate')
        indexes=[text.index('- name: '+name) for name in names]
        self.assertEqual(indexes,sorted(indexes))
        self.assertNotIn('workflow_run:',text)
        self.assertNotIn('download-artifact',text)
        self.assertIn('drain-house-ptr',text)
        self.assertNotIn('HOUSE_ARCHIVE_BATCH_SIZE',text)
        self.assertIn('echo "assembly_status=$assembly_status" >> "$GITHUB_OUTPUT"', text)
        self.assertIn("steps.candidate.outputs.assembly_status == 'ready'", text)
        self.assertNotIn('9115820',text);self.assertNotIn('9116141',text)

    def test_historical_checkpoint_cannot_advance_the_current_source_waterline(self):
        shell=bash_binary()
        if shell is None:self.skipTest('Bash required for actual workflow copy step')
        body=step('Save archive checkpoint and collection state')[1]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);state=root/'state';(state/'status').mkdir(parents=True);local=root/'.local';local.mkdir()
            current=state/'status/house_clerk.json';last=state/'status/last-run.json'
            current.write_bytes(b'current collection');last.write_bytes(b'current last run')
            (local/'checkpoint.json').write_bytes(b'historical checkpoint');(local/'last-run.json').write_bytes(b'historical collection')
            result=subprocess.run([shell,'-e','-c',body],cwd=root,
                env={**os.environ,'STATE_ROOT':str(state),'YEAR':'2025','MSYS_NO_PATHCONV':'1'},capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(current.read_bytes(),b'current collection');self.assertEqual(last.read_bytes(),b'current last run')
            self.assertEqual((state/'status/house_backfill_2025.json').read_bytes(),b'historical collection')
            self.assertEqual((state/'checkpoints/house/2025.json').read_bytes(),b'historical checkpoint')


if __name__=='__main__':unittest.main()
