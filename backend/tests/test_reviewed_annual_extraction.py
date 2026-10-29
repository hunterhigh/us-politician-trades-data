"""Annual source corrections conserve raw facts and assert every changed value."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.codec import encode, digest
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot import reviewed_annual_extraction as reviewed


def fixture():
    url = 'https://www.whitehouse.gov/wp-content/uploads/2026/annual-example.pdf'
    original = {'section': 'part2', 'page_number': 2, 'row_number': '1', 'owner': 'Self',
        'raw_columns': {'description': 'Example Asset', 'value': '|$1,001 to $15,000'},
        'reasons': ['holding_value_unreadable_or_open']}
    recovered = {**{k: v for k, v in original.items() if k != 'reasons'},
        'asset_name': 'Example Asset', 'raw_columns': {'description': 'Example Asset', 'value': '$1,001 to $15,000'},
        'value_low': 1001, 'value_high': 15000, 'ocr_recovery': {'method': reviewed.RECOVERY_METHOD,
            'source_sha256': 'a'*64, 'original_raw_columns': original['raw_columns'], 'original_reasons': original['reasons']}}
    before = {'source_id': 'whitehouse_public', 'source_url': url, 'source_sha256': 'a'*64,
        'parser_version': 'annual-layout/v1', 'filer_name': 'Example Filer', 'report_type': 'Annual',
        'report_period_end': '2025-12-31', 'holding_valuation_date': '2025-12-31',
        'filing_date': None, 'signature_evidence': None, 'holdings': [], 'quarantined': [original],
        'transactions': [], 'excluded': [], 'ocr_checkpoint': {'created_shard_count': 7, 'completed_page_count': 2}}
    evidence = {'type': 'handwritten_scan', 'signed_on': '2026-05-01', 'source_sha256': 'a'*64}
    after = {**deepcopy(before), 'holdings': [recovered], 'quarantined': [],
        'filing_date': '2026-05-01', 'signature_evidence': evidence}
    correction = {'schema_version': 'source-correction/v1', 'profile': reviewed.PROFILE, 'source': 'oge',
        'document_id': 'wh-url:'+digest(url.encode())[:24], 'source_url': url, 'source_sha256': 'a'*64,
        'reviewed_at': '2026-10-28', 'parser_version': before['parser_version'],
        'source_context': {k: before[k] for k in ['source_id', 'filer_name', 'report_type', 'report_period_end', 'holding_valuation_date']},
        'reserved_parser_versions': ['annual-layout/v1'], 'holding_page_ranges': {'part2': [2, 2]},
        'recovered_holdings': [recovered], 'existing_holding_approvals': [], 'variants': {},
        'assertions': {'row_inventory': ['quarantined/0'], 'input_sha256': reviewed.fingerprint(before),
            'output_sha256': reviewed.fingerprint(after), 'input_counts': {'holdings': 0, 'quarantined': 1},
            'output_counts': {'holdings': 1, 'quarantined': 0}},
        'actions': [
            {'op': 'set_report', 'field': 'filing_date', 'expect': None, 'value': after['filing_date'], 'evidence': {'page': 1}},
            {'op': 'set_report', 'field': 'signature_evidence', 'expect': None, 'value': evidence, 'evidence': {'page': 1}},
            {'op': 'set_field', 'row': 'quarantined/0', 'field': 'record', 'expect': original, 'value': recovered, 'evidence': {'page': 2}},
            {'op': 'set_field', 'row': 'quarantined/0', 'field': 'disposition', 'expect': 'quarantined', 'value': 'holdings', 'evidence': {'page': 2}}]}
    legacy_before = {**deepcopy(before), 'parser_version': 'annual-layout/v0'}
    legacy_after = {**deepcopy(after), 'parser_version': 'annual-layout/v0'}
    correction['variants']['annual-layout/v0'] = {'parser_version': 'annual-layout/v0',
        'actions': correction['actions'], 'assertions': {**correction['assertions'],
            'input_sha256': reviewed.fingerprint(legacy_before), 'output_sha256': reviewed.fingerprint(legacy_after)},
        'recovered_holdings': [recovered], 'existing_holding_approvals': []}
    return before, after, correction


class ReviewedAnnualExtractionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.patcher = patch.object(reviewed, 'ROOT', self.root)
        self.patcher.start(); self.addCleanup(self.patcher.stop)
        self.before, self.after, self.correction = fixture()
        self.path = self.root/(self.correction['document_id'].removeprefix('wh-url:')+'.json')
        self.save()

    def save(self):
        self.path.write_bytes(encode(self.correction))

    def test_recovery_preserves_input_and_unaffected_arrays(self):
        before = encode(self.before)
        result = reviewed.apply_reviewed_annual_corrections(self.before)
        self.assertEqual(result, self.after)
        self.assertEqual(encode(self.before), before)
        self.assertEqual(result['transactions'], self.before['transactions'])
        self.assertTrue(reviewed.scanned_signature_evidence_valid(result))
        self.assertTrue(reviewed.recovered_ocr_holding_valid(result['holdings'][0], result))
        forged = deepcopy(result['holdings'][0]); forged['value_high'] += 1
        self.assertFalse(reviewed.recovered_ocr_holding_valid(forged, result))

    def test_raw_and_old_value_changes_cannot_partially_apply(self):
        changed = deepcopy(self.before); changed['quarantined'][0]['row_number'] = '2'
        with self.assertRaisesRegex(OgeCatalogError, 'precondition'):
            reviewed.apply_reviewed_annual_corrections(changed)
        self.correction['actions'][0]['expect'] = 'different old date'; self.save()
        before = encode(self.before)
        with self.assertRaisesRegex(OgeCatalogError, 'precondition'):
            reviewed.apply_reviewed_annual_corrections(self.before)
        self.assertEqual(encode(self.before), before)

    def test_unknown_source_keeps_the_ordinary_lane(self):
        changed = deepcopy(self.before); changed['source_sha256'] = 'b'*64
        self.assertIs(reviewed.apply_reviewed_annual_corrections(changed), changed)
        self.assertFalse(reviewed.source_bound_parser_version_valid(changed))
        from unison_snapshot.oge_278e_public import PARSER_VERSION
        changed['parser_version'] = PARSER_VERSION
        self.assertTrue(reviewed.source_bound_parser_version_valid(changed))
        self.assertFalse(reviewed.scanned_signature_evidence_valid(changed))

    def test_record_conservation_and_output_hash_are_checked(self):
        self.correction['assertions']['output_counts']['holdings'] = 2; self.save()
        with self.assertRaisesRegex(OgeCatalogError, 'approved output'):
            reviewed.apply_reviewed_annual_corrections(self.before)
        self.correction['assertions']['output_counts']['holdings'] = 1
        self.correction['actions'][0]['field'] = 'source_url'; self.save()
        with self.assertRaisesRegex(OgeCatalogError, 'unsupported'):
            reviewed.apply_reviewed_annual_corrections(self.before)

    def test_ingress_validates_registered_sources_without_rewriting_checkpoints(self):
        original = encode(self.before)
        self.assertIs(reviewed.validate_reviewed_checkpoint(self.before), self.before)
        self.assertEqual(encode(self.before), original)
        for field, value in [('source_sha256', 'b'*64), ('filer_name', 'Other Filer')]:
            changed = {**self.before, field: value}
            with self.assertRaisesRegex(OgeCatalogError, 'binding changed'):
                reviewed.validate_reviewed_checkpoint(changed)
        changed = deepcopy(self.before); changed['transactions'].append({'unexpected': True})
        with self.assertRaisesRegex(OgeCatalogError, 'precondition changed'):
            reviewed.validate_reviewed_checkpoint(changed)
        ordinary = {**self.before, 'source_url': 'https://example.org/ordinary.pdf'}
        self.assertIs(reviewed.validate_reviewed_checkpoint(ordinary), ordinary)

    def test_legacy_variant_and_operational_resume_counter_reuse(self):
        legacy = deepcopy(self.before); legacy['parser_version'] = 'annual-layout/v0'
        expected = deepcopy(self.after); expected['parser_version'] = 'annual-layout/v0'
        self.assertEqual(reviewed.apply_reviewed_annual_corrections(legacy), expected)
        resumed = deepcopy(self.before); resumed['ocr_checkpoint']['created_shard_count'] = 0
        actual = reviewed.apply_reviewed_annual_corrections(resumed)
        self.assertEqual(actual['ocr_checkpoint']['created_shard_count'], 0)
        self.assertEqual(actual['holdings'], self.after['holdings'])
        actual['ocr_checkpoint']['completed_page_count'] = 1
        self.assertFalse(reviewed.scanned_signature_evidence_valid(actual))


if __name__ == '__main__':
    unittest.main()
