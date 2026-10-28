"""Source-selected versions and real native PDF partition consumer contracts."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

from whitehouse_account_display_fixture import fixture, encoded, write_files
from unison_snapshot.commands.whitehouse_extract import extract_batch
from unison_snapshot.commands.whitehouse_coverage import build_coverage
from unison_snapshot.whitehouse_disclosures import parse_disclosure_index
from unison_snapshot.whitehouse_278t import (
    PARSER_VERSION, GENERIC_PARSER_VERSION, NATIVE_VISIBLE_DEFAULT_VERSION,
    NATIVE_VISIBLE_GENERIC_VERSION, parse_whitehouse_278t_pdf,
    parse_generic_whitehouse_278t_pdf, parse_native_visible_whitehouse_278t_pdf,
    plan_whitehouse_278t_archive, validate_generic_whitehouse_report,
    saved_ocr_source_roots)
from unison_snapshot.oge import OgeCatalogError


def native_mutator(kind):
    def mutate(raw):
        old=pymupdf.open(stream=raw,filetype='pdf');doc=pymupdf.open()
        page=doc.new_page(width=700,height=792)
        page.insert_text((30,40),"Periodic Transaction Report (OGE Form 278-T)\nFiler's Information\nExample, Ada\nDirector - White House\nElectronic Signature -\n/s/ Ada Example [electronically signed on 06/13/2026 by Ada Example in Integrity.gov]\nAgency Ethics Official's Opinion",fontsize=8)
        doc.insert_pdf(old,from_page=1,to_page=1);old.close()
        target={'hidden_account':'Investment account #7','hidden_financial':'Example Common Stock (EXM)'}.get(kind)
        if target:
            for ref in doc[1].get_contents():
                data=doc.xref_stream(ref)
                if target.encode().hex().encode() in data.lower():
                    doc.update_stream(ref,data.replace(b'BT',b'BT\n3 Tr',1))
        elif kind=='hidden_first_row':
            for ref in doc[1].get_contents():
                data=doc.xref_stream(ref)
                if b' 627 Tm' in data:
                    doc.update_stream(ref,data.replace(b'BT',b'BT\n3 Tr',1))
        elif kind=='overlay':
            refs=doc[1].get_contents()
            for ref in list(refs):
                data=doc.xref_stream(ref)
                if b'BT' in data:
                    new=doc.get_new_xref();doc.update_object(new,'<< >>')
                    doc.update_stream(new,data.replace(b'BT',b'BT\n3 Tr',1));refs.append(new)
            doc.xref_set_key(doc[1].xref,'Contents','['+' '.join(f'{ref} 0 R' for ref in refs)+']')
        elif kind=='footer':
            before=set(doc[1].get_contents())
            doc[1].insert_text((40,700),'Hidden unrelated footer',fontsize=8)
            for ref in set(doc[1].get_contents())-before:
                doc.update_stream(ref,doc.xref_stream(ref).replace(b'BT',b'BT\n3 Tr',1))
        elif kind=='hidden_cover':
            for ref in doc[0].get_contents():
                doc.update_stream(ref,doc.xref_stream(ref).replace(b'BT',b'BT\n3 Tr',1))
        result=doc.tobytes();doc.close();return result
    return mutate


class WhiteHouseNativePartitionTests(unittest.TestCase):
    def source(self, root, kind='control'):
        _,_,_,evidence,_=fixture(inherit_legacy=False,extra='two_accounts' if kind=='hidden_first_row' else None)
        old_path=next(path for path in evidence if path.endswith('.pdf'))
        meta=json.loads(evidence.pop(old_path[:-4]+'.json'))
        content=native_mutator(kind)(evidence.pop(old_path));sha=hashlib.sha256(content).hexdigest()
        path=f"whitehouse/disclosures/reports/{meta['document_id'][7:]}/{sha}.pdf"
        meta.update(sha256=sha,byte_length=len(content),archive_path=path)
        evidence.update({path:content,path[:-4]+'.json':encoded(meta)})
        write_files(root,evidence)
        index=parse_disclosure_index(evidence[f"whitehouse/disclosures/pages/{meta['page_sha256']}.html"])
        batch={'schema_version':'whitehouse-public-disclosures-batch/v1','indexed_count':1,'reports':[meta],'failures':[]}
        return root/path,meta,index,batch

    def test_no_hidden_source_keeps_complete_default_and_generic_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path,meta,_,_=self.source(root)
            args=dict(source_url=meta['document_url'],source_sha256=meta['sha256'],document_id=meta['document_id'],
                filer_name=meta['filer_name_from_label'],amended_label=meta['link_label'])
            for generic in (False,True):
                with self.subTest(generic=generic), patch('unison_snapshot.whitehouse_278t._extract_ocr_pdf',side_effect=AssertionError('no OCR')):
                    before=(parse_generic_whitehouse_278t_pdf if generic else parse_whitehouse_278t_pdf)(path,**args)
                    plan=plan_whitehouse_278t_archive(meta,root,generic=generic)
                    self.assertEqual(plan['parser_version'],GENERIC_PARSER_VERSION if generic else PARSER_VERSION)
                    review=root/('generic' if generic else 'default')
                    status=extract_batch(root,review,limit=1,document_id=meta['document_id'],source_sha256=meta['sha256'],generic_trade=generic)
                    actual=json.loads(next((review/'whitehouse/extractions').rglob('*.json')).read_bytes())
                    self.assertEqual(actual,before)
                    self.assertEqual(status['extraction_created_count'],1)

    def test_old_same_sha_success_or_failure_cannot_select_hidden_source(self):
        for generic in (False,True):
            with self.subTest(generic=generic),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);_,meta,index,batch=self.source(root,'hidden_financial');review=root/'review'
                legacy_version=GENERIC_PARSER_VERSION if generic else PARSER_VERSION
                folder_path=f"whitehouse/extractions/{meta['document_id'][7:]}/{meta['sha256']}"
                write_files(review,{folder_path+'/'+legacy_version.replace('/','-')+'.json':encoded({'historical_only':True}),
                    folder_path+'/'+legacy_version.replace('/','-')+'.failure.json':encoded({'historical_only':True})})
                status=extract_batch(root,review,limit=1,document_id=meta['document_id'],source_sha256=meta['sha256'],generic_trade=generic)
                self.assertEqual(status['extraction_created_count'],1,status)
                coverage=build_coverage(index,batch,review,generic_trade=generic,evidence_root=root)
                record=coverage['reports'][0]
                self.assertEqual(record['extraction_parser_version'],NATIVE_VISIBLE_GENERIC_VERSION if generic else NATIVE_VISIBLE_DEFAULT_VERSION)
                extraction=json.loads((review/record['extraction_path']).read_bytes())
                self.assertFalse(extraction['transactions'])
                self.assertTrue(extraction['quarantined'])
                self.assertIn('native_hidden_financial_field_unresolved',extraction['quarantined'][0]['reasons'])
                self.assertEqual(extraction['native_text_partition']['source_sha256'],meta['sha256'])

    def test_redundant_overlay_and_unrelated_hidden_footer_keep_visible_facts(self):
        for kind in ('overlay','footer'):
            for generic in (False,True):
                with self.subTest(kind=kind,generic=generic),tempfile.TemporaryDirectory() as folder:
                    root=Path(folder);path,meta,_,_=self.source(root,kind)
                    report=parse_native_visible_whitehouse_278t_pdf(path,source_url=meta['document_url'],
                        source_sha256=meta['sha256'],document_id=meta['document_id'],filer_name=meta['filer_name_from_label'],
                        amended_label=meta['link_label'],generic=generic)
                    self.assertFalse(report['document_reasons'],report['document_reasons'])
                    self.assertEqual(len(report['transactions']),1,report['quarantined'])
                    self.assertEqual(report['transactions'][0]['owner'],'Undisclosed' if generic else 'Self')
                    with saved_ocr_source_roots(root,root):
                        validate_generic_whitehouse_report(report)
                    with self.assertRaisesRegex(OgeCatalogError,'controlled evidence roots'):
                        validate_generic_whitehouse_report(report)
                    altered=deepcopy(report);altered['transactions'][0]['asset_name']='FORGED'
                    with saved_ocr_source_roots(root,root),self.assertRaisesRegex(OgeCatalogError,'complete original-source replay'):
                        validate_generic_whitehouse_report(altered)

    def test_hidden_account_is_physical_barrier_not_a_disclosed_title(self):
        for generic in (False,True):
            with self.subTest(generic=generic),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);path,meta,_,_=self.source(root,'hidden_account')
                report=parse_native_visible_whitehouse_278t_pdf(path,source_url=meta['document_url'],
                    source_sha256=meta['sha256'],document_id=meta['document_id'],filer_name=meta['filer_name_from_label'],
                    amended_label=meta['link_label'],generic=generic)
                self.assertEqual(report['transactions'],[])
                self.assertTrue(report['native_text_partition']['scope_barriers_before'])
                self.assertTrue(any(row.get('native_unknown_source_row') for row in report['quarantined']))
                self.assertTrue(all(not row.get('account_title') for row in report['quarantined']))
                with saved_ocr_source_roots(root,root):validate_generic_whitehouse_report(report)

    def test_hidden_cover_does_not_trigger_ocr_or_supply_required_roles(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path,meta,_,_=self.source(root,'hidden_cover')
            with patch('unison_snapshot.whitehouse_278t._extract_ocr_pdf',side_effect=AssertionError('no OCR')):
                report=parse_native_visible_whitehouse_278t_pdf(path,source_url=meta['document_url'],
                    source_sha256=meta['sha256'],document_id=meta['document_id'],filer_name=meta['filer_name_from_label'])
            self.assertIsNone(report['filed_at'])
            self.assertIn('278t_form_title_not_verified',report['document_reasons'])

    def test_physical_unknown_can_explain_only_its_own_sequence_gap(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path,meta,_,_=self.source(root,'hidden_first_row')
            report=parse_native_visible_whitehouse_278t_pdf(path,source_url=meta['document_url'],
                source_sha256=meta['sha256'],document_id=meta['document_id'],filer_name=meta['filer_name_from_label'],
                amended_label=meta['link_label'])
            self.assertEqual([row['row_number'] for row in report['transactions']],[2])
            self.assertNotIn('transaction_row_sequence_incomplete',report['document_reasons'])
            self.assertEqual(report['native_visible_sequence_status'],'partial_physical_unknown')
            self.assertTrue(any(row.get('native_unknown_source_row') for row in report['quarantined']))
            from unison_snapshot.whitehouse_278t import _native_sequence_gap_explained
            proof=deepcopy(report['native_text_partition']);rows=report['transactions']+report['quarantined']
            for row in proof['rows']:
                row['financial_slot']=False
            self.assertFalse(_native_sequence_gap_explained(proof,rows))

    def test_normal_source_rebuild_reuses_only_the_complete_current_fact(self):
        from unison_snapshot.whitehouse_278t_audit import audit_whitehouse_278t
        from unison_snapshot.whitehouse_278t_candidate import build_whitehouse_278t_review_candidate
        for generic in (False,True):
            with self.subTest(generic=generic),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);path,meta,directory,_=self.source(root,'overlay')
                report=parse_native_visible_whitehouse_278t_pdf(path,source_url=meta['document_url'],
                    source_sha256=meta['sha256'],document_id=meta['document_id'],filer_name=meta['filer_name_from_label'],
                    amended_label=meta['link_label'],generic=generic)
                _,_,review,_,base=fixture(inherit_legacy=False)
                old=fixture()[0]
                old.update(id='oge-278t:'+'a'*24,filing_id='wh-url:'+'b'*24,asset_name='Control Anchor',ticker='CNT',
                    source_url='https://www.whitehouse.gov/wp-content/uploads/2026/06/Control-PTR.pdf',transaction_date='2026-05-01')
                base['transactions']=[old]
                coverage=json.loads(review['oge/whitehouse/coverage-current.json'])
                with saved_ocr_source_roots(root,root):
                    audit=audit_whitehouse_278t([report],coverage,base,directory_index=directory)
                    first,_=build_whitehouse_278t_review_candidate(base,[report],audit,coverage,
                        data_cutoff_at=base['meta']['data_cutoff_at'],directory_index=directory)
                    audit=audit_whitehouse_278t([report],coverage,first,directory_index=directory)
                    second,receipt=build_whitehouse_278t_review_candidate(first,[report],audit,coverage,
                        data_cutoff_at=base['meta']['data_cutoff_at'],directory_index=directory)
                    self.assertEqual(first,second)
                    self.assertEqual(receipt['promoted_transaction_count'],0)
                    self.assertEqual(len(second['transactions']),2)
                    bad=deepcopy(first)
                    target=next(row for row in bad['transactions'] if row['filing_id']==meta['document_id'])
                    target['amount_low']=1002
                    from unison_snapshot.whitehouse_278t import generic_existing_fact
                    self.assertIsNone(generic_existing_fact(report,report['transactions'][0],bad['transactions'],
                        expected_person_id=target['person_id']))
