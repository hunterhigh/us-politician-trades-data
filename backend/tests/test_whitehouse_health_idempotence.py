"""Repeated source health summaries retain every prior direct-source byte."""
from copy import deepcopy
import unittest
from test_whitehouse_278t_generic import report,consume,SHA,DOC,URL
from test_whitehouse_278t_candidate import _base
from unison_snapshot.oge_reports import parse_table_rows

ROW=["1","ALPHA","Purchase","06/01/2026","No","$1,001 - $15,000"]
def source(rows=None):
    rows=[(2,ROW)] if rows is None else rows
    good,bad=parse_table_rows(rows,source_sha=SHA)
    legacy={"schema_version":"whitehouse-278t-extraction/v1","parser_version":"whitehouse-278t-hybrid-geometry/v2",
            "source_sha256":SHA,"document_id":DOC,"source_url":URL,"filer_name":"Wiles, Susie",
            "transactions":good,"quarantined":bad}
    return report(rows,legacy=legacy)

class HealthIdempotenceTests(unittest.TestCase):
    def test_first_promoted_then_retained_has_equal_candidate_and_health(self):
        base=_base();before=deepcopy(base)
        item=source()
        first,audit=consume(item,base)
        second,repeated=consume(item,first)
        self.assertEqual(audit["promoted_transaction_count"],1)
        self.assertEqual(repeated["promoted_transaction_count"],0)
        self.assertEqual(repeated["retained_existing_transaction_ids"],[first["transactions"][0]["id"]])
        self.assertEqual(first,second)
        self.assertEqual(base,before)
        self.assertIn("1 transactions qualified",first["source_health"][0]["detail"])
        self.assertEqual({k:v for k,v in base["source_health"][0].items() if k!="detail"},
                         {k:v for k,v in first["source_health"][0].items() if k!="detail"})

    def test_an_already_retained_source_summary_is_unchanged_on_further_repeats(self):
        item=source();first,_=consume(item)
        # Models a base already holding an unrelated official fact and one
        # current-source formal row: the health count is qualified, not added.
        second,audit=consume(item,first);third,again=consume(item,second)
        self.assertEqual(first,second);self.assertEqual(second,third)
        self.assertEqual(audit["quarantined_row_count"],0);self.assertEqual(again["quarantined_row_count"],0)

    def test_similar_official_or_old_nested_detail_is_never_truncated(self):
        for original in (
            "Official note mentioning Prior direct OGE status: retain every word; White House public 278-T: not a generated summary",
            "Prior direct OGE status: official quoted text; White House public 278-T: 99 PDFs audited, 3 new transactions qualified, 7 rows quarantined; 100 total OGE candidate transactions."):
            base=_base();base["source_health"][0]["detail"]=original
            first,_=consume(source(),base);second,_=consume(source(),first)
            self.assertEqual(first,second)
            self.assertIn(original,first["source_health"][0]["detail"])
            self.assertTrue(first["source_health"][0]["detail"].startswith("Prior direct OGE status: "+original+"; "))

    def test_real_qualification_count_change_updates_once_and_preserves_old_detail(self):
        first,_=consume(source())
        old_detail=first["source_health"][0]["detail"]
        changed=source([(2,ROW),(2,["2","BETA","Purchase","06/01/2026","No","unreadable"])])
        second,audit=consume(changed,first);third,_=consume(changed,second)
        self.assertEqual(audit["quarantined_row_count"],1)
        self.assertEqual(second,third)
        self.assertIn(old_detail,second["source_health"][0]["detail"])
        self.assertIn("1 transactions qualified, 1 rows quarantined",second["source_health"][0]["detail"])

if __name__=="__main__":unittest.main()
