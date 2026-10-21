"""Constructed source-word amendment chains, never real predecessor accuracy gold."""
from copy import deepcopy
import hashlib,json,tempfile,unittest
from pathlib import Path
from test_house_ptr import amended_fixture_pages,META,IDENTITY
from test_house_candidate import BASE
from unison_snapshot.house import document_url,HouseIndexError
from unison_snapshot.house_ptr import parse_word_pages,qualify_automatic,resolve_automatic_revisions
from unison_snapshot.house_candidate import build_house_candidate
from unison_snapshot.disclosure_status_house import build_house_status
from unison_snapshot.builder import build
from unison_snapshot.disclosure_status import attach_disclosure_status
from unison_snapshot.public_repo import PublicSnapshotRepository
from test_public_repo import FakeTransport


def source(doc='20000001',sha='a'*64,status='New',filed='2026-09-10',identifier='2000140446',high='$1,000,000'):
    pages=amended_fixture_pages()
    for word in pages[0]['words']:
        if word['text']=='#20000001':word['text']='#'+doc
        if word['text']=='Amended':word['text']=status
        if word['text']=='2000140446':word['text']=identifier
        if word['text']=='$1,000,000':word['text']=high
    meta={**META,'document_id':doc,'filed_date':filed,'source_url':document_url(2026,'P',doc)}
    x=parse_word_pages(meta,sha,pages,copy_allowed=True)
    identity={**IDENTITY,'document_id':doc}
    return qualify_automatic(x,identity),x


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value),encoding='utf-8')


def fixture(root,inputs):
    review=root/'review';evidence=root/'evidence'
    for q,x in inputs:
        doc=x['source']['document_id'];sha=x['source_sha256'];relative=f'2026/{doc}/{sha}.json'
        write(review/'house_clerk/qualifications'/relative,q)
        write(review/'house_clerk/extractions'/relative,x)
        write(evidence/'house_clerk/documents'/relative,{'source_id':'house_clerk','filing_year':2026,
            'document_id':doc,'sha256':sha,'filing_type':'P','filed_date':x['source']['filed_date'],
            'source_url':document_url(2026,'P',doc),'archive_path':f'house_clerk/documents/2026/{doc}/{sha}.pdf',
            'retrieved_at':'2026-09-18T00:00:00Z'})
    write(review/'status/summary.json',{'parser_commit':'a'*40,'pending_parse_count':0,
        'qualification_count':len(inputs),'qualified_transaction_count':sum(len(q['transactions']) for q,_ in inputs),
        'quarantined_row_count':sum(len(q['quarantined']) for q,_ in inputs),'evidence_count':len(inputs),
        'extracted_count':len(inputs),'failure_count':0})
    state={'status':'ok','run_at':'2026-09-18T00:00:00Z','counts':{'archived':len(inputs),'pending':0,'failed':0}}
    return review,evidence,state


class AutomaticRevisionTests(unittest.TestCase):
    def inputs(self):return [source(),source('20000002','b'*64,'Amended','2026-09-15',high='$2,000,000')]

    def test_source_replay_unique_official_id_replaces_and_normal_candidate_status_repeat(self):
        inputs=self.inputs();old=inputs[0][0]['transactions'][0]
        with tempfile.TemporaryDirectory() as d:
            review,evidence,state=fixture(Path(d),inputs)
            base=deepcopy(BASE);base['transactions']=[old]
            candidate=build_house_candidate(review,state,base)
            self.assertEqual(len(candidate['transactions']),1)
            new=candidate['transactions'][0]
            self.assertEqual(new['id'],inputs[1][1]['transactions'][0]['extraction_id'])
            self.assertEqual(new['amount_high'],2000000)
            self.assertEqual(build_house_candidate(review,state,candidate),candidate)
            status=build_house_status(evidence,review,candidate['transactions'],[])[0]
            reports={r['document_id']:r for r in status['reports']}
            self.assertEqual(reports['20000001']['qualification_state'],'superseded')
            self.assertEqual(reports['20000001']['qualified_transaction_count'],0)
            self.assertEqual(reports['20000001']['superseded_transaction_ids'],[old['id']])
            self.assertEqual(reports['20000002']['qualified_transaction_count'],1)
            self.assertEqual(reports['20000002']['quarantined_row_count'],0)
            self.assertEqual(reports['20000002']['revision_reference_dispositions'][0]['action'],'replace_prior')
            self.assertEqual(status['unmatched_publication_transaction_ids'],[])
            snapshot=build(candidate,generated_at=candidate['meta']['data_cutoff_at'],allow_production=True)
            self.assertTrue(snapshot)
            attach_disclosure_status(snapshot,[status],transactions=candidate['transactions'],provenance={
                'code_commit':'a'*40,'evidence_commit':'b'*40,'review_commit':'c'*40,'previous_main_commit':'d'*40})
            read=PublicSnapshotRepository('example','data',transport=FakeTransport(snapshot.files)).fetch('dashboard').snapshot
            repeated=build_house_candidate(review,state,read)
            self.assertEqual(repeated['transactions'],read['transactions'])
            again=build_house_status(evidence,review,repeated['transactions'],[])[0]
            self.assertEqual(again,status)
            self.assertIn('revision_relationship_unresolved',inputs[1][0]['quarantined'][0]['reasons'])

    def test_missing_multiple_bad_same_day_earlier_and_other_person_keep_pending(self):
        original,amend=self.inputs()
        variants=[[amend], [original,source('20000003','c'*64),amend],
            [original,source('20000002','b'*64,'Amended','2026-09-10')],
            [original,source('20000002','b'*64,'Amended','2026-09-09')],
            [original,source('20000002','b'*64,'Amended','2026-09-15',high='$bad')]]
        otherq,otherx=source('20000003','c'*64)
        otherq['identity']={**otherq['identity'],'person_id':'house:C000001'}
        otherq=qualify_automatic(otherx,otherq['identity'])
        # Another person's identically printed number is not this namespace.
        variants.append([original,(qualify_automatic(amend[1],{**amend[0]['identity'],
            'person_id':'house:C000001'}),amend[1])])
        for inputs in variants:
            with self.subTest(inputs=len(inputs)):
                old=[r for q,_ in inputs for r in q['transactions']]
                resolved=resolve_automatic_revisions(inputs,old)
                self.assertEqual(resolved['transactions'],sorted(old,key=lambda r:r['id']))
                self.assertEqual(resolved['replacements'],[])
        separate=resolve_automatic_revisions([original,amend,(otherq,otherx)],
            original[0]['transactions']+otherq['transactions'])
        self.assertEqual(len(separate['replacements']),1)
        self.assertIn(otherq['transactions'][0],separate['transactions'])

    def test_formal_difference_no_words_and_tamper_do_not_authorize(self):
        inputs=self.inputs();old=deepcopy(inputs[0][0]['transactions'][0]);old['amount_high']=1234567
        resolved=resolve_automatic_revisions(inputs,inputs[0][0]['transactions'],prior_transactions=[old])
        self.assertEqual(resolved['transactions'],[old]);self.assertEqual(resolved['replacements'],[])
        missing=deepcopy(inputs);missing[0][1].pop('source_word_pages')
        self.assertEqual(resolve_automatic_revisions(missing,inputs[0][0]['transactions'])['replacements'],[])
        tamper=deepcopy(inputs);tamper[1][1]['transactions'][0]['reported_transaction_id']='123'
        with self.assertRaises(ValueError):resolve_automatic_revisions(tamper,inputs[0][0]['transactions'])

    def test_multiple_revisions_require_strict_source_chronology(self):
        inputs=self.inputs()+[source('20000003','c'*64,'Amended','2026-09-16',high='$3,000,000')]
        result=resolve_automatic_revisions(inputs,inputs[0][0]['transactions'])
        self.assertEqual(result['transactions'][0]['amount_high'],3000000)
        self.assertEqual(len(result['transactions']),1);self.assertEqual(len(result['replacements']),2)
        inputs[-1]=source('20000003','c'*64,'Amended','2026-09-15',high='$3,000,000')
        self.assertEqual(resolve_automatic_revisions(inputs,inputs[0][0]['transactions'])['replacements'],[])

    def test_annual_reference_redirects_to_verified_replacement_without_deleting_disclosure(self):
        inputs=self.inputs();old=inputs[0][0]['transactions'][0];sha='d'*64
        annual={**old,'id':'house-annual-b:'+hashlib.sha256(f'{sha}:2:100.00'.encode()).hexdigest()[:24],
                'filing_id':'10000001','source_url':document_url(2026,'F','10000001')}
        relation={'transaction_id':annual['id'],'source_id':'house_clerk','filing_id':annual['filing_id'],
            'source_url':annual['source_url'],'source_sha256':sha,'section':'Schedule B','page_number':2,
            'top_points':100.0,'status':'possible_ptr_overlap','possible_ptr_transaction_ids':[old['id']]}
        base=deepcopy(BASE);base['transactions']=[old,annual]
        base['meta']['transaction_disclosure_relations']=[relation]
        with tempfile.TemporaryDirectory() as d:
            review,_,state=fixture(Path(d),inputs)
            candidate=build_house_candidate(review,state,base)
            self.assertIn(annual,candidate['transactions'])
            new=next(r for r in candidate['transactions'] if r['id']!=annual['id'])
            self.assertEqual(candidate['meta']['transaction_disclosure_relations'][0]['possible_ptr_transaction_ids'],[new['id']])
            self.assertEqual(build_house_candidate(review,state,candidate),candidate)
            build(candidate,generated_at=candidate['meta']['data_cutoff_at'],allow_production=True)

    def test_existing_financial_conflict_keeps_prior_and_does_not_publish_revision(self):
        inputs=self.inputs();old=deepcopy(inputs[0][0]['transactions'][0]);old['amount_high']=1234567
        base=deepcopy(BASE);base['transactions']=[old]
        with tempfile.TemporaryDirectory() as d:
            review,_,state=fixture(Path(d),inputs)
            candidate=build_house_candidate(review,state,base)
            self.assertEqual(candidate['transactions'],[old])
            self.assertEqual(build_house_candidate(review,state,candidate),candidate)

    def test_colliding_prior_id_cannot_evade_full_fact_check_by_changing_filing(self):
        inputs=self.inputs();amend=inputs[1]
        projected=qualify_automatic(amend[1],amend[0]['identity'],_source_bound_revision_ids=frozenset(
            [amend[1]['transactions'][0]['extraction_id']]))['transactions'][0]
        for field,value in [('filing_id','99999999'),('person_id','house:C000001'),
                            ('source_url','https://disclosures-clerk.house.gov/changed.pdf')]:
            old={**projected,field:value}
            with self.subTest(field=field):
                result=resolve_automatic_revisions(inputs,inputs[0][0]['transactions'],prior_transactions=[old])
                self.assertEqual(result['replacements'],[])
                self.assertEqual(result['transactions'],[old])

    def test_unmapped_other_row_in_same_report_does_not_mix_old_and_new_copies(self):
        original,amend=self.inputs();x=original[1];pages=deepcopy(x['source_word_pages'])
        second=deepcopy([w for w in pages[0]['words'] if w['top']>=300])
        for word in second:
            word['top']+=200
            if word['text']=='2000140446':word['text']='2000999999'
        pages[0]['words']+=second
        x=parse_word_pages({**x['source'],'filing_type':'P'},x['source_sha256'],pages,copy_allowed=True)
        q=qualify_automatic(x,original[0]['identity']);self.assertEqual(len(q['transactions']),2)
        prior=deepcopy(q['transactions']);prior[1]['id']='house-ptr:old-unmapped-physical-row'
        with tempfile.TemporaryDirectory() as d:
            review,_,state=fixture(Path(d),[(q,x),amend]);base=deepcopy(BASE);base['transactions']=prior
            candidate=build_house_candidate(review,state,base)
            self.assertEqual(candidate['transactions'],sorted(prior,key=lambda r:r['id']))
            self.assertEqual(build_house_candidate(review,state,candidate),candidate)

    def test_same_person_official_evidence_url_change_is_not_a_new_identity_conflict(self):
        inputs=self.inputs();q,x=inputs[1]
        identity={**q['identity'],'evidence_url':q['identity']['evidence_url']+'?official=1'}
        inputs[1]=(qualify_automatic(x,identity),x)
        result=resolve_automatic_revisions(inputs,inputs[0][0]['transactions'])
        self.assertEqual(len(result['replacements']),1)


if __name__=='__main__':unittest.main()
