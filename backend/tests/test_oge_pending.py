from copy import deepcopy
import unittest
from unison_snapshot.codec import digest, encode
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_candidate import build_oge_candidate
from test_oge_candidate import catalog, direct, extraction, base, DOCUMENT_ID


class PendingOgeTests(unittest.TestCase):
    def inputs(self):
        first=direct();second=deepcopy(first)
        second['source_document_id']='c'*32
        second['document_url']=second['document_url'].replace(DOCUMENT_ID,'c'*32)
        a=extraction();b=deepcopy(a)
        b.update(document_id='c'*32,source_url=second['document_url'],source_sha256='c'*64)
        b['transactions'][0]['extraction_id']='oge-278t:'+'c'*24
        source=catalog();source['transactions']=[first,second];source['records_total']=2;source['catalog_rows_covered']=2
        prior,_=build_oge_candidate(source,[a,b],base(),data_cutoff_at='2025-06-04T00:00:00Z')
        board={key:prior[key] for key in ['people','transactions','reported_holdings','security_market_data']}
        publication={'main_commit':'a'*40,'board_sha256':digest(encode(board)),'snapshot_id':'b'*64,'board':board}
        return source,a,publication

    def test_missing_report_preserves_exact_published_facts_and_declares_its_gap(self):
        source,a,publication=self.inputs();before=deepcopy(publication)
        with self.assertRaisesRegex(OgeCatalogError,'do not close'):
            build_oge_candidate(source,[a],base(),data_cutoff_at='2025-06-05T00:00:00Z')
        result,audit=build_oge_candidate(source,[a],base(),data_cutoff_at='2025-06-05T00:00:00Z',incomplete_publication=publication)
        self.assertEqual(result['transactions'],publication['board']['transactions'])
        self.assertEqual(publication,before)
        self.assertEqual(audit['extraction_count'],1)
        self.assertEqual(audit['current_qualified_transaction_count'],1)
        self.assertEqual(audit['retained_published_fact_count'],1)
        self.assertEqual(audit['pending_reports'][0]['document_id'],'c'*32)
        self.assertEqual(audit['pending_reports'][0]['archive_state'],'not_archived')
        self.assertEqual(audit['prior_publication']['main_commit'],'a'*40)

    def test_unpublished_missing_report_never_invents_rows_and_modified_publication_is_rejected(self):
        source,a,publication=self.inputs()
        publication['board']['transactions']=[row for row in publication['board']['transactions'] if row['filing_id']==DOCUMENT_ID]
        publication['board_sha256']=digest(encode(publication['board']))
        result,audit=build_oge_candidate(source,[a],base(),data_cutoff_at='2025-06-05T00:00:00Z',incomplete_publication=publication)
        self.assertEqual(len(result['transactions']),1)
        self.assertEqual(audit['retained_published_fact_count'],0)
        publication['board']['transactions'][0]['asset_name']='changed after binding'
        with self.assertRaisesRegex(OgeCatalogError,'immutable binding'):
            build_oge_candidate(source,[a],base(),data_cutoff_at='2025-06-05T00:00:00Z',incomplete_publication=publication)

    def test_pending_retention_requires_the_same_official_source_url(self):
        source,a,publication=self.inputs()
        publication['board']['transactions'][1]['source_url']='https://extapps2.oge.gov/other.pdf'
        publication['board_sha256']=digest(encode(publication['board']))
        with self.assertRaisesRegex(OgeCatalogError, 'pending official source URL'):
            build_oge_candidate(source,[a],base(),data_cutoff_at='2025-06-05T00:00:00Z',
                                incomplete_publication=publication)


if __name__=='__main__':unittest.main()
