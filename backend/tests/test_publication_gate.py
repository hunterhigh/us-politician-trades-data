from copy import deepcopy
from datetime import datetime,timezone
import unittest
from unison_snapshot.publication_gate import evaluate,SOURCES


class PublicationGateTests(unittest.TestCase):
    def inputs(self):
        stamp='2026-10-29T00:00:00Z'
        candidates={s:{'meta':{'is_demo':False,'data_cutoff_at':stamp},
            'source_health':[{'source_id':s,'status':'partial','data_cutoff_at':stamp}]} for s in SOURCES}
        statuses={s:{'status':'ok','run_at':stamp,'catalog':{'retrieved_at':stamp}} for s in SOURCES}
        return candidates,statuses

    def test_fresh_partial_sources_and_transient_retry_are_publishable(self):
        candidates,statuses=self.inputs();statuses['senate_efd']['status']='unavailable'
        now=datetime(2026,10,29,8,tzinfo=timezone.utc)
        self.assertTrue(evaluate(candidates,statuses,now=now)['ready'])
        statuses['senate_efd']['status']='blocked'
        self.assertTrue(evaluate(candidates,statuses,now=now)['ready'])

    def test_identically_old_candidates_cannot_hide_behind_fresh_collection_or_mutual_alignment(self):
        candidates,statuses=self.inputs()
        for source in SOURCES:
            candidates[source]['meta']['data_cutoff_at']='2026-10-23T00:00:00Z'
            candidates[source]['source_health'][0]['data_cutoff_at']='2026-10-23T00:00:00Z'
        result=evaluate(candidates,statuses,now=datetime(2026,10,29,8,tzinfo=timezone.utc))
        self.assertEqual({row['source'] for row in result['blocking_sources']},{'house_clerk','oge'})
        self.assertTrue(all(row['reason']=='qualified_stale' for row in result['blocking_sources']))

    def test_stale_collection_cannot_be_hidden_by_new_qualification_and_recovery_requires_both(self):
        candidates,statuses=self.inputs();statuses['oge']['catalog']['retrieved_at']='2026-10-23T00:00:00Z'
        now=datetime(2026,10,29,8,tzinfo=timezone.utc)
        self.assertFalse(evaluate(candidates,statuses,now=now)['ready'])
        statuses['oge']['catalog']['retrieved_at']='2026-10-29T00:00:00Z'
        self.assertTrue(evaluate(candidates,statuses,now=now)['ready'])

    def test_unbound_or_future_waterlines_are_invariants(self):
        candidates,statuses=self.inputs();now=datetime(2026,10,29,8,tzinfo=timezone.utc)
        candidates['house_clerk']['source_health'][0]['data_cutoff_at']='2026-10-28T00:00:00Z'
        with self.assertRaisesRegex(ValueError,'not bound'):evaluate(candidates,statuses,now=now)
        candidates,statuses=self.inputs();statuses['oge']['catalog']['retrieved_at']='2027-01-01T00:00:00Z'
        with self.assertRaisesRegex(ValueError,'future'):evaluate(candidates,statuses,now=now)

    def test_fresh_successful_observation_is_independent_of_immutable_archive_creation(self):
        candidates,statuses=self.inputs()
        statuses['oge']['catalog']['retrieved_at']='2026-10-01T00:00:00Z'
        statuses['oge']['last_successful_collection_at']='2026-10-29T00:00:00Z'
        self.assertTrue(evaluate(candidates,statuses,now=datetime(2026,10,29,8,tzinfo=timezone.utc))['ready'])


if __name__=='__main__':unittest.main()
