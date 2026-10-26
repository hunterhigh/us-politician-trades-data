"""Real archived native PDF -> current normal CLI -> candidate/reader contracts."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pymupdf

from test_native_text_partition import source_pdf, TABLE, EDGES
from test_oge_candidate import base as oge_base, catalog, DOCUMENT_ID, URL
from test_oge_reports import record
from whitehouse_account_display_fixture import fixture, encoded, write_files

PROJECT = Path(__file__).resolve().parents[2]
COVER = ("Periodic Transaction Report (OGE Form 278-T)\nFiler's Information\n"
    "Example, Ada\nDirector - White House\nElectronic Signature - I certify this is correct.\n"
    "/s/ Example, Ada [electronically signed on 06/13/2026 by Example, Ada in Integrity.gov]\n"
    "Agency Ethics Official's Opinion\nDate: 06/15/2026")


def normal_pdf(kind):
    document = pymupdf.open(stream=source_pdf('hidden_scope' if kind=='account_only_reset' else kind),filetype='pdf')
    if kind=='account_only_reset':
        document.delete_page(1)
        body=document.new_page(width=700,height=792)
        rows=deepcopy(TABLE);rows.insert(3,['','Investment account #8','','','',''])
        for x in EDGES:body.draw_line((x,100),(x,100+25*len(rows)))
        for y in range(100,101+25*len(rows),25):body.draw_line((30,y),(680,y))
        for index,cells in enumerate(rows):
            for x,text in zip(EDGES,cells):
                before=set(body.get_contents());body.insert_text((x+2,115+index*25),text,fontsize=8)
                if index==2:
                    for ref in set(body.get_contents())-before:
                        document.update_stream(ref,document.xref_stream(ref).replace(b'BT',b'BT\n3 Tr',1))
    document.delete_page(0)
    page = document.new_page(pno=0,width=700,height=792)
    page.insert_text((30,40),COVER,fontsize=8)
    if kind == 'cover_footer':
        previous = set(page.get_contents())
        page.insert_text((30,740),'Unrelated hidden footer',fontsize=8)
        for ref in set(page.get_contents())-previous:
            document.update_stream(ref,document.xref_stream(ref).replace(b'BT',b'BT\n3 Tr',1))
    # MuPDF delete_page adds PageLabels; this fixture declares a closed
    # drawing profile and does not claim arbitrary catalog support.
    document.xref_set_key(document.pdf_catalog(),'PageLabels','null')
    raw=document.tobytes();document.close();return raw


class NativePartitionConsumptionTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.env=dict(os.environ,PYTHONPATH=str(PROJECT/'backend/src'),GIT_NO_LAZY_FETCH='1')

    def run_cli(self,args,*,success=True):
        result=subprocess.run([sys.executable,*map(str,args)],env=self.env,capture_output=True,text=True)
        self.assertEqual(result.returncode==0,success,result.stdout+'\n'+result.stderr)
        return result

    def oge_inputs(self,kind):
        root=self.root/f'{kind}-{len(list(self.root.iterdir()))}';root.mkdir()
        raw=normal_pdf(kind);sha=hashlib.sha256(raw).hexdigest()
        metadata={**record(),'schema_version':'oge-278t-archive/v1','document_id':DOCUMENT_ID,
            'sha256':sha,'byte_length':len(raw),'archive_path':f'oge/reports/{DOCUMENT_ID}/{sha}.pdf'}
        catalog_value=catalog();catalog_value['transactions'][0]['catalog_added_date']=metadata['catalog_added_date']
        write_files(root,{'catalog.json':encoded(catalog_value),'base.json':encoded(oge_base()),
            'batch.json':encoded({'reports':[metadata]}),metadata['archive_path']:raw,
            f'oge/reports/{DOCUMENT_ID}/{sha}.json':encoded(metadata)})
        return root,metadata,catalog_value

    def oge_extract(self,root,generic):
        args=['-m','unison_snapshot','extract-oge-direct-pdfs','--batch',root/'batch.json',
            '--archive',root,'--output-dir',root/'extractions','--output',root/'extraction-summary.json']
        if generic:args+=['--generic']
        self.run_cli(args)
        summary=json.loads((root/'extraction-summary.json').read_bytes())
        self.assertEqual(summary['failure_count'],0,summary)
        return json.loads((root/f'extractions/{DOCUMENT_ID}.json').read_bytes())

    def oge_candidate(self,root,generic,*,roots=True,success=True):
        args=['-m','unison_snapshot','build-oge-candidate','--catalog',root/'catalog.json',
            '--extractions-dir',root/'extractions','--base',root/'base.json',
            '--data-cutoff-at','2026-09-20T00:00:00Z','--output',root/'candidate.json',
            '--audit-output',root/'audit.json']
        if roots:args+=['--evidence-root',root]
        if generic:args+=['--generic']
        self.run_cli(args,success=success)
        return json.loads((root/'candidate.json').read_bytes()) if success else None

    def assert_reader(self,candidate):
        from unison_snapshot.builder import build
        from unison_snapshot.public_repo import PublicSnapshotRepository
        from test_public_repo import FakeTransport
        files=build(candidate,generated_at='2026-09-20T00:01:00Z',allow_production=True).files
        fetched=PublicSnapshotRepository('contract','data',transport=FakeTransport(files)).fetch().snapshot
        self.assertEqual(fetched['transactions'],candidate['transactions'])

    def test_oge_actual_default_and_generic_cli_overlay_reader_and_repeat(self):
        for generic in (False,True):
            with self.subTest(generic=generic):
                root,_,_=self.oge_inputs('overlay')
                extraction=self.oge_extract(root,generic)
                self.assertIn('native-visible',extraction['parser_version'])
                candidate=self.oge_candidate(root,generic)
                self.assertEqual(len(candidate['transactions']),2)
                self.assertTrue(all('Investment account' not in row['asset_name'] or '#7' in row['asset_name'] for row in candidate['transactions']))
                self.assert_reader(candidate)
                before=(root/'candidate.json').read_bytes(),(root/'audit.json').read_bytes()
                self.oge_candidate(root,generic)
                self.assertEqual(before,((root/'candidate.json').read_bytes(),(root/'audit.json').read_bytes()))

    def test_oge_normal_hidden_fields_header_scope_and_footer_are_local(self):
        expected={'hidden_asset':1,'hidden_financial_row':1,'hidden_header':0,'hidden_scope':1,'account_only_reset':1,'footer':2,'cover_footer':2}
        for kind,count in expected.items():
            for generic in (False,True):
                with self.subTest(kind=kind,generic=generic):
                    root,_,_=self.oge_inputs(kind)
                    self.oge_extract(root,generic)
                    candidate=self.oge_candidate(root,generic)
                    self.assertEqual(len(candidate['transactions']),count)
                    if (kind.startswith('hidden_') or kind=='account_only_reset') and count:
                        self.assertTrue(all('Other Visible' in row['asset_name'] for row in candidate['transactions']))

    def test_oge_same_sha_cache_selection_replay_tamper_and_missing_roots(self):
        from unison_snapshot.oge_reports import load_reusable_extraction,PARSER_VERSION
        root,metadata,_=self.oge_inputs('footer')
        extraction=self.oge_extract(root,False)
        folder=root/'reuse'/DOCUMENT_ID/metadata['sha256'];folder.mkdir(parents=True)
        metadata_path=root/f"oge/reports/{DOCUMENT_ID}/{metadata['sha256']}.json"
        write_files(folder,{PARSER_VERSION.replace('/','-')+'.json':encoded({**extraction,'parser_version':PARSER_VERSION})})
        self.assertIsNone(load_reusable_extraction(root,metadata_path,root/'reuse'))
        checkpoint=folder/(extraction['parser_version'].replace('/','-')+'.json')
        checkpoint.write_bytes(encoded(extraction))
        self.assertEqual(load_reusable_extraction(root,metadata_path,root/'reuse'),extraction)
        self.oge_candidate(root,False)
        before=(root/'candidate.json').read_bytes(),(root/'audit.json').read_bytes()
        self.oge_candidate(root,False,roots=False,success=False)
        self.assertEqual(before,((root/'candidate.json').read_bytes(),(root/'audit.json').read_bytes()))
        changed=deepcopy(extraction);changed['native_text_partition']['pages'][1]['text']+=' forged'
        write_files(root,{f'extractions/{DOCUMENT_ID}.json':encoded(changed)})
        self.oge_candidate(root,False,success=False)
        self.assertEqual(before,((root/'candidate.json').read_bytes(),(root/'audit.json').read_bytes()))
        write_files(root,{f'extractions/{DOCUMENT_ID}.json':encoded(extraction)})
        wrong={**metadata,'agency':'Forged agency'}
        metadata_path.write_bytes(encoded(wrong))
        self.oge_candidate(root,False,success=False)
        self.assertEqual(before,((root/'candidate.json').read_bytes(),(root/'audit.json').read_bytes()))

    def wh_inputs(self,kind):
        old,_,review,evidence,base=fixture()
        root=self.root/f'{kind}-{len(list(self.root.iterdir()))}';root.mkdir()
        old_pdf=next(path for path in evidence if path.startswith('whitehouse/') and path.endswith('.pdf'))
        metadata=json.loads(evidence.pop(old_pdf[:-4]+'.json'));evidence.pop(old_pdf)
        raw=normal_pdf(kind);sha=hashlib.sha256(raw).hexdigest()
        pdf=f"whitehouse/disclosures/reports/{metadata['document_id'][7:]}/{sha}.pdf"
        metadata.update(sha256=sha,byte_length=len(raw),archive_path=pdf)
        evidence.update({pdf:raw,pdf[:-4]+'.json':encoded(metadata)})
        review={path:raw for path,raw in review.items() if not path.startswith('whitehouse/extractions/')}
        batch=dict(schema_version='whitehouse-public-disclosures-batch/v1',indexed_count=1,reports=[metadata],failures=[])
        review['whitehouse/disclosures/batch-current.json']=encoded(batch)
        base['transactions']=[{**old,'id':'oge-278t:'+'f'*24,
            'filing_id':'wh-url:'+'e'*24,
            'source_url':'https://www.whitehouse.gov/wp-content/uploads/2026/06/Prior-PTR.pdf',
            'asset_name':'Unrelated Prior Corporation','ticker':'PRIOR',
            'transaction_date':'2026-05-10'}]
        write_files(root/'evidence',evidence);write_files(root/'review',review)
        write_files(root,{'base.json':encoded(base)})
        return root,metadata,base

    def wh_extract_coverage(self,root,metadata,generic):
        args=[PROJECT/'backend/scripts/whitehouse_extract.py','--evidence-root',root/'evidence',
            '--review-root',root/'review','--limit','1','--document-id',metadata['document_id'],
            '--source-sha256',metadata['sha256'],'--status-output',root/'review/whitehouse/extraction-status.json']
        if generic:args+=['--generic-trade']
        self.run_cli(args)
        status=json.loads((root/'review/whitehouse/extraction-status.json').read_bytes())
        self.assertEqual(status['failure_count'],0,status)
        args=[PROJECT/'backend/scripts/whitehouse_coverage.py','--review-root',root/'review',
            '--evidence-root',root/'evidence','--output',root/'review/whitehouse/coverage-current.json']
        if generic:args+=['--generic-trade']
        self.run_cli(args)

    def wh_candidate(self,root,*,roots=True,success=True,input_path=None):
        args=[PROJECT/'backend/scripts/build_whitehouse_278t_candidate.py','--oge-candidate',input_path or root/'base.json',
            '--review-root',root/'review','--candidate-out',root/'candidate.json','--audit-out',root/'audit.json',
            '--expected-report-count','1']
        if roots:args+=['--evidence-root',root/'evidence']
        result=self.run_cli(args,success=success)
        return (json.loads((root/'candidate.json').read_bytes()),json.loads(result.stdout)) if success else (None,None)

    def test_wh_actual_extract_coverage_candidate_overlay_default_generic_repeat(self):
        for generic in (False,True):
            with self.subTest(generic=generic):
                root,metadata,_=self.wh_inputs('overlay')
                self.wh_extract_coverage(root,metadata,generic)
                candidate,_=self.wh_candidate(root)
                self.assertEqual(len(candidate['transactions']),3)
                self.assert_reader(candidate)
                before=(root/'candidate.json').read_bytes(),(root/'audit.json').read_bytes()
                _,result=self.wh_candidate(root,input_path=root/'candidate.json')
                self.assertTrue(result['idempotent'])
                self.assertEqual(before,((root/'candidate.json').read_bytes(),(root/'audit.json').read_bytes()))

    def test_wh_hidden_scope_field_header_footer_normal_consumers(self):
        for kind,count in {'hidden_scope':1,'account_only_reset':1,'hidden_asset':1,'hidden_header':0,'footer':2,'cover_footer':2}.items():
            for generic in (False,True):
                with self.subTest(kind=kind,generic=generic):
                    root,metadata,_=self.wh_inputs(kind)
                    self.wh_extract_coverage(root,metadata,generic)
                    candidate,_=self.wh_candidate(root)
                    fresh=[row for row in candidate['transactions'] if row['id'] != 'oge-278t:'+'f'*24]
                    self.assertEqual(len(fresh),count)
                    self.assertIn('Unrelated Prior Corporation',[row['asset_name'] for row in candidate['transactions']])
                    if (kind.startswith('hidden_') or kind=='account_only_reset') and count:
                        self.assertTrue(all('Other Visible' in row['asset_name'] for row in fresh))


    def test_oge_full_historical_facts_keep_original_ids_without_new_copies(self):
        from unison_snapshot.oge_candidate import build_oge_candidate
        from unison_snapshot.oge_reports import (PARSER_VERSION, GENERIC_PARSER_VERSION,
            parse_table_rows, parse_generic_table_rows)
        for generic in (False,True):
            with self.subTest(generic=generic):
                root,_,catalog_value=self.oge_inputs('hidden_asset')
                current=self.oge_extract(root,generic)
                inventory=current['native_text_partition']['raw_inventory']
                if generic:
                    good,bad,structural=parse_generic_table_rows(inventory,source_sha256=current['source_sha256'])
                else:
                    good,bad=parse_table_rows(inventory,source_sha=current['source_sha256'])
                historical=deepcopy(current)
                historical.pop('native_text_partition')
                historical.update(parser_version=GENERIC_PARSER_VERSION if generic else PARSER_VERSION,
                    transactions=good,quarantined=bad,
                    source_rows=[{'page_number':page,'cells':cells} for page,cells in inventory])
                if generic:historical['structural_rows']=structural
                previous,_=build_oge_candidate(catalog_value,[historical],oge_base(),data_cutoff_at='2026-09-20T00:00:00Z')
                self.assertEqual(len(previous['transactions']),2)
                write_files(root,{'base.json':encoded(previous)})
                candidate=self.oge_candidate(root,generic)
                self.assertEqual(candidate['transactions'],previous['transactions'])
                self.assertEqual({r['id'] for r in candidate['transactions']},{r['id'] for r in previous['transactions']})
                self.assert_reader(candidate)

    def test_wh_cache_planning_proof_and_metadata_replay_fail_before_outputs(self):
        root,metadata,_=self.wh_inputs('footer')
        folder=root/f"review/whitehouse/extractions/{metadata['document_id'][7:]}/{metadata['sha256']}"
        # An old same-source success is history, even if correctly source-bound.
        from unison_snapshot.whitehouse_278t import parse_whitehouse_278t_pdf
        old=parse_whitehouse_278t_pdf(root/'evidence'/metadata['archive_path'],
            document_id=metadata['document_id'],source_sha256=metadata['sha256'],
            source_url=metadata['document_url'],filer_name='Example, Ada',amended_label=metadata['link_label'])
        write_files(folder,{old['parser_version'].replace('/','-')+'.json':encoded(old)})
        self.wh_extract_coverage(root,metadata,False)
        status=json.loads((root/'review/whitehouse/extraction-status.json').read_bytes())
        self.assertEqual(status['extraction_created_count'],1)
        selected=[p for p in folder.glob('*.json') if 'native-visible' in p.name]
        self.assertEqual(len(selected),1)
        path=selected[0];saved=path.read_bytes();value=json.loads(saved)
        self.wh_extract_coverage(root,metadata,False)
        status=json.loads((root/'review/whitehouse/extraction-status.json').read_bytes())
        self.assertEqual(status['existing_extraction_count'],1)
        self.assertEqual(path.read_bytes(),saved)
        self.wh_candidate(root)
        before=(root/'candidate.json').read_bytes(),(root/'audit.json').read_bytes()
        self.wh_candidate(root,roots=False,success=False)
        self.assertEqual(before,((root/'candidate.json').read_bytes(),(root/'audit.json').read_bytes()))
        # Re-signing the review inventory/digest is automatic; original replay
        # must independently reject the modified observation, even on repeat.
        value['native_text_partition']['pages'][1]['text']+=' forged visible content'
        path.write_bytes(encoded(value))
        self.wh_candidate(root,input_path=root/'candidate.json',success=False)
        self.assertEqual(before,((root/'candidate.json').read_bytes(),(root/'audit.json').read_bytes()))
        path.write_bytes(saved)
        meta=root/'evidence'/metadata['archive_path'].replace('.pdf','.json')
        original=meta.read_bytes();changed=json.loads(original)
        changed['filer_name_from_label']='Forged, Person';meta.write_bytes(encoded(changed))
        self.wh_candidate(root,input_path=root/'candidate.json',success=False)
        self.assertEqual(before,((root/'candidate.json').read_bytes(),(root/'audit.json').read_bytes()))
        meta.write_bytes(original)
        pdf=root/'evidence'/metadata['archive_path'];original=pdf.read_bytes();pdf.write_bytes(original+b'\nchanged')
        self.wh_candidate(root,input_path=root/'candidate.json',success=False)
        self.assertEqual(before,((root/'candidate.json').read_bytes(),(root/'audit.json').read_bytes()))
        self.assertEqual(path.read_bytes(),saved)


if __name__=='__main__':
    unittest.main()
