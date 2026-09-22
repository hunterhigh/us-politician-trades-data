from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError

from unison_snapshot.builder import build
from unison_snapshot.legacy import PROCESSOR_V2_SHA256, load
from unison_snapshot.twelve_data_market import (TwelveDataClient, TwelveDataError,
                                                 TwelveDataTransient, supplement)


class Response:
    def __init__(self, value):
        self.stream = io.BytesIO(json.dumps(value).encode())

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.stream.read()


def candidate():
    fixture = Path(__file__).resolve().parents[1] / "examples/synthetic.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    data["meta"].update(is_demo=False, snapshot_id="test-source-candidate")
    data["transactions"][1]["ticker"] = "FUNDX"
    data["transactions"][1]["asset_name"] = "Example Fund"
    hosts = {"house_clerk": "disclosures-clerk.house.gov",
             "senate_efd": "efdsearch.senate.gov"}
    for row in data["transactions"] + data["reported_holdings"]:
        row["verification_status"] = "official_matched"
        row["source_url"] = f"https://{hosts[row['source_id']]}/filing/{row['filing_id']}"
    for row in data["source_health"]:
        row["status"] = "ok"
    data["security_market_data"] = [{
        "ticker": "ZZDEMO", "company_name": "Fictional Company Alpha",
        "source_id": "alpaca_sip_eod", "price_source": "Alpaca SIP EOD",
        "source_url": "https://docs.alpaca.markets/docs/market-data",
        "feed": "sip", "timeframe": "1Day", "adjustment": "split",
        "price_history": [{"date": "2026-09-16", "close": 100},
                          {"date": "2026-09-17", "close": 101}],
    }]
    data["meta"]["market_coverage"] = {
        "schema_version": "alpaca-market-coverage/v1", "source_id": "alpaca_sip_eod",
        "covered_tickers": ["ZZDEMO"],
        "unsupported_tickers": [{"ticker": "FUNDX", "reason": "outside_sip_fund"}],
    }
    return data


class TwelveDataMarketTests(unittest.TestCase):
    def test_mixed_candidate_and_price_provenance(self):
        urls = []

        def opener(request, timeout):
            urls.append(request.full_url)
            if "symbol_search" in request.full_url:
                return Response({"status": "ok", "data": [{
                    "symbol": "FUNDX", "instrument_name": "Example Fund Class A",
                    "country": "United States", "currency": "USD",
                }]})
            return Response({"status": "ok", "meta": {"symbol": "FUNDX",
                             "interval": "1day"}, "values": [
                {"datetime": "2026-09-16", "close": "12.5"},
                {"datetime": "2026-09-17", "close": "12.6"},
            ]})

        client = TwelveDataClient("secret-for-test", opener=opener, pace_seconds=0)
        data, audit = supplement(candidate(), client=client,
                                 checked_at="2026-09-19T00:01:00Z")
        self.assertEqual(audit["accepted_count"], 1)
        self.assertEqual(audit["request_count"], 2)
        self.assertIn("adjust=splits", urls[1])
        self.assertNotIn("secret-for-test", json.dumps(audit))
        bundle = build(data, generated_at="2026-09-19T00:01:00Z",
                       allow_production=True, allow_market=True,
                       market_commit="1" * 40, market_pages=["2" * 64])
        self.assertEqual(bundle.manifest["processor_sha256"], PROCESSOR_V2_SHA256)
        processed = load("process_snapshot", version="v2").build_snapshot(data)
        self.assertEqual({row["ticker"]: row["price_source_id"]
                          for row in processed["transactions"]}, {
            "ZZDEMO": "alpaca_sip_eod", "FUNDX": "twelve_data_split_adjusted_eod"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            renderer = load("render_dashboard", version="v2")
            html = renderer.render_html(renderer.load_dashboard_data(path))
            self.assertIn("Twelve Data split-adjusted EOD", html)

    def test_name_mismatch_keeps_missing(self):
        def opener(request, timeout):
            return Response({"status": "ok", "data": [{
                "symbol": "FUNDX", "instrument_name": "Unrelated Bank",
                "country": "United States"}]})

        data, audit = supplement(candidate(), client=TwelveDataClient("test", opener=opener,
                                                                      pace_seconds=0),
                                 checked_at="2026-09-19T00:01:00Z")
        self.assertEqual(audit["accepted_count"], 0)
        self.assertEqual(data["meta"]["market_coverage"]["source_id"], "alpaca_sip_eod")

    def test_rate_limit_fails_publication_and_never_exposes_key(self):
        def opener(request, timeout):
            raise HTTPError(request.full_url, 429, "rate limit", {}, None)

        client = TwelveDataClient("sensitive-token", opener=opener, retries=0,
                                  pace_seconds=0)
        with self.assertRaises(TwelveDataTransient) as caught:
            client.search("FUNDX")
        self.assertNotIn("sensitive-token", str(caught.exception))

    def test_demo_key_rejected(self):
        with self.assertRaises(TwelveDataError):
            TwelveDataClient("demo")


if __name__ == "__main__":
    unittest.main()
