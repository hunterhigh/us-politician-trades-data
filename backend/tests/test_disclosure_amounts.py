from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from unison_snapshot.builder import build
from unison_snapshot.disclosure_amounts import validate_amount
from unison_snapshot.legacy import load, processor_version_for, PROCESSOR_V2_SHA256
from unison_snapshot.oge_candidate import build_oge_candidate
from test_oge_candidate import catalog, extraction, base
from test_public_repo import FakeTransport
from unison_snapshot.public_repo import PublicSnapshotRepository


def fixture():
    parsed = extraction()
    parsed['transactions'][0]['transaction_date'] = '2025-05-20'
    data, _ = build_oge_candidate(catalog(), [parsed], base(),
                                 data_cutoff_at='2025-06-04T00:00:00Z')
    data['meta']['snapshot_id'] = 'test-open-amounts'
    data['people'][0]['priority'] = True
    finite = data['transactions'][0]
    finite.update(amount_low=1000001, amount_high=5000000)
    opened = deepcopy(finite)
    opened.update(id='open-transaction', amount_low=50000000,
                  amount_high=None, amount_low_exclusive=True)
    data['transactions'].append(opened)
    holding = {key: finite[key] for key in ('filing_id', 'person_id', 'owner', 'asset_name',
               'ticker', 'ticker_mapping_basis', 'instrument_type', 'filed_at',
               'source_id', 'source', 'source_url', 'verification_status')}
    holding.update(id='bounded-holding', report_period_end='2024-12-31',
                   value_low=1001, value_high=15000, change_from_prior=None)
    open_holding = deepcopy(holding)
    open_holding.update(id='open-holding', value_low=1000000, value_high=None,
                        value_low_exclusive=True, owner='Spouse')
    data['reported_holdings'] = [holding, open_holding]
    return data


class DisclosureAmountTests(unittest.TestCase):
    def test_explicit_open_only_and_finite_sums(self):
        for invalid in ({'amount_low': 1, 'amount_low_exclusive': True},
                        {'amount_low': 1, 'amount_high': None},
                        {'amount_low': True, 'amount_high': 5},
                        {'amount_low': 1, 'amount_high': 5, 'amount_low_exclusive': False},
                        {'amount_low': 1, 'amount_high': None, 'amount_low_exclusive': 'true'}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_amount(invalid)

    def test_publication_excludes_open_values_without_mutating_review(self):
        data = fixture(); before = deepcopy(data)
        bundle = build(data, generated_at='2025-06-04T00:00:00Z', allow_production=True)
        board = json.loads(bundle.files['board/'+bundle.manifest['board']+'.json'])
        self.assertEqual(data, before)
        self.assertEqual(board['transactions'], [data['transactions'][0]])
        self.assertEqual(board['reported_holdings'], [data['reported_holdings'][0]])
        self.assertEqual(bundle.manifest['processor_sha256'], PROCESSOR_V2_SHA256)
        gaps = bundle.manifest['known_gaps']['excluded_open_amounts']
        self.assertEqual(gaps['transactions'][0]['record_ids'], ['open-transaction'])
        self.assertEqual(gaps['transactions'][0]['threshold'], 'Over $50,000,000')
        self.assertEqual(gaps['reported_holdings'][0]['record_ids'], ['open-holding'])
        self.assertEqual(gaps['reported_holdings'][0]['threshold'], 'Over $1,000,000')

    def test_existing_public_reader_all_modes_and_existing_renderer(self):
        data = fixture()
        bundle = build(data, generated_at='2025-06-04T00:00:00Z', allow_production=True)
        repository = PublicSnapshotRepository('example', 'data', transport=FakeTransport(bundle.files))
        for mode, key in [('dashboard', None), ('person', data['people'][0]['id']), ('ticker', 'EXM')]:
            snapshot = repository.fetch(mode, key).snapshot
            self.assertEqual(processor_version_for(snapshot), 'v2')
            processed = load('process_snapshot', version='v2').build_snapshot(snapshot)
            self.assertEqual(len(processed['transactions']), 1)
            self.assertEqual(len(processed['reported_holdings']), 1)
            html = load('render_dashboard', version='v2').render_html(processed)
            self.assertIn('Over $50,000,000', html)
            self.assertNotIn('approvedIsOpen', html)

    def test_unknown_upper_bound_is_not_silently_removed(self):
        from unison_snapshot.publication_amounts import assert_bounded
        for kind, prefix in [('transactions','amount'),('reported_holdings','value')]:
            data=fixture(); del data[kind][1][prefix+'_low_exclusive']
            with self.assertRaises(ValueError):
                build(data, generated_at='2025-06-04T00:00:00Z',allow_production=True)
        with self.assertRaises(ValueError): assert_bounded(fixture())

    def test_exclusion_receipts_reject_changed_counts_thresholds_and_ids(self):
        from unison_snapshot.publication_amounts import project
        from unison_snapshot.known_gaps import validate
        gaps=project(fixture())['meta']['known_gaps']
        for field, value in [('count',99),('minimum',10),('threshold','Over $0'),('record_ids',['x','x'])]:
            changed=deepcopy(gaps); changed['excluded_open_amounts']['transactions'][0][field]=value
            with self.subTest(field=field), self.assertRaises(ValueError):validate(changed)

    def test_open_input_order_does_not_change_built_files(self):
        data = fixture(); first = build(data, generated_at='2025-06-04T00:00:00Z', allow_production=True)
        data['transactions'].reverse(); data['reported_holdings'].reverse()
        second = build(data, generated_at='2025-06-04T00:00:00Z', allow_production=True)
        self.assertEqual(first.files, second.files)

    def test_public_reader_rejects_unbounded_arrays_and_changed_exclusion_identity(self):
        from unison_snapshot.codec import encode, digest
        from unison_snapshot.public_repo import PublicSnapshotError
        bundle = build(fixture(), generated_at='2025-06-04T00:00:00Z', allow_production=True)
        for change in ['array', 'receipt']:
            files = dict(bundle.files); manifest = deepcopy(bundle.manifest)
            if change == 'array':
                board = json.loads(files['board/'+manifest['board']+'.json'])
                board['transactions'][0].update(amount_high=None, amount_low_exclusive=True)
                raw = encode(board); manifest['board'] = digest(raw)
                files['board/'+manifest['board']+'.json'] = raw
            else:
                manifest['known_gaps']['excluded_open_amounts']['transactions'][0]['record_ids'] = ['another-unpublished-id']
            files['manifest.json'] = encode(manifest)
            with self.subTest(change=change), self.assertRaises(PublicSnapshotError):
                PublicSnapshotRepository('example','data', transport=FakeTransport(files)).fetch()


if __name__ == '__main__':
    unittest.main()
