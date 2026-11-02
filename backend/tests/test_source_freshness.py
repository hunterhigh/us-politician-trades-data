from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from unison_snapshot.builder import build
from unison_snapshot.commands.senate_source_state import collection_start
from unison_snapshot.legacy import load
from unison_snapshot.production_health import evaluate as health, reconcile_issues
from unison_snapshot.publication_gate import evaluate as gate
from unison_snapshot.public_repo import PublicSnapshotRepository
from unison_snapshot.public_repo import PublicSnapshotError
from unison_snapshot.codec import encode
from unison_snapshot.source_freshness import senate_max_stale_days
from test_public_repo import production_payload, FakeTransport


def freshness_fixture(stale=True):
    """Contract-only data, built and read through the ordinary public consumer."""
    payload = production_payload()
    payload['meta']['data_cutoff_at'] = '2026-09-26T00:00:00Z'
    payload['meta']['source_cutoff_policy'] = 'per_source/v1'
    for row in payload['source_health']:
        if row['source_id'] == 'house_clerk':
            row.update(data_cutoff_at=payload['meta']['data_cutoff_at'], stale=False)
        elif row['source_id'] == 'senate_efd':
            row.update(data_cutoff_at='2026-09-18T00:00:00Z' if stale else payload['meta']['data_cutoff_at'],
                status='stale' if stale else 'ok', stale=stale, unavailable=stale, max_stale_days=30)
    return payload, build(payload, generated_at='2026-09-26T00:00:00Z', allow_production=True)


class SourceFreshnessTests(unittest.TestCase):
    def inputs(self):
        from test_publication_gate import PublicationGateTests
        return PublicationGateTests().inputs()

    def test_senate_exact_thirty_days_and_one_second_over_limit(self):
        candidates, statuses = self.inputs()
        stamp = '2026-09-29T08:00:00Z'
        candidates['senate_efd']['meta']['data_cutoff_at'] = stamp
        candidates['senate_efd']['source_health'][0]['data_cutoff_at'] = stamp
        statuses['senate_efd'].update(status='blocked', catalog={'retrieved_at':stamp})
        now = datetime(2026,10,29,8,tzinfo=timezone.utc)
        self.assertTrue(gate(candidates,statuses,now=now)['ready'])
        result = gate(candidates,statuses,now=now+timedelta(seconds=1))
        self.assertFalse(result['ready'])
        self.assertEqual({r['source'] for r in result['blocking_sources']},{'senate_efd'})
        self.assertEqual({r['reason'] for r in result['blocking_sources']},{'collection_stale','qualified_stale'})

    def test_house_and_oge_still_have_exact_forty_eight_hour_limits(self):
        candidates,statuses = self.inputs()
        now = datetime(2026,10,31,tzinfo=timezone.utc)
        self.assertTrue(gate(candidates,statuses,now=now)['ready'])
        result = gate(candidates,statuses,now=now+timedelta(seconds=1))
        self.assertEqual({r['source'] for r in result['blocking_sources']},{'house_clerk','oge'})

    def test_allowance_configuration_does_not_allow_missing_or_unauthorized_sources(self):
        candidates,statuses = self.inputs()
        stamp = '2026-10-10T00:00:00Z'
        candidates['senate_efd']['meta']['data_cutoff_at'] = stamp
        candidates['senate_efd']['source_health'][0]['data_cutoff_at'] = stamp
        statuses['senate_efd'].update(status='blocked',catalog={'retrieved_at':stamp})
        now = datetime(2026,10,29,tzinfo=timezone.utc)
        self.assertTrue(gate(candidates,statuses,now=now)['ready'])
        with patch.dict('os.environ',{'SENATE_MAX_STALE_DAYS':'10'}):
            self.assertFalse(gate(candidates,statuses,now=now)['ready'])
        statuses['senate_efd']['catalog'] = {}
        self.assertFalse(gate(candidates,statuses,now=now)['ready'])
        statuses['senate_efd'].update(catalog={'retrieved_at':stamp},terms_acknowledged=False)
        self.assertFalse(gate(candidates,statuses,now=now)['ready'])
        for value in ('-1','nan','1.5',True,'0'):
            with self.assertRaises(ValueError):senate_max_stale_days(value)

    def test_twenty_day_reminder_is_one_shot_and_publication_alert_clears_independently(self):
        now = datetime(2026,10,29,8,tzinfo=timezone.utc)
        old = (now-timedelta(days=20)).isoformat()
        statuses = {s:{'status':'ok','run_at':now.isoformat(),
                       'catalog':{'retrieved_at':now.isoformat()}} for s in ('house_clerk','oge','senate_efd')}
        statuses['senate_efd'].update(status='blocked',catalog={'retrieved_at':old})
        manifest = {'generated_at':now.isoformat(),'source_health':[{'source_id':'senate_efd','data_cutoff_at':old}]}
        current = health(manifest,statuses,now=now,publication_success_at=now.isoformat())
        self.assertFalse(current['blocking_sources'])
        self.assertEqual({a['key'] for a in current['alerts']},{'source:senate_efd','senate-stale-20d'})
        class Api:
            def __init__(self):self.issues=[];self.writes=[]
            def request(self,path,method='GET',payload=None):
                if method=='GET':return deepcopy(self.issues)
                self.writes.append((method,payload))
                if method=='POST':
                    row={'number':len(self.issues)+1,'state':'open',**payload};self.issues.append(row);return row
                row=next(r for r in self.issues if r['number']==int(path.rsplit('/',1)[1]));row.update(payload);return row
        api=Api();reconcile_issues(api,current)
        reminder=next(r for r in api.issues if 'senate-stale-20d' in r['body'])
        reminder['state']='closed';api.writes.clear();reconcile_issues(api,current)
        self.assertFalse(api.writes)
        statuses['senate_efd'].update(status='ok',catalog={'retrieved_at':now.isoformat()})
        manifest['source_health'][0]['data_cutoff_at']=now.isoformat()
        recovered=health(manifest,statuses,now=now,publication_success_at=now.isoformat())
        self.assertFalse(recovered['alerts'])
        reconcile_issues(api,recovered)
        self.assertTrue(all(r['state']=='closed' for r in api.issues))

    def test_public_reading_and_rendering_preserve_window_calculations_and_recover(self):
        results=[]
        for stale in (True,False):
            payload,bundle=freshness_fixture(stale)
            repository=PublicSnapshotRepository('example','data',transport=FakeTransport(bundle.files))
            snapshot=repository.fetch().snapshot
            processed=load('process_snapshot',version='v3').build_snapshot(snapshot)
            html=load('render_dashboard',version='v3').render_html(processed)
            self.assertEqual('const PUBLISHED_SENATE_FRESHNESS' in html,stale)
            self.assertEqual(snapshot['transactions'],payload['transactions'])
            results.append(processed)
        self.assertEqual(results[0]['summary'],results[1]['summary'])
        self.assertEqual(results[0]['market_moves'],results[1]['market_moves'])

    def test_each_fact_is_bounded_by_its_own_source_cutoff(self):
        payload,_=freshness_fixture()
        row=next(r for r in payload['transactions'] if r['source_id']=='senate_efd')
        row['filed_at']='2026-09-19T00:00:00Z'
        with self.assertRaisesRegex(ValueError,'source coverage cutoff'):
            build(payload,generated_at='2026-09-26T00:00:00Z',allow_production=True)

    def test_public_reader_rejects_an_untrue_source_cutoff_or_untyped_stale_flag(self):
        _,bundle=freshness_fixture()
        for kind in ('cutoff','flag'):
            manifest=deepcopy(bundle.manifest)
            row=next(r for r in manifest['source_health'] if r['source_id']=='senate_efd')
            if kind=='cutoff':row['data_cutoff_at']='2020-01-01T00:00:00Z'
            else:row['stale']='false'
            files={**bundle.files,'manifest.json':encode(manifest)}
            with self.assertRaises(PublicSnapshotError):
                PublicSnapshotRepository('example','data',transport=FakeTransport(files)).fetch()

    def test_recovery_catalog_range_cannot_skip_last_success_even_when_configuration_moves(self):
        now=datetime(2026,10,29,tzinfo=timezone.utc)
        prior={'last_successful_collection_at':'2026-10-01T16:00:00Z',
               'catalog':{'retrieved_at':'2026-09-30T23:00:00Z'}}
        self.assertEqual(collection_start(prior,'2026-10-25',now=now),'2026-09-30')
        self.assertEqual(collection_start(prior,'2026-01-01',now=now),'2026-01-01')
        self.assertLessEqual(collection_start(prior,now=now),'2026-09-30')
        prior.update(last_successful_collection_at='2026-10-28T00:00:00Z',catalog={'retrieved_at':'2026-10-28T00:00:00Z'})
        self.assertEqual(collection_start(prior,'2026-10-25',now=now,
            qualified_at='2026-10-01T00:00:00Z'), '2026-10-01')
        self.assertEqual(collection_start(prior,'2026-10-25',now=now,
            qualified_at='2026-10-01T00:00:00Z',earliest_known_date='2026-05-01'),'2026-05-01')

    def test_unknown_native_cover_is_quarantined_without_inventing_a_filing_date(self):
        from unison_snapshot.whitehouse_278t import generic_whitehouse_filing_date, NATIVE_OPACITY_DEFAULT_VERSION
        from unison_snapshot.oge import OgeCatalogError
        report={'parser_version':NATIVE_OPACITY_DEFAULT_VERSION,'filed_at':None,'evidence_complete':False,
                'native_text_partition':{'status':'unknown','pages':[]},
                'document_reasons':['native_visible_cover_unproved']}
        self.assertIsNone(generic_whitehouse_filing_date(report))
        report['filed_at']='2026-10-29'
        with self.assertRaisesRegex(OgeCatalogError,'cover evidence is unavailable'):
            generic_whitehouse_filing_date(report)
