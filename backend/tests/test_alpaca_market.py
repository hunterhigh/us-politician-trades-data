from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unison_snapshot.alpaca_market import (
    ADJUSTMENT,
    FEED,
    TIMEFRAME,
    AlpacaMarketClient,
    AlpacaMarketError,
    build_market_validation,
    latest_safe_session,
)
from unison_snapshot.legacy import load


FIXTURE = Path(__file__).resolve().parents[1] / "examples/synthetic.json"


def production_candidate() -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["meta"].update(is_demo=False, snapshot_id="disclosure-candidate")
    for row in data["transactions"] + data["reported_holdings"]:
        row["verification_status"] = "official_matched"
        if row["source_id"] == "house_clerk":
            row["source_url"] = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/example.pdf"
        else:
            row["source_url"] = "https://efdsearch.senate.gov/search/view/ptr/example/"
    data["source_health"] = [
        {"source_id": "house_clerk", "source": "House Clerk", "status": "ok",
         "last_checked_at": "2026-09-18T00:00:00Z"},
        {"source_id": "senate_efd", "source": "Senate eFD", "status": "ok",
         "last_checked_at": "2026-09-18T00:00:00Z"},
    ]
    return data


class FakeMarketClient:
    def __init__(self, response: dict[str, list[dict]], assets: list[dict] | None = None):
        self.response = response
        self.asset_response = assets if assets is not None else [asset("ZZDEMO")]
        self.calls = []

    def assets(self):
        return self.asset_response

    def daily_bars(self, symbols, *, start, end):
        self.calls.append((symbols, start, end))
        return {symbol: self.response[symbol] for symbol in symbols if symbol in self.response}


def asset(symbol: str, *, exchange: str = "NASDAQ", status: str = "active") -> dict:
    return {"symbol": symbol, "class": "us_equity", "exchange": exchange,
            "status": status, "name": symbol}


class Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class AlpacaMarketTests(unittest.TestCase):
    def test_safe_session_waits_past_basic_delay_and_skips_weekend(self):
        before_close = datetime(2026, 9, 18, 19, 0, tzinfo=timezone.utc)
        after_close = datetime(2026, 9, 18, 21, 0, tzinfo=timezone.utc)
        sunday = datetime(2026, 9, 20, 21, 0, tzinfo=timezone.utc)
        self.assertEqual(str(latest_safe_session(before_close)), "2026-09-17")
        self.assertEqual(str(latest_safe_session(after_close)), "2026-09-18")
        self.assertEqual(str(latest_safe_session(sunday)), "2026-09-18")

    def test_http_client_uses_fixed_contract_and_follows_pagination(self):
        requests = []
        pages = [
            {"bars": {"AAPL": [{"t": "2026-09-17T04:00:00Z", "c": 100}]},
             "next_page_token": "next"},
            {"bars": {"AAPL": [{"t": "2026-09-18T04:00:00Z", "c": 101}]},
             "next_page_token": None},
        ]

        def opener(request, timeout):
            requests.append((request, timeout))
            return Response(pages.pop(0))

        client = AlpacaMarketClient("key", "secret", timeout=12, opener=opener)
        bars = client.daily_bars(
            ["AAPL"], start=datetime(2024, 9, 1, tzinfo=timezone.utc),
            end=datetime(2026, 9, 19, tzinfo=timezone.utc))
        self.assertEqual(len(bars["AAPL"]), 2)
        self.assertEqual(len(requests), 2)
        query = parse_qs(urlsplit(requests[0][0].full_url).query)
        self.assertEqual(query["feed"], [FEED])
        self.assertEqual(query["timeframe"], [TIMEFRAME])
        self.assertEqual(query["adjustment"], [ADJUSTMENT])
        self.assertEqual(parse_qs(urlsplit(requests[1][0].full_url).query)["page_token"], ["next"])
        self.assertEqual(requests[0][0].get_header("Apca-api-key-id"), "key")
        self.assertEqual(requests[0][1], 12)

    def test_http_client_retries_throttling_without_leaking_response(self):
        attempts = []
        sleeps = []

        def opener(request, timeout):
            attempts.append(request)
            if len(attempts) == 1:
                raise HTTPError(request.full_url, 429, "secret-shaped body", {}, None)
            return Response({"bars": {"AAPL": []}, "next_page_token": None})

        client = AlpacaMarketClient(
            "key", "secret", retries=1, opener=opener, sleeper=sleeps.append)
        self.assertEqual(client.daily_bars(
            ["AAPL"], start=datetime(2024, 9, 1, tzinfo=timezone.utc),
            end=datetime(2026, 9, 19, tzinfo=timezone.utc)), {"AAPL": []})
        self.assertEqual(len(attempts), 2)
        self.assertEqual(sleeps, [1])
        self.assertEqual(client.page_count, 1)
        self.assertEqual(client.request_count, 2)

    def test_http_client_loads_asset_master_and_rejects_secret_exfiltration_url(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return Response([asset("AAPL")])

        client = AlpacaMarketClient("key", "secret", opener=opener)
        self.assertEqual(client.assets()[0]["symbol"], "AAPL")
        self.assertEqual(
            parse_qs(urlsplit(requests[0].full_url).query)["asset_class"], ["us_equity"])
        with self.assertRaisesRegex(AlpacaMarketError, "allowlisted"):
            AlpacaMarketClient(
                "key", "secret", assets_url="https://example.com/v2/assets")

    def test_builds_local_partial_candidate_without_fabricating_missing_prices(self):
        snapshot = production_candidate()
        snapshot["transactions"][1]["ticker"] = "MISSING"
        client = FakeMarketClient({
            "ZZDEMO": [
                {"t": "2024-08-01T04:00:00Z", "c": 70},
                {"t": "2024-09-02T04:00:00Z", "c": 80},
                {"t": "2026-06-30T04:00:00Z", "c": 100},
                {"t": "2026-09-18T04:00:00Z", "c": 110},
                {"t": "2026-09-21T04:00:00Z", "c": 999},
            ]
        }, assets=[asset("ZZDEMO"), asset("MISSING")])
        validation = build_market_validation(
            snapshot, client=client, checked_at="2026-09-20T21:00:00Z", batch_size=1)
        self.assertEqual(validation.audit["symbol_count"], 2)
        self.assertEqual(validation.audit["market_row_count"], 1)
        self.assertEqual(validation.audit["missing_ticker_count"], 1)
        self.assertFalse(validation.audit["distribution_authorized"])
        self.assertEqual(validation.snapshot["security_market_data"][0]["ticker"], "ZZDEMO")
        market_health = next(row for row in validation.snapshot["source_health"]
                             if row["source_id"] == "alpaca_sip_eod")
        self.assertEqual(market_health["status"], "partial")
        self.assertIn("local_basic_validation", market_health["detail"])
        processed = load("process_snapshot").build_snapshot(validation.snapshot)
        market = processed["security_market_data"][0]
        self.assertEqual(market["as_of_date"], "2026-09-18")
        self.assertEqual(market["current_price"], 110.0)
        self.assertEqual(market["previous_quarter_end_price"], 100.0)
        self.assertEqual(len(market["price_history"]), 3)

    def test_production_rejects_supported_ticker_without_bars(self):
        snapshot = production_candidate()
        snapshot["transactions"][1]["ticker"] = "MISSING"
        client = FakeMarketClient({
            "ZZDEMO": [{"t": "2026-09-18T04:00:00Z", "c": 110}],
        }, assets=[asset("ZZDEMO"), asset("MISSING")])
        with self.assertRaisesRegex(AlpacaMarketError, "coverage is incomplete.*MISSING"):
            build_market_validation(
                snapshot, client=client, checked_at="2026-09-20T21:00:00Z",
                distribution_authorized=True)

    def test_production_rejects_stale_active_series_but_keeps_inactive_history(self):
        snapshot = production_candidate()
        stale = [{"t": "2026-08-01T04:00:00Z", "c": 110}]
        with self.assertRaisesRegex(AlpacaMarketError, "coverage is incomplete.*ZZDEMO"):
            build_market_validation(
                snapshot, client=FakeMarketClient({"ZZDEMO": stale}),
                checked_at="2026-09-20T21:00:00Z", distribution_authorized=True)
        validation = build_market_validation(
            snapshot,
            client=FakeMarketClient({"ZZDEMO": stale}, assets=[asset("ZZDEMO", status="inactive")]),
            checked_at="2026-09-20T21:00:00Z", distribution_authorized=True)
        self.assertEqual(validation.audit["market_row_count"], 1)

    def test_history_request_covers_earliest_supported_disclosure(self):
        snapshot = production_candidate()
        snapshot["transactions"][0].update(
            transaction_date="2020-01-03", filed_at="2020-02-11T00:00:00Z")
        client = FakeMarketClient({
            "ZZDEMO": [{"t": "2026-09-18T04:00:00Z", "c": 110}],
        })
        build_market_validation(
            snapshot, client=client, checked_at="2026-09-20T21:00:00Z")
        self.assertEqual(client.calls[0][1].date().isoformat(), "2019-12-27")

    def test_recovers_explicit_name_ticker_and_classifies_sip_scope(self):
        snapshot = production_candidate()
        snapshot["transactions"][0].update(
            ticker=None, ticker_mapping_basis=None,
            asset_name="Berkshire Hathaway Class B (BRK.B)")
        snapshot["transactions"][1].update(
            ticker="BOND12345", instrument_type="Bond", asset_name="Issuer Bond")
        snapshot["reported_holdings"][0].update(
            ticker="ADRNY", asset_name="Ahold Delhaize Sponsored ADR (ADRNY)")
        fund = deepcopy(snapshot["transactions"][1])
        fund.update(id="fund-row", ticker="FXAIX", instrument_type="Mutual Fund",
                    asset_name="Fidelity 500 Index Fund")
        snapshot["transactions"].append(fund)
        preferred = deepcopy(snapshot["transactions"][1])
        preferred.update(id="preferred-row", ticker="SNV-D", instrument_type="Stock",
                         asset_name="Synovus Financial Corp. 6.3%")
        snapshot["transactions"].append(preferred)
        client = FakeMarketClient({
            "BRK.B": [{"t": "2026-09-18T04:00:00Z", "c": 500}],
            "SNV.PR.D": [{"t": "2026-09-18T04:00:00Z", "c": 25}],
        }, assets=[asset("BRK.B"), asset("SNV.PR.D"), asset("ADRNY", exchange="OTC")])
        validation = build_market_validation(
            snapshot, client=client, checked_at="2026-09-20T21:00:00Z",
            distribution_authorized=True)
        self.assertEqual(validation.audit["recovered_ticker_count"], 1)
        self.assertEqual(validation.audit["supported_ticker_count"], 2)
        self.assertEqual(validation.audit["unsupported_ticker_count"], 3)
        self.assertEqual(validation.audit["unresolved_ticker_count"], 0)
        first = validation.snapshot["transactions"][0]
        self.assertEqual((first["ticker"], first["ticker_mapping_basis"]),
                         ("BRK.B", "filing_explicit_asset_name"))
        preferred_mapping = next(row for row in validation.audit["supported_tickers"]
                                 if row["ticker"] == "SNV-D")
        self.assertEqual(preferred_mapping["provider_symbol"], "SNV.PR.D")
        self.assertEqual(
            validation.snapshot["meta"]["market_coverage"]["unsupported_tickers"],
            [{"ticker": "ADRNY", "reason": "outside_sip_otc"},
             {"ticker": "BOND12345", "reason": "non_equity_debt"},
             {"ticker": "FXAIX", "reason": "outside_sip_fund"}])

    def test_production_rejects_symbol_absent_from_asset_master(self):
        snapshot = production_candidate()
        client = FakeMarketClient({}, assets=[asset("OTHER")])
        with self.assertRaisesRegex(AlpacaMarketError, "unresolved.*ZZDEMO"):
            build_market_validation(
                snapshot, client=client, checked_at="2026-09-20T21:00:00Z",
                distribution_authorized=True)

    def test_ignores_non_authoritative_asset_rows_but_keeps_required_symbols_fail_closed(self):
        snapshot = production_candidate()
        malformed = {
            "symbol": "NEW-ASSET-CLASS",
            "class": "global_equity",
            "exchange": "",
            "status": "active",
        }
        validation = build_market_validation(
            snapshot,
            client=FakeMarketClient(
                {"ZZDEMO": [{"t": "2026-09-18T04:00:00Z", "c": 110}]},
                assets=[malformed, asset("ZZDEMO")],
            ),
            checked_at="2026-09-20T21:00:00Z",
            distribution_authorized=True,
        )
        self.assertEqual(validation.audit["market_row_count"], 1)

        with self.assertRaisesRegex(AlpacaMarketError, "unresolved.*ZZDEMO"):
            build_market_validation(
                snapshot,
                client=FakeMarketClient({}, assets=[malformed, asset("OTHER")]),
                checked_at="2026-09-20T21:00:00Z",
                distribution_authorized=True,
            )

    def test_duplicate_asset_symbol_prefers_active_when_market_scope_is_unambiguous(self):
        snapshot = production_candidate()
        validation = build_market_validation(
            snapshot,
            client=FakeMarketClient(
                {"ZZDEMO": [{"t": "2026-09-18T04:00:00Z", "c": 110}]},
                assets=[
                    asset("ZZDEMO", exchange="OTC", status="inactive"),
                    asset("ZZDEMO", exchange="NASDAQ", status="active"),
                    asset("ZZDEMO", exchange="NYSE", status="active"),
                ],
            ),
            checked_at="2026-09-20T21:00:00Z",
            distribution_authorized=True,
        )
        self.assertEqual(validation.audit["market_row_count"], 1)

        with self.assertRaisesRegex(AlpacaMarketError, "conflicting market scope.*ZZDEMO"):
            build_market_validation(
                snapshot,
                client=FakeMarketClient(
                    {},
                    assets=[
                        asset("ZZDEMO", exchange="OTC", status="active"),
                        asset("ZZDEMO", exchange="NASDAQ", status="active"),
                    ],
                ),
                checked_at="2026-09-20T21:00:00Z",
                distribution_authorized=True,
            )

    def test_future_as_of_and_empty_response_fail_closed(self):
        snapshot = production_candidate()
        client = FakeMarketClient({})
        with self.assertRaisesRegex(AlpacaMarketError, "safe completed session"):
            build_market_validation(
                snapshot, client=client, checked_at="2026-09-20T21:00:00Z",
                as_of_date="2026-09-21")
        stale = production_candidate()
        stale["meta"]["data_cutoff_at"] = "2026-09-17T23:59:59Z"
        with self.assertRaisesRegex(AlpacaMarketError, "snapshot cutoff"):
            build_market_validation(
                stale, client=client, checked_at="2026-09-20T21:00:00Z",
                as_of_date="2026-09-18")
        with self.assertRaisesRegex(AlpacaMarketError, "no usable market rows"):
            build_market_validation(
                snapshot, client=client, checked_at="2026-09-20T21:00:00Z")

    def test_input_is_not_mutated(self):
        snapshot = production_candidate()
        original = deepcopy(snapshot)
        client = FakeMarketClient({
            "ZZDEMO": [{"t": "2026-09-18T04:00:00Z", "c": 110}],
        })
        build_market_validation(
            snapshot, client=client, checked_at="2026-09-20T21:00:00Z")
        self.assertEqual(snapshot, original)

    def test_malformed_bar_fails_the_candidate_instead_of_becoming_missing(self):
        snapshot = production_candidate()
        client = FakeMarketClient({
            "ZZDEMO": [
                {"t": "2026-09-18T04:00:00Z", "c": 110},
                {"t": "2026-09-18T05:00:00Z", "c": 110},
            ],
        })
        with self.assertRaisesRegex(AlpacaMarketError, "duplicate daily bars"):
            build_market_validation(
                snapshot, client=client, checked_at="2026-09-20T21:00:00Z")


if __name__ == "__main__":
    unittest.main()
