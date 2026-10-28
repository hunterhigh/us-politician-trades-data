"""Source durability and stale-peer deferral use real CLI assembly and Git remotes."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.codec import encode
from unison_snapshot.commands import assemble_source as assembly
from unison_snapshot.commands import data_trees as trees
from test_data_trees import SourceTreeFixture
from test_disclosure_candidate import candidate

ROOT=Path(__file__).resolve().parents[2]


class AssembleSourceTests(SourceTreeFixture, unittest.TestCase):
    def seed(self, cutoff):
        prepared=self.prepare(('review',));review=Path(prepared['trees']['review']['root'])
        for source,date in [('house_clerk','2026-10-27T00:00:00Z'),('senate_efd',cutoff)]:
            value=candidate(source,source+':person',source+':fact')
            value['transactions'][0]['source_url']='https://'+{
                'house_clerk':'disclosures-clerk.house.gov','senate_efd':'efdsearch.senate.gov'}[source]+'/test-fixture'
            value['meta']['data_cutoff_at']=date;value['source_health'][0]['data_cutoff_at']=date
            self.write(review/f'candidates/sources/{source}-current.json',encode(value).decode())
        # Seed the peer as an already published projection, then prepare a fresh control.
        trees.git(review,'add','--all');trees.git(review,'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','prior peer')
        trees.git(review,'push','origin','HEAD:refs/heads/review')
        control=json.loads((self.workspace/'trees.json').read_bytes())
        control['trees']['review']['base']=trees.git(review,'rev-parse','HEAD')
        (self.workspace/'trees.json').write_bytes(encode(control))
        for relative in ['candidates/disclosure-current.json','status/disclosure_cutoff.json','status/disclosure_candidate.json']:
            self.write(review/relative,'{"prior_complete":true}')
        return review

    def invoke(self):
        # Child commands execute the actual normal entry, including rendering.
        with patch.dict(os.environ,{'PYTHONPATH':str(ROOT/'backend/src'),
            'GITHUB_OUTPUT':str(self.root/'github-output'),'GITHUB_STEP_SUMMARY':str(self.root/'summary')}):
            return assembly.assemble(self.workspace,'house_clerk',ROOT/'backend/data/empty-disclosure.json',
                self.root/'report.html',self.code)

    def test_deferred_peer_retains_unified_bytes_but_saves_the_valid_source(self):
        review=self.seed('2026-10-23T00:00:00Z')
        prior={relative:(review/relative).read_bytes() for relative in [
            'candidates/disclosure-current.json','status/disclosure_cutoff.json','status/disclosure_candidate.json']}
        self.assertEqual(self.invoke(),'deferred')
        for relative,raw in prior.items():self.assertEqual((review/relative).read_bytes(),raw)
        self.assertEqual(json.loads((review/'status/disclosure_assembly.json').read_bytes())['status'],'deferred')
        remote=trees.git(self.repo,'--git-dir',self.remote,'show','review:candidates/sources/house_clerk-current.json')
        self.assertEqual(json.loads(remote)['transactions'][0]['id'],'house_clerk:fact')
        self.assertEqual(trees.git(self.repo,'rev-parse','HEAD'),self.code)
        self.assertFalse((self.root/'report.html').exists())

    def test_readiness_failure_keeps_prior_unified_remote_after_saving_valid_source(self):
        import subprocess
        review=self.seed('2026-10-27T00:00:00Z')
        with patch.dict(os.environ,{'PYTHONPATH':str(ROOT/'backend/src'),
            'GITHUB_OUTPUT':str(self.root/'output'),'GITHUB_STEP_SUMMARY':str(self.root/'summary')}):
            with self.assertRaises(subprocess.CalledProcessError):
                assembly.assemble(self.workspace,'house_clerk',ROOT/'backend/data/empty-disclosure.json',
                    None,self.code,verify_readiness=True)
        saved=trees.git(self.repo,'--git-dir',self.remote,'show','review:candidates/disclosure-current.json')
        self.assertEqual(json.loads(saved),{'prior_complete':True})
        source=trees.git(self.repo,'--git-dir',self.remote,'show','review:candidates/sources/house_clerk-current.json')
        self.assertEqual(json.loads(source)['transactions'][0]['id'],'house_clerk:fact')

    def test_ready_assembly_is_persisted_and_invalid_source_cannot_advance_remote(self):
        review=self.seed('2026-10-27T00:00:00Z')
        self.assertEqual(self.invoke(),'ready')
        candidate_bytes=(review/'candidates/disclosure-current.json').read_bytes()
        run=json.loads((review/'status/disclosure_candidate.json').read_bytes())
        self.assertEqual(run['transaction_count'],2)
        self.assertEqual(run['included_sources'],['house_clerk','senate_efd'])
        self.assertTrue((self.root/'report.html').is_file())
        prior=trees.git(self.repo,'ls-remote','origin','refs/heads/review').split()[0]
        source=review/'candidates/sources/house_clerk-current.json'
        value=json.loads(source.read_bytes());value['transactions'][0]['person_id']='missing'
        source.write_bytes(encode(value))
        with self.assertRaises(ValueError):self.invoke()
        self.assertEqual(trees.git(self.repo,'ls-remote','origin','refs/heads/review').split()[0],prior)
        self.assertEqual((review/'candidates/disclosure-current.json').read_bytes(),candidate_bytes)


if __name__=='__main__':unittest.main()
