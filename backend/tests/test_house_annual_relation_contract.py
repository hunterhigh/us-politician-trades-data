"""Source locator and existing sharded consumer contracts, synthetic facts only."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest

import test_house_candidate as house_case
import test_disclosure_candidate as mixed_case
from test_public_repo import FakeTransport
from unison_snapshot.builder import build, normalize
from unison_snapshot.house_candidate import build_house_candidate
from unison_snapshot.disclosure_candidate import build_disclosure_candidate
from unison_snapshot.legacy import load, processor_version_for, PROCESSOR_V3_SHA256
from unison_snapshot.public_repo import PublicSnapshotRepository


def fixture():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        house_case.HouseCandidateTests().fixture(root)
        data = build_house_candidate(root, {
            'status': 'ok', 'run_at': '2026-09-18T12:00:00Z',
            'counts': {'archived': 1, 'pending': 0, 'failed': 0}}, deepcopy(house_case.BASE))
    ptr = data['transactions'][0]
    ptr['ticker'] = 'XYZ'
    annual = deepcopy(ptr)
    sha, page, top = 'a' * 64, 10, 125.25
    annual.update(id='house-annual-b:' + hashlib.sha256(f'{sha}:{page}:{top:.2f}'.encode()).hexdigest()[:24],
                  filing_id='10000001', ticker='ABC',
                  source_url='https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2025/10000001.pdf')
    data['transactions'].append(annual)
    data['meta'].update(snapshot_id='house-annual-relation-unit',
                        generated_at=data['meta']['data_cutoff_at'])
    data['meta']['transaction_disclosure_relations'] = [{
        'transaction_id': annual['id'], 'source_id': 'house_clerk',
        'filing_id': annual['filing_id'], 'source_url': annual['source_url'],
        'source_sha256': sha, 'section': 'Schedule B', 'page_number': page,
        'top_points': top, 'status': 'possible_ptr_overlap',
        'possible_ptr_transaction_ids': [ptr['id']]}]
    return data


class HouseAnnualRelationContractTests(unittest.TestCase):
    def test_pinned_processor_uses_repository_lf_bytes(self):
        path = Path(__file__).resolve().parents[2] / 'frontend-contract-v3/scripts/process_snapshot.py'
        raw = path.read_bytes()
        self.assertNotIn(b'\r\n', raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), PROCESSOR_V3_SHA256)

    def test_house_locator_is_not_a_senate_printed_row(self):
        data = fixture()
        normalized = normalize(data, allow_production=True)
        self.assertEqual(len(normalized['transactions']), 2)
        self.assertEqual(processor_version_for(data), 'v3')
        relation = normalized['meta']['transaction_disclosure_relations'][0]
        self.assertNotIn('printed_row_number', relation)
        processed = load('process_snapshot', version='v3').build_snapshot(normalized)
        self.assertEqual(processed['meta']['transaction_disclosure_relations'], [relation])

    def test_invalid_source_locators_and_cross_source_ptr_are_rejected(self):
        for field, value in [('source_sha256', 'b'*64), ('page_number', True),
                             ('page_number', 0), ('top_points', 125.251),
                             ('top_points', float('nan')), ('section', 'Part 4b'),
                             ('printed_row_number', '1'),
                             ('source_url', 'https://example.com/other.pdf'),
                             ('possible_ptr_transaction_ids', ['senate-ptr:one'])]:
            with self.subTest(field=field, value=value):
                data = fixture()
                data['meta']['transaction_disclosure_relations'][0][field] = value
                with self.assertRaises(ValueError):
                    normalize(data, allow_production=True)
                with self.assertRaises(ValueError):
                    load('process_snapshot', version='v3').build_snapshot(data)

    def test_missing_or_duplicate_annotations_cannot_hide_house_annual_facts(self):
        for mode in ('missing', 'empty', 'duplicate'):
            with self.subTest(mode=mode):
                data = fixture()
                if mode == 'missing':
                    data['meta'].pop('transaction_disclosure_relations')
                elif mode == 'empty':
                    data['meta']['transaction_disclosure_relations'] = []
                else:
                    data['meta']['transaction_disclosure_relations'] *= 2
                self.assertEqual(processor_version_for(data), 'v3')
                with self.assertRaises(ValueError):
                    normalize(data, allow_production=True)
                with self.assertRaises(ValueError):
                    load('process_snapshot', version='v3').build_snapshot(data)

    def test_existing_shards_restore_only_loaded_house_relations(self):
        data = fixture()
        bundle = build(data, generated_at=data['meta']['data_cutoff_at'], allow_production=True)
        self.assertNotIn('transaction_disclosure_relations', bundle.manifest)
        self.assertLess(len(bundle.files['manifest.json']), 8192)
        for mode, key in [('dashboard', None), ('search', None),
                          ('person', data['people'][0]['id']), ('ticker', 'ABC'),
                          ('ticker', 'XYZ')]:
            with self.subTest(mode=mode, key=key):
                transport = FakeTransport(bundle.files)
                repo = PublicSnapshotRepository('example', 'data', transport=transport)
                selected = repo.fetch(mode, key).snapshot
                expected_count = 0 if key == 'XYZ' else 1
                self.assertEqual(len(selected['meta']['transaction_disclosure_relations']), expected_count)
                processed = load('process_snapshot', version='v3').build_snapshot(selected)
                self.assertEqual(len(processed['meta']['transaction_disclosure_relations']), expected_count)
                if key == 'ABC':
                    self.assertEqual(len(selected['transactions']), 1)
                    self.assertFalse(any('people/' in call[0] for call in transport.calls))

    def test_house_and_senate_relations_coexist_in_the_existing_merge(self):
        house = fixture()
        house['meta']['data_cutoff_at'] = mixed_case.CUTOFF
        house['source_health'][0]['data_cutoff_at'] = mixed_case.CUTOFF
        senate = mixed_case.candidate('senate_efd', 'senate:example', 'temporary')
        fact = senate['transactions'][0]
        fact['source_url'] = 'https://efdsearch.senate.gov/search/view/annual/example/'
        sha, number = 'b'*64, '1'
        fact['id'] = 'senate-annual-part4b:' + hashlib.sha256(f'{sha}:part4b:{number}'.encode()).hexdigest()[:24]
        senate['meta']['transaction_disclosure_relations'] = [{
            'transaction_id': fact['id'], 'source_id': 'senate_efd',
            'filing_id': fact['filing_id'], 'source_url': fact['source_url'],
            'source_sha256': sha, 'section': 'Part 4b', 'printed_row_number': number,
            'status': 'cross_report_relationship_unconfirmed', 'possible_ptr_transaction_ids': []}]
        base = deepcopy(mixed_case.BASE)
        base['security_market_data'] = []
        merged = build_disclosure_candidate(base, {'house_clerk': house, 'senate_efd': senate})
        self.assertEqual(len(merged['meta']['transaction_disclosure_relations']), 2)
        bundle = build(merged, generated_at=mixed_case.CUTOFF, allow_production=True)
        repo = PublicSnapshotRepository('example', 'data', transport=FakeTransport(bundle.files))
        selected = repo.fetch().snapshot
        processed = load('process_snapshot', version='v3').build_snapshot(selected)
        self.assertEqual({r['source_id'] for r in processed['meta']['transaction_disclosure_relations']},
                         {'house_clerk', 'senate_efd'})


if __name__ == '__main__':
    unittest.main()
