from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_public_repo import COMMIT, FakeTransport, production_payload
from unison_snapshot.builder import build
from unison_snapshot.public_repo import HTTPTransport, PublicSnapshotTransportError
from unison_snapshot.publication_smoke import check_client, check_circuit, recovery_decision, observe_smoke, save_json

ROOT = Path(__file__).resolve().parents[2]
GOOD = '2'*40


class RecoveryAPI:
    def __init__(self):
        self.current, self.tag, self.comparison, self.calls = COMMIT, GOOD, 'ahead', []

    def request(self, path, method='GET', payload=None):
        self.calls.append((path,method,payload))
        if method == 'POST': return None
        if path == '/git/ref/heads/main': return {'object':{'sha':self.current,'type':'commit'}}
        if path.startswith('/git/ref/tags/published/'): return {'object':{'sha':self.tag,'type':'commit'}}
        if path.startswith('/compare/'): return {'status':self.comparison}
        raise AssertionError(path)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.api = RecoveryAPI()
        self.receipt = {'status':'content_failure','commit':COMMIT}
        self.previous = {'last_good_commit':GOOD}

    def test_only_content_failure_of_current_head_dispatches_expected_previous_publication(self):
        for status in ('network_failure','operational_failure','superseded','passed'):
            self.receipt['status']=status
            self.assertEqual(recovery_decision(self.api,self.receipt,self.previous,apply=True)['action'],'none')
        self.assertEqual(self.api.calls,[])
        self.receipt['status']='content_failure'
        self.assertEqual(recovery_decision(self.api,self.receipt,self.previous)['action'],'would_request')
        self.assertFalse(any(c[1]=='POST' for c in self.api.calls))
        self.assertEqual(recovery_decision(self.api,self.receipt,self.previous,apply=True)['action'],'requested')
        self.assertEqual(self.api.calls[-1][2]['inputs'],{'target_commit':GOOD,'expected_head':COMMIT,'dry_run':False})

    def test_newer_head_untagged_target_nonancestor_and_repeat_cannot_dispatch(self):
        for attr,value,expected in [('current','3'*40,'superseded'),('tag','3'*40,'held'),('comparison','diverged','held')]:
            api=RecoveryAPI();setattr(api,attr,value)
            self.assertEqual(recovery_decision(api,self.receipt,self.previous,apply=True)['action'],expected)
            self.assertFalse(any(c[1]=='POST' for c in api.calls))
        self.previous['rollback_requested_for']=COMMIT
        self.assertEqual(recovery_decision(self.api,self.receipt,self.previous,apply=True)['action'],'already_requested')
        self.assertEqual(self.api.calls,[])

    def test_no_previous_known_good_never_guesses_a_target(self):
        self.assertEqual(recovery_decision(self.api,self.receipt,{},apply=True)['action'],'held')
        self.assertEqual(self.api.calls,[])

    def test_saved_failure_circuit_survives_network_retry_and_good_rollback(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary)/'status/publication_smoke.json'
            save_json(receipt_path,{'schema_version':'publication-smoke/v1','commit':GOOD,'status':'passed',
                'last_good_commit':GOOD,'failed_snapshots':{}})
            def check(**kwargs): return {'status':'content_failure','checks':[]}
            observed=observe_smoke(self.api,repository='example/data',commit=COMMIT,
                manifest={'snapshot_id':'bad'},client_root=ROOT,health_root=temporary,checker=check,apply=True)
            with self.assertRaisesRegex(ValueError,'previously failed'): check_circuit({'snapshot_id':'bad'},observed)
            check_circuit({'snapshot_id':'new'},observed)
            self.assertEqual(observed['last_good_commit'],GOOD)
            self.assertEqual(observed['rollback_requested_for'],COMMIT)
            observed=observe_smoke(self.api,repository='example/data',commit=COMMIT,
                manifest={'snapshot_id':'bad'},client_root=ROOT,health_root=temporary,
                checker=lambda **kwargs:{'status':'network_failure','checks':[]},apply=True)
            self.assertEqual(observed['last_good_commit'],GOOD)
            self.assertIn('bad',observed['failed_snapshots'])
            observed=observe_smoke(self.api,repository='example/data',commit='4'*40,
                manifest={'snapshot_id':'restored'},client_root=ROOT,health_root=temporary,
                checker=lambda **kwargs:{'status':'passed','checks':[]},apply=True)
            self.assertIn('bad',observed['failed_snapshots'])
            self.assertEqual(observed['last_good_commit'],'4'*40)


class ShippedClientSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0,str(ROOT/'client'))
        spec=importlib.util.spec_from_file_location('smoke_shipped_client',ROOT/'client/fetch_snapshot.py')
        cls.client=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.client)

    def setUp(self):
        payload=production_payload()
        third=deepcopy(payload['people'][0]);third['id']='house:THIRD'
        payload['people'].append(third)
        self.people=[p['id'] for p in payload['people']]
        self.files=build(payload,generated_at='2026-09-18T00:01:00Z',allow_production=True).files
        self.transport=FakeTransport(self.files)
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.output=Path(temporary.name)

    def runner(self, command, **kwargs):
        # Execute the shipped main() with its real CLI, processor, hash-checking
        # repository and writer; replace only the network with immutable bytes.
        with patch.object(sys,'argv',command[1:]), patch.object(self.client,'HTTPTransport',return_value=self.transport), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try: status=self.client.main()
            except SystemExit as error: status=error.code
        return SimpleNamespace(returncode=status)

    def check(self):
        return check_client(repository='example/data',commit=COMMIT,client_root=ROOT,
            output_dir=self.output,people=self.people,runner=self.runner)

    def test_real_dashboard_three_people_processing_and_report_render(self):
        result=self.check()
        self.assertEqual(result['status'],'passed')
        self.assertEqual(len(result['checks']),5)
        self.assertTrue((self.output/'dashboard.html').is_file())
        self.assertTrue(all(c['status']=='passed' for c in result['checks']))

    def test_corrupt_hash_is_content_failure_and_unavailable_object_is_network_only(self):
        board=next(k for k in self.files if k.startswith('board/'))
        original=self.files[board];self.files[board]=b'{}'
        self.assertEqual(self.check()['status'],'content_failure')
        self.files[board]=original
        self.transport.get=lambda *args,**kwargs: (_ for _ in ()).throw(PublicSnapshotTransportError('HTTP 503'))
        self.assertEqual(self.check()['status'],'network_failure')

    def test_advancing_head_and_untyped_crash_never_authorize_rollback(self):
        self.transport.commit='3'*40
        self.assertEqual(self.check()['status'],'superseded')
        result=check_client(repository='example/data',commit=COMMIT,client_root=ROOT,
            output_dir=self.output,people=self.people,runner=lambda *args,**kwargs:SimpleNamespace(returncode=1))
        self.assertEqual(result['status'],'operational_failure')

    def test_http_retry_exhaustion_remains_transport_error(self):
        import urllib.error
        transport=HTTPTransport(retries=3,backoff=0)
        with patch.object(transport.opener,'open',side_effect=urllib.error.HTTPError('https://example',503,'down',{},None)) as request:
            with self.assertRaises(PublicSnapshotTransportError): transport.get('https://example',100)
            self.assertEqual(request.call_count,4)
        with patch.object(transport.opener,'open',side_effect=PublicSnapshotTransportError('redirect')) as request:
            with self.assertRaises(PublicSnapshotTransportError): transport.get('https://example',100)
            self.assertEqual(request.call_count,4)
