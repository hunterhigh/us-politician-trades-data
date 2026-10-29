import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from unison_snapshot.source_failures import record,persist
from unison_snapshot.commands import data_trees as trees
from test_data_trees import SourceTreeFixture


class SourceFailureTests(SourceTreeFixture,unittest.TestCase):
    def seed(self):
        control=self.prepare()
        self.state=Path(control['trees']['state']['root'])
        self.prior={'schema_version':'house-source-run/v1','source_id':'house_clerk',
                    'status':'ok','run_at':'2026-10-28T00:00:00Z','counts':{'archived':2}}
        self.write(self.state/'status/house_clerk.json',json.dumps(self.prior))
        trees.publish(self.workspace,('state',),'known successful collection')
        return control

    def event(self,year=None):
        directory=self.root/'failure receipts'
        with patch.dict(os.environ,{'UNISON_INTEGRITY_SCOPE':'house_clerk',
            'UNISON_SOURCE_FAILURE_DIRECTORY':str(directory),'GITHUB_RUN_ID':'123',
            'GITHUB_REPOSITORY':'owner/repository','GITHUB_SHA':self.code}):
            record('discover-house-index',HTTPError('https://example.invalid/?private=not-for-logs',403,'secret',{},None),filing_year=year)
        self.assertNotIn('private',next(directory.glob('*.json')).read_text())
        return directory

    def test_failed_collection_preserves_success_and_never_commits_dirty_checkpoint_or_candidate(self):
        control=self.seed();directory=self.event()
        self.write(self.state/'checkpoints/unsafe.json','unpublished partial checkpoint')
        review=Path(control['trees']['review']['root'])
        self.write(review/'candidates/sources/house_clerk-current.json','unqualified partial candidate')
        persist(self.workspace,directory)
        value=json.loads(trees.git(self.repo,'show','origin/state:status/house_clerk.json'))
        self.assertEqual(value['status'],'blocked')
        self.assertEqual(value['last_successful_collection_at'],self.prior['run_at'])
        self.assertEqual(value['counts'],self.prior['counts'])
        result=subprocess.run(['git','-C',str(self.repo),'cat-file','-e','origin/state:checkpoints/unsafe.json'],capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(trees.git(review,'status','--porcelain').splitlines()[0][:2],'??')
        before=trees.git(self.repo,'rev-parse','origin/state')
        persist(self.workspace,directory)
        self.assertEqual(trees.git(self.repo,'rev-parse','origin/state'),before)

    def test_fixed_historical_failure_cannot_mark_the_current_source_blocked(self):
        self.seed();persist(self.workspace,self.event(2025))
        self.assertEqual(json.loads(trees.git(self.repo,'show','origin/state:status/house_clerk.json')),self.prior)
        historical=json.loads(trees.git(self.repo,'show','origin/state:status/house_backfill_2025.json'))
        self.assertEqual(historical['status'],'blocked')


if __name__=='__main__':unittest.main()
