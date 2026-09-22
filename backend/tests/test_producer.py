from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.builder import build
from unison_snapshot.codec import bucket, digest, encode
from unison_snapshot.legacy import load
from unison_snapshot.store import GitStore, assemble

FIXTURE = Path(__file__).resolve().parents[1] / "examples/synthetic.json"
NOW = "2026-09-18T00:01:00Z"


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def build(self, data=None, **kw):
        return build(self.data if data is None else data, generated_at=NOW, **kw)

    def test_canonical_utf8_and_nonfinite(self):
        self.assertEqual(encode({"z": 1, "a": "中"}), '{"a":"中","z":1}'.encode())
        with self.assertRaises(ValueError):
            encode(float("nan"))
        self.assertEqual(bucket("tickers", "zzdemo"), bucket("tickers", "ZZDEMO"))
        self.assertNotEqual(bucket("people", "ZZDEMO"), bucket("tickers", "ZZDEMO"))

    def test_reordered_input_is_identical(self):
        expected = self.build()
        for name in ("people", "transactions", "source_health"):
            self.data[name].reverse()
        self.data["transactions"][0]["ticker"] = "zzdemo"
        self.assertEqual(expected.files, self.build().files)

    def test_health_changes_no_content_address(self):
        before = self.build()
        self.data["source_health"][0]["last_checked_at"] = "2026-09-18T00:00:30Z"
        after = self.build()
        self.assertEqual(before.manifest["snapshot_id"], after.manifest["snapshot_id"])
        self.assertNotEqual(before.manifest["status_revision"], after.manifest["status_revision"])
        changed = [path for path in before.files if before.files[path] != after.files[path]]
        self.assertEqual(changed, ["manifest.json"])

    def test_business_cutoff_invalidates_report_not_shards(self):
        before = self.build()
        self.data["meta"]["data_cutoff_at"] = "2026-09-18T00:00:30Z"
        after = self.build()
        self.assertNotEqual(before.manifest["snapshot_id"], after.manifest["snapshot_id"])
        self.assertEqual(before.manifest["board"], after.manifest["board"])

    def test_invalid_inputs_rejected(self):
        changes = [
            lambda d: d.update(meta=None),
            lambda d: d["meta"].update(is_demo=False),
            lambda d: d["transactions"][0].update(amount_low=None),
            lambda d: d["transactions"][0].update(amount_low=True),
            lambda d: d["transactions"][0].update(amount_high=-1),
            lambda d: d["transactions"][0].update(person_id="missing"),
            lambda d: d["transactions"][0].update(filed_at="2026-08-01T00:00:00Z"),
            lambda d: d["transactions"][0].update(filed_at="2027-01-01T00:00:00Z"),
            lambda d: d["transactions"].append(deepcopy(d["transactions"][0])),
            lambda d: d["transactions"][0].update(api_key="secret"),
            lambda d: d["security_market_data"].append({"ticker": "ZZDEMO"}),
            lambda d: d["transactions"][0].update(verification_status="official_matched"),
        ]
        for change in changes:
            with self.subTest(change=change):
                data = deepcopy(self.data)
                change(data)
                with self.assertRaises(ValueError):
                    self.build(data)

    def test_capacity_fails_without_truncation(self):
        with self.assertRaisesRegex(ValueError, "Index size"):
            self.build(max_index_bytes=20)
        with self.assertRaisesRegex(ValueError, "Shard size"):
            self.build(max_blob_bytes=20)

    def test_option_contract_requires_complete_explicit_terms(self):
        data = deepcopy(self.data)
        data["transactions"][0].update(
            instrument_type="Option", option_type="Call", strike_price=150,
            expiration_date="2027-01-15")
        self.build(data)
        del data["transactions"][0]["strike_price"]
        with self.assertRaisesRegex(ValueError, "Option transactions require"):
            self.build(data)

    def test_empty_production_requires_explicit_bootstrap_gate(self):
        data = deepcopy(self.data)
        data["meta"]["is_demo"] = False
        for key in ("people", "transactions", "reported_holdings"):
            data[key] = []
        for row in data["source_health"]:
            row["status"] = "disabled"
        with self.assertRaisesRegex(ValueError, "bootstrap gate"):
            self.build(data, allow_production=True)
        bundle = self.build(data, allow_production=True, allow_empty_production=True)
        self.assertFalse(bundle.manifest["is_demo"])
        self.assertEqual(bundle.manifest["coverage"]["publication_state"], "bootstrap_empty")

    def test_official_white_house_oge_pdf_url_is_admitted(self):
        data = deepcopy(self.data)
        data["meta"]["is_demo"] = False
        hosts = {"house_clerk": "disclosures-clerk.house.gov",
                 "senate_efd": "efdsearch.senate.gov"}
        for row in data["transactions"] + data["reported_holdings"]:
            row["verification_status"] = "official_matched"
            row["source_url"] = f"https://{hosts[row['source_id']]}/{row['filing_id']}"
        for row in data["source_health"]:
            if row["status"] == "simulated":
                row["status"] = "ok"
        row = data["transactions"][0]
        row["source_id"] = "oge"
        row["source_url"] = "https://www.whitehouse.gov/wp-content/uploads/2026/09/example-278t.pdf"
        build(data, generated_at=NOW, allow_production=True)
        for url in ("http://www.whitehouse.gov/example.pdf",
                    "https://www.whitehouse.gov/disclosures/",
                    "https://www.whitehouse.gov.evil.example/example.pdf",
                    "https://www.whitehouse.gov@evil.example/example.pdf"):
            with self.subTest(url=url):
                row["source_url"] = url
                with self.assertRaisesRegex(ValueError, "allowlisted official host"):
                    build(data, generated_at=NOW, allow_production=True)

    def test_licensed_market_requires_commit_and_is_sharded_from_entities(self):
        data = deepcopy(self.data)
        data["meta"]["is_demo"] = False
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
            "price_history": [{"date": "2026-09-17", "close": 100},
                              {"date": "2026-09-18", "close": 101}],
        }]
        data["meta"]["market_coverage"] = {
            "schema_version": "alpaca-market-coverage/v1",
            "source_id": "alpaca_sip_eod",
            "covered_tickers": ["ZZDEMO"],
            "unsupported_tickers": [],
        }
        with self.assertRaisesRegex(ValueError, "frozen market commit"):
            build(data, generated_at=NOW, allow_production=True, allow_market=True)
        bundle = build(data, generated_at=NOW, allow_production=True, allow_market=True,
                       market_commit="1" * 40, market_pages=["2" * 64])
        self.assertEqual(bundle.manifest["market_commit"], "1" * 40)
        self.assertTrue(bundle.manifest["coverage"]["market_enabled"])
        self.assertTrue(bundle.manifest["coverage"]["market_supported_complete"])
        self.assertEqual(bundle.manifest["coverage"]["market_missing_ticker_count"], 0)
        person_index = json.loads(bundle.files[
            f"people/{bucket('people', 'house:DEMO001')}/index.json"])
        person_sha = person_index["shards"]["house:DEMO001"]
        person = json.loads(bundle.files[
            f"people/{bucket('people', 'house:DEMO001')}/{person_sha}.json"])
        self.assertEqual(person["requires"], ["market:ZZDEMO"])
        incomplete = deepcopy(data)
        incomplete["meta"]["market_coverage"]["covered_tickers"] = []
        with self.assertRaisesRegex(ValueError, "exactly match market rows"):
            build(incomplete, generated_at=NOW, allow_production=True, allow_market=True,
                  market_commit="1" * 40, market_pages=["2" * 64])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GitStore(Path(self.temp.name) / "snapshots.git")
        self.store.initialize()

    def build(self):
        return build(self.data, generated_at=NOW)

    def publish(self):
        return self.store.publish(self.build(), expected_head=self.store.head())

    def test_publication_is_pinned_and_recoverable(self):
        first = self.publish()
        self.assertEqual(self.store.head(), first)
        self.assertEqual(self.publish(), first)
        later = build(self.data, generated_at="2026-09-18T01:00:00Z")
        self.assertEqual(self.store.publish(later, expected_head=first), first)
        old = assemble(self.store, first)
        self.data["transactions"][0]["amount_high"] = 16000
        second = self.publish()
        self.assertNotEqual(first, second)
        self.assertEqual(assemble(self.store, first), old)
        self.assertTrue(self.store.paths(first).issubset(self.store.paths(second)))
        self.store.rollback(first, expected_head=second)
        self.assertEqual(self.store.head(), first)
        self.assertEqual(len(assemble(self.store, second)["transactions"]), 2)

    def test_stale_writer_does_not_move_current(self):
        first = self.publish()
        self.data["transactions"][0]["amount_high"] = 16000
        with self.assertRaisesRegex(ValueError, "Concurrent"):
            self.store.publish(self.build(), expected_head=None)
        self.assertEqual(self.store.head(), first)

    def test_invalid_hash_failure_preserves_release(self):
        first = self.publish()
        bundle = self.build()
        bundle.files[f"board/{bundle.manifest['board']}.json"] = b"{}"
        with self.assertRaisesRegex(ValueError, "Content address"):
            self.store.publish(bundle, expected_head=first)
        self.assertEqual(self.store.head(), first)

    def test_removed_entity_index_not_resurrected(self):
        first = self.publish()
        self.data["people"] = self.data["people"][:1]
        self.data["transactions"] = self.data["transactions"][:1]
        second = self.publish()
        with self.assertRaises((RuntimeError, KeyError)):
            assemble(self.store, second, mode="people", key="senate:DEMO002")
        self.assertEqual(len(assemble(self.store, first, mode="people", key="senate:DEMO002")["people"]), 1)

    def test_scope_and_actual_supplied_processor_renderer(self):
        commit = self.publish()
        processor, renderer = load("process_snapshot"), load("render_dashboard")
        for mode, key, people, transactions in [
            ("dashboard", None, 2, 2), ("people", "house:DEMO001", 1, 1), ("tickers", "zzdemo", 2, 2)
        ]:
            with self.subTest(mode=mode):
                assembled = assemble(self.store, commit, mode=mode, key=key)
                processed = processor.build_snapshot(assembled)
                self.assertEqual(len(processed["people"]), people)
                self.assertEqual(len(processed["transactions"]), transactions)
                self.assertEqual(processed["security_market_data"], [])
                input_path = Path(self.temp.name) / "render-input.json"
                input_path.write_bytes(encode(assembled))
                render_data = renderer.load_dashboard_data(input_path)
                self.assertTrue(render_data["people"][0]["portrait_is_placeholder"])
                html = renderer.render_html(render_data)
                self.assertIn("SIMULATED DATA", html)
                self.assertIn("Demo Person One", html)
                self.assertNotIn("__DATA__", html)

    def test_paths_and_refs_rejected(self):
        commit = self.publish()
        for ref, path in [("demo", "manifest.json"), (commit, "../secret"), (commit, "raw/a.json")]:
            with self.assertRaises(ValueError):
                self.store.read(ref, path)

    def test_restore_bundle_in_empty_repository(self):
        commit = self.publish()
        backup = Path(self.temp.name) / "backup.bundle"
        self.store.git("bundle", "create", str(backup), "--all")
        restored = Path(self.temp.name) / "restored.git"
        subprocess.run(["git", "clone", "--bare", str(backup), str(restored)], check=True, capture_output=True)
        self.assertEqual(assemble(GitStore(restored), commit), assemble(self.store, commit))


if __name__ == "__main__":
    unittest.main()
