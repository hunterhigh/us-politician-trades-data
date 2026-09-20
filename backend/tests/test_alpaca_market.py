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
    def __init__(self, response: dict[str, list[dict]]):
        self.response = response
        self.calls = []

    def daily_bars(self, symbols, *, start, end):
        self.calls.append((symbols, start, end))
        return {symbol: self.response[symbol] for symbol in symbols if symbol in self.response}


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

    def test_builds_partial_candidate_accepted_by_frozen_frontend(self):
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
        })
        validation = build_market_validation(
            snapshot, client=client, checked_at="2026-09-20T21:00:00Z", batch_size=1)
        self.assertEqual(validation.audit["symbol_count"], 2)
        self.assertEqual(validation.audit["market_row_count"], 1)
        self.assertEqual(validation.audit["missing_ticker_count"], 1)
        self.assertEqual(validation.snapshot["security_market_data"][0]["ticker"], "ZZDEMO")
        market_health = next(row for row in validation.snapshot["source_health"]
                             if row["source_id"] == "alpaca_sip_eod")
        self.assertEqual(market_health["status"], "partial")
        processed = load("process_snapshot").build_snapshot(validation.snapshot)
        market = processed["security_market_data"][0]
        self.assertEqual(market["as_of_date"], "2026-09-18")
        self.assertEqual(market["current_price"], 110.0)
        self.assertEqual(market["previous_quarter_end_price"], 100.0)
        self.assertEqual(len(market["price_history"]), 3)

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
