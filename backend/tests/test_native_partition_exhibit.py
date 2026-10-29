"""Real native visible targets in the existing legacy EXHIBIT source lane."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

import pymupdf

import test_native_partition_consumption as consumption
from whitehouse_account_display_fixture import encoded, write_files
from unison_snapshot.commands import build_whitehouse_278t_candidate as candidate_cli
from unison_snapshot.oge_reports import parse_archived_pdf
from unison_snapshot.oge_278t_page_coverage import observe_native_exhibit_page
from unison_snapshot.oge_cross_source_replacements import (
    _canonical, _bound_whitehouse_candidate, apply_cross_source_native_replacements,
    verify_cross_source_native_applications, replay_cross_source_context)
from unison_snapshot.whitehouse_278t import saved_ocr_source_roots

DOCUMENT='d'*32


def amendment_pdf(target_url):
    document=pymupdf.open();page=document.new_page(width=800,height=792)
    text=("AMENDMENT TO EXECUTIVE BRANCH PERSONNEL PUBLIC FINANCIAL\n"
        "DISCLOSURE REPORT\nPERIODIC TRANSACTION REPORT\nFiler: Ada Example\n"
        "Reporting Period: June 1, 2026 - June 2, 2026\nDate of Report: June 13, 2026\n"
        "The purpose of this amendment is to modify certain transaction amounts as set forth on Exhibit A.\n"
        "This amendment will be attached to and become a part of the above-referenced report.\n"
        "Agency review: report originally signed on June 13, 2026.\n"
        "Electronic Signature -\n"
        "/s/ Example, Ada [electronically signed on 06/14/2026 by Example, Ada in Integrity.gov]")
    page.insert_text((30,35),text,fontsize=8)
    page=document.new_page(width=800,height=792)
    page.insert_text((30,35),f'In place of line item #1 in report {target_url}, substitute the following:',fontsize=7)
    edges=[30,60,350,420,500,570,770]
    for x in edges:page.draw_line((x,100),(x,150))
    for y in (100,125,150):page.draw_line((30,y),(770,y))
    for row,cells in enumerate((['#','Description','Type','Date','Notification','Amount'],
        ['1','Example Common Stock (EXM)','Purchase','06/01/2026','No','$15,001 - $50,000'])):
        for x,text in zip(edges,cells):page.insert_text((x+2,115+25*row),text,fontsize=8)
    raw=document.tobytes();document.close();return raw


class NativePartitionExhibitTests(unittest.TestCase):
    def setUp(self):
        self.driver=consumption.NativePartitionConsumptionTests(methodName='runTest')
        self.driver.setUp();self.addCleanup(self.driver.doCleanups)

    def inputs(self,kind,generic):
        driver=self.driver;root,metadata,base=driver.wh_inputs(kind)
        driver.wh_extract_coverage(root,metadata,generic)
        before,_=driver.wh_candidate(root)
        review,evidence=root/'review',root/'evidence'
        extractions,annual,coverage,directory,people,*_=candidate_cli._verify_review(review,expected_report_count=1)
        wh_inputs=dict(base=base,extractions=extractions,oge_whitehouse_coverage=coverage,
            directory_index=directory,annual_extractions=annual,annual_identity_people=people,
            exact_source_copies=False,source_bindings=None)
        raw=amendment_pdf(metadata['document_url']);sha=hashlib.sha256(raw).hexdigest()
        archive=f'oge/reports/{DOCUMENT}/{sha}.pdf'
        url=f'https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/{DOCUMENT}/$FILE/Example-278t(1)%20AMENDED.pdf'
        source_meta=dict(schema_version='oge-278t-archive/v1',source_id='oge',document_id=DOCUMENT,
            document_url=url,sha256=sha,byte_length=len(raw),archive_path=archive,
            filer_name='Example, Ada',agency='White House Office',position_title='Director',
            catalog_added_date='2026-06-15',amended_label='Amended',pending_final_oge_disposition=False)
        write_files(evidence,{archive:raw,archive[:-4]+'.json':encoded(source_meta)})
        source=parse_archived_pdf(evidence,evidence/(archive[:-4]+'.json'))
        self.assertEqual(source['document_reasons'],[])
        # Existing legacy caches carry references but no physical row inventory.
        # The actual EXHIBIT observer reopens the source and supplies every new
        # row; neither cache text nor a replacement marker authorizes that row.
        source.pop('source_rows')
        write_files(review,{f'oge/extractions/{DOCUMENT}/{sha}/{source["parser_version"].replace("/","-")}.json':encoded(source)})
        observed=observe_native_exhibit_page(evidence/archive,source_sha256=sha,page_number=2)
        saved={DOCUMENT:dict(observations=[observed],amendment_context_page_number=1,
            replacement_application_mode='verified_amount_only/v1',application_stage='post_whitehouse/v1',
            source_extraction_canonical_sha256=_canonical(source))}
        return root,metadata,before,wh_inputs,saved

    def test_real_visible_overlay_target_apply_verify_and_nested_original_base(self):
        for generic in (False,True):
            with self.subTest(generic=generic):
                root,metadata,before,inputs,saved=self.inputs('overlay',generic)
                evidence,review=root/'evidence',root/'review'
                with saved_ocr_source_roots(evidence,review):
                    normal,_,_=_bound_whitehouse_candidate(inputs,evidence,review)
                    self.assertEqual(normal,before)
                    result,proof=apply_cross_source_native_replacements(normal,saved,inputs,evidence,review)
                self.assertEqual(len(proof['applications']),1,proof['source_views'])
                application=proof['applications'][0]
                target=application['target']
                self.assertEqual(target['target_source_sha256'],metadata['sha256'])
                self.assertEqual(target['target_raw_row_basis'],'same_source_visible_glyph_cell_projection_fully_reobserved_from_archive')
                self.assertIn('native-visible',target['target_parser_version'])
                self.assertEqual(target['target_printed_number'],1)
                self.assertEqual(proof['context']['wh_inputs']['base'],inputs['base'])
                self.assertEqual(len(result['transactions']),len(before['transactions']))
                changed=next(row for row in result['transactions'] if row['filing_id']==DOCUMENT)
                self.assertEqual((changed['amount_low'],changed['amount_high']),(15001,50000))
                self.assertEqual(verify_cross_source_native_applications(proof,evidence,review),(result,proof))
                self.assertEqual(replay_cross_source_context(proof['context'],evidence,review),(result,proof))
                from unison_snapshot.oge import OgeCatalogError
                bad=deepcopy(proof)
                bad['context']['wh_inputs']['extractions'][0]['native_text_partition']['pages'][1]['text']+=' forged'
                with self.assertRaises(OgeCatalogError):verify_cross_source_native_applications(bad,evidence,review)

    def test_hidden_financial_target_cannot_gain_exhibit_qualification(self):
        for generic in (False,True):
            with self.subTest(generic=generic):
                root,_,before,inputs,saved=self.inputs('hidden_asset',generic)
                result,proof=apply_cross_source_native_replacements(before,saved,inputs,root/'evidence',root/'review')
                self.assertEqual(result,before)
                self.assertEqual(proof['applications'],[])
                self.assertEqual(verify_cross_source_native_applications(proof,root/'evidence',root/'review'),(result,proof))


if __name__=='__main__':unittest.main()
