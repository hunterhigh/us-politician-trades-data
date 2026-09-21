import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.builder import build
from unison_snapshot.codec import bucket, digest, encode
from unison_snapshot.materialize import materialize
from unison_snapshot.legacy import load
from unison_snapshot.market_store import build_market_bundle
from unison_snapshot.public_repo import HTTPTransport, PublicSnapshotError, PublicSnapshotRepository

FIXTURE = Path(__file__).resolve().parents[1] / "examples/synthetic.json"
COMMIT = "1" * 40
MARKET_COMMIT = "2" * 40


class FakeTransport:
    def __init__(self, files, commit=COMMIT, extra_commits=None):
        self.files = files
        self.commit = commit
        self.files_by_commit = {commit: files, **(extra_commits or {})}
        self.calls = []

    def get(self, url, limit, *, api=False):
        self.calls.append((url, limit, api))
        if api:
            reference = {"type": "commit", "sha": self.commit}
            value = encode({"object": reference})
        else:
            matched = next(((sha, files) for sha, files in self.files_by_commit.items()
                            if f"/{sha}/" in url), None)
            if matched is None:
                raise AssertionError(url)
            sha, files = matched
            value = files[url.split(f"/{sha}/", 1)[1]]
        if len(value) > limit:
            raise PublicSnapshotError("Repository object exceeds the contract size limit")
        return value


def production_payload():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["meta"]["is_demo"] = False
    hosts = {"house_clerk": "disclosures-clerk.house.gov", "senate_efd": "efdsearch.senate.gov"}
    for row in data["transactions"] + data["reported_holdings"]:
        row["verification_status"] = "official_matched"
        row["source_url"] = f"https://{hosts[row['source_id']]}/test-contract/{row['filing_id']}"
    for row in data["source_health"]:
        row["status"] = "disabled" if row["source_id"] == "alpaca_sip_eod" else "ok"
    return data


def production_files():
    return build(production_payload(), generated_at="2026-09-18T00:01:00Z",
                 allow_production=True).files


class PublicRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.files = production_files()
        self.transport = FakeTransport(self.files)
        self.repo = PublicSnapshotRepository("example", "data", transport=self.transport)

    def test_dashboard_pins_commit_and_uses_two_payload_reads(self):
        selection = self.repo.fetch("dashboard")
        self.assertEqual(selection.commit, COMMIT)
        self.assertEqual(len(selection.snapshot["people"]), 2)
        self.assertFalse(selection.snapshot["meta"]["is_demo"])
        self.assertEqual([call[2] for call in self.transport.calls], [True, False, False])
        self.assertTrue(all(f"/{COMMIT}/" in call[0] for call in self.transport.calls[1:]))
        processed = load("process_snapshot").build_snapshot(selection.snapshot)
        self.assertEqual(processed["meta"]["snapshot_commit"], COMMIT)

    def test_scoped_person_and_ticker(self):
        person = self.repo.fetch("person", "house:DEMO001").snapshot
        self.assertEqual([row["id"] for row in person["people"]], ["house:DEMO001"])
        self.assertEqual(len(person["transactions"]), 1)
        ticker = self.repo.fetch("ticker", "zzdemo").snapshot
        self.assertEqual(len(ticker["people"]), 2)
        self.assertEqual(len(ticker["transactions"]), 2)
        self.assertEqual(ticker["meta"]["selection_scope"]["key"], "ZZDEMO")

    def test_licensed_market_commit_is_loaded_for_scoped_views(self):
        data = production_payload()
        market = {
            "ticker": "ZZDEMO", "company_name": "Fictional Company Alpha",
            "source_id": "alpaca_sip_eod", "price_source": "Alpaca SIP EOD",
            "source_url": "https://docs.alpaca.markets/docs/market-data",
            "feed": "sip", "timeframe": "1Day", "adjustment": "split",
            "price_history": [{"date": "2026-09-17", "close": 100},
                              {"date": "2026-09-18", "close": 101}],
        }
        data["security_market_data"] = [market]
        data["meta"]["market_coverage"] = {
            "schema_version": "alpaca-market-coverage/v1",
            "source_id": "alpaca_sip_eod",
            "covered_tickers": ["ZZDEMO"],
            "unsupported_tickers": [],
        }
        data["source_health"][-1].update(status="ok", source="Alpaca SIP EOD")
        market_bundle = build_market_bundle(
            [market], data_cutoff_at=data["meta"]["data_cutoff_at"])
        main_files = build(
            data, generated_at="2026-09-18T00:01:00Z", allow_production=True,
            allow_market=True, market_commit=MARKET_COMMIT,
            market_pages=market_bundle.page_shas).files
        market_files = market_bundle.files
        repo = PublicSnapshotRepository(
            "example", "data",
            transport=FakeTransport(main_files, extra_commits={MARKET_COMMIT: market_files}))
        dashboard = repo.fetch("dashboard").snapshot
        self.assertEqual(dashboard["security_market_data"][0]["ticker"], "ZZDEMO")
        ticker = repo.fetch("ticker", "ZZDEMO").snapshot
        self.assertEqual(ticker["security_market_data"][0]["ticker"], "ZZDEMO")

    def test_person_name_resolves_within_the_frozen_board(self):
        person = self.repo.fetch("person", "  demo   person ONE ").snapshot
        self.assertEqual([row["id"] for row in person["people"]], ["house:DEMO001"])
        self.assertEqual(person["meta"]["selection_scope"]["key"], "house:DEMO001")
        self.assertTrue(all(f"/{COMMIT}/" in call[0] for call in self.transport.calls[1:]))
        with self.assertRaisesRegex(PublicSnapshotError, "not found"):
            self.repo.fetch("person", "No Such Person")

    def test_hash_demo_and_reference_fail_closed(self):
        board = json.loads(self.files["manifest.json"])["board"]
        corrupt = dict(self.files)
        corrupt[f"board/{board}.json"] = b"{}"
        with self.assertRaisesRegex(PublicSnapshotError, "hash"):
            PublicSnapshotRepository("example", "data", transport=FakeTransport(corrupt)).fetch()
        demo = dict(self.files)
        manifest = json.loads(demo["manifest.json"])
        manifest["is_demo"] = True
        demo["manifest.json"] = encode(manifest)
        with self.assertRaisesRegex(PublicSnapshotError, "Demo"):
            PublicSnapshotRepository("example", "data", transport=FakeTransport(demo)).fetch()
        missing = dict(self.files)
        person_path = f"people/{bucket('people', 'house:DEMO001')}/index.json"
        index = json.loads(missing[person_path])
        index["shards"].clear()
        missing[person_path] = encode(index)
        with self.assertRaises(PublicSnapshotError):
            PublicSnapshotRepository("example", "data", transport=FakeTransport(missing)).fetch("person", "house:DEMO001")

    def test_bad_manifest_time_and_missing_person_fail_closed(self):
        bad = dict(self.files)
        manifest = json.loads(bad["manifest.json"])
        manifest["generated_at"] = "2026-02-30T00:00:00Z"
        bad["manifest.json"] = encode(manifest)
        with self.assertRaisesRegex(PublicSnapshotError, "timestamps"):
            PublicSnapshotRepository("example", "data", transport=FakeTransport(bad)).fetch()
        bad = dict(self.files)
        manifest = json.loads(bad["manifest.json"])
        board_path = f"board/{manifest['board']}.json"
        board = json.loads(bad[board_path])
        board["people"] = board["people"][:1]
        content = encode(board)
        new_sha = digest(content)
        manifest["board"] = new_sha
        bad[f"board/{new_sha}.json"] = content
        bad["manifest.json"] = encode(manifest)
        with self.assertRaisesRegex(PublicSnapshotError, "missing person"):
            PublicSnapshotRepository("example", "data", transport=FakeTransport(bad)).fetch()

    def test_identity_and_path_inputs_are_fixed(self):
        for owner, repo in [("x/y", "data"), ("example", "https://evil.test"), ("", "data")]:
            with self.subTest(owner=owner, repo=repo), self.assertRaises(ValueError):
                PublicSnapshotRepository(owner, repo)

    def test_http_transport_retries_transient_failure(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b"{}"

        class FlakyOpener:
            def __init__(self):
                self.calls = 0

            def open(self, _request, *, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise urllib.error.URLError("temporary")
                return Response()

        transport = HTTPTransport(retries=1, backoff=0)
        transport.opener = FlakyOpener()
        self.assertEqual(transport.get("https://example.test/data", 10), b"{}")
        self.assertEqual(transport.opener.calls, 2)


class MaterializeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def bundle(self, generated="2026-09-18T00:01:00Z"):
        return build(self.data, generated_at=generated)

    def test_manifest_written_last_and_noop_is_clean(self):
        first = materialize(self.root, self.bundle())
        self.assertTrue(first.changed)
        self.assertTrue(first.business_changed)
        second = materialize(self.root, self.bundle())
        self.assertFalse(second.changed)
        later = materialize(self.root, self.bundle("2026-09-18T01:00:00Z"))
        self.assertFalse(later.changed)
        self.assertFalse(later.business_changed)

    def test_old_blobs_remain_and_stale_index_is_removed(self):
        original = self.bundle()
        materialize(self.root, original)
        old_blobs = {path for path in original.files if len(Path(path).stem) == 64}
        self.data["people"] = self.data["people"][:1]
        self.data["transactions"] = self.data["transactions"][:1]
        result = materialize(self.root, self.bundle())
        self.assertTrue(result.removed)
        self.assertTrue(all((self.root / path).is_file() for path in old_blobs))

    def test_immutable_collision_and_invalid_manifest_abort(self):
        bundle = self.bundle()
        materialize(self.root, bundle)
        blob = next(path for path in bundle.files if len(Path(path).stem) == 64)
        (self.root / blob).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "Immutable"):
            materialize(self.root, bundle)
        (self.root / "manifest.json").write_text("not json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manifest"):
            materialize(self.root, bundle)


if __name__ == "__main__":
    unittest.main()
