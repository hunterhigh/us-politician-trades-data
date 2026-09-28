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
    @staticmethod
    def _whitehouse_transaction():
        return {
            "id": "trump-wh-tx", "person_id": "trump", "source_id": "oge",
            "filing_id": "wh-url:fixed", "verification_status": "official_matched",
            "source_url": "https://www.whitehouse.gov/wp-content/uploads/2025/08/report.pdf",
        }

    @staticmethod
    def _oge_transaction():
        return {
            "id": "oge-278t:fixed", "person_id": "trump", "source_id": "oge",
            "filing_id": script.TRUMP_SEPT_2026_DOCUMENT_ID,
            "verification_status": "official_matched",
            "source_url": script.TRUMP_SEPT_2026_SOURCE_URL,
        }

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

    def _run_preflight(self, person_transactions, *, omitted_from=None, calls=None):
        main, market = "a" * 40, "b" * 40
        base = {"people": [{"id": "trump"}],
                "transactions": [{"id": row["id"], "ticker": "MSFT"}
                                 for row in person_transactions],
                "reported_holdings": [{"person_id": "trump"}],
                "security_market_data": [{"ticker": "MSFT"}],
                "source_health": [{"source_id": "oge"}]}

        def selection(mode, key=None):
            value = {name: list(rows) for name, rows in base.items()}
            value["meta"] = {"market_commit": market,
                             "selection_scope": {"mode": mode, "key": key}}
            if mode == "person":
                value["transactions"] = person_transactions
                value["reported_holdings"] = [{
                    "person_id": key, "source_url": script.TRUMP_2025_SOURCE_URL,
                    "report_period_end": "2025-12-31",
                    "verification_status": "official_matched"}]
            if mode == omitted_from:
                value["transactions"] = value["transactions"][:-1]
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
                if calls is not None:
                    calls.append(mode)
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
                expected_transactions=len(base["transactions"]) -
                (omitted_from == "dashboard"), expected_holdings=1,
                expected_market_rows=1, expected_source_health=1,
                expected_person_holdings=1,
                expected_person_transactions=len(person_transactions),
                transport=object())
            self.assertTrue((Path(folder) / "dashboard.html").is_file())
            return result

    def test_trump_person_transactions_and_fixed_market_commit_pass_preflight(self):
        result = self._run_preflight([self._whitehouse_transaction()])
        self.assertEqual(result["reported_holding_count"], 1)
        self.assertEqual(result["person_transaction_count"], 1)
        self.assertTrue(result["trump_transactions_visible_in_dashboard_search"])

    def test_whitehouse_and_fixed_oge_transactions_pass_preflight(self):
        calls = []
        result = self._run_preflight(
            [self._whitehouse_transaction(), self._oge_transaction()], calls=calls)
        self.assertEqual(result["person_transaction_count"], 2)
        self.assertEqual(calls[0], "person")
        self.assertTrue(result["trump_transactions_visible_in_dashboard_search"])

    def test_invalid_oge_provenance_fails_before_large_readback(self):
        for changed in (
                {"filing_id": "other-document"},
                {"source_url": "https://example.org/report.pdf"},
                {"source_url": script.TRUMP_SEPT_2026_SOURCE_URL + "?copy=1"},
                {"id": "other-row"},
                {"person_id": "other-person"},
                {"source_id": "house"},
                {"verification_status": "unverified"}):
            with self.subTest(changed=changed):
                oge = {**self._oge_transaction(), **changed}
                calls = []
                with self.assertRaisesRegex(ValueError, "Trump transaction provenance"):
                    self._run_preflight(
                        [self._whitehouse_transaction(), oge], calls=calls)
                self.assertEqual(calls, ["person"])

    def test_whitehouse_filing_requires_official_pdf_url(self):
        for url in ("https://example.org/report.pdf",
                    "https://www.whitehouse.gov/wp-content/uploads/report.pdf?copy=1"):
            with self.subTest(url=url):
                row = {**self._whitehouse_transaction(), "source_url": url}
                with self.assertRaisesRegex(ValueError, "Trump transaction provenance"):
                    self._run_preflight([row, self._oge_transaction()])

    def test_fixed_oge_without_whitehouse_transaction_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Trump transaction provenance"):
            self._run_preflight([self._oge_transaction()])

    def test_dashboard_and_search_must_contain_every_trump_transaction(self):
        transactions = [self._whitehouse_transaction(), self._oge_transaction()]
        for mode in ("dashboard", "search"):
            with self.subTest(mode=mode), self.assertRaisesRegex(
                    ValueError, "dashboard/search omit Trump transactions"):
                self._run_preflight(transactions, omitted_from=mode)


if __name__ == "__main__":
    unittest.main()
