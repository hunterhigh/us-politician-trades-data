"""Count-unit/binding checks; the source consumer itself is tested separately."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.disclosure_status_oge import _apply_audit


class SavedWhitehouseStatusTests(unittest.TestCase):
    def fixture(self, root):
        sha='a'*64
        record={'report_id':'wh-url:'+'b'*24, 'source_url':'https://www.whitehouse.gov/wp-content/uploads/2026/07/source.pdf',
                'source_versions':[sha], 'reason_codes':[]}
        receipt={'schema_version':'whitehouse-278t-saved-ocr-receipt/v1',
            'mode':'saved_ocr_words','source_sha256':sha,'coverage_complete':False,
            'unique_source_row_count':None,'observed_word_page_count':2,
            'observed_physical_slot_count':0,'selected_page_numbers':[1,2],
            'unobserved_pages':[3],'geometry_unknown_pages':[2],
            'source_original_verified':False,'page_count_basis':'declared_prior_input_not_verified_original'}
        report={'document_id':record['report_id'],'source_url':record['source_url'],
            'source_sha256':sha,'parser_version':'whitehouse-278t-saved-ocr-roles/v6',
            'observation_receipt':receipt,'saved_ocr_input':{'checkpoint_sha256':'c'*64},
            'transactions':[],'quarantined':[]}
        cache=root/'whitehouse/extractions'/('b'*24)/sha/'saved.json'
        cache.parent.mkdir(parents=True)
        cache.write_text(json.dumps(report),encoding='utf-8')
        audit={**deepcopy(report),'promoted_count':0,'quarantined_count':0,'source_row_count':0,'rows':[],
               'document_reasons':['saved_source_geometry_unverified']}
        return record,audit,cache

    def test_saved_word_pages_do_not_become_source_zero_or_unique_row_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);record,audit,_=self.fixture(root)
            with patch('unison_snapshot.whitehouse_278t.validate_generic_whitehouse_report') as replay:
                _apply_audit(record,audit,review=root)
            replay.assert_called_once()
            self.assertEqual(record['qualification_state'],'quarantined')
            self.assertIsNone(record['quarantined_row_count'])
            self.assertEqual(record['observed_word_page_count'],2)
            self.assertEqual(record['observed_physical_slot_count'],0)
            self.assertEqual(record['source_observation_coverage']['geometry_unknown_pages'],[2])

    def test_source_receipt_refs_and_parser_cannot_diverge_from_bound_cache(self):
        for kind in ['source','receipt','refs','parser','missing_cache','duplicate_cache']:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);record,audit,cache=self.fixture(root)
                if kind=='source':audit['source_sha256']='d'*64
                if kind=='receipt':audit['observation_receipt']['coverage_complete']=True
                if kind=='refs':audit['saved_ocr_input']['checkpoint_sha256']='d'*64
                if kind=='parser':audit.pop('parser_version')
                if kind=='missing_cache':cache.unlink()
                if kind=='duplicate_cache':cache.with_name('duplicate.json').write_bytes(cache.read_bytes())
                with self.assertRaises(ValueError):_apply_audit(record,audit,review=root)

    def test_matching_receipt_cannot_bypass_consumer_replay_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);record,audit,_=self.fixture(root)
            with patch('unison_snapshot.whitehouse_278t.validate_generic_whitehouse_report',
                       side_effect=ValueError('checkpoint changed')):
                with self.assertRaisesRegex(ValueError,'checkpoint changed'):
                    _apply_audit(record,audit,review=root)

    def test_receipt_cannot_authorize_fabricated_counts_or_decisions(self):
        for kind in ['promoted_count','quarantined_count','source_row_count','rows','retained_count']:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);record,audit,_=self.fixture(root)
                audit[kind]=[{'extraction_id':'fake','status':'promoted'}] if kind=='rows' else 999
                with patch('unison_snapshot.whitehouse_278t.validate_generic_whitehouse_report'):
                    with self.assertRaisesRegex(ValueError,'conserve'):
                        _apply_audit(record,audit,review=root)


if __name__=='__main__':unittest.main()
