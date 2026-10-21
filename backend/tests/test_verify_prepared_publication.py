import importlib.util
import json
import subprocess
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
    def test_index_reuse_is_bound_to_published_git_blob_not_worktree_contents(self):
        from unison_snapshot.codec import encode
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            main, market = root / "main", root / "market"
            main.mkdir(); (market / "market/aa").mkdir(parents=True)
            path = market / "market/aa/index.json"
            raw = encode({"shards": {"MSFT": "d" * 64}})
            path.write_bytes(raw)

            def git(*args):
                return subprocess.check_output(["git", "-C", str(market), *args], stderr=subprocess.PIPE).decode().strip()

            git("init")
            git("-c", "core.autocrlf=false", "add", ".")
            git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
            commit = git("rev-parse", "HEAD")
            (main / "manifest.json").write_bytes(encode({
                "is_demo": False, "market_commit": commit, "market_pages": []}))
            delegate = Delegate()
            cache = script.PublishedMarketTransport(owner="owner", repo="repo", main_root=main,
                market_root=market, market_commit=commit, delegate=delegate)
            url = f"https://raw.githubusercontent.com/owner/repo/{commit}/market/aa/index.json"
            self.assertEqual(cache.get(url, 1000), raw)
            self.assertEqual(delegate.calls, [])
            self.assertEqual(cache.metrics["reused_by_kind"]["market/index"]["objects"], 1)
            # A new commit and another repository always read remotely.
            self.assertEqual(cache.get(url.replace(commit, "f" * 40), 1000), b"raw")
            self.assertEqual(cache.get(url.replace("owner/repo", "other/repo"), 1000), b"raw")
            # Even valid JSON at the expected path cannot substitute different bytes.
            path.write_bytes(encode({"shards": {"MSFT": "e" * 64}}))
            self.assertEqual(cache.get(url, 1000), b"raw")
            path.unlink()
            self.assertEqual(cache.get(url, 1000), b"raw")
            path.write_bytes(raw)
            self.assertEqual(cache.get(url, len(raw) - 1), b"raw")
            self.assertEqual(cache.get(url.replace("/aa/", "/bb/"), 1000), b"raw")
            self.assertEqual(cache.metrics["cache_misses"], 3)
            self.assertEqual(cache.metrics["remote_requests"], 6)
            self.assertEqual(cache.metrics["remote_by_kind"]["market/index"]["objects"], 6)

    def test_published_cache_reuses_only_fixed_market_objects_and_falls_back_on_damage(self):
        from unison_snapshot.codec import encode, digest
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            main, market = root / "main", root / "market"
            main.mkdir(); (market / "market-pages").mkdir(parents=True)
            raw = encode({"security_market_data": [{"ticker": "MSFT"}]})
            sha = digest(raw)
            path = market / "market-pages" / f"{sha}.json"
            path.write_bytes(raw)
            (main / "manifest.json").write_bytes(encode({
                "is_demo": False, "market_commit": "b" * 40, "market_pages": [sha]}))
            delegate = Delegate()
            cache = script.PublishedMarketTransport(owner="owner", repo="repo", main_root=main,
                market_root=market, market_commit="b" * 40, delegate=delegate)
            prefix = "https://raw.githubusercontent.com/owner/repo/"
            url = prefix + "b" * 40 + f"/market-pages/{sha}.json"
            self.assertEqual(cache.get(url, 1000), raw)
            self.assertEqual(delegate.calls, [])
            # Unchanged content at a NEW commit must still be available remotely.
            for address in (url.replace("b" * 40, "c" * 40),
                            prefix + "b" * 40 + "/market/aa/index.json",
                            prefix + "a" * 40 + f"/board/{sha}.json",
                            url.replace("owner/repo", "other/repo")):
                self.assertEqual(cache.get(address, 1000), b"raw")
            path.write_bytes(raw + b" ")
            self.assertEqual(cache.get(url, 1000), b"raw")
            path.unlink()
            self.assertEqual(cache.get(url, 1000), b"raw")
            self.assertEqual(cache.metrics["reused_objects"], 1)
            self.assertEqual(cache.metrics["cache_misses"], 2)
            self.assertEqual(cache.metrics["remote_requests"], 6)
            with self.assertRaisesRegex(ValueError, "published main/market pair"):
                script.PublishedMarketTransport(owner="owner", repo="repo", main_root=main,
                    market_root=market, market_commit="c" * 40)

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

    def _run_preflight(self, person_transactions, *, omitted_from=None, calls=None,
                       processing_status=None, require_status=False, expected_review=None):
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

            def disclosure_status(self, commit, *, include_records=False):
                if commit != main or not include_records:
                    raise AssertionError("Status must use the prepared commit and verify all records")
                return processing_status

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
                require_disclosure_status=require_status,
                expected_review_commit=expected_review,
                transport=object())
            self.assertTrue((Path(folder) / "dashboard.html").is_file())
            self.assertTrue((Path(folder) / "dashboard.json").is_file())
            for unused in ("person", "search", "ticker", "twelve"):
                self.assertFalse((Path(folder) / f"{unused}.json").exists())
            return result

    def test_trump_person_transactions_and_fixed_market_commit_pass_preflight(self):
        result = self._run_preflight([self._whitehouse_transaction()])
        self.assertEqual(result["reported_holding_count"], 1)
        self.assertEqual(result["person_transaction_count"], 1)
        self.assertTrue(result["trump_transactions_visible_in_dashboard_search"])

    def test_required_processing_status_cannot_be_missing_or_from_another_review(self):
        rows = [self._whitehouse_transaction()]
        with self.assertRaisesRegex(ValueError, "missing disclosure"):
            self._run_preflight(rows, require_status=True)
        status = {"provenance": {"review_commit": "1" * 40}, "sources": [
            {"counts": {"publication_transaction_count": 1,
                        "unmatched_publication_transaction_count": 0}}]}
        with self.assertRaisesRegex(ValueError, "unexpected review_commit"):
            self._run_preflight(rows, processing_status=status, expected_review="2" * 40)
        result = self._run_preflight(rows, require_status=True,
                                     processing_status=status, expected_review="1" * 40)
        self.assertTrue(result["disclosure_status_verified"])

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
