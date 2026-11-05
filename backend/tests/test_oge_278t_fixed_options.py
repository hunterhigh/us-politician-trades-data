from copy import deepcopy
import unittest

from unison_snapshot.oge_278t_fixed_options import align_type, align_amount, align_date, interpret_report, source_reporting_period


class FixedOptionsTests(unittest.TestCase):
    def test_complete_type_near_matches_only(self):
        for raw in ['ourchaso', 'lourchase', 'IPUrchaSO', '[purchase']:
            self.assertEqual(align_type(raw)['value'], 'purchase')
        for raw in ['', 'pu', 'purchase sale', 'sale/exchange', 'never purchase']:
            self.assertIsNone(align_type(raw)['value'])

    def test_unique_statutory_amount_and_full_cell(self):
        self.assertEqual(align_amount('$1.001 • $15 000')['value'], [1001, 15000])
        self.assertEqual(align_amount('$100.001 -$250 000')['value'], [100001, 250000])
        for raw in ['', '$100', '$1,001 - $15,000 $15,001 - $50,000', '$1,001\n$15,000', '$999 - $999', '$1,001 - $50,000']:
            self.assertIsNone(align_amount(raw)['value'])

    def test_dates_need_a_source_period_and_unique_calendar_candidate(self):
        for raw, expected in [('7/812026', '2026-07-08'), ('7/2912026', '2026-07-29')]:
            self.assertEqual(align_date(raw, period_start='2026-07-01', period_end='2026-07-31')['value'], expected)
        self.assertIsNone(align_date('7/2912026', period_start='2026-08-01', period_end='2026-08-31')['value'])
        self.assertIsNone(align_date('7/2912026', period_start=None, period_end=None)['value'])
        self.assertIsNone(align_date('2/3012026', period_start='2026-01-01', period_end='2026-12-31')['value'])
        self.assertEqual(align_date('111112026', period_start='2026-01-01', period_end='2026-12-31')['status'], 'ambiguous')
        self.assertEqual(align_date('111112026', period_start='2026-01-01', period_end='2026-01-31')['value'], '2026-01-11')

    def test_raw_cells_ids_and_existing_facts_are_immutable(self):
        old = {'extraction_id': 'old', 'transaction_type': 'sale'}
        row = {'extraction_id': 'new', 'cells': ['1', 'Asset', 'ourchaso', '7/8/2026', 'No', '$1.001 • $15 000'],
               'reasons': ['transaction_type_unsupported', 'amount_range_unsupported']}
        base = {'source_sha256': 'a'*64, 'transactions': [old], 'quarantined': [row]}
        frozen = deepcopy(base); value = interpret_report(base)
        self.assertEqual(base, frozen)
        self.assertEqual(value['transactions'][0], old)
        self.assertEqual(value['transactions'][1]['cells'], row['cells'])
        self.assertEqual(value['transactions'][1]['extraction_id'], 'new')
        self.assertEqual(value['quarantined'], [])

    def test_other_blockers_and_merged_rows_stay_quarantined(self):
        row = {'extraction_id': 'x', 'cells': ['1', 'Asset', 'ourchaso', '7/812026', 'No', ''],
               'reasons': ['transaction_type_unsupported', 'transaction_date_invalid', 'amount_range_unsupported', 'ocr_row_confidence_below_threshold']}
        value = interpret_report({'source_sha256': 'a'*64, 'transactions': [], 'quarantined': [row]})
        self.assertEqual(value['transactions'], [])
        self.assertIn('transaction_date_invalid', value['quarantined'][0]['reasons'])
        self.assertIn('amount_range_unsupported', value['quarantined'][0]['reasons'])
        row['cells'][0] = '1 2'
        value = interpret_report({'source_sha256': 'a'*64, 'transactions': [], 'quarantined': [row]})
        self.assertEqual(value['quarantined'], [row])
        row['cells'][0] = '1'
        row['cells'][4] = 'Yes Yes | $15,001'
        self.assertEqual(interpret_report({'source_sha256': 'a'*64, 'transactions': [], 'quarantined': [row]})['quarantined'], [row])

    def test_reporting_period_requires_bound_cover_and_explicit_label(self):
        from unison_snapshot.oge_reports import read_278t_cover_dates
        sha = 'a'*64
        text = 'Reporting Period: 7/1/2026 - 7/31/2026\n'
        report = {'source_sha256': sha, 'cover_date_evidence': read_278t_cover_dates(text, source_sha256=sha)}
        self.assertEqual(source_reporting_period(report), ('2026-07-01', '2026-07-31'))
        report['cover_date_evidence']['cover_text'] += 'edited'
        self.assertIsNone(source_reporting_period(report))
        text = 'Filer Signature\nDate: 8/1/2026\n'
        report['cover_date_evidence'] = read_278t_cover_dates(text, source_sha256=sha)
        self.assertIsNone(source_reporting_period(report))

    def test_unexplained_prior_quarantine_is_not_released(self):
        row = {'extraction_id': 'x', 'cells': ['1', 'Asset', 'ourchaso', '7/8/2026', 'No', '$1,001 - $15,000'],
               'reasons': ['transaction_type_unsupported']}
        unknown = {'extraction_id': 'u', 'cells': ['2', 'Asset', 'Sale', '7/8/2026', 'No', '$1,001 - $15,000'], 'reasons': []}
        result = interpret_report({'source_sha256': 'a'*64, 'transactions': [], 'quarantined': [row, unknown]})
        self.assertEqual(result['quarantined'], [unknown])
        self.assertEqual(len(result['transactions']), 1)

    def test_new_interpretation_cannot_escape_a_known_period(self):
        row = {'extraction_id': 'x', 'cells': ['1', 'Asset', 'ourchaso', '6/30/2026', 'No', '$1,001 - $15,000'],
               'transaction_date': '2026-06-30', 'reasons': ['transaction_type_unsupported']}
        result = interpret_report({'source_sha256': 'a'*64, 'transactions': [], 'quarantined': [row]},
                                  period=('2026-07-01', '2026-07-31'))
        self.assertEqual(result['transactions'], [])
        self.assertIn('transaction_date_outside_source_reporting_period', result['quarantined'][0]['reasons'])
