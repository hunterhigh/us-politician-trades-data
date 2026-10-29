"""Source-independent regression for an Endnote debt-classification typo."""
import unittest
from unison_snapshot.oge_candidate import _instrument
from unison_snapshot.whitehouse_278t_candidate import _transaction

class InstrumentClassificationTests(unittest.TestCase):
    def test_stock_with_endnote_callout_is_not_a_bond(self):
        for text in ("Meta Platforms Inc. Class A Common Stock See Endnote",
                     "Example Common Stock See Endnotes"):
            for ticker in ("META",None):self.assertEqual(_instrument(text,ticker),"Stock")
        self.assertEqual(_instrument("See Endnote",None),"Unspecified")

    def test_actual_note_debt_remains_bond_and_other_known_classes_are_unchanged(self):
        for text in ("US Treasury Note","Senior Notes Due 2035","Promissory note","Stock Linked Note"):
            self.assertEqual(_instrument(text,"EXM"),"Bond")
        self.assertEqual(_instrument("Treasury Bond",None),"Bond")
        self.assertEqual(_instrument("Example Mutual Fund See Endnote",None),"Mutual Fund")
        self.assertEqual(_instrument("Example ETF See Endnote",None),"ETF")

    def test_whitehouse_projection_uses_same_classification_and_preserves_source_text(self):
        report={"document_id":"wh-url:test","source_url":"https://www.whitehouse.gov/wp-content/uploads/a.pdf",
                "parser_version":"whitehouse-278t-native-roles/v5","filed_at":"2025-10-30"}
        row={"extraction_id":"oge-278t:example","owner":"Undisclosed",
             "asset_name":"Meta Platforms Inc. Class A Common Stock See Endnote","ticker":"META",
             "transaction_type":"purchase","transaction_date":"2025-10-27","amount_low":1001,"amount_high":15000}
        projected=_transaction(report,row,"oge:example")
        self.assertEqual(projected["instrument_type"],"Stock")
        self.assertEqual(projected["asset_name"],row["asset_name"])
        self.assertEqual(projected["id"],row["extraction_id"])

if __name__=="__main__":unittest.main()
