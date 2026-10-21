"""Constructed OGE->WH source-family contracts, not real four-block recovery."""
from copy import deepcopy
import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_oge_native_amount_only_application import fixture,mocks,CUTOFF,DOCUMENT_ID,runner_arguments
from test_oge_native_exhibit import observation
from test_oge_candidate import base,catalog
from test_whitehouse_278t_candidate import _wiles,_base
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.oge_cross_source_replacements import (_whitehouse_candidate,
    apply_cross_source_native_replacements,verify_cross_source_native_applications,_canonical)
from unison_snapshot.oge_transaction_adapter import adapt_oge_278t_extraction
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.disclosure_status_oge import build_oge_status
from unison_snapshot.builder import build
from unison_snapshot.public_repo import PublicSnapshotRepository
from test_public_repo import FakeTransport
from scripts.build_whitehouse_278t_candidate import main as wh_cli
from unison_snapshot.oge_reports import parse_table_rows
from unison_snapshot.pipeline_qa.oge_run_cli import main as runner_cli

WH_URL='https://www.whitehouse.gov/wp-content/uploads/2025/08/Example-Periodic-Transaction-Report-8.12.25-2.pdf'
WH_DOC='wh-url:'+hashlib.sha256(WH_URL.encode()).hexdigest()[:24]


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value),encoding='utf-8')


def cross_fixture(root):
    d=fixture(root);old=d['old'];report=_wiles()
    d['src']['agency']='White House Office';d['new']['agency']='White House Office'
    source_path=d['evidence']/'oge/reports'/DOCUMENT_ID/(d['new']['source_sha256']+'.json')
    source_meta=json.loads(source_path.read_text());source_meta['agency']='White House Office';write(source_path,source_meta)
    write(d['review']/'oge/extractions'/DOCUMENT_ID/d['new']['source_sha256']/'oge-278t-pdf-v2.json',d['new'])
    report.update(document_id=WH_DOC,source_url=WH_URL,source_sha256=old['source_sha256'],
        filed_at=old['filed_at'],filer_name='Example, Ada',pdf_filer_name='Ada Example',
        pdf_position_title='Director',pdf_agency_label='White House',filer_signature_name='Ada Example',
        filer_signature_evidence='/s/ Ada Example [electronically signed on 08/12/2025 by Ada Example in Integrity.gov]',
        transactions=deepcopy(old['transactions']),quarantined=[],page_count=1)
    sha=report['source_sha256'];tail=WH_DOC[7:]
    archive=d['evidence']/'whitehouse/disclosures/reports'/tail/(sha+'.pdf')
    archive.parent.mkdir(parents=True);archive.write_bytes((d['evidence']/'oge/reports'/old['document_id']/(sha+'.pdf')).read_bytes())
    write(archive.with_suffix('.json'),{'schema_version':'whitehouse-public-disclosures-pdf/v1',
        'document_id':WH_DOC,'document_url':WH_URL,'sha256':sha,'archive_path':archive.relative_to(d['evidence']).as_posix(),
        'byte_length':archive.stat().st_size,'filer_name_from_label':'Ada Example',
        'link_label':'Ada Example Periodic Transaction Report 08.12.25 (2)',
        'page_sha256':'e'*64,'document_type_from_label':'278t'})
    write(d['review']/'whitehouse/extractions'/tail/sha/'whitehouse-278t-pdf-v1.json',report)
    coverage={'schema_version':'oge-whitehouse-coverage/v1','catalog_sha256':'a'*64,'reports':[{
        'document_type':'278t','filer_name':'Example, Ada','agency':'White House Office','position_title':'Director',
        'catalog_entry_id':'official-example'}]}
    write(d['review']/'oge/whitehouse/coverage-current.json',coverage)
    obs=observation(WH_URL);obs['source_sha256']=d['new']['source_sha256']
    for table in obs['tables']:
        for cells in table:
            for i,value in enumerate(cells):
                if value=='05/01/2025':cells[i]='07/11/2025'
    for word in obs['words']:
        if word['text']=='05/01/2025':word['text']='07/11/2025'
    obs['page_text']=obs['page_text'].replace('05/01/2025','07/11/2025')
    obs['page_text_sha256']=hashlib.sha256(obs['page_text'].encode()).hexdigest()
    d['obs'].clear();d['obs'].update(obs)
    d['inputs'].update(observations=[d['obs']],application_stage='post_whitehouse/v1',
        source_extraction_canonical_sha256=_canonical(d['new']))
    d['inputs']['replacement_targets']=[]
    pure=_base();pure['meta']['data_cutoff_at']=CUTOFF
    d['wh_inputs']={'base':pure,'extractions':[report],'oge_whitehouse_coverage':coverage,
        'annual_extractions':None,'directory_index':None,'annual_identity_people':None,'exact_source_copies':False,
        'source_bindings':None}
    d['wh']=report
    return d


def complete_review(d):
    report=d['wh'];review=d['review'];url=report['source_url'];sha=report['source_sha256']
    directory={'schema_version':'whitehouse-public-disclosures-index/v1','page_sha256':'f'*64,
        'reports':[{'source_document_id':WH_DOC,'document_url':url,'document_type_from_label':'278t',
            'source_section':'Transaction Reports','filer_name_from_label':'Ada Example'}]}
    coverage={'schema_version':'whitehouse-public-coverage/v1','source_id':'whitehouse_public_disclosures',
        'index_page_sha256':'f'*64,'report_link_count':1,'counts_by_review_state':{'extracted_review_only':1},
        'reports':[{'document_id':WH_DOC,'document_url':url,'document_type_from_label':'278t',
            'archive_sha256_versions':[sha],'review_state':'extracted_review_only'}]}
    write(review/'whitehouse/disclosures/index-current.json',directory)
    write(review/'whitehouse/coverage-current.json',coverage)
    write(review/'whitehouse/extraction-status.json',{'schema_version':'whitehouse-public-extraction-status/v1',
        'source_id':'whitehouse_public_disclosures','archived_version_count':1,'pending_count':0})
    d['wh_inputs']['directory_index']=directory
    source_catalog=catalog(d['src']);raw=json.dumps(source_catalog,sort_keys=True).encode();digest=hashlib.sha256(raw).hexdigest()
    normalized=d['evidence']/'oge/catalog/normalized'/(digest+'.json')
    normalized.parent.mkdir(parents=True,exist_ok=True);normalized.write_bytes(raw)
    write(d['evidence']/'oge/catalog'/(digest+'.json'),{'schema_version':'oge-catalog-archive/v1',
        'sha256':digest,'retrieved_at':CUTOFF})
    write(d['root']/'saved.json',{DOCUMENT_ID:d['inputs']})
    return directory


class CrossSourceReplacementTests(unittest.TestCase):
    def save_wh(self,d):
        write(d['review']/'whitehouse/extractions'/WH_DOC[7:]/d['wh']['source_sha256']/'whitehouse-278t-pdf-v1.json',d['wh'])

    def test_raw_owner_account_amount_printed_marker_and_unbound_frame_do_not_apply(self):
        for field,value in [('owner','Spouse'),('account_title','Invented account'),('amount_high',50001),
                            ('row_number',52),('source_url',WH_URL+'?changed')]:
            with self.subTest(field=field),tempfile.TemporaryDirectory() as folder:
                d=cross_fixture(Path(folder))
                if field=='source_url':d['wh'][field]=value
                else:d['wh']['transactions'][0][field]=value
                self.save_wh(d)
                with mocks(d):
                    wh,_,_=_whitehouse_candidate(d['wh_inputs'])
                    result,audit=apply_cross_source_native_replacements(wh,{DOCUMENT_ID:d['inputs']},d['wh_inputs'],d['evidence'],d['review'])
                    self.assertEqual(result,wh);self.assertEqual(audit['applications'],[])
        with tempfile.TemporaryDirectory() as folder:
            d=cross_fixture(Path(folder))
            from test_oge_annual import _Document,_Page
            with mocks(d),patch('pdfplumber.open',return_value=_Document([_Page('',[])])):
                wh,_,_=_whitehouse_candidate(d['wh_inputs'])
                result,audit=apply_cross_source_native_replacements(wh,{DOCUMENT_ID:d['inputs']},d['wh_inputs'],d['evidence'],d['review'])
                self.assertEqual(result,wh);self.assertEqual(audit['applications'],[])

    def test_notification_metadata_does_not_add_a_financial_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            d=cross_fixture(Path(folder));d['raw'][4]='Yes'
            rows,_=parse_table_rows([(1,d['raw'])],source_sha=d['wh']['source_sha256'])
            d['wh']['transactions']=rows;self.save_wh(d)
            with mocks(d):
                wh,_,_=_whitehouse_candidate(d['wh_inputs'])
                result,audit=apply_cross_source_native_replacements(wh,{DOCUMENT_ID:d['inputs']},d['wh_inputs'],d['evidence'],d['review'])
            self.assertEqual(len(audit['applications']),1)
            self.assertEqual(audit['applications'][0]['notification_metadata']['original_raw'],'Yes')
            self.assertEqual(len(result['transactions']),1)

    def test_explicit_other_predecessor_url_never_binds_equal_financial_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            d=cross_fixture(Path(folder));obs=d['obs'];other=WH_URL.replace('Example-Periodic','Other-Periodic')
            for word in obs['words']:word['text']=word['text'].replace(WH_URL,other)
            for table in obs['tables']:
                for row in table:
                    for i,text in enumerate(row):row[i]=text.replace(WH_URL,other)
            obs['page_text']=obs['page_text'].replace(WH_URL,other)
            obs['page_text_sha256']=hashlib.sha256(obs['page_text'].encode()).hexdigest()
            with mocks(d):
                wh,_,_=_whitehouse_candidate(d['wh_inputs'])
                result,audit=apply_cross_source_native_replacements(wh,{DOCUMENT_ID:d['inputs']},d['wh_inputs'],d['evidence'],d['review'])
            self.assertEqual(result,wh);self.assertEqual(audit['applications'],[])

    def test_context_without_url_needs_unique_document_not_just_one_qualified_row(self):
        with tempfile.TemporaryDirectory() as folder:
            d=cross_fixture(Path(folder));obs=observation();obs['source_sha256']=d['new']['source_sha256']
            for table in obs['tables']:
                for row in table:
                    for i,text in enumerate(row):row[i]=text.replace('05/01/2025','07/11/2025')
            for word in obs['words']:word['text']=word['text'].replace('05/01/2025','07/11/2025')
            obs['page_text']=obs['page_text'].replace('05/01/2025','07/11/2025')
            obs['page_text_sha256']=hashlib.sha256(obs['page_text'].encode()).hexdigest()
            d['obs'].clear();d['obs'].update(obs)
            with mocks(d):
                wh,_,_=_whitehouse_candidate(d['wh_inputs'])
                _,audit=apply_cross_source_native_replacements(wh,{DOCUMENT_ID:d['inputs']},d['wh_inputs'],d['evidence'],d['review'])
                self.assertEqual(len(audit['applications']),1)
                other=deepcopy(d['wh']);other['source_url']=WH_URL.replace('Example-Periodic','Other-Periodic')
                other['document_id']='wh-url:'+hashlib.sha256(other['source_url'].encode()).hexdigest()[:24]
                other['source_sha256']='6'*64
                raw=deepcopy(d['raw']);raw[1]='BETA'
                rows,_=parse_table_rows([(1,raw)],source_sha=other['source_sha256'])
                other.update(transactions=rows,document_reasons=['source_read_failure'])
                d['wh_inputs']['extractions'].append(other)
                write(d['review']/'whitehouse/extractions'/other['document_id'][7:]/other['source_sha256']/'whitehouse-278t-pdf-v1.json',other)
                original_metadata=json.loads((d['evidence']/'whitehouse/disclosures/reports'/WH_DOC[7:]/(d['wh']['source_sha256']+'.json')).read_text())
                original_metadata.update(document_id=other['document_id'],document_url=other['source_url'],sha256=other['source_sha256'])
                write(d['evidence']/'whitehouse/disclosures/reports'/other['document_id'][7:]/(other['source_sha256']+'.json'),original_metadata)
                wh,_,qa=_whitehouse_candidate(d['wh_inputs'])
                self.assertEqual(qa['reports'][0]['eligible_row_count'],1)
                result,audit=apply_cross_source_native_replacements(wh,{DOCUMENT_ID:d['inputs']},d['wh_inputs'],d['evidence'],d['review'])
                self.assertEqual(result,wh);self.assertEqual(audit['applications'],[])

    def test_multiple_parser_caches_use_explicit_source_reference_or_stay_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            d=cross_fixture(Path(folder));other=deepcopy(d['new']);other['parser_version']='oge-278t-pdf/v7'
            write(d['review']/'oge/extractions'/DOCUMENT_ID/d['new']['source_sha256']/'other-parser.json',other)
            with mocks(d):
                wh,_,_=_whitehouse_candidate(d['wh_inputs'])
                _,audit=apply_cross_source_native_replacements(wh,{DOCUMENT_ID:d['inputs']},d['wh_inputs'],d['evidence'],d['review'])
                self.assertEqual(len(audit['applications']),1)
                d['inputs'].pop('source_extraction_canonical_sha256')
                result,audit=apply_cross_source_native_replacements(wh,{DOCUMENT_ID:d['inputs']},d['wh_inputs'],d['evidence'],d['review'])
                self.assertEqual(result,wh);self.assertEqual(audit['applications'],[])
                self.assertEqual(len(audit['source_selection_unresolved']),1)

    def test_source_missing_date_and_target_report_failure_remain_pending(self):
        for kind in ['source_date','target_date','target_failure']:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                d=cross_fixture(Path(folder))
                if kind=='source_date':
                    d['new']['filed_at']=None
                    write(d['review']/'oge/extractions'/DOCUMENT_ID/d['new']['source_sha256']/'oge-278t-pdf-v2.json',d['new'])
                    d['inputs']['source_extraction_canonical_sha256']=_canonical(d['new'])
                elif kind=='target_date':d['wh']['filed_at']=None;self.save_wh(d)
                else:d['wh']['document_reasons']=['source_read_failure'];self.save_wh(d)
                with mocks(d):
                    wh,_,_=_whitehouse_candidate(d['wh_inputs'])
                    result,audit=apply_cross_source_native_replacements(wh,{DOCUMENT_ID:d['inputs']},d['wh_inputs'],d['evidence'],d['review'])
                self.assertEqual(result,wh);self.assertEqual(audit['applications'],[])

    def test_runner_defers_wh_schema_without_qualifying_or_dropping_old_formal(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);d=cross_fixture(root)
            with mocks(d):
                wh,_,_=_whitehouse_candidate(d['wh_inputs'])
                # Only the selected OGE source is in the direct source inventory.
                args=runner_arguments(d,root/'runner-inputs',wh,root/'runner-output')
                for name,value in [('catalog',catalog(d['src'])),('archive-batch',None),('extraction-batch',None)]:
                    path=root/'runner-inputs'/(name+'.json')
                    if value is None:
                        value=json.loads(path.read_text(encoding='utf-8'))
                        if name=='archive-batch':
                            value.update(catalog_direct_count=1,attempted_count=1,archived_count=1,
                                reports=[r for r in value['reports'] if r['document_id']==DOCUMENT_ID])
                        else:value.update(report_count=1,extraction_count=1)
                    write(path,value)
                (root/'runner-inputs/extractions'/ (d['old']['document_id']+'.json')).unlink()
                self.assertEqual(runner_cli(args),0)
                ledger=json.loads((root/'runner-output/candidate_rows.json').read_text(encoding='utf-8'))
                self.assertEqual([row['disposition'] for row in ledger],['quarantined'])

    def test_normal_wh_cli_status_builder_and_complete_chain_repeat(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);d=cross_fixture(root);d['root']=root;complete_review(d)
            with mocks(d):
                original,_,_=_whitehouse_candidate(d['wh_inputs'])
                def run(prior):
                    direct,qa=build_oge_candidate(catalog(d['src']),[d['new']],prior,data_cutoff_at=CUTOFF,
                        saved_exhibit_observations_by_document={DOCUMENT_ID:d['inputs']})
                    write(root/'candidate.json',direct);write(d['review']/'oge/qualifications/current.json',qa)
                    args=['--oge-candidate',str(root/'candidate.json'),'--candidate-out',str(root/'candidate.json'),
                        '--review-root',str(d['review']),'--audit-out',str(d['review']/'whitehouse/qualifications/candidate-current.json'),
                        '--evidence-root',str(d['evidence']),'--saved-exhibit-observations',str(root/'saved.json')]
                    self.assertEqual(wh_cli(args),0)
                    return json.loads((root/'candidate.json').read_text(encoding='utf-8')),args
                first,args=run(original)
                self.assertEqual(len(first['transactions']),1)
                first_status=build_oge_status(d['evidence'],d['review'],first['transactions'],[])
                public=next(s for s in first_status if s['scope_id']=='whitehouse_public')
                self.assertEqual(next(r for r in public['reports'] if r['report_id']==WH_DOC)['qualification_state'],'superseded')
                self.assertEqual(wh_cli(args),0)
                self.assertEqual(first,json.loads((root/'candidate.json').read_text(encoding='utf-8')))
                repeated,_=run(first)
                self.assertEqual(first,repeated)
                self.assertEqual(first_status,build_oge_status(d['evidence'],d['review'],repeated['transactions'],[]))
                bundle=build(repeated,generated_at=CUTOFF,allow_production=True)
                read=PublicSnapshotRepository('example','data',transport=FakeTransport(bundle.files)).fetch('dashboard').snapshot
                self.assertEqual(read['transactions'],repeated['transactions'])

    def test_actual_materializer_wh_qualification_application_and_source_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            d=cross_fixture(Path(folder))
            with mocks(d):
                wh,_,qa=_whitehouse_candidate(d['wh_inputs'])
                self.assertEqual(qa['reports'][0]['document_reasons'],[])
                self.assertEqual(len(wh['transactions']),1)
                result,audit=apply_cross_source_native_replacements(wh,{DOCUMENT_ID:d['inputs']},d['wh_inputs'],d['evidence'],d['review'])
                self.assertEqual(len(audit['applications']),1)
                self.assertEqual(len(result['transactions']),1)
                self.assertEqual(result['transactions'][0]['filing_id'],DOCUMENT_ID)
                self.assertEqual(audit['applications'][0]['prior_fact']['filing_id'],WH_DOC)
                replay,_=verify_cross_source_native_applications(audit,d['evidence'],d['review'])
                self.assertEqual(result,replay)
                source=audit['source_views'][DOCUMENT_ID]
                ledger=adapt_oge_278t_extraction(source,d['src'],cross_source_evidence_root=d['evidence'],cross_source_review_root=d['review'])
                self.assertEqual(ledger['counts']['qualified'],1)

    def test_complete_direct_wh_repeat_chain_keeps_actual_formal_and_all_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            d=cross_fixture(Path(folder))
            with mocks(d):
                direct,qa=build_oge_candidate(catalog(d['src']),[d['new']],base(),data_cutoff_at=CUTOFF,
                    saved_exhibit_observations_by_document={DOCUMENT_ID:d['inputs']})
                d['wh_inputs']['base']=direct
                wh,_,_=_whitehouse_candidate(d['wh_inputs'])
                first,audit=apply_cross_source_native_replacements(wh,{DOCUMENT_ID:d['inputs']},d['wh_inputs'],d['evidence'],d['review'])
                again,qa=build_oge_candidate(catalog(d['src']),[d['new']],first,data_cutoff_at=CUTOFF,
                    saved_exhibit_observations_by_document={DOCUMENT_ID:d['inputs']})
                self.assertEqual(again['transactions'],first['transactions'])
                self.assertEqual(qa['reports'][0]['promoted_count'],0)
                d['wh_inputs']['base']=again
                wh,_,_=_whitehouse_candidate(d['wh_inputs'])
                repeated,_=apply_cross_source_native_replacements(wh,{DOCUMENT_ID:d['inputs']},d['wh_inputs'],d['evidence'],d['review'])
                self.assertEqual(first,repeated)


if __name__=='__main__':unittest.main()
