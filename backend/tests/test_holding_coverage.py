"""Large partial-holding evidence stays complete through public and local reads."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from test_house_partial_holdings import candidate_fixture
from test_public_repo import FakeTransport, COMMIT
from unison_snapshot.builder import build
from unison_snapshot.codec import encode
from unison_snapshot.holding_coverage import MAX_BYTES, restore
from unison_snapshot.public_repo import PublicSnapshotRepository, PublicSnapshotError
from unison_snapshot.legacy import load
from unison_snapshot.store import assemble


class HoldingCoverageTests(unittest.TestCase):
    def bundle(self):
        with tempfile.TemporaryDirectory() as d:
            candidate,_,_,_=candidate_fixture(Path(d))
        coverage=candidate['meta']['holding_report_coverage'][0]
        original=coverage['unresolved_rows'][0]
        coverage['unresolved_rows']=[dict(deepcopy(original),extraction_id='unknown-'+str(i),
            asset_name='Source-disclosed asset '+('x'*2000)) for i in range(20)]
        coverage['observed_row_count']=len(coverage['published_holding_ids'])+len(coverage['excluded_row_ids'])+20
        return build(candidate,generated_at='2026-09-18T12:00:00Z',allow_production=True),candidate

    def test_large_evidence_is_not_truncated_and_both_readers_restore_it(self):
        bundle,candidate=self.bundle();ref=bundle.manifest['holding_report_coverage']
        self.assertEqual(ref['schema_version'],'holding-report-coverage-reference/v1')
        self.assertLessEqual(len(bundle.files['manifest.json']),16*1024)
        self.assertGreater(ref['byte_length'],16*1024)
        transport=FakeTransport(bundle.files)
        repo=PublicSnapshotRepository('example','data',transport=transport)
        for mode,key in [('dashboard',None),('person',candidate['people'][0]['id']),('ticker','EXM')]:
            snapshot=repo.fetch(mode,key).snapshot
            self.assertEqual(snapshot['meta']['holding_report_coverage'],candidate['meta']['holding_report_coverage'])
            load('process_snapshot',version='v3').build_snapshot(snapshot)
        store=SimpleNamespace(read=lambda commit,path: bundle.files[path])
        local=assemble(store,COMMIT)
        self.assertEqual(local['meta']['holding_report_coverage'],candidate['meta']['holding_report_coverage'])
        calls=[c for c in transport.calls if '/status/'+ref['sha256']+'.json' in c[0]]
        self.assertTrue(calls)
        self.assertTrue(all('/'+COMMIT+'/' in c[0] and c[1]==MAX_BYTES for c in calls))

    def test_corruption_or_oversized_reference_fails_before_rendering(self):
        bundle,_=self.bundle();ref=bundle.manifest['holding_report_coverage']
        path='status/'+ref['sha256']+'.json'
        corrupt=dict(bundle.files);corrupt[path]=corrupt[path][:-1]+b' '
        with self.assertRaisesRegex(PublicSnapshotError,'hash'):
            PublicSnapshotRepository('example','data',transport=FakeTransport(corrupt)).fetch()
        for change in [{'byte_length':MAX_BYTES+1},{'byte_length':True},{'sha256':'../other'},
                       {'schema_version':'unknown'}]:
            read=Mock()
            with self.assertRaises(ValueError):restore({'holding_report_coverage':dict(ref,**change)},read)
            read.assert_not_called()
