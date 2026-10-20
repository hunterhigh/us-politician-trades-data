"""Count-unit/binding checks; the source consumer itself is tested separately."""
from copy import deepcopy
import hashlib
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
        for kind in ['source','receipt','refs','parser','filed_date','identity_resolution','missing_cache','duplicate_cache',
                     'dispatch_tags','dispatch_tags_and_schema']:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);record,audit,cache=self.fixture(root)
                if kind=='source':audit['source_sha256']='d'*64
                if kind=='receipt':audit['observation_receipt']['coverage_complete']=True
                if kind=='refs':audit['saved_ocr_input']['checkpoint_sha256']='d'*64
                if kind=='parser':audit.pop('parser_version')
                if kind=='filed_date':audit['filed_at']='2027-01-01'
                if kind=='identity_resolution':audit['source_identity_resolution']={'status':'resolved'}
                if kind in {'dispatch_tags','dispatch_tags_and_schema'}:
                    audit.pop('parser_version')
                    audit['observation_receipt'].pop('mode')
                    if kind=='dispatch_tags_and_schema':audit['observation_receipt'].pop('schema_version')
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

    def test_identity_resolution_must_equal_replayed_cache_even_when_counts_match(self):
        for change in [None,'removed','person','missing_fields']:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);record,audit,cache=self.fixture(root)
                report=json.loads(cache.read_text(encoding='utf-8'))
                resolution={'status':'resolved','catalog_identity':{'filer_name':'Synthetic Person'},
                            'resolved_missing_fields':['name']}
                report['source_identity_resolution']=deepcopy(resolution)
                cache.write_text(json.dumps(report),encoding='utf-8')
                audit['source_identity_resolution']=deepcopy(resolution)
                if change=='removed':audit.pop('source_identity_resolution')
                if change=='person':audit['source_identity_resolution']['catalog_identity']['filer_name']='Other Person'
                if change=='missing_fields':audit['source_identity_resolution']['resolved_missing_fields']=[]
                # This test isolates audit/cache binding. Full source reproof is
                # exercised by the saved-input materializer tests.
                with patch('unison_snapshot.whitehouse_278t.validate_generic_whitehouse_report'):
                    if change is None:_apply_audit(record,audit,review=root)
                    else:
                        with self.assertRaisesRegex(ValueError,'source cache'):_apply_audit(record,audit,review=root)

    def test_receipt_cannot_authorize_fabricated_counts_or_decisions(self):
        for kind in ['promoted_count','quarantined_count','source_row_count','rows','retained_count']:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);record,audit,_=self.fixture(root)
                audit[kind]=[{'extraction_id':'fake','status':'promoted'}] if kind=='rows' else 999
                with patch('unison_snapshot.whitehouse_278t.validate_generic_whitehouse_report'):
                    with self.assertRaisesRegex(ValueError,'conserve'):
                        _apply_audit(record,audit,review=root)

    def test_document_blocker_cannot_be_removed_or_overridden_by_promotion(self):
        for keep_blocker in [False, True]:
            with self.subTest(keep_blocker=keep_blocker),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);record,audit,cache=self.fixture(root)
                report=json.loads(cache.read_text(encoding='utf-8'))
                report.update(filed_at='2026-05-08',
                    document_reasons=['ocr_source_filing_date_conflict'],
                    transactions=[{'extraction_id':'observed-one','reasons':[]}])
                report['observation_receipt']['source_original_verified']=True
                cache.write_text(json.dumps(report),encoding='utf-8')
                audit.update(filed_at=report['filed_at'],
                    observation_receipt=deepcopy(report['observation_receipt']),
                    document_reasons=report['document_reasons'][:] if keep_blocker else [],
                    source_row_count=1,promoted_count=1,quarantined_count=0,
                    rows=[{'extraction_id':'observed-one','status':'promoted','reasons':[]}])
                with patch('unison_snapshot.whitehouse_278t.validate_generic_whitehouse_report'):
                    with self.assertRaisesRegex(ValueError,'document blocker|promotion lacks'):
                        _apply_audit(record,audit,review=root)

    def test_partial_regions_words_and_known_slots_are_bound_and_total_stays_unknown(self):
        for change in [None,'regions','words','known_count','unit_removed']:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);record,audit,cache=self.fixture(root)
                report=json.loads(cache.read_text(encoding='utf-8'))
                partial=[{'page_number':2,'known_physical_slot_count':2,
                    'whole_page_physical_slot_count':None,'unresolved_regions':[[100,120]],
                    'unbound_page_ocr_words':[{'word_index':1},{'word_index':2}]}]
                report['content_observations']={'partial_geometry_pages':partial}
                report['raw_page_observations']=[{'page_number':2,'words':[{},{}]}]
                report['observation_receipt'].update(local_row_geometry_mode='strict_joint_boundaries/v1',
                    geometry_partial_pages=[2],unresolved_regions=[{'page_number':2,'regions':[[100,120]]}],
                    whole_selected_table_slot_count=None,unbound_observed_word_count=2,
                    observed_physical_slot_count=2,
                    unbound_observed_word_unit='selected_table_page_words_outside_known_slots',
                    unbound_observed_word_pages=[{'page_number':2,'word_count':2,'basis':'partial_grid_unbound_saved_words'}])
                cache.write_text(json.dumps(report),encoding='utf-8')
                audit.update(observation_receipt=deepcopy(report['observation_receipt']),
                             partial_geometry_pages=deepcopy(partial))
                if change=='regions':audit['partial_geometry_pages'][0]['unresolved_regions']=[]
                if change=='words':audit['partial_geometry_pages'][0]['unbound_page_ocr_words']=[]
                if change=='known_count':audit['partial_geometry_pages'][0]['whole_page_physical_slot_count']=2
                if change=='unit_removed':
                    report['observation_receipt'].pop('local_row_geometry_mode')
                    audit['observation_receipt']=deepcopy(report['observation_receipt'])
                    cache.write_text(json.dumps(report),encoding='utf-8')
                with patch('unison_snapshot.whitehouse_278t.validate_generic_whitehouse_report'):
                    if change is not None:
                        with self.assertRaises(ValueError):_apply_audit(record,audit,review=root)
                    else:
                        _apply_audit(record,audit,review=root)
                        coverage=record['source_observation_coverage']
                        self.assertIsNone(coverage['whole_selected_table_slot_count'])
                        self.assertEqual(coverage['unbound_observed_word_count'],2)
                        self.assertEqual(coverage['geometry_partial_pages'],[2])

    def test_dual_axis_status_requires_explicit_consistent_mode_without_migrating_v6(self):
        for change in [None,'audit','input','report','receipt','local','old_parser','missing_parser']:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);record,audit,cache=self.fixture(root)
                report=json.loads(cache.read_text(encoding='utf-8'))
                mode='source_pdf_dual_axis/v1'
                report.update(parser_version='whitehouse-278t-saved-ocr-dual-axis/v7',geometry_mode=mode,
                    raw_page_observations=[{'page_number':2,'words':[{}, {}, {}]}])
                report['saved_ocr_input']['geometry_mode']=mode
                report['observation_receipt'].update(geometry_mode=mode,local_row_geometry_mode=mode,
                    geometry_partial_pages=[],unresolved_regions=[],whole_selected_table_slot_count=None,
                    unbound_observed_word_count=3,
                    unbound_observed_word_unit='selected_table_page_words_outside_known_slots',
                    unbound_observed_word_pages=[{'page_number':2,'word_count':3,
                                                'basis':'geometry_unknown_all_saved_words'}])
                if change=='report':report.pop('geometry_mode')
                if change=='input':report['saved_ocr_input'].pop('geometry_mode')
                if change=='receipt':report['observation_receipt'].pop('geometry_mode')
                if change=='local':report['observation_receipt'].pop('local_row_geometry_mode')
                if change=='old_parser':report['parser_version']='whitehouse-278t-saved-ocr-roles/v6'
                cache.write_text(json.dumps(report),encoding='utf-8')
                audit.update(parser_version=report['parser_version'],geometry_mode=mode,
                    saved_ocr_input=deepcopy(report['saved_ocr_input']),
                    observation_receipt=deepcopy(report['observation_receipt']))
                if change=='audit':audit.pop('geometry_mode')
                if change=='missing_parser':audit.pop('parser_version')
                # An old cache can coexist; it must not be mistaken for a second v7 source.
                if change is None:
                    old=deepcopy(report);old['parser_version']='whitehouse-278t-saved-ocr-roles/v6'
                    cache.with_name('old-v6.json').write_text(json.dumps(old),encoding='utf-8')
                with patch('unison_snapshot.whitehouse_278t.validate_generic_whitehouse_report'):
                    if change is None:
                        _apply_audit(record,audit,review=root)
                        self.assertIsNone(record['quarantined_row_count'])
                        self.assertEqual(record['source_observation_coverage']['local_row_geometry_mode'],mode)
                    else:
                        with self.assertRaises(ValueError):_apply_audit(record,audit,review=root)

    def test_dual_retained_ids_require_current_replayed_source_and_qualification(self):
        for change in [None,'binding','missing_binding','ids','count','source_reason','document_reason','source_quarantine']:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);record,audit,cache=self.fixture(root)
                report=json.loads(cache.read_text(encoding='utf-8'))
                mode='source_pdf_dual_axis/v1';parser='whitehouse-278t-saved-ocr-dual-axis/v7'
                source={'extraction_id':'retained-one','source_rows_index':0,'page_number':2,
                    'cells':['1','ALPHA','purchase','01/01/2026','No','$1,001 - $15,000'],'reasons':[]}
                report.update(parser_version=parser,geometry_mode=mode,filed_at='2026-05-08',
                    document_reasons=[],transactions=[source],raw_page_observations=[{'page_number':2,'words':[{}]}])
                report['saved_ocr_input']['geometry_mode']=mode
                report['observation_receipt'].update(geometry_mode=mode,local_row_geometry_mode=mode,
                    source_original_verified=True,geometry_partial_pages=[],unresolved_regions=[],
                    whole_selected_table_slot_count=None,unbound_observed_word_count=1,
                    unbound_observed_word_unit='selected_table_page_words_outside_known_slots',
                    unbound_observed_word_pages=[{'page_number':2,'word_count':1,
                                                'basis':'geometry_unknown_all_saved_words'}])
                binding={'schema_version':'whitehouse-saved-current-retained/v1',
                    'source_sha256':report['source_sha256'],'document_id':report['document_id'],
                    'parser_version':parser,'geometry_mode':mode,'rows':[{
                        'formal_id':'retained-one','source_rows_index':0,'page_number':2,
                        'raw_cells_canonical_sha256':hashlib.sha256(json.dumps(source['cells'],
                            ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                        'basis':'current_source_replayed_exact_formal_projection'}]}
                if change=='source_reason':source['reasons']=['date_unreadable']
                if change=='document_reason':report['document_reasons']=['ocr_source_filing_date_conflict']
                if change=='source_quarantine':report.update(transactions=[],quarantined=[source])
                cache.write_text(json.dumps(report),encoding='utf-8')
                audit.update(parser_version=parser,geometry_mode=mode,filed_at=report['filed_at'],
                    saved_ocr_input=deepcopy(report['saved_ocr_input']),document_reasons=report['document_reasons'][:],
                    observation_receipt=deepcopy(report['observation_receipt']),promoted_count=0,
                    source_row_count=1,quarantined_count=0,retained_count=1,retained_ids=['retained-one'],
                    current_source_retained_bindings=deepcopy(binding),
                    rows=[{'extraction_id':'retained-one','status':'retained_existing','reasons':[]}])
                if change=='binding':audit['current_source_retained_bindings']['rows'][0]['raw_cells_canonical_sha256']='d'*64
                if change=='missing_binding':audit.pop('current_source_retained_bindings')
                if change=='ids':audit['retained_ids']=['different']
                if change=='count':audit['retained_count']=2
                with patch('unison_snapshot.whitehouse_278t.validate_generic_whitehouse_report'):
                    if change is None:
                        _apply_audit(record,audit,review=root)
                        self.assertEqual(record['qualified_transaction_count'],1)
                        self.assertEqual(record['qualified_retained_transaction_ids'],['retained-one'])
                        self.assertEqual(record['qualification_state'],'partial')
                    else:
                        with self.assertRaises(ValueError):_apply_audit(record,audit,review=root)

    def test_whole_unknown_geometry_keeps_all_saved_words_unbound(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);record,audit,cache=self.fixture(root)
            report=json.loads(cache.read_text(encoding='utf-8'))
            report['raw_page_observations']=[{'page_number':1,'words':[{}]},
                                              {'page_number':2,'words':[{}, {}, {}]}]
            report['observation_receipt'].update(local_row_geometry_mode='strict_joint_boundaries/v1',
                geometry_partial_pages=[],unresolved_regions=[],whole_selected_table_slot_count=None,
                unbound_observed_word_count=3,
                unbound_observed_word_unit='selected_table_page_words_outside_known_slots',
                unbound_observed_word_pages=[{'page_number':2,'word_count':3,'basis':'geometry_unknown_all_saved_words'}])
            cache.write_text(json.dumps(report),encoding='utf-8')
            audit['observation_receipt']=deepcopy(report['observation_receipt'])
            with patch('unison_snapshot.whitehouse_278t.validate_generic_whitehouse_report'):
                _apply_audit(record,audit,review=root)
            self.assertEqual(record['source_observation_coverage']['unbound_observed_word_count'],3)
            self.assertIsNone(record['quarantined_row_count'])


if __name__=='__main__':unittest.main()
