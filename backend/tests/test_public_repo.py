import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.builder import build
from unison_snapshot.codec import bucket, digest, encode
from unison_snapshot.materialize import materialize
from unison_snapshot.legacy import load
from unison_snapshot.public_repo import PublicSnapshotError, PublicSnapshotRepository

FIXTURE = Path(__file__).resolve().parents[1] / "examples/synthetic.json"
COMMIT = "1" * 40


class FakeTransport:
    def __init__(self, files, commit=COMMIT):
        self.files = files
        self.commit = commit
        self.calls = []

    def get(self, url, limit, *, api=False):
        self.calls.append((url, limit, api))
        if api:
            reference = {"type": "commit", "sha": self.commit}
            value = encode({"object": reference})
        else:
            marker = f"/{self.commit}/"
            if marker not in url:
                raise AssertionError(url)
            value = self.files[url.split(marker, 1)[1]]
        if len(value) > limit:
            raise PublicSnapshotError("Repository object exceeds the contract size limit")
        return value


def production_files():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["meta"]["is_demo"] = False
    hosts = {"house_clerk": "disclosures-clerk.house.gov", "senate_efd": "efdsearch.senate.gov"}
    for row in data["transactions"] + data["reported_holdings"]:
        row["verification_status"] = "official_matched"
        row["source_url"] = f"https://{hosts[row['source_id']]}/test-contract/{row['filing_id']}"
    for row in data["source_health"]:
        row["status"] = "disabled" if row["source_id"] == "alpaca_sip_eod" else "ok"
    return build(data, generated_at="2026-09-18T00:01:00Z", allow_production=True).files


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
