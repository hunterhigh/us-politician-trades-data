"""Bounded amendment-reference contracts; mocked native pages are not accuracy gold."""
from copy import deepcopy
import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_oge_native_exhibit import observation,empty_source
from test_oge_candidate import direct, DOCUMENT_ID
from unison_snapshot.oge_reports import (_native_amendment_context,_native_exhibit_context_target,
    materialize_saved_oge_278t_exhibits,_native_exhibit_period_date,parse_table_rows)
from unison_snapshot.oge import OgeCatalogError

SOURCE_URL='https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/'+DOCUMENT_ID+'/$FILE/Example-08.12.2025-278T(2)%20AMENDED.pdf'
TARGET_URL='https://www.whitehouse.gov/wp-content/uploads/2025/08/Example-Periodic-Transaction-Report-8.12.25-2.pdf'
COVER=('AMENDMENT TO EXECUTIVE BRANCH PERSONNEL PUBLIC FINANCIAL\nDISCLOSURE REPORT\nPERIODIC TRANSACTION REPORT\n'
       'Filer: Ada Example\nReporting Period: July 1, 2025 - July 23, 2025\nDate of Report: August 12, 2025\n'
       'The purpose of this amendment is to modify certain transaction amounts as set forth on Exhibit A.\n'
       'This amendment will be attached to and become a part of the above-referenced report.\n'
       'Agency review: report originally signed on August 12, 2025.\n')

def cover(text=COVER):
    value=observation();value.update(page_number=1,page_text=text,page_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        tables=[],table_bounds=[],words=[{'text':line,'x0':5.,'x1':590.,'top':float(10+20*n),'bottom':float(20+20*n)} for n,line in enumerate(text.splitlines())])
    return value


def target(root):
    content=b'%PDF-1.7\nsynthetic previous source\n%%EOF';sha=hashlib.sha256(content).hexdigest()
    path=root/'original.pdf';path.write_bytes(content)
    meta={'schema_version':'whitehouse-public-disclosures-pdf/v1','source_id':'whitehouse_public_disclosures',
        'document_id':'wh-url:'+hashlib.sha256(TARGET_URL.encode()).hexdigest()[:24],'sha256':sha,
        'byte_length':len(content),'archive_path':'original.pdf','document_url':TARGET_URL,
        'filer_name_from_label':'Ada Example','link_label':'Ada Example Periodic Transaction Report 08.12.25 (2)',
        'document_type_from_label':'278t'}
    metadata=root/'metadata.json';metadata.write_text(json.dumps(meta),encoding='utf-8')
    return {'evidence_root':str(root),'archive_metadata_path':str(metadata)},meta


def anchors(*args):
    return {n:[{'printed_number_raw':str(n),'page_number':2,'word_index':n,
                'basis':'synthetic_native_marker_not_ordinal'}] for n in args[-1]}


class NativeAmendmentContextTests(unittest.TestCase):
    def test_period_date_requires_unique_interpretation_and_matching_context(self):
        context=_native_amendment_context(cover(),SOURCE_URL)
        relation={'status':'candidate_context_match','candidate':{
            'source_context_sha256':context['source_observation_canonical_sha256']}}
        for raw,expected in [('7/17/25','2025-07-17'),('7/ 11/25','2025-07-11'),('7/ 7 /25','2025-07-07')]:
            self.assertEqual(_native_exhibit_period_date(raw,context,relation)['normalized_value'],expected)
        for raw in ['11/7/25','7/24/25','7/17/24','7/l7/25','7/32/25']:
            self.assertIsNone(_native_exhibit_period_date(raw,context,relation))
        wide=deepcopy(context);wide['original_reporting_period']=['1925-01-01','2025-12-31']
        self.assertIsNone(_native_exhibit_period_date('7/11/25',wide,relation))
        relation['candidate']['source_context_sha256']='forged'
        self.assertIsNone(_native_exhibit_period_date('7/17/25',context,relation))
        self.assertIsNone(_native_exhibit_period_date('7/17/25',context,{'status':'pending'}))

    def test_period_date_consumed_by_adapter_candidate_preserves_raw_id_and_other_blocks(self):
        from test_oge_candidate import base,catalog
        from unison_snapshot.oge_candidate import build_oge_candidate
        from unison_snapshot.oge_transaction_adapter import adapt_oge_278t_extraction
        with tempfile.TemporaryDirectory() as folder:
            bundle,_=target(Path(folder));source=empty_source();source['source_url']=SOURCE_URL
            record=direct();record['document_url']=SOURCE_URL;cat=catalog();cat['records']=[record]
            obs=observation();old='05/01/2025';new='7/ 11/25'
            obs['tables'][0][1][3]=new
            for word in obs['words']:
                if word['text']==old:word['text']=new
            obs['page_text']=obs['page_text'].replace(old,new)
            obs['page_text_sha256']=hashlib.sha256(obs['page_text'].encode()).hexdigest()
            inputs={'observations':[obs],'archived_pdf_path':'mock.pdf','replacement_targets':[bundle],
                    'amendment_context_page_number':1}
            def native(path,*,source_sha256,page_number):return cover() if page_number==1 else obs
            with patch('unison_snapshot.oge_278t_page_coverage.observe_native_exhibit_page',side_effect=native),patch(
                    'unison_snapshot.oge_reports._native_original_printed_anchors',side_effect=anchors):
                result=materialize_saved_oge_278t_exhibits(source,record,**inputs)
                row=result['quarantined'][0]
                self.assertEqual(row['transaction_date'],'2025-07-11')
                self.assertNotIn('transaction_date_invalid',row['reasons'])
                self.assertIn('replacement_target_extraction_row_unverified',row['reasons'])
                self.assertIsNone(result['filed_at'])
                _,raw=parse_table_rows([(3,row['cells'])],source_sha=source['source_sha256'],default_owner='Undisclosed')
                self.assertEqual(row['extraction_id'],raw[0]['extraction_id'])
                self.assertEqual(row['cells'][3],new)
                ledger=adapt_oge_278t_extraction(result,record)
                date=next(o for o in ledger['rows'][0]['observations'] if o['field']=='transaction_date')
                self.assertEqual((date['raw_value'],date['normalized_value']),(new,'2025-07-11'))
                candidate,audit=build_oge_candidate(cat,[source],base(),data_cutoff_at='2025-08-20T00:00:00Z',
                    saved_exhibit_observations_by_document={DOCUMENT_ID:inputs})
                self.assertEqual(candidate['transactions'],[])
                ref=audit['reports'][0]['quarantined'][0]['native_exhibit_reference']
                self.assertEqual(ref['transaction_date_interpretation']['normalized_value'],'2025-07-11')
                tampered=deepcopy(result);tampered['quarantined'][0]['transaction_date']='2025-07-12'
                with self.assertRaises(OgeCatalogError):adapt_oge_278t_extraction(tampered,record)

    def test_reference_date_is_not_filer_date_and_wrong_cover_date_is_unresolved(self):
        actual=_native_amendment_context(cover(),SOURCE_URL)
        self.assertEqual(actual['status'],'source_context_available')
        self.assertEqual(actual['original_report_date'],'2025-08-12')
        self.assertEqual(actual['original_reporting_period'],['2025-07-01','2025-07-23'])
        self.assertNotIn('filed_at',actual)
        self.assertEqual(actual['page_text'],COVER)
        changed=_native_amendment_context(cover(COVER.replace('Date of Report: August 12','Date of Report: August 13')),SOURCE_URL)
        self.assertEqual(changed['status'],'unresolved')
        self.assertIn('amendment_report_reference_dates_conflict',changed['reasons'])
        tampered=cover();tampered['page_text']='forged context'
        with self.assertRaises(ValueError):_native_amendment_context(tampered,SOURCE_URL)

    def test_context_resolves_candidate_not_applied_or_qualified_row(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle,meta=target(Path(folder));context=_native_amendment_context(cover(),SOURCE_URL)
            with patch('unison_snapshot.oge_reports._native_original_printed_anchors',side_effect=anchors):
                result=_native_exhibit_context_target({'target_printed_number':27},[bundle],context,[27])
            self.assertEqual(result['status'],'candidate_context_match')
            self.assertEqual(result['candidate']['target_document_id'],meta['document_id'])
            self.assertNotIn('target_extraction_id',result['candidate'])
            self.assertIn('replacement_target_extraction_row_unverified',result['reasons'])
            self.assertIn('not_applied',result['application_status'])

    def test_wrong_part_date_person_and_multiple_candidates_stay_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle,meta=target(Path(folder));context=_native_amendment_context(cover(),SOURCE_URL)
            for field,value in [('original_report_component',1),('original_report_date','2025-08-13'),('filer_name_raw','Another Person')]:
                changed=deepcopy(context);changed[field]=value
                with self.subTest(field=field),patch('unison_snapshot.oge_reports._native_original_printed_anchors',side_effect=anchors):
                    actual=_native_exhibit_context_target({'target_printed_number':27},[bundle],changed,[27])
                    self.assertEqual(actual['status'],'pending')
            with patch('unison_snapshot.oge_reports._native_original_printed_anchors',side_effect=anchors):
                actual=_native_exhibit_context_target({'target_printed_number':27},[bundle,bundle],context,[27])
            self.assertEqual(actual['status'],'pending');self.assertEqual(actual['candidate_count'],2)
            meta['link_label']='Ada Example Periodic Transaction Report 08.12.25 (1)'
            Path(bundle['archive_metadata_path']).write_text(json.dumps(meta),encoding='utf-8')
            actual=_native_exhibit_context_target({'target_printed_number':27},[bundle],context,[27])
            self.assertEqual(actual['status'],'pending')

    def test_source_bytes_and_ambiguous_printed_marker_cannot_bind_context(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);bundle,_=target(root);context=_native_amendment_context(cover(),SOURCE_URL)
            with patch('unison_snapshot.oge_reports._native_original_printed_anchors',return_value={27:[{},{}]}):
                self.assertEqual(_native_exhibit_context_target({'target_printed_number':27},[bundle],context,[27])['status'],'pending')
            (root/'original.pdf').write_bytes(b'%PDF-1.7\nchanged\n%%EOF')
            with self.assertRaises(OgeCatalogError):_native_exhibit_context_target({'target_printed_number':27},[bundle],context,[27])

    def test_optional_saved_input_flows_to_materialized_audit_and_reuses_only_one_call_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle,_=target(Path(folder));source=empty_source();source['source_url']=SOURCE_URL
            record=direct();record['document_url']=SOURCE_URL;obs=observation()
            def native(path,*,source_sha256,page_number):return cover() if page_number==1 else obs
            with patch('unison_snapshot.oge_278t_page_coverage.observe_native_exhibit_page',side_effect=native),patch(
                    'unison_snapshot.oge_reports._native_original_printed_anchors',side_effect=anchors) as anchor_reader:
                materialized=materialize_saved_oge_278t_exhibits(source,record,observations=[obs],archived_pdf_path='mock.pdf',
                    replacement_targets=[bundle],amendment_context_page_number=1)
                self.assertEqual(anchor_reader.call_count,1)
                relation=materialized['quarantined'][0]['native_exhibit_reference']['replacement_relation']
                self.assertEqual(relation['status'],'candidate_context_match')
                self.assertEqual(materialized['observation_receipt']['amendment_context']['page_text'],COVER)
                self.assertFalse(materialized['observation_receipt']['coverage_complete'])
                self.assertEqual(materialized['transactions'],[])
                self.assertNotIn('filed_at',materialized['observation_receipt']['amendment_context'])
            with patch('unison_snapshot.oge_reports._native_original_printed_anchors',side_effect=anchors) as anchor_reader:
                cache={};context=_native_amendment_context(cover(),SOURCE_URL)
                for n in [27,52]:_native_exhibit_context_target({'target_printed_number':n},[bundle],context,[27,52],cache)
                self.assertEqual(anchor_reader.call_count,1)
                _native_exhibit_context_target({'target_printed_number':27},[bundle],context,[27,52],{})
                self.assertEqual(anchor_reader.call_count,2)  # No cross-run cache.

if __name__=='__main__':unittest.main()
