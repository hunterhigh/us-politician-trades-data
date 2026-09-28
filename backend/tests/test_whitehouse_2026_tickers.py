from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unison_snapshot.oge_reports import (
    TRUMP_SEPT_2026_DOCUMENT_ID, TRUMP_SEPT_2026_SOURCE_URL,
)
from unison_snapshot.whitehouse_2026_tickers import (
    PRIOR_TRUMP_BASIS, TRUMP_PERSON_ID, enrich_trump_2026_tickers,
)


class TrumpSeptemberTickerTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
