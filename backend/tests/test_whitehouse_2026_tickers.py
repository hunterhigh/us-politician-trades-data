from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unison_snapshot.oge_reports import (
    TRUMP_SEPT_2026_DOCUMENT_ID, TRUMP_SEPT_2026_SOURCE_URL,
)
from unison_snapshot.whitehouse_2026_tickers import (
    PRIOR_TRUMP_BASIS, SECURITY_DIRECTORY, SEPTEMBER_SOURCE_ALIASES,
    SOURCE_DIRECTORY_BASIS, TRUMP_PERSON_ID, enrich_trump_2026_tickers,
    restore_pre_enrichment,
)


class TrumpSeptemberTickerTests(unittest.TestCase):
    def target(self, record_id, asset_name, transaction_date="2026-08-01"):
        return {
            "id": record_id, "filing_id": TRUMP_SEPT_2026_DOCUMENT_ID,
            "person_id": TRUMP_PERSON_ID, "source_id": "oge",
            "verification_status": "official_matched", "asset_name": asset_name,
            "instrument_type": "Unspecified", "ticker": None,
            "ticker_mapping_basis": None, "source_url": TRUMP_SEPT_2026_SOURCE_URL,
            "filed_at": "2026-09-08T00:00:00Z",
            "transaction_date": transaction_date,
        }

    @staticmethod
    def asset(ticker, name, *, status="active", exchange="NASDAQ", asset_id=None):
        return {"symbol": ticker, "class": "us_equity", "exchange": exchange,
                "status": status, "name": name, "id": asset_id or f"asset-{ticker}"}

    def test_only_fixed_report_reuses_unique_prior_trump_name_with_active_sip_asset(self):
        prior = {
            "id": "wh-annual:prior", "filing_id": "wh-annual:filing",
            "person_id": TRUMP_PERSON_ID, "source_id": "oge",
            "verification_status": "official_matched", "asset_name": "EXAMPLE INC COM",
            "instrument_type": "Stock", "ticker": "EXM",
            "ticker_mapping_basis": "alpaca_unique_asset_name",
        }
        target = {
            **prior, "id": "oge-278t:" + "a" * 24,
            "filing_id": TRUMP_SEPT_2026_DOCUMENT_ID,
            "source_url": TRUMP_SEPT_2026_SOURCE_URL,
            "filed_at": "2026-09-08T00:00:00Z", "ticker": None,
            "ticker_mapping_basis": None,
        }
        candidate = {
            "meta": {"is_demo": False}, "transactions": [prior, target],
            "reported_holdings": [],
        }
        assets = [{"symbol": "EXM", "class": "us_equity", "exchange": "NYSE",
                   "status": "active", "name": "Example Holdings"}]
        result, audit = enrich_trump_2026_tickers(
            candidate, assets, checked_at="2026-09-28T00:00:00Z")
        self.assertEqual(result["transactions"][1]["ticker"], "EXM")
        self.assertEqual(result["transactions"][1]["ticker_mapping_basis"], PRIOR_TRUMP_BASIS)
        self.assertEqual(audit["new_mapping_count"], 1)
        self.assertEqual(audit["mappings"][0]["prior_record_ids"], ["wh-annual:prior"])

    def test_fixed_source_rows_require_official_directory_and_active_sip_assets(self):
        transactions = [self.target(record_id, rule[0], rule[4])
                        for record_id, rule in SEPTEMBER_SOURCE_ALIASES.items()]
        assets = [self.asset(ticker, directory[0])
                  for ticker, directory in SECURITY_DIRECTORY.items()]
        candidate = {"meta": {"is_demo": False}, "transactions": transactions,
                     "reported_holdings": []}

        result, audit = enrich_trump_2026_tickers(
            candidate, assets, checked_at="2026-09-28T00:00:00Z")

        self.assertEqual(len(SEPTEMBER_SOURCE_ALIASES), 155)
        self.assertEqual(audit["new_mapping_count"], 155)
        self.assertEqual(audit["source_directory_mapping_count"], 155)
        self.assertEqual(audit["unmatched_record_count"], 0)
        self.assertEqual(
            {row["ticker"] for row in result["transactions"]},
            {rule[1] for rule in SEPTEMBER_SOURCE_ALIASES.values()})
        mapping = next(row for row in audit["mappings"]
                       if row["record_id"] == "oge-278t:bbaeac29d4e0be338447c0c3")
        self.assertEqual(mapping["mapping_basis"], SOURCE_DIRECTORY_BASIS)
        self.assertEqual(mapping["source_page_number"], 36)
        self.assertEqual(mapping["source_row_number"], 1148)
        self.assertEqual(mapping["source_transaction_date"], "2026-07-23")
        self.assertEqual(mapping["source_evidence_url"], TRUMP_SEPT_2026_SOURCE_URL)
        self.assertEqual(mapping["provider_active_sip_match_count"], 1)
        self.assertEqual(mapping["provider_asset_id"], "asset-ACN")
        self.assertIn("nasdaqtrader.com", mapping["security_directory_url"])

        restored = restore_pre_enrichment(result, audit)
        repeated, repeated_audit = enrich_trump_2026_tickers(
            restored, assets, checked_at="2026-09-28T01:00:00Z", previous=audit)
        self.assertEqual(repeated["transactions"], result["transactions"])
        self.assertEqual(repeated_audit["retained_mapping_count"], 155)
        self.assertEqual(repeated_audit["new_mapping_count"], 0)

    def test_sticky_unique_ticker_survives_provider_display_name_change(self):
        record_id = "oge-278t:" + "a" * 24
        candidate = {"meta": {"is_demo": False},
                     "transactions": [self.target(record_id, "LUMEN TECHNOLOGIES INC")],
                     "reported_holdings": []}
        old_assets = [self.asset("LUMN", "Lumen Technologies, Inc.")]
        enriched, audit = enrich_trump_2026_tickers(
            candidate, old_assets, checked_at="2026-09-28T00:00:00Z")
        self.assertEqual(audit["mappings"][0]["mapping_basis"],
                         "alpaca_unique_asset_name")
        restored = restore_pre_enrichment(enriched, audit)
        revised_assets = [self.asset("LUMN", "Lumen Technologies Inc")]
        repeated, repeated_audit = enrich_trump_2026_tickers(
            restored, revised_assets, checked_at="2026-10-08T00:00:00Z",
            previous=audit)
        self.assertEqual(repeated["transactions"], enriched["transactions"])
        self.assertEqual(repeated_audit["mappings"], audit["mappings"])

    def test_fixed_september_kroger_false_explicit_ticker_is_corrected_only_on_its_row(self):
        kroger_id = "oge-278t:6edb6800bfd420ed027f7f1c"
        kroger = self.target(kroger_id, "KROGER CO", "2026-07-23")
        kroger.update(ticker="THE", ticker_mapping_basis="filing_explicit")
        unrelated = self.target("oge-278t:" + "9" * 24, "UNRELATED CORP")
        unrelated.update(ticker="ZZZ", ticker_mapping_basis="filing_explicit")
        candidate = {"meta": {"is_demo": False},
                     "transactions": [kroger, unrelated],
                     "reported_holdings": []}
        assets = [self.asset("KR", "The Kroger Co.", exchange="NYSE")]

        result, audit = enrich_trump_2026_tickers(
            candidate, assets, checked_at="2026-09-28T00:00:00Z")
        self.assertEqual(result["transactions"][0]["ticker"], "KR")
        self.assertEqual(result["transactions"][0]["ticker_mapping_basis"],
                         SOURCE_DIRECTORY_BASIS)
        self.assertEqual(result["transactions"][1], unrelated)
        self.assertEqual(audit["correction_ids"], [kroger_id])
        mapping = next(row for row in audit["mappings"]
                       if row["record_id"] == kroger_id)
        self.assertEqual(mapping["source_page_number"], 36)
        self.assertEqual(mapping["source_row_number"], 1129)
        self.assertEqual(mapping["ticker"], "KR")

        restored = restore_pre_enrichment(result, audit)
        self.assertEqual(restored["transactions"], candidate["transactions"])
        repeated, repeated_audit = enrich_trump_2026_tickers(
            restored, assets, checked_at="2026-09-28T01:00:00Z", previous=audit)
        self.assertEqual(repeated["transactions"], result["transactions"])
        self.assertEqual(repeated_audit["retained_mapping_count"], 1)
        self.assertEqual(repeated_audit["correction_ids"], [kroger_id])

    def test_fixed_source_mapping_fails_closed_outside_active_sip_scope(self):
        record_id = "oge-278t:bbaeac29d4e0be338447c0c3"
        candidate = {"meta": {"is_demo": False},
                     "transactions": [self.target(
                         record_id, "ACCENTIJRE PLC", "2026-07-23")],
                     "reported_holdings": []}
        for status, exchange in (("inactive", "NASDAQ"), ("active", "OTC")):
            with self.subTest(status=status, exchange=exchange):
                result, audit = enrich_trump_2026_tickers(
                    candidate, [self.asset("ACN", "Accenture plc", status=status,
                                           exchange=exchange)],
                    checked_at="2026-09-28T00:00:00Z")
                self.assertIsNone(result["transactions"][0]["ticker"])
                self.assertEqual(audit["new_mapping_count"], 0)
                self.assertEqual(audit["unmatched_record_count"], 1)

        duplicate_assets = [
            self.asset("ACN", "Accenture plc", asset_id="asset-acn-1"),
            self.asset("ACN", "Accenture plc", asset_id="asset-acn-2"),
        ]
        result, audit = enrich_trump_2026_tickers(
            candidate, duplicate_assets, checked_at="2026-09-28T00:00:00Z")
        self.assertIsNone(result["transactions"][0]["ticker"])
        self.assertEqual(audit["new_mapping_count"], 0)

        wrong_date = {"meta": {"is_demo": False},
                      "transactions": [self.target(
                          record_id, "ACCENTIJRE PLC", "2026-07-22")],
                      "reported_holdings": []}
        with self.assertRaisesRegex(ValueError, "changed its transaction date"):
            enrich_trump_2026_tickers(
                wrong_date, [self.asset("ACN", "Accenture plc")],
                checked_at="2026-09-28T00:00:00Z")

    def test_bonds_concatenated_assets_private_classes_and_ambiguous_names_stay_unmapped(self):
        excluded = [
            ("oge-278t:" + "1" * 24, "!SHARES U.S. TREASURY BOND ETF"),
            ("oge-278t:" + "2" * 24, "TESLA INC MICROSOFT CORP"),
            ("oge-278t:" + "3" * 24, "SCHNEIDER NATL INC WIS CLASS B"),
            ("oge-278t:" + "4" * 24, "Alphabet Inc"),
            ("oge-278t:" + "5" * 24, "VERSIGENT LTD F"),
        ]
        candidate = {"meta": {"is_demo": False},
                     "transactions": [self.target(*row) for row in excluded],
                     "reported_holdings": []}
        assets = [
            self.asset("GOVT", "iShares U.S. Treasury Bond ETF", exchange="ARCA"),
            self.asset("TSLA", "Tesla Inc"), self.asset("MSFT", "Microsoft Corp"),
            self.asset("SNDR", "Schneider National Inc Class A"),
            self.asset("GOOG", "Alphabet Inc Class C"),
            self.asset("GOOGL", "Alphabet Inc Class A"),
            self.asset("VRSN", "Verisign Inc"),
        ]
        result, audit = enrich_trump_2026_tickers(
            candidate, assets, checked_at="2026-09-28T00:00:00Z")
        self.assertTrue(all(row["ticker"] is None for row in result["transactions"]))
        self.assertEqual(audit["new_mapping_count"], 0)
        self.assertEqual(
            audit["unmatched_record_count"] + audit["ambiguous_record_count"],
            len(excluded))


if __name__ == "__main__":
    unittest.main()
