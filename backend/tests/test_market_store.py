import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unison_snapshot.codec import bucket
from unison_snapshot.market_store import build_market_bundle, materialize_market


def row(ticker="AAPL"):
    return {
        "ticker": ticker,
        "company_name": ticker,
        "source_id": "alpaca_sip_eod",
        "price_source": "Alpaca SIP EOD",
        "source_url": "https://docs.alpaca.markets/docs/market-data",
        "feed": "sip",
        "timeframe": "1Day",
        "adjustment": "split",
        "price_history": [
            {"date": "2026-09-17", "close": 100},
            {"date": "2026-09-18", "close": 101.25},
        ],
    }


class MarketStoreTests(unittest.TestCase):
    def test_builds_content_addressed_ticker_shards(self):
        bundle = build_market_bundle([row()], data_cutoff_at="2026-09-20T23:59:59Z")
        prefix = f"market/{bucket('market', 'AAPL')}"
        index = json.loads(bundle.files[f"{prefix}/index.json"])
        sha = index["shards"]["AAPL"]
        value = json.loads(bundle.files[f"{prefix}/{sha}.json"])
        self.assertEqual(value["security_market_data"][0]["ticker"], "AAPL")
        self.assertEqual(len(bundle.page_shas), 1)
        page = json.loads(bundle.files[f"market-pages/{bundle.page_shas[0]}.json"])
        self.assertEqual(page["security_market_data"][0]["ticker"], "AAPL")

    def test_materialize_preserves_old_objects_and_replaces_indexes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = build_market_bundle([row("AAPL")], data_cutoff_at="2026-09-20T23:59:59Z")
            materialize_market(root, first)
            old_blobs = [path for path in first.files if not path.endswith("index.json")]
            second = build_market_bundle([row("MSFT")], data_cutoff_at="2026-09-20T23:59:59Z")
            result = materialize_market(root, second)
            self.assertTrue(result.changed)
            self.assertTrue(all((root / path).is_file() for path in old_blobs))

    def test_rejects_wrong_source_and_future_prices(self):
        bad = row()
        bad["source_id"] = "other"
        with self.assertRaisesRegex(ValueError, "licensed Alpaca"):
            build_market_bundle([bad], data_cutoff_at="2026-09-20T23:59:59Z")
        bad = row()
        bad["price_history"][-1]["date"] = "2026-09-21"
        with self.assertRaisesRegex(ValueError, "invalid completed close"):
            build_market_bundle([bad], data_cutoff_at="2026-09-20T23:59:59Z")


if __name__ == "__main__":
    unittest.main()
