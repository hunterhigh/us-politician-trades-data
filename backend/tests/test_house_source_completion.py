"""House source completion regressions: units, source refs, and native tails."""
from copy import deepcopy
from unittest.mock import patch
import unittest
import test_house_ptr_paper_qualification as paper
import test_disclosure_status_house as status_fixture
from test_house_annual_sections import word
from unison_snapshot.house_annual_sections import observe_annual_sections
from unison_snapshot.house_ptr_paper_qualification import materialize_paper_observation, paper_materialization_counts
from unison_snapshot.house_ptr import revision_reference_dispositions
from unison_snapshot.disclosure_status_house import build_house_status


class HouseSourceCompletionTests(unittest.TestCase):
    def paper_result(self):
        f=paper.HousePaperQualificationTests();f.setUp()
        raw,header,reread,slots=f._source_bound_fixture()
        with patch('unison_snapshot.house_ptr_fixed_source_core.check_source_cells',return_value=slots),patch(
                'unison_snapshot.house_ptr_paper_qualification._asset_reading_is_bound',return_value=True):
            return materialize_paper_observation(raw,f.observation,f.metadata,f.identity,
                                                amount_header=header,field_reread=reread)

    def test_physical_unknowns_are_not_unique_quarantined_disclosures(self):
        q=self.paper_result();counts=paper_materialization_counts(q)
        self.assertEqual(counts['observed_transaction_row_count'],1)
        self.assertEqual(counts['observed_transaction_quarantined_count'],0)
        self.assertEqual(counts['unresolved_physical_slot_count'],1)
        self.assertIsNone(counts['whole_source_unique_quarantined_row_count'])
        s=status_fixture.HouseDisclosureStatusTests();s.setUp();self.addCleanup(s.temporary.cleanup)
        doc=q['document_id'];sha=q['source_sha256'];s.archive(doc,sha=sha)
        s.write(s.review/f'house_clerk/extractions/2026/{doc}/{sha}.json',q['materialized_extraction'])
        s.artifact('qualifications',doc,sha=sha,**q)
        record=build_house_status(s.evidence,s.review,q['transactions'],[])[0]['reports'][0]
        self.assertEqual(record['qualification_state'],'partial')
        self.assertEqual(record['qualified_transaction_count'],1)
        self.assertIsNone(record['quarantined_row_count'])
        self.assertEqual(record['unresolved_physical_slot_count'],1)

    def test_labels_or_broken_receipts_cannot_bypass_count_binding(self):
        for change in ['count','bool_count','source','slot','extraction','logical_count']:
            with self.subTest(change=change):
                q=self.paper_result()
                if change=='count':q['observation_materialization']['unresolved_physical_row_count']=0
                if change=='bool_count':q['observation_materialization']['observed_data_row_count']=True
                if change=='source':q['observation_materialization']['source_sha256']='f'*64
                if change=='slot':q['quarantined'][0]['evidence']['physical_band_index']=99
                if change=='extraction':q['materialized_extraction']['source']['document_id']='wrong'
                if change=='logical_count':q['source_report_ledger']['logical_row_count']=0
                with self.assertRaises(ValueError):paper_materialization_counts(q)

    def annual_pages(self):
        header=[word(t,x,100) for t,x in [('Asset',25),('Owner',280),('Date',319),('Tx.',401),('Amount',440),('Cap.',523)]]
        first={'width':612,'height':792,'words':header+[
            word('Account',25,700),word('⇒',80,700),word('Fund',100,700),word('SP',280,700),
            word('06/11/2025',319,700),word('P',401,700),word('$1,001',440,700),word('-',475,700),word('$15,000',481,700)]}
        second={'width':612,'height':792,'words':header+[word('$200?',523,115),word('(FUNDX)',25,130),word('[MF]',80,130)]}
        x={'schema_version':'house-holding-extraction/v1','source_sha256':'a'*64,'rows':[]}
        return x,[first,second]

    def test_ticker_only_tail_closes_adjacent_explicit_path_without_new_id(self):
        x,pages=self.annual_pages()
        before=observe_annual_sections(pages[:1],x)['schedule_b_rows'][0]
        result=observe_annual_sections(pages,x);row=result['schedule_b_rows'][0]
        self.assertEqual(len(result['schedule_b_rows']),1)
        self.assertEqual(row['observation_id'],before['observation_id'])
        self.assertEqual(row['ticker'],'FUNDX');self.assertEqual(row['problems'],[])
        self.assertEqual(row['evidence']['continuation_segments'][0]['page'],2)
        cov=result['source_text_coverage'];self.assertEqual(cov['count_unit'],'observed_native_text_lines')
        self.assertEqual(cov['unresolved_financial_text_line_count'],0)
        self.assertIsNone(cov['whole_source_unique_disclosure_row_count'])

    def test_tail_with_explicit_other_fields_or_page_gap_stays_unresolved(self):
        for extra in [word('JT',280,130),word('01/01/2025',319,130),word('P',401,130),word('$5,000',440,130)]:
            x,pages=self.annual_pages();pages[1]['words'].append(extra)
            result=observe_annual_sections(pages,x)
            self.assertIn('asset_cell_unresolved',result['schedule_b_rows'][0]['problems'])
        x,pages=self.annual_pages();pages.insert(1,{'width':612,'height':792,'words':[]})
        self.assertIn('asset_cell_unresolved',observe_annual_sections(pages,x)['schedule_b_rows'][0]['problems'])

    def test_unanchored_financial_body_is_visible_but_notes_are_not_new_trades(self):
        x,pages=self.annual_pages();page=pages[1]
        page['words']=[w for w in page['words'] if w['top']<130]+[
            word('$7,000',440,130),word('Description:',25,150),word('note',100,150),
            word('Schedule',25,180),word('C',75,180),word('$99,000',440,200)]
        result=observe_annual_sections([page],x)
        self.assertEqual(result['schedule_b_observed_row_count'],0)
        self.assertEqual(result['source_text_coverage']['unresolved_financial_text_line_count'],1)
        lines=result['source_text_coverage']['pages'][0]['sections'][0]['lines']
        self.assertEqual(sum(line['disposition']=='explicit_note_or_none' for line in lines),1)
        self.assertFalse(any('99,000' in line['text'] for line in lines))

    def test_official_amendment_target_is_not_confused_with_absent_predecessor(self):
        x={'source_sha256':'a'*64,'source':{'document_id':'20035445'},'transactions':[
            {'extraction_id':'current','filing_status':'Amended','reported_transaction_id':'2000140446','description':'official correction'}]}
        q={'source_sha256':'a'*64,'document_id':'20035445','identity':{'person_id':'house:R000612'}}
        self.assertEqual(revision_reference_dispositions(q,x,[]),[])
        q['revision_observations']=[{'extraction_id':'current','reported_transaction_id':'2000140446',
            'reference_status':'official_target_id_observed','source_sha256':'a'*64,
            'evidence':None,'description':'official correction'}]
        result=revision_reference_dispositions(q,x,[])
        self.assertEqual(result[0]['reported_transaction_id'],'2000140446')
        self.assertEqual(result[0]['disposition'],'official_target_id_observed_predecessor_absent_from_candidate')
        result=revision_reference_dispositions(q,x,[{'source_id':'house_clerk','person_id':'house:R000612','id':'unrelated'}])
        self.assertEqual(result[0]['disposition'],'official_target_id_observed_predecessor_binding_unavailable')
        q['revision_observations']=[{'reported_transaction_id':'fake'}]
        with self.assertRaisesRegex(ValueError,'reference differs'):revision_reference_dispositions(q,x,[])

    def test_legacy_v3_saved_fields_reproduce_while_new_census_reaches_consumer(self):
        from test_annual_transaction_candidate_relations import house_input
        from unison_snapshot.house_annual_sections import consume_schedule_b_inputs
        payload=house_input()
        payload['observation']['schema_version']='house-annual-sections-observation/v3'
        payload['observation'].pop('source_text_coverage',None)
        payload['observation'].pop('source_word_pages',None)
        payload['observation'].pop('source_word_pages_sha256',None)
        report=consume_schedule_b_inputs([payload],transactions=[],reported_holdings=[])['reports'][0]
        self.assertEqual(report['report_blocking_reasons'],[])
        self.assertIsInstance(report['source_text_coverage'],dict)


if __name__=='__main__':unittest.main()
