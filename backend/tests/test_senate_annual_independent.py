"""Independent annual rows reach the existing candidate and sharded reader."""
from copy import deepcopy
from pathlib import Path
import hashlib,json,tempfile,unittest
from test_senate_annual import annual_html
from test_senate_annual_transactions import sample
from test_senate_members import ADA_CA,xml
from test_public_repo import FakeTransport
from unison_snapshot.senate_annual import build_annual_review,overlay_annual_candidate,DISCOVERY_SCHEMA,_json
from unison_snapshot.senate_members import build_roster
from unison_snapshot.builder import build,normalize
from unison_snapshot.legacy import load
from unison_snapshot.public_repo import PublicSnapshotRepository
from unison_snapshot.senate import SenateEfdError

QUESTION=b'Did you, your spouse, or dependent child buy, sell, or exchange an asset where the transaction exceeded $1,000 and was not reported on Part 4a? Yes'

def original(amendment=0,*,bad_asset=False):
    raw=annual_html(amendment=amendment,value='Unascertainable' if bad_asset else '$1,001 - $15,000')
    transaction=sample()[1];transaction=transaction[transaction.index(b'<h3>Part 4a.'):transaction.index(b'</body>')]
    transaction=transaction.replace(b'<h3>Part 4b. Transactions</h3>',b'<h3>Part 4b. Transactions</h3><p>'+QUESTION+b'</p>')
    return raw.replace(b'</body>',transaction+b'</body>')

def setup(root,raws):
    evidence=root/'e';review=root/'r';reports=[]
    roster_raw=xml(ADA_CA);roster=build_roster(roster_raw)
    p=evidence/'senate_efd/members'/f"{roster['metadata']['sha256']}.xml";p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(roster_raw)
    for index,(amendment,raw) in enumerate(raws):
        doc=f'11111111-1111-4111-8111-{index+1:012d}';sha=hashlib.sha256(raw).hexdigest()
        report={'document_id':doc,'document_url':f'https://efdsearch.senate.gov/search/view/annual/{doc}/',
            'filer_name':'Ada Example','office':'Example, Ada (Senator)','report_year':2025,'amendment_number':amendment,
            'portal_listed_date':'2026-05-01','catalog_index':index}
        meta={**report,'schema_version':'senate-efd-annual-archive/v1','source_sha256':sha,'byte_length':len(raw)}
        folder=evidence/'senate_efd/annual/reports'/doc;folder.mkdir(parents=True,exist_ok=True)
        (folder/f'{sha}.html').write_bytes(raw);(folder/f'{sha}.metadata.json').write_bytes(_json(meta));reports.append(report)
    p=evidence/'senate_efd/annual/catalog/current.json';p.parent.mkdir(parents=True,exist_ok=True)
    p.write_bytes(_json({'schema_version':DISCOVERY_SCHEMA,'source_id':'senate_efd','catalog_record_count':len(reports),
        'annual_report_count':len(reports),'nonannual_count':0,'reports':reports}))
    build_annual_review(evidence,review,roster,independent_transactions=True)
    return evidence,review,roster

def base():
    return {'meta':{'is_demo':False,'data_cutoff_at':'2026-10-20T00:00:00Z'},'people':[],'transactions':[],
        'reported_holdings':[],'security_market_data':[],'source_health':[]}

def overlay(data,evidence,review,roster):
    return overlay_annual_candidate(deepcopy(data),review,expected_roster_sha256=roster['metadata']['sha256'],evidence_root=evidence)

class IndependentAnnualTests(unittest.TestCase):
    def test_default_build_keeps_activated_mode_and_merges_with_approved_partial_holdings(self):
        from test_house_partial_holdings import candidate_fixture
        from unison_snapshot.disclosure_candidate import build_disclosure_candidate
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);e,r,roster=setup(root/'senate',[(0,original())]);candidate,_=overlay(base(),e,r,roster)
            build_annual_review(e,r,roster)
            again,_=overlay(candidate,e,r,roster);self.assertEqual(candidate,again)
            self.assertEqual(json.loads((r/'senate_efd/annual/current.json').read_bytes())['transaction_mode'],'independent_part4b/v1')
            house,q,_,_=candidate_fixture(root/'house')
            cutoff=house['meta']['data_cutoff_at'];candidate['meta']['data_cutoff_at']=cutoff
            candidate['source_health']=[{'source_id':'senate_efd','status':'partial','data_cutoff_at':cutoff,'last_checked_at':cutoff}]
            merged_base=deepcopy(house)
            for name in ('people','transactions','reported_holdings','security_market_data'):merged_base[name]=[]
            merged=build_disclosure_candidate(merged_base,{'house_clerk':house,'senate_efd':candidate})
            self.assertEqual(merged['meta']['holding_report_coverage'],[q['holding_coverage']])
            self.assertEqual(merged['meta']['transaction_disclosure_relations'],candidate['meta']['transaction_disclosure_relations'])
            bundle=build(merged,generated_at=cutoff,allow_production=True)
            snapshot=PublicSnapshotRepository('example','data',transport=FakeTransport(bundle.files)).fetch().snapshot
            processed=load('process_snapshot',version='v3').build_snapshot(snapshot)
            self.assertEqual(processed['meta']['holding_report_coverage'],[q['holding_coverage']])
            self.assertEqual(processed['meta']['transaction_disclosure_relations'],candidate['meta']['transaction_disclosure_relations'])

    def test_part3_problem_does_not_block_part4b_and_repeating_does_not_append(self):
        with tempfile.TemporaryDirectory() as d:
            e,r,roster=setup(Path(d),[(0,original(bad_asset=True))]);candidate,audit=overlay(base(),e,r,roster)
            self.assertEqual(len(candidate['transactions']),1);self.assertEqual(candidate['reported_holdings'],[])
            self.assertEqual(candidate['transactions'][0]['instrument_type'],'Unspecified')
            again,second=overlay(candidate,e,r,roster)
            self.assertEqual(candidate,again)
            self.assertEqual(second['annual_transaction_candidate_decisions']['candidate_disposition_counts'],{'retained_existing':1})
            self.assertTrue(build(candidate,generated_at=candidate['meta']['data_cutoff_at'],allow_production=True).manifest['snapshot_id'])

    def test_possible_ptr_is_an_annotation_and_ptr_object_is_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            e,r,roster=setup(Path(d),[(0,original())]);initial,_=overlay(base(),e,r,roster)
            ptr={**initial['transactions'][0],'id':'senate-ptr:'+'a'*24,'filing_id':'ptr-example',
                 'source_url':'https://efdsearch.senate.gov/search/view/ptr/example/'}
            before=base();before['people']=initial['people'];before['transactions']=[ptr]
            candidate,audit=overlay(before,e,r,roster)
            self.assertIn(ptr,candidate['transactions']);self.assertEqual(len(candidate['transactions']),2)
            self.assertEqual(candidate['meta']['transaction_disclosure_relations'][0]['possible_ptr_transaction_ids'],[ptr['id']])
            normalize(candidate,allow_production=True)

    def test_amendment_replaces_only_annual_rows_and_exact_copy_has_one_stable_id(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);e,r,roster=setup(root,[(0,original())]);old,_=overlay(base(),e,r,roster)
            oldid=old['transactions'][0]['id'];ptr={**old['transactions'][0],'id':'senate-ptr:'+'b'*24,
                'filing_id':'prior-ptr','source_url':'https://efdsearch.senate.gov/search/view/ptr/prior/'}
            old['transactions'].append(ptr)
            e,r,roster=setup(root,[(0,original()),(1,original(1).replace(b'Example Fund',b'Changed Fund'))])
            candidate,_=overlay(old,e,r,roster)
            self.assertNotIn(oldid,{row['id'] for row in candidate['transactions']});self.assertIn(ptr,candidate['transactions'])
            no=original(1)
            start=no.index(b'<h3>Part 4b.');end=no.index(b'</body>')
            no=no[:start]+b'<h3>Part 4b. Transactions</h3><p>'+QUESTION.replace(b'? Yes',b'? No')+b'</p>'+no[end:]
            # A proved correction declaring no 4b transactions removes only
            # replaced annual facts and their annotations, keeping the PTR.
            with tempfile.TemporaryDirectory() as nested:
                e2,r2,roster2=setup(Path(nested),[(0,original()),(1,no)])
                empty,_=overlay(old,e2,r2,roster2)
                self.assertEqual(empty['transactions'],[ptr])
                self.assertNotIn('transaction_disclosure_relations',empty['meta'])
        with tempfile.TemporaryDirectory() as d:
            e,r,roster=setup(Path(d),[(0,original()),(0,original())]);candidate,audit=overlay(base(),e,r,roster)
            self.assertEqual(len(candidate['transactions']),1)
            self.assertEqual(audit['annual_transaction_candidate_decisions']['candidate_disposition_counts'],{'excluded_exact_source_copy':1,'promoted':1})
            again,_=overlay(candidate,e,r,roster);self.assertEqual(again,candidate)

    def test_current_catalog_missing_original_keeps_old_facts_and_blocks_yes_or_no_amendment(self):
        # A current discovery page is not a complete historic amendment chain.
        # The archived A0 remains available, but absent chain selection must not
        # silently append A1 or interpret No as permission to erase A0.
        for declares_no in (False, True):
            with self.subTest(declares_no=declares_no), tempfile.TemporaryDirectory() as d:
                root=Path(d);e,r,roster=setup(root,[(0,original())])
                old,_=overlay(base(),e,r,roster)
                replacement=original(1).replace(b'Example Fund',b'Changed Fund')
                if declares_no:
                    start=replacement.index(b'<h3>Part 4b.');end=replacement.index(b'</body>')
                    replacement=(replacement[:start]+b'<h3>Part 4b. Transactions</h3><p>'+
                        QUESTION.replace(b'? Yes',b'? No')+b'</p>'+replacement[end:])
                e,r,roster=setup(root,[(0,original()),(1,replacement)])
                catalog=e/'senate_efd/annual/catalog/current.json'
                data=json.loads(catalog.read_bytes());data['reports']=data['reports'][1:]
                data['catalog_record_count']=data['annual_report_count']=1
                catalog.write_bytes(_json(data))
                build_annual_review(e,r,roster)
                result,audit=overlay(old,e,r,roster)
                self.assertEqual(result['transactions'],old['transactions'])
                self.assertEqual(result['meta']['transaction_disclosure_relations'],
                                 old['meta']['transaction_disclosure_relations'])
                self.assertEqual(audit['annual_part4b_relationships'][0]['selection_blocking_reason'],
                                 'annual_transaction_amendment_or_source_unresolved')
                self.assertEqual(audit['annual_transaction_candidate_decisions']['superseded_annual_transaction_ids'],[])
                self.assertTrue(list((e/'senate_efd/annual/reports'/old['transactions'][0]['filing_id']).glob('*.html')))

    def test_wrong_fields_declaration_or_source_cache_do_not_promote(self):
        for raw in [original().replace(QUESTION,b'Unclear declaration'),original().replace(b'Spouse',b'Unknown owner'),
                    original().replace(b'11/12/2025',b'11/12/2027'),original().replace(b'$500,001 - $1,000,000',b'Unclear')]:
            with tempfile.TemporaryDirectory() as d:
                e,r,roster=setup(Path(d),[(0,raw)]);candidate,audit=overlay(base(),e,r,roster)
                self.assertEqual(candidate['transactions'],[])
        with tempfile.TemporaryDirectory() as d:
            e,r,roster=setup(Path(d),[(0,original())]);p=next((r/'senate_efd/annual/part4b').glob('*/*/*.json'))
            data=json.loads(p.read_bytes());data['rows'][0]['asset_name_raw']='Forged';p.write_bytes(_json(data))
            with self.assertRaises(SenateEfdError):overlay(base(),e,r,roster)

    def test_board_person_ticker_reader_carries_only_loaded_relations_and_uses_v3(self):
        with tempfile.TemporaryDirectory() as d:
            raw=original().replace(b'<td>--</td><td>Example Fund',b'<td>ABC</td><td>Example Fund')
            e,r,roster=setup(Path(d),[(0,raw)]);candidate,_=overlay(base(),e,r,roster)
            ptr={**candidate['transactions'][0],'id':'senate-ptr:'+'c'*24,'ticker':'XYZ','filing_id':'ptr-other',
                 'source_url':'https://efdsearch.senate.gov/search/view/ptr/other/'}
            candidate['transactions'].append(ptr)
            candidate['meta']['transaction_disclosure_relations'][0].update(status='possible_ptr_overlap',possible_ptr_transaction_ids=[ptr['id']])
            bundle=build(candidate,generated_at=candidate['meta']['data_cutoff_at'],allow_production=True)
            self.assertLess(len(bundle.files['manifest.json']),16*1024)
            self.assertNotIn('transaction_disclosure_relations',bundle.manifest)
            for mode,key in [('dashboard',None),('person',candidate['people'][0]['id']),('ticker','ABC')]:
                transport=FakeTransport(bundle.files);repo=PublicSnapshotRepository('example','data',transport=transport)
                selected=repo.fetch(mode,key).snapshot
                self.assertEqual(len(selected['meta']['transaction_disclosure_relations']),1)
                processed=load('process_snapshot',version='v3').build_snapshot(selected)
                self.assertEqual(processed['meta']['transaction_disclosure_relations'][0]['possible_ptr_transaction_ids'],[ptr['id']])
                if mode=='ticker':self.assertFalse(any('people/' in call[0] for call in transport.calls))
            for field,value in [('source_sha256','e'*64),('filing_id','other'),('possible_ptr_transaction_ids',['senate-ptr:'+'d'*24])]:
                changed=deepcopy(candidate);changed['meta']['transaction_disclosure_relations'][0][field]=value
                with self.assertRaises(ValueError):normalize(changed,allow_production=True)
            for missing in (None,[]):
                changed=deepcopy(candidate)
                if missing is None:changed['meta'].pop('transaction_disclosure_relations')
                else:changed['meta']['transaction_disclosure_relations']=missing
                with self.assertRaises(ValueError):normalize(changed,allow_production=True)
                changed['meta'].update(snapshot_id='test',generated_at=changed['meta']['data_cutoff_at'])
                with self.assertRaises(ValueError):load('process_snapshot',version='v3').build_snapshot(changed)

if __name__=='__main__':unittest.main()
