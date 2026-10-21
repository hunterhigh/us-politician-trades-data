"""House annual disclosures: source replay, lifecycle, and actual normalized consumers."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from test_annual_transaction_candidate_relations import house_input,house_fixture
from test_house_candidate import BASE
from test_public_repo import FakeTransport
from unison_snapshot.house_annual_sections import observe_annual_sections,consume_schedule_b_inputs
from unison_snapshot.house_candidate import build_house_candidate
from unison_snapshot.builder import build
from unison_snapshot.public_repo import PublicSnapshotRepository


def consumed(item=None,**kwargs):
    return consume_schedule_b_inputs([item or house_input()],transactions=[],reported_holdings=[],**kwargs)


class HouseAnnualIndependentTests(unittest.TestCase):
    def test_valid_source_is_independent_and_has_exact_source_locator_annotation(self):
        result=consumed();fact=result['transactions'][0];relation=result['transaction_disclosure_relations'][0]
        self.assertEqual(fact['id'],house_input()['observation']['schedule_b_rows'][0]['observation_id'])
        self.assertEqual(set(relation),{'transaction_id','source_id','filing_id','source_url','source_sha256',
            'section','page_number','top_points','status','possible_ptr_transaction_ids'})
        self.assertEqual(relation['status'],'cross_report_relationship_unconfirmed')
        self.assertEqual(relation['possible_ptr_transaction_ids'],[])
        self.assertEqual((fact['option_type'],fact['strike_price'],fact['expiration_date']),(None,None,None))

    def test_normalized_public_reader_roundtrip_base_keeps_fact_and_annotation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);old,state=house_fixture(root);item=house_input()
            first=build_house_candidate(root,state,deepcopy(BASE),annual_transaction_inputs=[item])
            bundle=build(first,generated_at=state['run_at'],allow_production=True)
            normalized=PublicSnapshotRepository('example','data',transport=FakeTransport(bundle.files)).fetch('dashboard').snapshot
            second=build_house_candidate(root,state,normalized,annual_transaction_inputs=[item])
            self.assertEqual(first['transactions'],second['transactions'])
            self.assertEqual(first['meta']['transaction_disclosure_relations'],second['meta']['transaction_disclosure_relations'])
            self.assertEqual(next(r for r in second['transactions'] if r['id']==old['id']),old)
            bad=deepcopy(item);bad.pop('pages')
            retained=build_house_candidate(root,state,normalized,annual_transaction_inputs=[bad])
            self.assertEqual(retained['transactions'],first['transactions'])
            self.assertEqual(retained['meta']['transaction_disclosure_relations'],first['meta']['transaction_disclosure_relations'])
            unavailable=build_house_candidate(root,state,normalized,annual_transaction_inputs=[])
            self.assertEqual(unavailable['transactions'],first['transactions'])
            self.assertEqual(unavailable['reported_holdings'],first['reported_holdings'])
            self.assertEqual(unavailable['meta']['transaction_disclosure_relations'],first['meta']['transaction_disclosure_relations'])
            self.assertIn('existing annual B disclosures retained',next(r for r in unavailable['source_health'] if r['source_id']=='house_clerk')['detail'])

    def test_same_filing_new_source_sha_requires_explicit_revision_binding(self):
        old=consumed();new=house_input(raw=b'different cached original bytes')
        out=consumed(new,existing_annual_transactions=old['transactions'],existing_relations=old['transaction_disclosure_relations'])
        self.assertEqual(out['transactions'],old['transactions'])
        self.assertEqual(out['transaction_disclosure_relations'],old['transaction_disclosure_relations'])
        self.assertIn('existing_annual_source_replacement_unresolved',out['reports'][0]['report_blocking_reasons'])
        self.assertEqual(out['new_candidate_transaction_count'],0)

    def test_explicit_existing_none_evidence_removes_old_rows_and_metadata_unknown_does_not(self):
        old=consumed()
        for declared in [True,False]:
            item=house_input();page=item['pages'][0]
            page['words']=[w for w in page['words'] if w['top']<130]
            if declared:page['words'].append({'text':'None disclosed.','x0':25,'top':130,'size':9})
            item['observation']=observe_annual_sections(item['pages'],item['extraction'])
            out=consumed(item,existing_annual_transactions=old['transactions'],existing_relations=old['transaction_disclosure_relations'])
            self.assertEqual(out['transactions'],[] if declared else old['transactions'])
            self.assertEqual(out['transaction_disclosure_relations'],[] if declared else old['transaction_disclosure_relations'])

    def test_exact_copy_keeps_existing_b_context_before_holdings_preference(self):
        existing=consumed(house_input('10000002'))
        out=consume_schedule_b_inputs([house_input(),house_input('10000002')],transactions=[],
            reported_holdings=[{'filing_id':'10000001'}],existing_annual_transactions=existing['transactions'],
            existing_relations=existing['transaction_disclosure_relations'])
        self.assertEqual(out['transactions'],existing['transactions'])
        self.assertEqual(out['reports'][0]['rows'][0]['canonical_document_id'],'10000002')
        self.assertEqual(out['reports'][0]['rows'][0]['candidate_disposition'],'excluded_exact_source_copy')

    def test_field_source_date_identity_or_old_cache_problem_never_grants_new_fact(self):
        for kind in ['amount','date','identity','missing_words','mutated_observation','revision']:
            with self.subTest(kind=kind):
                item=house_input()
                if kind in {'amount','date'}:
                    token='$1,001' if kind=='amount' else '02/18/2025'
                    next(w for w in item['pages'][0]['words'] if w['text']==token)['text']='unknown'
                    item['observation']=observe_annual_sections(item['pages'],item['extraction'])
                if kind=='identity':item['identity']['status']='unmatched'
                if kind=='missing_words':item.pop('pages')
                if kind=='mutated_observation':item['observation']['schedule_b_rows'][0]['owner']='Self'
                if kind=='revision':
                    item['extraction']['source']['report_type']='Amendment'
                    item['observation']=observe_annual_sections(item['pages'],item['extraction'])
                out=consumed(item)
                self.assertEqual(out['transactions'],[])
                self.assertEqual(out['new_candidate_transaction_count'],0)

    def test_workflow_uses_existing_source_candidate_as_base_after_activation(self):
        for name in ['house-review.yml', 'house-holdings.yml']:
            with self.subTest(workflow=name):
                workflow=(Path(__file__).resolve().parents[2]/'.github/workflows'/name).read_text(encoding='utf-8')
                self.assertIn('candidate_base="$REVIEW_ROOT/candidates/sources/house_clerk-current.json"',workflow)
                self.assertIn('--base "$candidate_base"',workflow)
                self.assertRegex(workflow, r'--base ingest/current\.json\s+(?:\\\s+)?--harmonize-cutoffs')


if __name__=='__main__':unittest.main()
