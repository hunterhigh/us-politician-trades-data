from copy import deepcopy
import json
import gzip
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_alpaca_market import production_candidate
from test_market_store import row
from test_public_repo import FakeTransport, COMMIT, MARKET_COMMIT
from unison_snapshot.builder import build
from unison_snapshot.cached_publication import join_disclosures, published_prices, prepared_prices
from unison_snapshot.codec import encode, digest
from unison_snapshot.legacy import load
from unison_snapshot.market_store import build_market_bundle
from unison_snapshot.public_repo import PublicSnapshotRepository


class CachedPublicationTests(unittest.TestCase):
    def prices(self):
        return {"meta": {"market_coverage": {"unsupported_tickers": []}},
                "security_market_data": [row("AAPL"), row("OLDSYMBOL")],
                "source_health": [{"source_id": "alpaca_sip_eod", "source": "Alpaca",
                    "status": "ok", "last_checked_at": "2026-09-18T00:00:00Z",
                    "last_successful_sync_at": "2026-09-18T00:00:00Z"}]}

    def test_new_disclosure_is_published_without_prices_and_old_price_time_is_preserved(self):
        data = production_candidate()
        new = dict(data["transactions"][0], id="new-disclosure", ticker="NEWCO", asset_name="New company")
        data["transactions"].append(new)
        data["meta"]["data_cutoff_at"] = "2026-09-20T23:59:59Z"
        original = deepcopy(data)
        prices = self.prices()
        # A prepared price job may contain stale facts: the join never uses them.
        prices["transactions"] = []
        with patch("urllib.request.urlopen", side_effect=AssertionError("No vendor calls")):
            candidate, audit = join_disclosures(data, prices, generated_at="2026-09-21T00:00:00Z")
            market = build_market_bundle(candidate["security_market_data"],
                                         data_cutoff_at=data["meta"]["data_cutoff_at"])
            bundle = build(candidate, generated_at="2026-09-21T00:00:00Z", allow_production=True,
                           allow_market=True, market_commit=MARKET_COMMIT, market_pages=market.page_shas)
            processed = load("process_snapshot", version="v2").build_snapshot(candidate)
        self.assertEqual(data, original)
        self.assertIn(new, candidate["transactions"])
        self.assertIn("NEWCO", audit["pending_tickers"])
        self.assertFalse(bundle.manifest["coverage"]["market_supported_complete"])
        self.assertEqual(bundle.manifest["coverage"]["market_missing_ticker_count"], len(audit["pending_tickers"]))
        self.assertEqual([r for r in candidate["source_health"] if r["source_id"] == "alpaca_sip_eod"],
                         prices["source_health"])
        record = next(r for r in processed["transactions"] if r["id"] == "new-disclosure")
        self.assertIsNone(record["underlying_return_since_trade"])
        self.assertFalse(record["performance_eligible"])
        self.assertEqual(record["performance_ineligible_reason"], "missing_market_series")
        self.assertEqual(candidate["security_market_data"], prices["security_market_data"])
        repo = PublicSnapshotRepository("example", "data", transport=FakeTransport(
            bundle.files, extra_commits={MARKET_COMMIT: market.files}))
        for mode, key in (("dashboard", None), ("person", new["person_id"]), ("ticker", "NEWCO")):
            read = repo.fetch(mode, key).snapshot
            self.assertIn(new, read["transactions"])
            processed = load("process_snapshot", version="v2").build_snapshot(read)
            record = next(r for r in processed["transactions"] if r["id"] == new["id"])
            self.assertEqual(record["performance_ineligible_reason"], "missing_market_series")

    def test_stale_refresh_cannot_overwrite_another_price_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "market-base.txt").write_text("a" * 40)
            (root / "prices.json.gz").write_bytes(gzip.compress(encode(self.prices())))
            self.assertEqual(prepared_prices(root, "a" * 40), self.prices())
            with self.assertRaisesRegex(ValueError, "Market changed during refresh"):
                prepared_prices(root, "b" * 40)

    def test_pending_cannot_hide_overlap_or_masquerade_as_complete_live_coverage(self):
        data = production_candidate()
        data["meta"]["data_cutoff_at"] = "2026-09-20T23:59:59Z"
        candidate, _ = join_disclosures(data, self.prices(), generated_at="2026-09-21T00:00:00Z")
        kwargs = dict(generated_at="2026-09-21T00:00:00Z", allow_production=True,
                      allow_market=True, market_commit="a" * 40, market_pages=["b" * 64])
        candidate["meta"]["market_coverage"]["pending_tickers"] = ["AAPL"]
        with self.assertRaises(ValueError):
            build(candidate, **kwargs)
        candidate["meta"]["market_coverage"]["pending_tickers"] = ["NEWCO"]
        del candidate["meta"]["market_coverage"]["price_scope"]
        with self.assertRaisesRegex(ValueError, "explicit cached publication"):
            build(candidate, **kwargs)

    def test_fixed_cache_hash_pair_and_non_security_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main, market = root / "main", root / "prices"
            main.mkdir(); (market / "market-pages").mkdir(parents=True)
            (market / "market").mkdir()
            raw = encode({"security_market_data": self.prices()["security_market_data"]})
            sha = digest(raw)
            path = market / "market-pages" / f"{sha}.json"
            path.write_bytes(raw)
            (market / "market/twelve-data-state.json").write_text(json.dumps({"entries": {
                "BOND": {"status": "not_market_security"}}}))
            manifest = {"is_demo": False, "market_commit": "a" * 40, "market_pages": [sha],
                        "source_health": self.prices()["source_health"],
                        "coverage": {"market_ticker_count": 2}}
            (main / "manifest.json").write_text(json.dumps(manifest))
            cached = published_prices(main, market, "a" * 40)
            self.assertEqual(cached["security_market_data"], self.prices()["security_market_data"])
            self.assertEqual(cached["meta"]["market_coverage"]["unsupported_tickers"],
                             [{"ticker": "BOND", "reason": "not_market_security"}])
            with self.assertRaisesRegex(ValueError, "matching published"):
                published_prices(main, market, "c" * 40)
            path.write_bytes(raw + b" ")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                published_prices(main, market, "a" * 40)


if __name__ == "__main__":
    unittest.main()
