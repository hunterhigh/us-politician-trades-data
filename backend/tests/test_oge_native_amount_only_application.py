"""Constructed source-bound native replacement contracts, not real four-block recovery."""
from copy import deepcopy
import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_oge_native_amendment_context import cover,COVER
from test_oge_native_exhibit import observation,empty_source
from test_oge_candidate import direct,catalog,extraction,base,DOCUMENT_ID
from test_oge_annual import _Document,_Page
from unison_snapshot.oge_reports import parse_table_rows
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.oge_native_replacements import (prepare_native_exhibit_applications,
    _ambiguous_native_application_nodes, reconcile_annual_ptr_references)
from unison_snapshot.oge_transaction_adapter import adapt_oge_278t_extraction
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.disclosure_status_oge import build_oge_status
from unison_snapshot.disclosure_status import attach_disclosure_status
from unison_snapshot.builder import build
from unison_snapshot.public_repo import PublicSnapshotRepository
from test_public_repo import FakeTransport

OLD_DOC='b'*32
URL='https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/'+DOCUMENT_ID+'/$FILE/Example-08.12.2025-278T(2)%20AMENDED.pdf'
OLD_URL='https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/'+OLD_DOC+'/$FILE/Original.pdf'
CUTOFF='2025-08-20T00:00:00Z'


def fixture(root):
    evidence=root/'evidence';review=root/'review'
    content=b'%PDF-1.7\nsynthetic original source\n%%EOF';sha=hashlib.sha256(content).hexdigest()
    source_content=b'%PDF-1.7\nsynthetic amendment source\n%%EOF';source_sha=hashlib.sha256(source_content).hexdigest()
    raw=['27','ALPHA','Purchase','07/11/2025','No','$15,001 - $50,000']
    rows,_=parse_table_rows([(1,raw)],source_sha=sha)
    old=extraction();old.update(document_id=OLD_DOC,source_url=OLD_URL,source_sha256=sha,
        filed_at='2025-08-12',transactions=rows,quarantined=[],source_rows=[{'page_number':1,'cells':raw}])
    new=empty_source();new.update(source_url=URL,source_sha256=source_sha,filed_at='2025-08-13',
        document_reasons=['transaction_table_not_found'],source_rows=[])
    src=direct('Amended');src['document_url']=URL
    prior=direct();prior.update(source_document_id=OLD_DOC,document_url=OLD_URL,catalog_index=1)
    for record,x,content in [(src,new,source_content),(prior,old,content)]:
        path=evidence/'oge/reports'/x['document_id']/(x['source_sha256']+'.pdf');path.parent.mkdir(parents=True);path.write_bytes(content)
        meta={'schema_version':'oge-278t-archive/v1','source_id':'oge','document_id':x['document_id'],
            'sha256':x['source_sha256'],'byte_length':len(content),'archive_path':path.relative_to(evidence).as_posix(),
            'document_url':record['document_url'],'filer_name':record['filer_name'],'agency':record['agency'],
            'position_title':record['position_title'],'catalog_added_date':record['catalog_added_date'],
            'amended_label':record['amended_label'],'pending_final_oge_disposition':False}
        path.with_suffix('.json').write_text(json.dumps(meta),encoding='utf-8')
        cache=review/'oge/extractions'/x['document_id']/x['source_sha256']/'oge-278t-pdf-v2.json'
        cache.parent.mkdir(parents=True);cache.write_text(json.dumps(x),encoding='utf-8')
    obs=observation(OLD_URL);obs['source_sha256']=source_sha
    obs['tables'][0][1][3]='07/11/2025'
    for word in obs['words']:
        if word['text']=='05/01/2025':word['text']='07/11/2025'
    obs['page_text']=obs['page_text'].replace('05/01/2025','07/11/2025')
    obs['page_text_sha256']=hashlib.sha256(obs['page_text'].encode()).hexdigest()
    cov=cover();cov['source_sha256']=source_sha
    inputs={'observations':[obs],'archived_pdf_path':str(evidence/'oge/reports'/DOCUMENT_ID/(source_sha+'.pdf')),
        'replacement_targets':[{'evidence_root':str(evidence),'archive_metadata_path':str(evidence/'oge/reports'/OLD_DOC/(sha+'.json')),'extraction':old}],
        'amendment_context_page_number':1,'replacement_application_mode':'verified_amount_only/v1'}
    cat=catalog();cat['transactions']=[src,prior]
    def native(path,*,source_sha256,page_number,**kwargs):return cov if page_number==1 else obs
    return {'evidence':evidence,'review':review,'old':old,'new':new,'src':src,'prior':prior,'catalog':cat,
        'inputs':inputs,'obs':obs,'cover':cov,'raw':raw,'native':native}


def mocks(data):
    from contextlib import ExitStack
    stack=ExitStack();stack.enter_context(patch('unison_snapshot.oge_278t_page_shadow.observe_native_exhibit_page',side_effect=data['native']))
    stack.enter_context(patch('pdfplumber.open',side_effect=lambda *a,**k:_Document([_Page('',[data['raw']])])))
    stack.enter_context(patch('unison_snapshot.oge_reports._extract_borderless_transaction_tables',return_value=[]))
    # These deliberately non-PDF bytes substitute the low-level parser and its
    # selector together. Real original/version binding has separate source tests.
    stack.enter_context(patch('unison_snapshot.oge_reports.selected_parser_version',
        return_value='oge-278t-pdf/v2'))
    return stack


def save_status_inputs(data,audit):
    root=data['review'];path=root/'oge/qualifications/current.json';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(audit),encoding='utf-8')
    raw=json.dumps(data['catalog'],sort_keys=True).encode();sha=hashlib.sha256(raw).hexdigest()
    path=data['evidence']/'oge/catalog/normalized'/(sha+'.json');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    path=data['evidence']/'oge/catalog'/(sha+'.json');path.write_text(json.dumps({
        'schema_version':'oge-catalog-archive/v1','sha256':sha,'retrieved_at':'2025-08-20T00:00:00Z'}),encoding='utf-8')


def runner_arguments(d,root,prior,output):
    root.mkdir(parents=True,exist_ok=True)
    extraction_root=root/'extractions';extraction_root.mkdir(exist_ok=True)
    reports=[]
    for source in (d['new'],d['old']):
        (extraction_root/(source['document_id']+'.json')).write_text(json.dumps(source),encoding='utf-8')
        path=d['evidence']/'oge/reports'/source['document_id']/(source['source_sha256']+'.json')
        reports.append(json.loads(path.read_text(encoding='utf-8')))
    values={'catalog':d['catalog'],'archive-batch':{'schema_version':'oge-278t-archive-batch/v1',
        'catalog_direct_count':2,'pending_count':0,'attempted_count':2,'archived_count':2,'failure_count':0,
        'failures':[],'reports':reports},'extraction-batch':{'schema_version':'oge-278t-extraction-batch/v1',
        'report_count':2,'extraction_count':2,'failure_count':0,'failures':[]},
        'candidate-base':prior,'saved-exhibit-observations':{DOCUMENT_ID:d['inputs']}}
    args=[]
    for name,value in values.items():
        path=root/(name+'.json');path.write_text(json.dumps(value),encoding='utf-8');args+=['--'+name,str(path)]
    return args+['--extractions-dir',str(extraction_root),'--output-dir',str(output),
        '--data-cutoff-at',CUTOFF,'--run-id','native-amount-only-contract','--code-commit','b'*40,
        '--evidence-commit','c'*40,'--review-commit','d'*40,'--started-at',CUTOFF,'--completed-at',CUTOFF]


class NativeAmountOnlyApplicationTests(unittest.TestCase):
    def test_default_direct_projection_cleans_only_metadata_for_facts_it_does_not_output(self):
        with tempfile.TemporaryDirectory() as folder:
            d=fixture(Path(folder));previous=base()
            previous['meta']['holding_report_coverage']=[{'report_id':'annual-other'}]
            previous['meta']['transaction_disclosure_relations']=[{'transaction_id':'annual-other',
                'possible_ptr_transaction_ids':[],'status':'cross_report_relationship_unconfirmed'}]
            previous['reported_holdings']=[{'id':'annual-holding-other'}]
            with mocks(d):
                result,_=build_oge_candidate(catalog(d['prior']),[d['old']],previous,data_cutoff_at=CUTOFF)
            self.assertEqual(len(result['transactions']),1)
            self.assertEqual(result['reported_holdings'],[])
            self.assertNotIn('holding_report_coverage',result['meta'])
            self.assertNotIn('transaction_disclosure_relations',result['meta'])
            self.assertIn('holding_report_coverage',previous['meta'])

    def candidate(self,d,prior=None):
        return build_oge_candidate(d['catalog'],[d['new'],d['old']],prior or base(),data_cutoff_at=CUTOFF,
            saved_exhibit_observations_by_document={DOCUMENT_ID:d['inputs']})

    def test_effective_graph_rejects_branch_cycle_self_and_same_day_component(self):
        def entry(node,day):return ('doc',{'extraction_id':node},{'amendment_filed_at':day})
        self.assertEqual(_ambiguous_native_application_nodes({'A':[entry('B','2025-08-12')]}),set())
        self.assertEqual(_ambiguous_native_application_nodes({'A':[entry('B','2025-08-12'),entry('C','2025-08-13')]}),set())
        for groups,expected in [
            ({'A':[entry('B','2025-08-12'),entry('C','2025-08-13')],
              'B':[entry('D','2025-08-14')]},{'A','B','C','D'}),
            ({'A':[entry('B','2025-08-12')],'B':[entry('A','2025-08-13')]},{'A','B'}),
            ({'A':[entry('A','2025-08-12')]},{'A'}),
            ({'A':[entry('B','2025-08-12'),entry('C','2025-08-12')]},{'A','B','C'}),
            ({'A':[entry('C','2025-08-12')],'B':[entry('C','2025-08-13')]},{'A','B','C'}),
        ]:
            with self.subTest(groups=groups):self.assertEqual(_ambiguous_native_application_nodes(groups),expected)

    def test_owner_or_account_cache_cannot_supply_its_own_scope_proof(self):
        for owner,title in [('Spouse',None),('Self','Invented account')]:
            with self.subTest(owner=owner,title=title),tempfile.TemporaryDirectory() as folder:
                d=fixture(Path(folder))
                rows,_=parse_table_rows([(1,d['raw'])],source_sha=d['old']['source_sha256'],
                    default_owner=owner,account_title=title)
                d['old']['transactions']=rows
                with mocks(d):
                    result,audit=self.candidate(d)
                self.assertEqual(len(result['transactions']),1)
                report=next(r for r in audit['reports'] if r['document_id']==DOCUMENT_ID)
                self.assertEqual(report['promoted_count'],0)
                self.assertEqual(report['quarantined_count'],1)
                self.assertNotIn('native_exhibit_replacement_applications',report)

    def test_missing_filing_earlier_filing_bad_financial_and_cutoff_do_not_retire_prior(self):
        for field,value in [('filed_at',None),('filed_at','2025-08-11'),('filed_at','2025-08-21')]:
            with self.subTest(value=value),tempfile.TemporaryDirectory() as folder:
                d=fixture(Path(folder))
                with mocks(d):
                    old,_=build_oge_candidate(catalog(d['prior']),[d['old']],base(),data_cutoff_at=CUTOFF)
                    d['new'][field]=value
                    result,audit=self.candidate(d,old)
                self.assertEqual(result['transactions'],old['transactions'])
                self.assertEqual(next(r for r in audit['reports'] if r['document_id']==DOCUMENT_ID)['promoted_count'],0)

    def test_same_day_single_explicit_amendment_and_notification_change_are_allowed(self):
        with tempfile.TemporaryDirectory() as folder:
            d=fixture(Path(folder));d['new']['filed_at']='2025-08-12'
            d['old']['transactions'][0]['late_notification_raw']='Yes'
            with mocks(d):result,audit=self.candidate(d)
            self.assertEqual(len(result['transactions']),1)
            self.assertEqual(result['transactions'][0]['filing_id'],DOCUMENT_ID)
            self.assertEqual(result['transactions'][0]['owner'],'Undisclosed')
            app=next(r for r in audit['reports'] if r['document_id']==DOCUMENT_ID)['native_exhibit_replacement_applications'][0]
            self.assertEqual(app['notification_metadata']['original_raw'],'Yes')

    def test_formal_financial_difference_is_preserved_and_new_amount_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            d=fixture(Path(folder))
            with mocks(d):
                old,_=build_oge_candidate(catalog(d['prior']),[d['old']],base(),data_cutoff_at=CUTOFF)
                old['transactions'][0]['amount_high']=50001
                result,audit=self.candidate(d,old)
            self.assertEqual(result['transactions'],old['transactions'])
            self.assertEqual(next(r for r in audit['reports'] if r['document_id']==DOCUMENT_ID)['promoted_count'],0)

    def test_unmapped_legacy_formal_id_is_not_duplicated_or_guessed(self):
        with tempfile.TemporaryDirectory() as folder:
            d=fixture(Path(folder))
            with mocks(d):
                old,_=build_oge_candidate(catalog(d['prior']),[d['old']],base(),data_cutoff_at=CUTOFF)
                old['transactions'][0]['id']='legacy-formal-with-no-raw-id-map'
                result,audit=self.candidate(d,old)
            self.assertEqual(result['transactions'],old['transactions'])
            self.assertEqual(next(r for r in audit['reports'] if r['document_id']==DOCUMENT_ID)['promoted_count'],0)

    def test_target_catalog_pending_final_is_not_bypassed_by_qualified_amendment(self):
        with tempfile.TemporaryDirectory() as folder:
            d=fixture(Path(folder));d['prior']['pending_final_oge_disposition']=True
            d['catalog']['transactions'][1]['pending_final_oge_disposition']=True
            with mocks(d):result,audit=self.candidate(d)
            self.assertEqual(result['transactions'],[])
            report=next(r for r in audit['reports'] if r['document_id']==DOCUMENT_ID)
            self.assertEqual(report['promoted_count'],0)
            self.assertEqual(report['quarantined'][0]['reasons'],['replacement_target_pending_final_oge_disposition'])


    def test_non_amount_financial_difference_and_source_mutation_do_not_apply(self):
        for changed_text in ['BETA','Sale','07/12/2025']:
            with self.subTest(text=changed_text),tempfile.TemporaryDirectory() as folder:
                d=fixture(Path(folder));obs=d['obs']
                old_text={'BETA':'ALPHA','Sale':'Purchase','07/12/2025':'07/11/2025'}[changed_text]
                for table in obs['tables']:
                    for cells in table:
                        for i,value in enumerate(cells):
                            if value==old_text:cells[i]=changed_text
                for word in obs['words']:
                    if word['text']==old_text:word['text']=changed_text
                obs['page_text']=obs['page_text'].replace(old_text,changed_text)
                obs['page_text_sha256']=hashlib.sha256(obs['page_text'].encode()).hexdigest()
                with mocks(d):
                    old,_=build_oge_candidate(catalog(d['prior']),[d['old']],base(),data_cutoff_at=CUTOFF)
                    result,audit=self.candidate(d,old)
                self.assertEqual(result['transactions'],old['transactions'])
                self.assertIn('amount_only_replacement_non_amount_financial_conflict',
                    next(r for r in audit['reports'] if r['document_id']==DOCUMENT_ID)['quarantined'][0]['reasons'])

    def test_status_rejects_tampered_mapping_source_and_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            d=fixture(Path(folder))
            with mocks(d):
                result,audit=self.candidate(d)
                for kind in ['mapping','source','counts','identity']:
                    changed=deepcopy(audit)
                    report=next(r for r in changed['reports'] if r['document_id']==DOCUMENT_ID)
                    if kind=='mapping':report['native_exhibit_replacement_applications'][0]['transaction_id']='fake'
                    elif kind=='source':report['native_exhibit_application_context']['extractions'][0]['filed_at']='2025-08-14'
                    elif kind=='identity':report['native_exhibit_application_context']['records'][DOCUMENT_ID]['filer_name']='Other, Person'
                    else:report['promoted_count']=9
                    save_status_inputs(d,changed)
                    with self.subTest(kind=kind),self.assertRaises((ValueError,OgeCatalogError)):
                        build_oge_status(d['evidence'],d['review'],result['transactions'],[])

    def test_exact_source_amount_replaces_not_appends_and_repeats(self):
        with tempfile.TemporaryDirectory() as folder:
            d=fixture(Path(folder))
            with mocks(d):
                old,_=build_oge_candidate(catalog(d['prior']),[d['old']],base(),data_cutoff_at=CUTOFF)
                result,audit=build_oge_candidate(d['catalog'],[d['new'],d['old']],old,data_cutoff_at=CUTOFF,
                    saved_exhibit_observations_by_document={DOCUMENT_ID:d['inputs']})
                self.assertEqual(len(result['transactions']),1)
                fact=result['transactions'][0]
                self.assertNotEqual(fact['id'],old['transactions'][0]['id'])
                self.assertEqual(fact['owner'],'Self')
                self.assertEqual((fact['amount_low'],fact['amount_high']),(1001,15000))
                repeated,repeated_audit=build_oge_candidate(d['catalog'],[d['new'],d['old']],result,data_cutoff_at=CUTOFF,
                    saved_exhibit_observations_by_document={DOCUMENT_ID:d['inputs']})
                self.assertEqual(result,repeated)
                views=prepare_native_exhibit_applications([d['new'],d['old']],{r['source_document_id']:r for r in d['catalog']['transactions']},
                    {DOCUMENT_ID:d['inputs']},old['transactions'],CUTOFF)
                self.assertEqual(adapt_oge_278t_extraction(views[DOCUMENT_ID],d['src'])['counts']['qualified'],1)
                self.assertEqual(adapt_oge_278t_extraction(views[OLD_DOC],d['prior'])['counts']['excluded'],1)
                self.assertEqual(result['transactions'][0]['filed_at'],'2025-08-13T00:00:00Z')
                self.assertFalse(next(r for r in audit['reports'] if r['document_id']==DOCUMENT_ID)['observation_receipt']['coverage_complete'])
                save_status_inputs(d,audit)
                scopes=build_oge_status(d['evidence'],d['review'],result['transactions'],[])
                direct_scope=next(s for s in scopes if s['scope_id']=='oge_direct_278t')
                reports={r['report_id']:r for r in direct_scope['reports']}
                self.assertEqual(reports[OLD_DOC]['qualification_state'],'superseded')
                self.assertEqual(reports[DOCUMENT_ID]['qualified_transaction_count'],1)
                bundle=build(result,generated_at=CUTOFF,allow_production=True)
                bundle=attach_disclosure_status(bundle,scopes,transactions=result['transactions'],provenance={
                    k:'a'*40 for k in ['code_commit','review_commit','evidence_commit','previous_main_commit']})
                read=PublicSnapshotRepository('example','data',transport=FakeTransport(bundle.files)).fetch('dashboard').snapshot
                self.assertEqual([r['id'] for r in read['transactions']],[fact['id']])
                after,_=build_oge_candidate(d['catalog'],[d['new'],d['old']],read,data_cutoff_at=CUTOFF,
                    saved_exhibit_observations_by_document={DOCUMENT_ID:d['inputs']})
                self.assertEqual(after['transactions'],read['transactions'])


if __name__=='__main__':unittest.main()
