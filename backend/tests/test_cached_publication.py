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
    def test_cached_and_prepared_cli_use_current_consumer_and_preserve_approved_annual_facts(self):
        import importlib.util
        from test_oge_annual_approved_policy import complete_case
        from unison_snapshot.legacy import processor_version_for
        script = Path(__file__).resolve().parents[1] / "scripts/build_cached_candidate.py"
        spec = importlib.util.spec_from_file_location("cached_candidate_cli", script)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        _, (annual, _) = complete_case()
        annual["transactions"].append(dict(annual["transactions"][0], id="new-cached-v3",
                                           ticker="NEWCO", asset_name="New company"))
        for prepared, data, version in [(False, annual, "v3"), (True, annual, "v3"),
                                        (False, production_candidate(), "v2")]:
            with self.subTest(prepared=prepared, version=version), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                main, prices = root / "main", root / "prices"
                main.mkdir(); (prices / "market-pages").mkdir(parents=True)
                raw = encode({"security_market_data": self.prices()["security_market_data"]})
                sha = digest(raw)
                (prices / "market-pages" / f"{sha}.json").write_bytes(raw)
                manifest = {"is_demo": False, "market_commit": MARKET_COMMIT, "market_pages": [sha],
                            "source_health": self.prices()["source_health"],
                            "coverage": {"market_ticker_count": 2}}
                (main / "manifest.json").write_bytes(encode(manifest))
                source = root / "input.json"; source.write_bytes(encode(data))
                output = root / "output"
                args = [str(script), "--input", str(source), "--main-root", str(main),
                        "--market-root", str(prices), "--market-commit", MARKET_COMMIT,
                        "--output-dir", str(output)]
                if prepared:
                    folder = root / "prepared"; folder.mkdir()
                    (folder / "market-base.txt").write_text(MARKET_COMMIT)
                    (folder / "prices.json.gz").write_bytes(gzip.compress(encode(self.prices())))
                    args += ["--prepared", str(folder)]
                with patch.object(sys, "argv", args), patch.object(cli, "load", wraps=load) as loader, \
                        patch("urllib.request.urlopen", side_effect=AssertionError("No vendor calls")):
                    cli.main()
                loader.assert_called_once_with("render_dashboard", version=version)
                candidate = json.loads((output / "complete-candidate.json").read_bytes())
                self.assertEqual(processor_version_for(candidate), version)
                self.assertEqual(candidate["transactions"], data["transactions"])
                self.assertEqual(candidate["reported_holdings"], data["reported_holdings"])
                for key in ("holding_report_coverage", "transaction_disclosure_relations"):
                    self.assertEqual(candidate["meta"].get(key), data["meta"].get(key))
                self.assertEqual(source.read_bytes(), encode(data))
                market = build_market_bundle(candidate["security_market_data"],
                                             data_cutoff_at=candidate["meta"]["data_cutoff_at"])
                bundle = build(candidate, generated_at=candidate["meta"]["generated_at"],
                               allow_production=True, allow_market=True,
                               market_commit=MARKET_COMMIT, market_pages=market.page_shas)
                reader = PublicSnapshotRepository("example", "data", transport=FakeTransport(
                    bundle.files, extra_commits={MARKET_COMMIT: market.files}))
                selected = reader.fetch("dashboard").snapshot
                self.assertEqual({row["id"]: row for row in selected["transactions"]},
                                 {row["id"]: row for row in candidate["transactions"]})
                for key in ("holding_report_coverage", "transaction_disclosure_relations"):
                    self.assertEqual(selected["meta"].get(key), candidate["meta"].get(key))
                if version == "v3":
                    self.assertEqual(bundle.manifest["coverage"]["market_missing_ticker_count"], 1)
                    self.assertFalse(bundle.manifest["coverage"]["market_supported_complete"])
                    processed = load("process_snapshot", version="v3").build_snapshot(selected)
                    pending = next(row for row in processed["transactions"] if row["id"] == "new-cached-v3")
                    self.assertIsNone(pending["underlying_return_since_trade"])
                    self.assertEqual(pending["performance_ineligible_reason"], "missing_market_series")
                self.assertGreater((output / "complete-dashboard.html").stat().st_size, 0)

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
