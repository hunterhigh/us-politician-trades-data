import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unison_snapshot.codec import bucket
from unison_snapshot.market_store import (build_market_bundle,
                                          load_published_market_cache,
                                          load_published_twelve_data_cache,
                                          load_price_cache_with_recovery,
                                          materialize_market)


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
    def test_additive_recovery_keeps_newer_rows_and_current_identity_rejections(self):
        def twelve(ticker, day, close):
            value = row(ticker)
            value.update(source_id='twelve_data_split_adjusted_eod',
                price_source='Twelve Data split-adjusted EOD', source_url='https://twelvedata.com/docs',
                feed='twelve_data', price_history=[{'date': day, 'close': close}])
            return value
        old = [twelve('PREFERRED', '2026-09-17', 90), twelve('LOST', '2026-09-17', 80),
               twelve('REJECTED', '2026-09-17', 70)]
        latest = [twelve('PREFERRED', '2026-09-18', 99), twelve('NEW', '2026-09-18', 100)]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, rows, sha in [('current', latest, 'a'*40), ('recovery', old, 'b'*40)]:
                target = root / label; target.mkdir()
                state = {'schema_version': 'twelve-data-market-state/v1', 'entries': {
                    r['ticker']: {'status': 'accepted', 'checked_at': r['price_history'][-1]['date']+'T00:00:00Z',
                                  'fingerprint': 'c'*64} for r in rows}}
                if label == 'current':
                    state['entries']['REJECTED'] = {'status': 'identity_unresolved'}
                bundle = build_market_bundle(rows, data_cutoff_at='2026-09-20T00:00:00Z',
                                              twelve_audit={'state': state})
                materialize_market(target, bundle)
                (target/'manifest.json').write_text(json.dumps({'is_demo': False,
                    'market_commit': sha, 'data_cutoff_at': '2026-09-20T00:00:00Z'}))
            kwargs = {'main_root':root/'current', 'market_root':root/'current', 'market_commit':'a'*40,
                      'recovery_main_root':root/'recovery', 'recovery_market_root':root/'recovery',
                      'recovery_market_commit':'b'*40}
            result = load_price_cache_with_recovery(load_published_twelve_data_cache, **kwargs)
            self.assertEqual(result.rows['PREFERRED'], latest[0])
            self.assertEqual(result.rows['NEW'], latest[1])
            self.assertEqual(result.rows['LOST'], old[1])
            self.assertNotIn('REJECTED', result.rows)
            self.assertEqual(result.state['entries']['LOST']['checked_at'], '2026-09-17T00:00:00Z')
            with self.assertRaisesRegex(ValueError, 'matching published main/market pair'):
                load_price_cache_with_recovery(load_published_twelve_data_cache,
                    **dict(kwargs, recovery_market_commit='d'*40))
            with self.assertRaisesRegex(ValueError, 'provided together'):
                load_price_cache_with_recovery(load_published_twelve_data_cache,
                    **dict(kwargs, recovery_market_root=None))

    def test_published_twelve_data_state_rehydrates_accepted_rows(self):
        twelve = row("FUNDX")
        twelve.update(source_id="twelve_data_split_adjusted_eod",
                      price_source="Twelve Data split-adjusted EOD",
                      source_url="https://twelvedata.com/docs", feed="twelve_data")
        twelve_audit = {"state": {
            "schema_version": "twelve-data-market-state/v1",
            "updated_at": "2026-09-20T00:00:00Z",
            "next_work_kind": "new",
            "entries": {"FUNDX": {"status": "accepted",
                                    "checked_at": "2026-09-20T00:00:00Z",
                                    "last_attempt_at": "2026-09-20T00:00:00Z",
                                    "history_start_date": "2026-09-01",
                                    "history_end_date": "2026-09-19",
                                    "full_refreshed_at": "2026-09-20T00:00:00Z",
                                    "fingerprint": "a" * 64}},
        }}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main, market = root / "main", root / "market-branch"
            main.mkdir()
            bundle = build_market_bundle([twelve],
                                         data_cutoff_at="2026-09-20T23:59:59Z",
                                         twelve_audit=twelve_audit)
            materialize_market(market, bundle)
            (main / "manifest.json").write_text(json.dumps({
                "is_demo": False, "market_commit": "a" * 40,
                "data_cutoff_at": "2026-09-20T23:59:59Z",
            }), encoding="utf-8")
            cache = load_published_twelve_data_cache(
                main, market, market_commit="a" * 40)
            self.assertEqual(cache.rows["FUNDX"]["source_id"],
                             "twelve_data_split_adjusted_eod")
            self.assertEqual(cache.state["entries"]["FUNDX"]["status"], "accepted")
            self.assertEqual(cache.state, twelve_audit["state"])

    def test_published_cache_requires_matching_pointer_and_valid_shard_hash(self):
        audit = {
            "schema_version": "alpaca-market-validation/v2",
            "start_date": "2026-09-01", "requested_as_of_date": "2026-09-18",
            "full_history_verified_at": "2026-09-18",
            "feed": "sip", "timeframe": "1Day", "adjustment": "split",
            "market_row_count": 1, "covered_tickers": ["AAPL"],
            "supported_tickers": [{"ticker": "AAPL", "provider_symbol": "AAPL"}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main = root / "main"
            market = root / "market-branch"
            main.mkdir()
            bundle = build_market_bundle([row()], data_cutoff_at="2026-09-20T23:59:59Z",
                                         audit=audit)
            materialize_market(market, bundle)
            (main / "manifest.json").write_text(json.dumps({
                "is_demo": False, "market_commit": "a" * 40,
                "coverage": {"market_ticker_count": 1},
            }), encoding="utf-8")
            self.assertIsNone(load_published_market_cache(
                main, market, market_commit="b" * 40))
            cache = load_published_market_cache(main, market, market_commit="a" * 40)
            self.assertEqual(cache.rows["AAPL"]["price_history"][-1]["close"], 101.25)
            self.assertEqual(cache.requested_start_date.isoformat(), "2026-09-01")
            index_path = market / "market" / bucket("market", "AAPL") / "index.json"
            sha = json.loads(index_path.read_text())["shards"]["AAPL"]
            (index_path.parent / f"{sha}.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                load_published_market_cache(main, market, market_commit="a" * 40)

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

    def test_materialize_prunes_old_objects_and_replaces_indexes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = build_market_bundle([row("AAPL")], data_cutoff_at="2026-09-20T23:59:59Z")
            materialize_market(root, first)
            second = build_market_bundle([row("MSFT")], data_cutoff_at="2026-09-20T23:59:59Z")
            old_only = set(first.files) - set(second.files)
            result = materialize_market(root, second)
            self.assertTrue(result.changed)
            self.assertTrue(old_only)
            self.assertTrue(old_only <= set(result.removed))
            self.assertTrue(all(not (root / path).exists() for path in old_only))
            self.assertTrue(all((root / path).is_file() for path in second.files))

    def test_rejects_wrong_source_and_future_prices(self):
        bad = row()
        bad["source_id"] = "other"
        with self.assertRaisesRegex(ValueError, "invalid source or price basis"):
            build_market_bundle([bad], data_cutoff_at="2026-09-20T23:59:59Z")
        bad = row()
        bad["price_history"][-1]["date"] = "2026-09-21"
        with self.assertRaisesRegex(ValueError, "invalid completed close"):
            build_market_bundle([bad], data_cutoff_at="2026-09-20T23:59:59Z")


if __name__ == "__main__":
    unittest.main()
