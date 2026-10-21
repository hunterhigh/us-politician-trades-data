"""Proven distinct unpublished WH native rows may join a partially published report."""
from copy import deepcopy
import unittest

from test_whitehouse_278t_generic import report, consume, ROW, SHA, DOC, URL
from test_whitehouse_278t_candidate import _base, _catalog
from unison_snapshot.oge_reports import parse_table_rows
from unison_snapshot.whitehouse_278t import validate_generic_whitehouse_report
from unison_snapshot.whitehouse_278t_candidate import _person, _transaction
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.builder import build


def fixed():
    one=ROW[:];one[2]='Sale';one[-1]='$1,001 - $15,000'
    two=one[:];two[0]='2'
    raw=[(2,one),(2,two)]
    oldrows,_=parse_table_rows(raw,source_sha=SHA)
    legacy={'schema_version':'whitehouse-278t-extraction/v1','parser_version':'whitehouse-278t-hybrid-geometry/v2',
            'document_id':DOC,'source_url':URL,'source_sha256':SHA,'filer_name':'Wiles, Susie',
            'transactions':oldrows,'quarantined':[]}
    item=report(raw,legacy=legacy)
    base=_base();person=_person(_catalog()['reports'][0]);base['people']=[person]
    original=deepcopy(item);original['parser_version']=legacy['parser_version']
    base['transactions']=[_transaction(original,oldrows[0],person['id'])]
    base['meta']['data_cutoff_at']='2026-06-20T00:00:00Z'
    return item,base,raw


class NativeReportCompletionTests(unittest.TestCase):
    def test_distinct_printed_row_with_same_financial_fields_promotes_and_repeats(self):
        item,base,_=fixed()
        candidate,audit=consume(item,base)
        self.assertEqual(len(candidate['transactions']),2)
        self.assertEqual(audit['promoted_transaction_count'],1)
        self.assertEqual(audit['reports'][0]['retained_count'],1)
        self.assertEqual(audit['quarantined_row_count'],0)
        self.assertEqual({r['id'] for r in candidate['transactions']},
                         {r['extraction_id'] for r in item['transactions']})
        first=deepcopy(base['transactions'][0]);first['owner']='Undisclosed'
        self.assertEqual(candidate['transactions'][0],first)
        repeated,again=consume(item,candidate)
        self.assertEqual(repeated,candidate)
        self.assertEqual(again['promoted_transaction_count'],0)
        self.assertEqual(again['reports'][0]['retained_count'],2)
        self.assertEqual(again['quarantined_row_count'],0)
        self.assertTrue(build(candidate,generated_at=base['meta']['data_cutoff_at'],allow_production=True).manifest)

    def test_unrepresented_old_fact_blocks_new_rows_and_keeps_old(self):
        item,base,_=fixed()
        extra=deepcopy(base['transactions'][0]);extra['id']='oge-278t:'+'f'*24
        base['transactions'].append(extra)
        candidate,audit=consume(item,base)
        self.assertEqual(len(candidate['transactions']),2)
        self.assertEqual(audit['promoted_transaction_count'],0)
        self.assertIn('legacy_report_new_row_binding_pending',audit['reports'][0]['rows'][1]['reasons'])
        self.assertEqual(next(r for r in candidate['transactions'] if r['id']==extra['id']),extra)

    def test_old_financial_difference_blocks_new_rows_without_overwriting(self):
        item,base,_=fixed();base['transactions'][0]['amount_high']=14999
        candidate,audit=consume(item,base)
        self.assertEqual(candidate['transactions'],base['transactions'])
        self.assertEqual(audit['promoted_transaction_count'],0)
        self.assertIn('legacy_report_new_row_binding_pending',audit['reports'][0]['rows'][1]['reasons'])

    def test_source_inventory_missing_published_row_blocks_addition(self):
        item,base,raw=fixed()
        partial=report([raw[1]],legacy=None)
        candidate,audit=consume(partial,base)
        self.assertEqual(candidate['transactions'],base['transactions'])
        self.assertIn('legacy_report_new_row_binding_pending',audit['reports'][0]['rows'][0]['reasons'])

    def test_unbound_legacy_absence_does_not_allow_report_addition(self):
        _,base,raw=fixed();item=report(raw,legacy=None)
        candidate,audit=consume(item,base)
        self.assertEqual(candidate['transactions'],base['transactions'])
        self.assertEqual(audit['promoted_transaction_count'],0)

    def test_removed_report_legacy_binding_does_not_allow_addition(self):
        item,base,_=fixed();item.pop('legacy_source_binding')
        candidate,audit=consume(item,base)
        self.assertEqual(audit['promoted_transaction_count'],0)
        self.assertIn('legacy_report_new_row_binding_pending',audit['reports'][0]['rows'][1]['reasons'])

    def test_cross_report_financial_duplicate_is_not_exempted(self):
        item,base,_=fixed()
        other=deepcopy(_transaction(item,item['transactions'][1],base['people'][0]['id']))
        other['id']='oge-278t:'+'e'*24;other['filing_id']='wh-url:'+'b'*24
        other['source_url']=URL+'?different-source';base['transactions'].append(other)
        candidate,audit=consume(item,base)
        self.assertEqual(audit['promoted_transaction_count'],0)
        self.assertIn('possible_existing_oge_transaction_duplicate',audit['reports'][0]['rows'][1]['reasons'])

    def test_same_physical_row_with_two_ids_cannot_supply_inheritance(self):
        item,base,_=fixed()
        altered=deepcopy(item)
        altered['transactions'][1]['source_rows_index']=0
        with self.assertRaises(OgeCatalogError):consume(altered,base)
        altered=deepcopy(item)
        altered['transactions'][1]['legacy_inheritance']['legacy_row']['extraction_id']=altered['transactions'][0]['extraction_id']
        with self.assertRaises(OgeCatalogError):consume(altered,base)

    def test_other_person_formal_fact_cannot_prove_report_conservation(self):
        item,base,_=fixed();other=deepcopy(base['people'][0]);other['id']='oge:'+'d'*16;other['display_name']='Other Person';base['people'].append(other);base['transactions'][0]['person_id']=other['id']
        candidate,audit=consume(item,base)
        self.assertEqual(audit['promoted_transaction_count'],0)
        self.assertIn('legacy_report_new_row_binding_pending',audit['reports'][0]['rows'][1]['reasons'])


if __name__=='__main__':unittest.main()
