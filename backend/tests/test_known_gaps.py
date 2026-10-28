from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.builder import Bundle, build
from unison_snapshot.codec import encode
from unison_snapshot.disclosure_status import attach_disclosure_status
from unison_snapshot.known_gaps import validate
from unison_snapshot.legacy import load
from unison_snapshot.materialize import materialize
from unison_snapshot.public_repo import PublicSnapshotRepository, PublicSnapshotError
from test_disclosure_status import scopes, record, PROVENANCE, NOW
from test_public_repo import production_payload, FakeTransport


def fixture():
    payload = production_payload()
    sources = scopes(payload)
    sources[0]['reports'][0].update(qualification_state='partial', quarantined_row_count=3,
        reason_codes=['transaction_date_invalid'], filed_date='2026-09-01', filed_date_basis='qualified_report')
    sources[0]['reports'].append(record('unknown-report', acquisition_state='request_required',
        qualification_state='not_evaluated', publication_binding='unknown',
        qualified_transaction_count=None, quarantined_row_count=None,
        reason_codes=['request_required']))
    bundle = build(payload, generated_at=NOW, allow_production=True)
    attached = attach_disclosure_status(bundle, sources, transactions=payload['transactions'], provenance=PROVENANCE)
    return payload, bundle, attached


class KnownGapsTests(unittest.TestCase):
    def large_fixture(self):
        payload = production_payload()
        sources = scopes(payload)
        sources[0]['limitations'].append('Full source inventory limitation ' * 700)
        original = build(payload, generated_at=NOW, allow_production=True)
        bundle = attach_disclosure_status(original, sources, transactions=payload['transactions'], provenance=PROVENANCE)
        return payload, original, bundle

    def test_large_gaps_use_verified_status_object_in_every_normal_reader(self):
        payload, original, bundle = self.large_fixture()
        self.assertLessEqual(len(bundle.files['manifest.json']), 16*1024)
        self.assertEqual(bundle.manifest['snapshot_id'], original.manifest['snapshot_id'])
        reference = bundle.manifest['known_gaps']
        self.assertEqual(reference['schema_version'], 'known-gaps-reference/v1')
        content = bundle.files[f"status/{reference['sha256']}.json"]
        self.assertEqual(reference['byte_length'], len(content))
        gaps = json.loads(content)
        reader = PublicSnapshotRepository('example', 'data', transport=FakeTransport(bundle.files))
        for mode, key in [('dashboard', None), ('search', None), ('person', payload['people'][0]['id']),
                          ('ticker', payload['transactions'][0]['ticker'])]:
            snapshot = reader.fetch(mode, key).snapshot
            self.assertEqual(snapshot['meta']['known_gaps'], gaps)
            html = load('render_dashboard', version='v3').render_html(
                load('process_snapshot', version='v3').build_snapshot(snapshot))
            self.assertIn('const PUBLISHED_KNOWN_GAPS = ', html)
            self.assertIn('Full source inventory limitation ', html)

    def test_large_gaps_reject_corruption_bad_reference_and_absent_object(self):
        _, _, bundle = self.large_fixture()
        reference = bundle.manifest['known_gaps']
        path = f"status/{reference['sha256']}.json"
        for kind in ('hash', 'length', 'budget', 'missing', 'schema'):
            with self.subTest(kind=kind):
                files = dict(bundle.files)
                manifest = deepcopy(bundle.manifest)
                if kind == 'hash': files[path] = files[path].replace(b'limitation', b'limitatioN', 1)
                elif kind == 'length': manifest['known_gaps']['byte_length'] -= 1
                elif kind == 'budget': manifest['known_gaps']['byte_length'] = 256*1024+1
                elif kind == 'missing': files.pop(path)
                else: manifest['known_gaps']['unexpected'] = True
                files['manifest.json'] = encode(manifest)
                with self.assertRaises((PublicSnapshotError, KeyError)):
                    PublicSnapshotRepository('example', 'data', transport=FakeTransport(files)).fetch()

    def test_source_inventory_gaps_survive_normal_dashboard_and_person_readers(self):
        payload, original, bundle = fixture()
        self.assertEqual(bundle.manifest['snapshot_id'], original.manifest['snapshot_id'])
        self.assertEqual(bundle.files['board/'+original.manifest['board']+'.json'],
                         original.files['board/'+original.manifest['board']+'.json'])
        reader = PublicSnapshotRepository('example', 'data', transport=FakeTransport(bundle.files))
        for mode,key in [('dashboard',None),('person',payload['people'][0]['id'])]:
            snapshot = reader.fetch(mode,key).snapshot
            gaps = snapshot['meta']['known_gaps']
            source = next(s for s in gaps['sources'] if s['partial_report_count'])
            self.assertEqual((source['quarantined_row_count'],source['unknown_row_count_report_count']), (3,1))
            self.assertEqual(source['request_required_report_count'],1)
            self.assertTrue(source['affected_person_ids'])
            self.assertEqual(source['unidentified_affected_report_count'], 1)
            processed = load('process_snapshot',version='v3').build_snapshot(snapshot)
            self.assertEqual(processed['meta']['known_gaps'], gaps)
            html = load('render_dashboard',version='v3').render_html(processed)
            self.assertIn('const PUBLISHED_KNOWN_GAPS = ', html)
            self.assertIn('Known data gaps', html)
            self.assertIn('personPanel()', html)

    def test_invalid_counts_and_silent_removal_are_rejected(self):
        _, _, bundle = fixture()
        bad = deepcopy(bundle.manifest);bad['known_gaps']['sources'][0]['quarantined_row_count'] = -1
        files = {**bundle.files,'manifest.json':encode(bad)}
        with self.assertRaisesRegex(PublicSnapshotError,'source counts'):
            PublicSnapshotRepository('example','data',transport=FakeTransport(files)).fetch()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);materialize(root,bundle)
            before = (root/'manifest.json').read_bytes()
            removed = deepcopy(bundle.manifest);removed.pop('known_gaps')
            with self.assertRaisesRegex(ValueError,'known data gaps'):
                materialize(root,Bundle({**bundle.files,'manifest.json':encode(removed)},removed))
            self.assertEqual((root/'manifest.json').read_bytes(),before)

    def test_unknown_counts_cannot_be_presented_as_complete(self):
        _,_,bundle = fixture()
        gaps = deepcopy(bundle.manifest['known_gaps'])
        gaps['sources'][0]['unknown_row_count_report_count'] = gaps['sources'][0]['report_count']+1
        with self.assertRaises(ValueError):validate(gaps)


if __name__ == '__main__':
    unittest.main()
