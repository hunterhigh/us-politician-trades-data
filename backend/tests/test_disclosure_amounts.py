from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from unison_snapshot.builder import build, normalize
from unison_snapshot.disclosure_amounts import summarize_amounts, validate_amount
from unison_snapshot.legacy import load, processor_version_for, PROCESSOR_V3_SHA256
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
        data = fixture()
        result = summarize_amounts(data['transactions'])
        self.assertEqual((result['amount_low'], result['amount_high']), (1000001, 5000000))
        self.assertEqual(result['amount_open_count'], 1)
        self.assertEqual(result['amount_open_thresholds'],
                         [{'minimum': 50000000, 'minimum_exclusive': True, 'count': 1}])
        for invalid in ({'amount_low': 1, 'amount_high': None},
                        {'amount_low': True, 'amount_high': 5},
                        {'amount_low': 1, 'amount_high': 5, 'amount_low_exclusive': False},
                        {'amount_low': 1, 'amount_high': None, 'amount_low_exclusive': 'true'}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_amount(invalid)

    def test_finite_consumer_unchanged(self):
        data = fixture()
        data['transactions'].pop()
        data['reported_holdings'].pop()
        self.assertEqual(load('process_snapshot', version='v2').build_snapshot(data),
                         load('process_snapshot', version='v3').build_snapshot(data))
        self.assertEqual(processor_version_for(data), 'v2')

    def test_consumer_counts_all_separates_transaction_and_holding_values(self):
        result = load('process_snapshot', version='v3').build_snapshot(fixture())
        self.assertEqual(len(result['transactions']), 2)
        self.assertEqual(result['summary']['amount_low_30d'], 1000001)
        self.assertEqual(result['summary']['amount_high_30d'], 5000000)
        self.assertIsNone(next(r for r in result['transactions'] if r['id']=='open-transaction')['amount_high'])
        person = result['priority_people'][0]
        self.assertEqual(person['reported_holding_count'], 2)
        self.assertEqual(person['reported_value_high'], 15000)
        self.assertEqual(person['reported_value_open_count'], 1)
        self.assertEqual([r['id'] for r in person['top_holdings']], ['bounded-holding'])
        self.assertEqual([r['id'] for r in person['open_holdings']], ['open-holding'])

    def test_missing_upper_bound_is_not_legal_open_and_duplicates_still_fail(self):
        processor = load('process_snapshot', version='v3')
        for kind, prefix in [('transactions', 'amount'), ('reported_holdings', 'value')]:
            data = fixture()
            del data[kind][1][prefix + '_low_exclusive']
            with self.subTest(kind=kind), self.assertRaises(processor.ProcessingError):
                processor.build_snapshot(data)
            with self.assertRaises(ValueError):
                normalize(data, allow_production=True)
        data = fixture()
        data['transactions'][1]['id'] = data['transactions'][0]['id']
        with self.assertRaises(processor.ProcessingError):
            processor.build_snapshot(data)

    def test_actual_final_builder_routes_open_holdings_and_transactions(self):
        for only_holdings in (False, True):
            data = fixture()
            if only_holdings:
                data['transactions'].pop()
            bundle = build(data, generated_at='2025-06-04T00:00:00Z', allow_production=True)
            self.assertEqual(bundle.manifest['processor_sha256'], PROCESSOR_V3_SHA256)
            self.assertEqual(bundle.manifest['amount_policy'], 'bounded-and-open-separate/v1')
            self.assertTrue(any(b'"value_high":null' in raw for raw in bundle.files.values()))

    def test_renderer_uses_unchanged_highest_html_with_explicit_approved_patch(self):
        renderer = load('render_dashboard', version='v3')
        html = renderer.render_html(load('process_snapshot', version='v3').build_snapshot(fixture()))
        self.assertIn('data.openHoldingsPanel', html.replace('dataset.openHoldingsPanel', 'data.openHoldingsPanel'))
        self.assertIn('upper limit not disclosed', html)
        self.assertIn('function renderApprovedBase()', html)
        self.assertNotIn('__LATEST_HTML_DATA_JSON__', html)

    def test_existing_public_reader_all_modes_preserve_open_semantics(self):
        data = fixture()
        bundle = build(data, generated_at='2025-06-04T00:00:00Z', allow_production=True)
        repository = PublicSnapshotRepository('example', 'data', transport=FakeTransport(bundle.files))
        for mode, key in [('dashboard', None), ('search', None),
                          ('person', data['people'][0]['id']), ('ticker', 'EXM')]:
            with self.subTest(mode=mode):
                snapshot = repository.fetch(mode, key).snapshot
                self.assertEqual(processor_version_for(snapshot), 'v3')
                processed = load('process_snapshot', version='v3').build_snapshot(snapshot)
                self.assertEqual(len(processed['transactions']), 2)
                self.assertEqual(len(processed['reported_holdings']), 2)
                self.assertEqual(processed['summary']['amount_high_30d'], 5000000)

    def test_open_input_order_does_not_change_built_files(self):
        data = fixture()
        first = build(data, generated_at='2025-06-04T00:00:00Z', allow_production=True)
        data['transactions'].reverse()
        data['reported_holdings'].reverse()
        second = build(data, generated_at='2025-06-04T00:00:00Z', allow_production=True)
        self.assertEqual(first.files, second.files)


if __name__ == '__main__':
    unittest.main()
