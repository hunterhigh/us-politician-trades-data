from copy import deepcopy
from datetime import date, datetime, timezone
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
from unison_snapshot.market_store import PublishedMarketCache
from unison_snapshot.whitehouse_annual_tickers import enrich_whitehouse_annual_tickers


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

    def daily_bars(self, symbols, *, start, end, asof=None):
        self.calls.append((symbols, start, end, asof))
        return {symbol: self.response[symbol] for symbol in symbols if symbol in self.response}


def asset(symbol: str, *, exchange: str = "NASDAQ", status: str = "active",
          name: str | None = None) -> dict:
    return {"symbol": symbol, "class": "us_equity", "exchange": exchange,
            "status": status, "name": name or symbol}


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
    def test_incremental_history_reuses_verified_older_closes(self):
        snapshot = production_candidate()
        snapshot["meta"]["data_cutoff_at"] = "2026-09-22T23:59:59Z"
        cached = PublishedMarketCache(
            rows={"ZZDEMO": {
                "price_history": [
                    {"date": "2024-09-02", "close": 80.0},
                    {"date": "2026-09-18", "close": 110.0},
                ]}},
            requested_start_date=date(2024, 8, 1),
            requested_as_of_date=date(2026, 9, 18),
            provider_symbols={"ZZDEMO": "ZZDEMO"},
        )

        class DateFilteredClient(FakeMarketClient):
            def daily_bars(self, symbols, *, start, end, asof=None):
                self.calls.append((symbols, start, end, asof))
                return {symbol: [bar for bar in self.response.get(symbol, [])
                                 if start.date() <= date.fromisoformat(bar["t"][:10])
                                 and date.fromisoformat(bar["t"][:10]) <= end.date()]
                        for symbol in symbols}

        client = DateFilteredClient({"ZZDEMO": [
            {"t": "2026-09-18T04:00:00Z", "c": 110},
            {"t": "2026-09-21T04:00:00Z", "c": 111},
            {"t": "2026-09-22T04:00:00Z", "c": 112},
        ]})
        result = build_market_validation(
            snapshot, client=client, checked_at="2026-09-22T21:00:00Z",
            distribution_authorized=True, previous_market=cached)
        self.assertEqual(result.audit["incremental_ticker_count"], 1)
        self.assertEqual(result.audit["full_refresh_ticker_count"], 0)
        self.assertEqual(result.snapshot["security_market_data"][0]["price_history"][0],
                         {"date": "2024-09-02", "close": 80.0})
        self.assertEqual(client.calls[0][1].date(), date(2026, 8, 4))
        full = DateFilteredClient({"ZZDEMO": [
            {"t": "2024-09-02T04:00:00Z", "c": 80}, *client.response["ZZDEMO"],
        ]})
        full_result = build_market_validation(
            snapshot, client=full, checked_at="2026-09-22T21:00:00Z",
            distribution_authorized=True)
        self.assertEqual(result.snapshot["security_market_data"],
                         full_result.snapshot["security_market_data"])

    def test_changed_split_adjusted_overlap_forces_full_refetch(self):
        snapshot = production_candidate()
        snapshot["meta"]["data_cutoff_at"] = "2026-09-22T23:59:59Z"
        cached = PublishedMarketCache(
            rows={"ZZDEMO": {"price_history": [
                {"date": "2024-09-02", "close": 80.0},
                {"date": "2026-09-18", "close": 110.0}]}},
            requested_start_date=date(2024, 8, 1),
            requested_as_of_date=date(2026, 9, 18),
            provider_symbols={"ZZDEMO": "ZZDEMO"},
        )

        class DateFilteredClient(FakeMarketClient):
            def daily_bars(self, symbols, *, start, end, asof=None):
                self.calls.append((symbols, start, end, asof))
                return {symbol: [bar for bar in self.response.get(symbol, [])
                                 if start.date() <= date.fromisoformat(bar["t"][:10])]
                        for symbol in symbols}

        client = DateFilteredClient({"ZZDEMO": [
            {"t": "2024-09-02T04:00:00Z", "c": 40},
            {"t": "2026-09-18T04:00:00Z", "c": 55},
            {"t": "2026-09-21T04:00:00Z", "c": 56},
        ]})
        result = build_market_validation(
            snapshot, client=client, checked_at="2026-09-22T21:00:00Z",
            distribution_authorized=True, previous_market=cached)
        self.assertEqual(result.audit["split_refresh_ticker_count"], 1)
        self.assertEqual(result.audit["incremental_ticker_count"], 0)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(result.snapshot["security_market_data"][0]["price_history"][0],
                         {"date": "2024-09-02", "close": 40.0})

    def test_old_cache_falls_back_to_full_history(self):
        snapshot = production_candidate()
        snapshot["meta"]["data_cutoff_at"] = "2026-09-22T23:59:59Z"
        cached = PublishedMarketCache(
            rows={"ZZDEMO": {"price_history": [
                {"date": "2026-09-10", "close": 100.0}]}},
            requested_start_date=date(2024, 8, 1),
            requested_as_of_date=date(2026, 9, 10),
            provider_symbols={"ZZDEMO": "ZZDEMO"},
        )
        client = FakeMarketClient({
            "ZZDEMO": [{"t": "2026-09-22T04:00:00Z", "c": 110}],
        })
        result = build_market_validation(
            snapshot, client=client, checked_at="2026-09-22T21:00:00Z",
            distribution_authorized=True, previous_market=cached)
        self.assertEqual(result.audit["cache_status"], "ineligible")
        self.assertEqual(result.audit["full_refresh_ticker_count"], 1)
        self.assertLess(client.calls[0][1].date(), date(2025, 1, 1))

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

    def test_production_accounts_for_inactive_asset_without_available_bars(self):
        snapshot = production_candidate()
        snapshot["transactions"][1]["ticker"] = "INACTIVE"
        validation = build_market_validation(
            snapshot,
            client=FakeMarketClient(
                {"ZZDEMO": [{"t": "2026-09-18T04:00:00Z", "c": 110}]},
                assets=[asset("ZZDEMO"), asset("INACTIVE", status="inactive")],
            ),
            checked_at="2026-09-20T21:00:00Z",
            distribution_authorized=True,
        )
        self.assertEqual(
            validation.snapshot["meta"]["market_coverage"]["unsupported_tickers"],
            [{"ticker": "INACTIVE", "reason": "outside_sip_inactive"}],
        )

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

    def test_recovers_unique_alpaca_names_for_white_house_annual_rows(self):
        snapshot = production_candidate()
        examples = [
            ("wh-annual-tx:kraft", "KRAFT HEINZ CO", "Unspecified"),
            ("wh-annual-tx:texas", "TEXAS INSTRS INC", "Unspecified"),
            ("wh-annual-tx:zoetis", "ZOETIS INC", "Unspecified"),
            ("wh-annual-tx:synopsys", "SYNOPSYS INC", "Unspecified"),
            ("wh-annual-tx:costco", "COSTCO WHSL CORP NEW", "Unspecified"),
            ("wh-annual-tx:capital-one", "CAPITAL ONE FINANCIAL CORP", "Unspecified"),
            ("wh-annual-tx:alphabet", "ALPHABET INC", "Unspecified"),
            ("wh-annual-tx:berkshire", "BERKSHIRE HATHAWAY", "Unspecified"),
            ("wh-annual-tx:under-armour", "UNDER ARMOUR INC", "Unspecified"),
        ]
        for record_id, name, instrument in examples:
            row = deepcopy(snapshot["transactions"][0])
            row.update(id=record_id, asset_name=name, instrument_type=instrument,
                       ticker=None, ticker_mapping_basis=None)
            snapshot["transactions"].append(row)
        non_annual = deepcopy(snapshot["transactions"][0])
        non_annual.update(id="oge:unmapped-kraft", asset_name="KRAFT HEINZ CO",
                          ticker=None, ticker_mapping_basis=None)
        snapshot["transactions"].append(non_annual)
        bars = [{"t": "2026-09-18T04:00:00Z", "c": 100}]
        client = FakeMarketClient(
            {ticker: bars for ticker in ("ZZDEMO", "KHC", "TXN", "ZTS", "SNPS", "COST", "COF")},
            assets=[
                asset("ZZDEMO"),
                asset("KHC", name="The Kraft Heinz Company Common Stock"),
                asset("TXN", name="Texas Instruments Incorporated Common Stock"),
                asset("ZTS", name="Zoetis Inc. Class A Common Stock"),
                asset("SNPS", name="Synopsys, Inc. Common Stock"),
                asset("COST", name="Costco Wholesale Corporation Common Stock"),
                asset("COF", name="Capital One Financial Corporation Common Stock"),
                asset("GOOG", name="Alphabet Inc. Class C Capital Stock"),
                asset("GOOGL", name="Alphabet Inc. Class A Common Stock"),
                asset("BRK.A", name="Berkshire Hathaway Inc. Class A Common Stock"),
                asset("BRK.B", name="Berkshire Hathaway Inc. Class B Common Stock"),
                asset("UA", name="Under Armour, Inc. Class C Common Stock"),
                asset("UAA", name="Under Armour, Inc. Class A Common Stock"),
            ],
        )
        enriched, audit = enrich_whitehouse_annual_tickers(
            snapshot, client.assets(), checked_at="2026-09-20T21:00:00Z")
        recovered = {row["id"]: row for row in enriched["transactions"]}
        self.assertEqual(
            (recovered["wh-annual-tx:kraft"]["ticker"],
             recovered["wh-annual-tx:kraft"]["ticker_mapping_basis"]),
            ("KHC", "alpaca_unique_asset_name"))
        self.assertEqual(recovered["wh-annual-tx:texas"]["ticker"], "TXN")
        self.assertEqual(recovered["wh-annual-tx:synopsys"]["ticker"], "SNPS")
        self.assertEqual(recovered["wh-annual-tx:costco"]["ticker"], "COST")
        self.assertEqual(recovered["wh-annual-tx:capital-one"]["ticker"], "COF")
        self.assertEqual(
            (recovered["wh-annual-tx:zoetis"]["ticker"],
             recovered["wh-annual-tx:zoetis"]["ticker_mapping_basis"]),
            ("ZTS", "alpaca_unique_classless_asset_name"))
        self.assertIsNone(recovered["wh-annual-tx:alphabet"]["ticker"])
        self.assertIsNone(recovered["wh-annual-tx:berkshire"]["ticker"])
        self.assertIsNone(recovered["wh-annual-tx:under-armour"]["ticker"])
        self.assertIsNone(recovered["oge:unmapped-kraft"]["ticker"])
        self.assertEqual(audit["mapping_count"], 6)
        self.assertEqual(audit["new_mapping_count"], 6)
        self.assertEqual(audit["ambiguous_record_count"], 3)
        self.assertEqual(audit["unmatched_record_count"], 0)

        retained, second_audit = enrich_whitehouse_annual_tickers(
            snapshot, list(reversed(client.assets())),
            checked_at="2026-09-21T21:00:00Z", previous=audit)
        self.assertEqual(retained, enriched)
        self.assertEqual(second_audit["retained_mapping_count"], 6)
        self.assertEqual(second_audit["new_mapping_count"], 0)
        self.assertEqual(second_audit["asset_master_sha256"],
                         audit["asset_master_sha256"])

    def test_production_accounts_for_symbol_absent_from_asset_master(self):
        snapshot = production_candidate()
        snapshot["transactions"][1]["ticker"] = "MISSING"
        client = FakeMarketClient(
            {"ZZDEMO": [{"t": "2026-09-18T04:00:00Z", "c": 110}]},
            assets=[asset("ZZDEMO")],
        )
        validation = build_market_validation(
            snapshot, client=client, checked_at="2026-09-20T21:00:00Z",
            distribution_authorized=True)
        self.assertEqual(
            validation.snapshot["meta"]["market_coverage"]["unsupported_tickers"],
            [{"ticker": "MISSING", "reason": "outside_sip_not_listed"}],
        )
        self.assertEqual(client.calls[-1][3], "-")

    def test_historical_sip_bars_recover_symbol_missing_from_current_assets(self):
        snapshot = production_candidate()
        snapshot["transactions"][1].update(
            ticker="OLD", transaction_date="2024-08-01",
            asset_name="Historical Stock (OLD)")
        client = FakeMarketClient({
            "ZZDEMO": [{"t": "2026-09-18T04:00:00Z", "c": 110}],
            "OLD": [{"t": "2024-08-01T04:00:00Z", "c": 25}],
        }, assets=[asset("ZZDEMO")])
        validation = build_market_validation(
            snapshot, client=client, checked_at="2026-09-20T21:00:00Z",
            distribution_authorized=True)
        self.assertEqual(
            {row["ticker"] for row in validation.snapshot["security_market_data"]},
            {"OLD", "ZZDEMO"})
        self.assertEqual(client.calls[-1][3], "-")
        self.assertEqual(validation.snapshot["meta"]["market_coverage"]["unsupported_tickers"], [])

    def test_historical_batch_isolates_rejected_symbol(self):
        snapshot = production_candidate()
        snapshot["transactions"][1].update(
            ticker="OLD", transaction_date="2024-08-01")
        extra = deepcopy(snapshot["transactions"][1])
        extra.update(id="rejected-row", ticker="BAD^", asset_name="Bad Symbol")
        snapshot["transactions"].append(extra)

        class RejectingClient(FakeMarketClient):
            def daily_bars(self, symbols, *, start, end, asof=None):
                if "BAD^" in symbols:
                    raise AlpacaMarketError("Alpaca HTTP 400")
                return super().daily_bars(symbols, start=start, end=end, asof=asof)

        client = RejectingClient({
            "ZZDEMO": [{"t": "2026-09-18T04:00:00Z", "c": 110}],
            "OLD": [{"t": "2024-08-01T04:00:00Z", "c": 25}],
        }, assets=[asset("ZZDEMO")])
        validation = build_market_validation(
            snapshot, client=client, checked_at="2026-09-20T21:00:00Z",
            distribution_authorized=True)
        self.assertEqual(validation.audit["historical_rejected_symbols"], ["BAD^"])
        self.assertEqual(validation.audit["historical_recovered_count"], 1)
        self.assertIn(
            {"ticker": "BAD^", "reason": "outside_sip_not_listed"},
            validation.snapshot["meta"]["market_coverage"]["unsupported_tickers"])

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

        unavailable_snapshot = deepcopy(snapshot)
        unavailable_snapshot["transactions"][1]["ticker"] = "OTHER"
        unavailable = build_market_validation(
            unavailable_snapshot,
            client=FakeMarketClient(
                {"OTHER": [{"t": "2026-09-18T04:00:00Z", "c": 50}]},
                assets=[malformed, asset("OTHER")],
            ),
            checked_at="2026-09-20T21:00:00Z",
            distribution_authorized=True,
        )
        self.assertEqual(
            unavailable.snapshot["meta"]["market_coverage"]["unsupported_tickers"],
            [{"ticker": "ZZDEMO", "reason": "outside_sip_not_listed"}],
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
