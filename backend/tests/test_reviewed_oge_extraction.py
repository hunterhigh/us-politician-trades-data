"""Reviewed facts cannot bypass source, old-value or artifact checks."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.codec import encode, digest
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot import reviewed_oge_extraction as reviewed
from unison_snapshot.oge_candidate import build_oge_candidate
from test_oge_candidate import extraction, direct, catalog, base


def fixture():
    original = extraction()
    corrected = deepcopy(original)
    corrected['parser_version'] = 'oge-278t-pdf/v41'
    corrected['transactions'][0]['asset_name'] = 'Reviewed Company'
    corrected['filing_date_evidence'] = {'filed_at': original['filed_at'], 'raw': '6/3/25'}
    identity = original['transactions'][0]['extraction_id']
    correction = {
        'schema_version': 'source-correction/v1', 'profile': reviewed.PROFILE,
        'source': 'oge', 'document_id': original['document_id'],
        'source_sha256': original['source_sha256'], 'reviewed_at': '2026-10-28',
        'base_parser_version': original['parser_version'],
        'parser_version': corrected['parser_version'],
        'parser_versions': [corrected['parser_version']],
        'assertions': {
            'metadata': {'document_id': original['document_id'],
                'document_url': original['source_url'], 'filer_name': original['catalog_filer_name'],
                'agency': original['agency'], 'position_title': original['position_title']},
            'person': {'id': 'oge:reviewed', 'display_name': 'Ada Example',
                'short_name': 'Example', 'role': 'Director'},
            'filing_date_evidence': corrected['filing_date_evidence'],
            'input_facts_sha256': digest(encode(original)),
            'input_row_inventory': [identity], 'row_inventory': [identity],
            'output_transactions': [identity], 'output_quarantined': [],
            'approved_output_sha256': [digest(encode(corrected))], 'legacy_versions': {},
        },
        'actions': [
            {'op': 'set_field', 'row': identity, 'field': 'record',
             'expect': original['transactions'][0], 'value': corrected['transactions'][0],
             'evidence': {'basis': 'source review'}},
            {'op': 'set_report', 'field': 'parser_version', 'expect': original['parser_version'],
             'value': corrected['parser_version'], 'evidence': {'basis': 'source review'}},
            {'op': 'set_report', 'field': 'filing_date_evidence', 'expect': None,
             'expect_present': False, 'value': corrected['filing_date_evidence'],
             'evidence': {'basis': 'source review'}},
        ],
    }
    return original, corrected, correction


class ReviewedOgeExtractionTests(unittest.TestCase):
    def test_original_assertions_preserve_ids_and_input(self):
        original, expected, correction = fixture()
        before = encode(original)
        self.assertEqual(reviewed.correct_extraction(original, correction), expected)
        self.assertEqual(encode(original), before)
        for change in [('source_sha256', 'b' * 64), ('parser_version', 'wrong')]:
            changed = deepcopy(original); changed[change[0]] = change[1]
            with self.subTest(change=change), self.assertRaises(OgeCatalogError):
                reviewed.correct_extraction(changed, correction)
        changed = deepcopy(original); changed['transactions'][0]['asset_name'] = 'Different old value'
        with self.assertRaisesRegex(OgeCatalogError, 'precondition'):
            reviewed.correct_extraction(changed, correction)

    def test_correction_cannot_rewrite_source_cells_or_identity(self):
        original, _, correction = fixture()
        for field in ('extraction_id', 'page_number', 'owner', 'amount_raw', 'transaction_type_raw', 'cells'):
            changed = deepcopy(correction); changed['actions'][0]['value'][field] = 'forged'
            with self.subTest(field=field), self.assertRaisesRegex(OgeCatalogError, 'source cells or ID'):
                reviewed.correct_extraction(original, changed)
        changed = deepcopy(correction)
        changed['actions'].append({'op': 'set_report', 'field': 'source_url',
            'expect': original['source_url'], 'value': 'https://example.com', 'evidence': {'basis': 'invalid'}})
        with self.assertRaisesRegex(OgeCatalogError, 'source identity'):
            reviewed.correct_extraction(original, changed)

    def test_changed_review_and_duplicate_actions_fail_atomically(self):
        original, _, correction = fixture()
        before = encode(original)
        changed = deepcopy(correction); changed['actions'][0]['value']['asset_name'] = 'Unapproved value'
        with self.assertRaisesRegex(OgeCatalogError, 'approved source output'):
            reviewed.correct_extraction(original, changed)
        changed = deepcopy(correction); changed['actions'].append(changed['actions'][0])
        with self.assertRaisesRegex(OgeCatalogError, 'Conflicting'):
            reviewed.correct_extraction(original, changed)
        changed = deepcopy(correction); changed['assertions']['output_transactions'] = []
        with self.assertRaisesRegex(OgeCatalogError, 'conserve'):
            reviewed.correct_extraction(original, changed)
        self.assertEqual(encode(original), before)

    def test_registry_binds_full_artifact_and_replays_only_valid_checkpoint(self):
        original, expected, correction = fixture()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); data = root/'corrections'; data.mkdir()
            path = data/(correction['document_id']+'.json'); path.write_bytes(encode(correction))
            with patch.object(reviewed, 'ROOT', data):
                self.assertEqual(reviewed.source_correction(correction['assertions']['metadata'],
                    correction['source_sha256']), correction)
                self.assertTrue(reviewed.source_binding(expected, direct()))
                result, _ = build_oge_candidate(catalog(), [expected], base(),
                    data_cutoff_at='2025-06-04T00:00:00Z')
                self.assertEqual(result['transactions'][0]['person_id'], 'oge:reviewed')
                forged = deepcopy(expected); forged['transactions'][0]['amount_high'] += 1
                self.assertFalse(reviewed.approved_extraction(forged))
                self.assertFalse(reviewed.source_binding(forged, direct()))
                result, audit = build_oge_candidate(catalog(), [forged], base(),
                    data_cutoff_at='2025-06-04T00:00:00Z')
                self.assertFalse(result['transactions'])
                self.assertIn('extraction_contract_invalid', audit['reports'][0]['document_reasons'])
                checkpoint = root/correction['document_id']/correction['source_sha256']/'oge-278t-pdf-v2.json'
                checkpoint.parent.mkdir(parents=True); checkpoint.write_bytes(encode(original))
                self.assertEqual(reviewed.replay_base_checkpoint(root, correction), expected)
                checkpoint.write_text('{}', encoding='utf-8')
                self.assertIsNone(reviewed.replay_base_checkpoint(root, correction))
                with self.assertRaisesRegex(OgeCatalogError, 'source hash'):
                    reviewed.source_correction(correction['assertions']['metadata'], 'b'*64)

    def test_ordinary_sources_do_not_require_reviewed_data(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(reviewed, 'ROOT', Path(folder)):
            self.assertIsNone(reviewed.correction_for(extraction()['document_id']))
            self.assertEqual(reviewed.parser_versions(), set())
            self.assertFalse(reviewed.source_binding(extraction(), direct()))
            self.assertIsNone(reviewed.correction_for('../escape'))


if __name__ == '__main__':
    unittest.main()
