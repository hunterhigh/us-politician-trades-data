"""Source geometry corrections assert original observations and cached facts."""
from copy import deepcopy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from unison_snapshot.codec import encode, digest
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot import reviewed_whitehouse_ocr as reviewed


def fixture():
    raw = [{'page_number': 2, 'cells': ['bad', 'Example Inc.', 'Purchase', '01/01/2025',
        'No', '$1,001 - $15,000'], 'geometry_top': 100.0, 'ocr_confidence': 95.0}]
    corrected = [{**deepcopy(raw[0]), 'cells': ['1', *raw[0]['cells'][1:]],
        'raw_cells': raw[0]['cells'], 'printed_row_number_raw': 'bad',
        'printed_row_number_resolution': 'source_bound_geometry_order'}]
    identity = {'document_id': 'wh-url:'+'a'*24, 'source_url': 'https://www.whitehouse.gov/example.pdf',
        'source_sha256': 'a'*64}
    header = {**identity, 'source_id': 'oge', 'parser_version': 'whitehouse-278t-hybrid-geometry/v41',
        'page_count': 2, 'filed_at': '2025-01-02'}
    row = {**deepcopy(corrected[0]), 'geometry_row_index': 1, 'ocr_raw_cells': raw[0]['cells'],
        'source_bound_normalized_cells': corrected[0]['cells'], 'asset_name': 'Example Inc.',
        'transaction_date': '2025-01-01', 'amount_high': 15000}
    report = {**header, 'transactions': [row], 'quarantined': []}
    correction = {**identity, 'source': 'oge', 'schema_version': 'source-correction/v1',
        'reviewed_at': '2026-10-28', 'profile': reviewed.PROFILE, 'parser_version': header['parser_version'],
        'output_mode': 'source_listing_evidence', 'source_profile': {**identity, 'page_count': 2},
        'header': header, 'assertions': {'input_rows_sha256': digest(encode(raw)),
            'output_rows_sha256': digest(encode(corrected)), 'row_inventory': ['1'], 'page_row_counts': {'2': 1},
            'approved_artifact_sha256': [reviewed.artifact_fingerprint(report)]},
        'actions': [{'op': 'set_field', 'row': '1', 'field': 'printed_row_number', 'expect': 'bad',
            'value': '1', 'evidence': {'basis': 'source-bound review'}}]}
    return raw, corrected, report, correction


class ReviewedWhiteHouseOcrTests(unittest.TestCase):
    def test_current_correction_preserves_original_cells_and_input(self):
        raw, expected, _, correction = fixture()
        before = encode(raw)
        self.assertEqual(reviewed.apply_row_corrections(raw, correction), expected)
        self.assertEqual(encode(raw), before)
        for field in ('cells', 'geometry_top', 'page_number', 'ocr_confidence'):
            changed = deepcopy(raw)
            changed[0][field] = ['changed', *raw[0]['cells'][1:]] if field == 'cells' else -1
            with self.subTest(field=field), self.assertRaises(OgeCatalogError):
                reviewed.apply_row_corrections(changed, correction)
        with self.assertRaises(OgeCatalogError):
            reviewed.apply_row_corrections([], correction)

    def test_old_value_conflicts_and_unapproved_output_fail_atomically(self):
        raw, _, _, correction = fixture()
        for kind in ('expect', 'duplicate', 'output', 'operation'):
            changed = deepcopy(correction)
            if kind == 'expect': changed['actions'][0]['expect'] = []
            if kind == 'duplicate': changed['actions'].append(changed['actions'][0])
            if kind == 'output': changed['actions'][0]['value'] = '2'
            if kind == 'operation': changed['actions'][0]['op'] = 'drop_row'
            with self.subTest(kind=kind), self.assertRaises(OgeCatalogError):
                reviewed.apply_row_corrections(raw, changed)
        self.assertEqual(raw[0]['cells'][0], 'bad')

    def test_cache_rechecks_review_data_and_all_facts_without_rewriting(self):
        _, _, report, correction = fixture()
        before = encode(report)
        self.assertIs(reviewed.validate_reusable_report(report, correction), report)
        self.assertEqual(encode(report), before)
        for field in ('source_sha256', 'filed_at'):
            changed = deepcopy(report); changed[field] = 'changed'
            with self.subTest(field=field), self.assertRaises(OgeCatalogError):
                reviewed.validate_reusable_report(changed, correction)
        changed = deepcopy(report); changed['transactions'][0]['amount_high'] += 1
        with self.assertRaises(OgeCatalogError): reviewed.validate_reusable_report(changed, correction)
        changed = deepcopy(correction); changed['actions'][0]['expect'] = []
        with self.assertRaises(OgeCatalogError): reviewed.validate_reusable_report(report, changed)
        changed = deepcopy(correction); changed['header']['filed_at'] = '2025-01-03'
        with self.assertRaises(OgeCatalogError): reviewed.validate_reusable_report(report, changed)
        provenance = deepcopy(report); provenance.update(ocr_engine='saved engine', ocr_checkpoint={'created': 0})
        self.assertIs(reviewed.validate_reusable_report(provenance, correction), provenance)

    def test_registry_source_identity_and_empty_ordinary_lane(self):
        _, _, _, correction = fixture()
        with tempfile.TemporaryDirectory() as folder, patch.object(reviewed, 'ROOT', Path(folder)):
            self.assertIsNone(reviewed.profile_for(None, correction['source_url'], correction['source_sha256']))
            path = Path(folder)/(correction['document_id'].removeprefix('wh-url:')+'.json')
            path.write_bytes(encode(correction))
            self.assertEqual(reviewed.profile_for(correction['document_id'], correction['source_url'],
                correction['source_sha256']), correction)
            with self.assertRaisesRegex(OgeCatalogError, 'binding'):
                reviewed.profile_for('wrong', correction['source_url'], correction['source_sha256'])

    def test_cover_assertions_cannot_rewrite_source_identity(self):
        _, _, _, correction = fixture()
        report = {'filed_at': None}
        correction['cover_actions'] = [{'op': 'set_report', 'field': 'filed_at', 'expect': None,
            'value': '2025-01-02', 'evidence': {'basis': 'source review'}}]
        self.assertEqual(reviewed.apply_cover_corrections(report, correction), {'filed_at': '2025-01-02'})
        self.assertIsNone(report['filed_at'])
        with self.assertRaises(OgeCatalogError):
            reviewed.apply_cover_corrections({'filed_at': '2025-01-03'}, correction)
        correction['cover_actions'][0]['field'] = 'source_url'
        with self.assertRaises(OgeCatalogError): reviewed.apply_cover_corrections(report, correction)


if __name__ == '__main__':
    unittest.main()
