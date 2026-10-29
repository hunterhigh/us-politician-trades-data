"""Source-bound interpretation of official two-ended Exchange ticker cells."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.builder import build
from unison_snapshot.senate import SenateEfdError
from unison_snapshot.senate_candidate import build_senate_candidate
from unison_snapshot.senate_reports import parse_electronic_ptr
from tests import test_senate_candidate as fixtures
from tests.test_senate_reports import html, metadata_for


class SenateExchangeSourceTests(unittest.TestCase):
    def fixture(self, folder, *, ticker='--<br/><a href="https://finance.yahoo.com/quote/AMCR">AMCR</a>',
                asset='Berry Global (Exchanged)<br/>Amcor (Received)', direction='Exchange',
                date='09/01/2026', amount='$100,001 - $250,000'):
        raw = html(rows=[['1', date, 'Spouse', ticker, asset, 'Stock', direction, amount, '--']])
        metadata = metadata_for(raw)
        extraction = parse_electronic_ptr(metadata, raw)
        root = Path(folder) / 'review'
        with patch.object(fixtures, 'PARSER', extraction['parser_version']):
            state = fixtures.SenateCandidateTests().fixture(
                root, [fixtures.identity(extraction['document_id'])], [extraction])
        evidence = Path(folder) / 'evidence'
        prefix = evidence / 'senate_efd/reports' / extraction['document_id'] / extraction['source_sha256']
        prefix.parent.mkdir(parents=True)
        prefix.with_suffix('.html').write_bytes(raw)
        prefix.with_suffix('.metadata.json').write_text(json.dumps(metadata), encoding='utf8')
        return root, evidence, state, extraction, prefix

    def test_two_ended_exchange_has_no_single_ticker_preserves_raw_id_and_repeats(self):
        with tempfile.TemporaryDirectory() as folder:
            root, evidence, state, extraction, prefix = self.fixture(folder)
            original = deepcopy(extraction)
            candidate, audit = build_senate_candidate(root, state, deepcopy(fixtures.BASE), evidence_root=evidence)
            self.assertEqual(len(candidate['transactions']), 1)
            tx = candidate['transactions'][0]
            self.assertEqual(tx['id'], extraction['transactions'][0]['extraction_id'])
            self.assertIsNone(tx['ticker'])
            self.assertIsNone(tx['ticker_mapping_basis'])
            self.assertEqual(tx['asset_name'], 'Berry Global (Exchanged) Amcor (Received)')
            self.assertEqual(tx['transaction_type'], 'exchange')
            self.assertEqual((tx['amount_low'], tx['amount_high']), (100001, 250000))
            self.assertEqual(audit['exchange_ticker_interpretations'][0]['ticker_parts_raw'], ['--', 'AMCR'])
            self.assertEqual(extraction, original)
            second, _ = build_senate_candidate(root, state, deepcopy(candidate), evidence_root=evidence)
            self.assertEqual(second['transactions'], candidate['transactions'])
            built = build(candidate, generated_at=state['run_at'], allow_production=True)
            self.assertTrue(built.manifest['snapshot_id'])

    def test_unknown_end_can_be_received_without_assigning_exchanged_ticker_to_row(self):
        with tempfile.TemporaryDirectory() as folder:
            root, evidence, state, _, _ = self.fixture(folder, ticker='BERY<br/>--')
            candidate, _ = build_senate_candidate(root, state, deepcopy(fixtures.BASE), evidence_root=evidence)
            self.assertIsNone(candidate['transactions'][0]['ticker'])

    def test_no_source_cannot_use_derived_or_self_asserted_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            root, _, state, extraction, _ = self.fixture(folder)
            extraction['transactions'][0]['exchange_ticker_interpretation'] = {
                'basis': 'source_html_two_ended_exchange_has_no_single_ticker', 'ticker': None}
            path = next((root/'senate_efd/extractions').glob('*/*/*.json'))
            path.write_text(json.dumps(extraction), encoding='utf8')
            candidate, audit = build_senate_candidate(root, state, deepcopy(fixtures.BASE))
            self.assertEqual(candidate['transactions'], [])
            self.assertIn('ticker_invalid', audit['quarantined_row_reasons'])
            self.assertEqual(audit['exchange_ticker_interpretations'], [])

    def test_ordinary_invalid_ticker_and_unproven_exchange_segments_stay_blocked(self):
        for updates in [dict(direction='Purchase'), dict(ticker='-- AMCR'),
                        dict(ticker='BERY<br/>AMCR'), dict(ticker='--<br/>???'),
                        dict(asset='Berry Global<br/>Amcor'),
                        dict(asset='Berry Global (Received)<br/>Amcor (Exchanged)')]:
            with self.subTest(updates=updates), tempfile.TemporaryDirectory() as folder:
                root, evidence, state, _, _ = self.fixture(folder, **updates)
                candidate, audit = build_senate_candidate(root, state, deepcopy(fixtures.BASE), evidence_root=evidence)
                self.assertEqual(candidate['transactions'], [])
                self.assertIn('ticker_invalid', audit['quarantined_row_reasons'])
                self.assertEqual(audit['exchange_ticker_interpretations'], [])

    def test_date_and_amount_blockers_are_not_removed_by_ticker_interpretation(self):
        for updates, reason in [(dict(date='09/18/2026'), 'date_sequence_invalid'),
                                (dict(amount='Unknown'), 'amount_range_not_supported')]:
            with self.subTest(updates=updates), tempfile.TemporaryDirectory() as folder:
                root, evidence, state, _, _ = self.fixture(folder, **updates)
                candidate, audit = build_senate_candidate(root, state, deepcopy(fixtures.BASE), evidence_root=evidence)
                self.assertEqual(candidate['transactions'], [])
                self.assertIn(reason, audit['quarantined_row_reasons'])
                self.assertNotIn('ticker_invalid', audit['quarantined_row_reasons'])

    def test_changed_extraction_fields_cannot_borrow_original_structure(self):
        for field, value in [('asset_name_raw', 'Forged'), ('amount_raw', '$1,001 - $15,000'),
                             ('transaction_date', '2026-09-02'), ('ticker_raw', '-- OTHER')]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as folder:
                root, evidence, state, extraction, _ = self.fixture(folder)
                extraction['transactions'][0][field] = value
                path = next((root/'senate_efd/extractions').glob('*/*/*.json'))
                path.write_text(json.dumps(extraction), encoding='utf8')
                with self.assertRaisesRegex(SenateEfdError, 'differs from archived v2'):
                    build_senate_candidate(root, state, deepcopy(fixtures.BASE), evidence_root=evidence)

    def test_changed_original_hash_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root, evidence, state, _, prefix = self.fixture(folder)
            prefix.with_suffix('.html').write_bytes(prefix.with_suffix('.html').read_bytes()+b' ')
            with self.assertRaisesRegex(SenateEfdError, 'bytes do not match'):
                build_senate_candidate(root, state, deepcopy(fixtures.BASE), evidence_root=evidence)


if __name__ == '__main__':
    unittest.main()
