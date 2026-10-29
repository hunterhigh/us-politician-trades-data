"""Constructed structure/data boundary cases; no new source accuracy claim."""
import unittest
import test_oge_native_scopes as native
from test_oge_native_scopes import ROW,label
from test_oge_reports import record
from unison_snapshot.oge_reports import parse_table_rows,parse_generic_table_rows,classify_table_structure
from unison_snapshot.oge_transaction_adapter import adapt_oge_278t_extraction,_structural_disposition

SHA="a"*64
class StructureAccountingTests(unittest.TestCase):
    def test_financial_rows_with_unknown_number_are_quarantined_never_headers_or_footer(self):
        for number,asset in [("#","ALPHA"),("?","Description Type Date Amount Received Days Ago Fund"),
                             ("Transaction ?","ALPHA")]:
            cells=[number,asset,*ROW[2:]]
            good,bad=parse_table_rows([(2,cells)],source_sha=SHA)
            self.assertEqual(good,[]);self.assertEqual(len(bad),1)
            self.assertIn("row_number_invalid",bad[0]["reasons"])
            self.assertIsNone(classify_table_structure(cells))
            self.assertIsNone(_structural_disposition(cells))
            source=native.NativeScopeTests().source([(2,cells),(2,["2",*ROW[1:]])])
            result,audit=native.NativeScopeTests().candidate(source)
            self.assertEqual(len(result["transactions"]),1)
            self.assertEqual(audit["reports"][0]["quarantined_count"],1)
            ledger=adapt_oge_278t_extraction(source,record())
            self.assertEqual(ledger["counts"]["qualified"],1)
            self.assertEqual(ledger["counts"]["quarantined"],1)
            self.assertEqual(ledger["counts"]["excluded"],0)

    def test_bad_numbered_asset_does_not_become_owner_heading_or_change_following_owner(self):
        cells=["7","Spouse Account Fund","","","No",""]
        self.assertIsNone(classify_table_structure(cells))
        good,bad=parse_table_rows([(2,cells),(2,["8",*ROW[1:]])],source_sha=SHA)
        self.assertEqual(len(bad),1);self.assertEqual(good[0]["owner"],"Self")
        source=native.NativeScopeTests().source([(2,cells),(2,["8",*ROW[1:]])])
        self.assertEqual(source["transactions"][0]["owner"],"Undisclosed")
        self.assertEqual(len(source["quarantined"]),1)
        self.assertFalse(source["structural_rows"])
        self.assertEqual(adapt_oge_278t_extraction(source,record())["counts"]["quarantined"],1)

    def test_ambiguous_unnumbered_owner_heading_keeps_scope_barrier_until_explicit_owner(self):
        rows=[(2,label("Spouse assets")),(2,label("Spouse Account Fund")),(2,ROW),
              (2,label("Investment account #2")),(2,["2",*ROW[1:]]),
              (2,label("Self assets")),(2,["3",*ROW[1:]])]
        good,bad,structural=parse_generic_table_rows(rows,source_sha256=SHA)
        self.assertEqual(len(good),1);self.assertEqual(good[0]["owner"],"Self")
        self.assertEqual(len(bad),3)
        self.assertTrue(all("owner_context_unresolved" in row["reasons"] for row in bad))
        self.assertIn("ambiguous_owner_heading",{r["kind"] for r in structural})

    def test_only_column_role_headers_and_exact_blank_or_account_titles_are_excluded(self):
        header=["#","DESCRIPTION","TYPE","DATE","NOTIFICATION RECEIVED OVER 30 DAYS AGO","AMOUNT"]
        self.assertEqual(classify_table_structure(header)["kind"],"header")
        for number,text in [("2","Spouse Investment Account #1"),("4","Retirement Account #1"),
                            ("6","Line is intentionally left blank")]:
            cells=[number,text,"","","No",""]
            self.assertIsNotNone(classify_table_structure(cells))
            self.assertEqual(parse_table_rows([(2,cells)],source_sha=SHA),([],[]))
            self.assertEqual(_structural_disposition(cells)[0],"excluded")
        for text in ("Spouse Investment Account #1 Fund","Fund is intentionally left blank Investments"):
            self.assertIsNone(classify_table_structure(["7",text,"","","No",""]))
        self.assertIsNone(classify_table_structure(["?","DESCRIPTION TYPE DATE",*ROW[2:]]))
        partial=["","","","","Notification received over 30 days ago","Amount"]
        self.assertEqual(classify_table_structure(partial)["kind"],"header")
        self.assertIsNone(classify_table_structure([*partial[:5],"$1,001 - $15,000"]))
        self.assertIsNone(classify_table_structure(["#","Description","Type (Purchase Sale Exchange)","Date","Notification","Amount"]))
        self.assertEqual(classify_table_structure(["Transactions","","","","",""])["kind"],"footer")

    def test_numbered_explicit_owner_account_scope_persists_to_following_transaction(self):
        rows=[(2,["2","Spouse Investment Account #1","","","No",""]),(2,["3",*ROW[1:]]),
              (2,["4","Retirement Account #1","","","No",""]),(2,["5",*ROW[1:]])]
        source=native.NativeScopeTests().source(rows)
        self.assertEqual(len(source["transactions"]),2)
        self.assertEqual({r["owner"] for r in source["transactions"]},{"Spouse"})
        self.assertEqual([r["account_title"] for r in source["transactions"]],
                         ["Spouse Investment Account #1","Retirement Account #1"])
        facts,_=native.NativeScopeTests().candidate(source)
        self.assertEqual(len(facts["transactions"]),2)
        self.assertTrue(all(r["owner"]=="Spouse" for r in facts["transactions"]))
        ledger=adapt_oge_278t_extraction(source,record())
        self.assertEqual(ledger["counts"],{"qualified":2,"quarantined":0,"excluded":2,"unrecognized":0})


    def test_marker_only_is_physical_unknown_and_never_an_invented_transaction(self):
        for cells in (["#"],["#","","","","",""]):
            source=native.NativeScopeTests().source([(2,cells)])
            self.assertEqual(source["transactions"],[]);self.assertEqual(source["quarantined"],[])
            self.assertEqual(source["structural_rows"],[{"source_rows_index":0,"kind":"unknown_table_content","text":"#"}])
            ledger=adapt_oge_278t_extraction(source,record())
            self.assertEqual(ledger["counts"]["unrecognized"],1)
            self.assertEqual(ledger["counts"]["excluded"],0)

if __name__=="__main__":unittest.main()
