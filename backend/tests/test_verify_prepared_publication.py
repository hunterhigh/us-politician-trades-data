import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location(
    "verify_prepared_publication", ROOT / "scripts/verify_prepared_publication.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


class Delegate:
    def __init__(self):
        self.calls = []

    def get(self, url, limit, *, api=False):
        self.calls.append((url, limit, api))
        return b"raw"


class PreparedPublicationTests(unittest.TestCase):
    def test_fixed_transport_resolves_candidate_without_forwarding_api_credentials(self):
        delegate = Delegate()
        commit = "a" * 40
        transport = script.FixedCommitTransport(commit, delegate=delegate)
        resolved = json.loads(transport.get("https://api.example.invalid", 100, api=True))
        self.assertEqual(resolved, {"object": {"type": "commit", "sha": commit}})
        self.assertEqual(delegate.calls, [])
        self.assertEqual(transport.get("https://raw.example.invalid", 100), b"raw")
        self.assertEqual(delegate.calls, [("https://raw.example.invalid", 100, False)])

    def test_fixed_transport_rejects_non_commit(self):
        with self.assertRaisesRegex(ValueError, "main commit"):
            script.FixedCommitTransport("main")

    def test_trump_person_transactions_and_fixed_market_commit_pass_preflight(self):
        main, market = "a" * 40, "b" * 40
        base = {"people": [{"id": "trump"}],
                "transactions": [{"id": "trump-tx", "ticker": "MSFT"}],
                "reported_holdings": [{"person_id": "trump"}],
                "security_market_data": [{"ticker": "MSFT"}],
                "source_health": [{"source_id": "oge"}]}

        def selection(mode, key=None):
            value = {name: list(rows) for name, rows in base.items()}
            value["meta"] = {"market_commit": market,
                             "selection_scope": {"mode": mode, "key": key}}
            if mode == "person":
                value["transactions"] = [{
                    "id": "trump-tx",
                    "person_id": key, "source_id": "oge",
                    "filing_id": "wh-url:fixed", "verification_status": "official_matched",
                    "source_url": "https://www.whitehouse.gov/wp-content/uploads/2025/08/report.pdf"}]
                value["reported_holdings"] = [{
                    "person_id": key, "source_url": script.TRUMP_2025_SOURCE_URL,
                    "report_period_end": "2025-12-31",
                    "verification_status": "official_matched"}]
            if mode == "ticker":
                value["transactions"] = [{"ticker": key}]
                source = ("twelve_data_split_adjusted_eod" if key == "AAPL" else
                          "alpaca_market_data")
                value["security_market_data"] = [{"ticker": key, "source_id": source}]
            return SimpleNamespace(commit=main, snapshot=value)

        class Repository:
            def __init__(self, *_args, **_kwargs):
                pass

            def fetch(self, mode, key=None):
                return selection(mode, key)

        renderer = SimpleNamespace(
            load_dashboard_data=lambda _path: {},
            render_html=lambda _value: "<html>prepared</html>")
        with tempfile.TemporaryDirectory() as folder, patch.object(
                script, "PublicSnapshotRepository", Repository), patch.object(
                script, "load", return_value=renderer):
            result = script.verify_prepared_publication(
                owner="owner", repo="repo", main_commit=main, market_commit=market,
                person_id="trump", ticker="MSFT", twelve_ticker="AAPL",
                output_dir=Path(folder), expected_people=1,
                expected_transactions=1, expected_holdings=1,
                expected_market_rows=1, expected_source_health=1,
                expected_person_holdings=1, expected_person_transactions=1,
                transport=object())
            self.assertEqual(result["reported_holding_count"], 1)
            self.assertEqual(result["person_transaction_count"], 1)
            self.assertTrue(result["trump_transactions_visible_in_dashboard_search"])
            self.assertTrue((Path(folder) / "dashboard.html").is_file())


if __name__ == "__main__":
    unittest.main()
