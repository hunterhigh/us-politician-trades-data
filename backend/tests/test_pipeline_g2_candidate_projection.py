import hashlib
import json
from pathlib import Path
import unittest

from unison_snapshot.pipeline_qa.g2_candidate_projection import build_g2_candidate
from unison_snapshot.pipeline_qa.migration_inventory import InventoryError


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class G2CandidateProjectionTests(unittest.TestCase):
    def fixture(self):
        main, review, market = "a" * 40, "b" * 40, "c" * 40
        person = {"id": "person", "display_name": "Example"}
        tx = {"id": "tx", "ticker": None, "ticker_mapping_basis": None}
        enriched = {**tx, "ticker": "EX", "ticker_mapping_basis": "legacy_market_mapping"}
        holding = {"id": "holding", "ticker": None}
        row = {"ticker": "EX", "price_history": [{"date": "2026-01-02", "close": 10}]}
        source = {"meta": {"is_demo": False}, "people": [person], "transactions": [tx],
                  "reported_holdings": [holding]}
        board = {"people": [person], "transactions": [enriched],
                 "reported_holdings": [holding]}
        page = encoded({"security_market_data": [row]})
        page_sha = hashlib.sha256(page).hexdigest()
        manifest = {"market_commit": market, "board": hashlib.sha256(encoded(board)).hexdigest(),
                    "market_pages": [page_sha], "source_health": [{"source_id": "house_clerk"}]}
        blobs = {(main, "manifest.json"): encoded(manifest),
                 (main, f"board/{manifest['board']}.json"): encoded(board),
                 (review, "candidates/disclosure-current.json"): encoded(source),
                 (market, f"market-pages/{page_sha}.json"): page}
        fixed = {"main_commit": main, "review_commit": review, "market_commit": market,
                 "manifest_sha256": hashlib.sha256(blobs[(main, "manifest.json")]).hexdigest(),
                 "board_sha256": manifest["board"],
                 "unified_candidate_sha256": hashlib.sha256(blobs[(review, "candidates/disclosure-current.json")]).hexdigest()}
        inventory = {"schema_version": "pipeline-migration-inventory/v1", "fixed_inputs": fixed,
                     "market": {"verification_status": "content_verified", "page_count": 1,
                                "ticker_count": 1, "records": [{"ticker": "EX"}]},
                     "source_health": {"records": manifest["source_health"]},
                     "records": {"people": [{"id": "person"}], "transactions": [{"id": "tx"}],
                                 "reported_holdings": [{"id": "holding"}]}}
        gate = {"schema_version": "pipeline-g1-legacy-gate/v1",
                "fixed_inputs": {**fixed, "old_main_commit": "d" * 40},
                "g1_legacy_candidate_binding_complete": True, "market_page_bytes_verified": True}
        def read(_repo, commit, path):
            return blobs[(commit, path)]
        return inventory, gate, read, blobs, page_sha

    def test_reconstructs_all_five_arrays_and_only_known_enrichment(self):
        inventory, gate, read, _, _ = self.fixture()
        candidate, report = build_g2_candidate(repo=Path("."), inventory=inventory,
                                               gate=gate, read_object=read)
        self.assertEqual(candidate["transactions"][0]["ticker"], "EX")
        self.assertEqual(report["counts"], {"people": 1, "transactions": 1,
                         "reported_holdings": 1, "security_market_data": 1, "source_health": 1})
        self.assertEqual(report["legacy_market_enrichment"]["transactions"], 1)
        self.assertFalse(report["published"])

    def test_rejects_changed_market_page_or_incomplete_gate(self):
        inventory, gate, read, blobs, page_sha = self.fixture()
        gate["g1_legacy_candidate_binding_complete"] = False
        with self.assertRaises(InventoryError):
            build_g2_candidate(repo=Path("."), inventory=inventory, gate=gate, read_object=read)
        gate["g1_legacy_candidate_binding_complete"] = True
        blobs[(inventory["fixed_inputs"]["market_commit"], f"market-pages/{page_sha}.json")] = b"{}"
        with self.assertRaises(InventoryError):
            build_g2_candidate(repo=Path("."), inventory=inventory, gate=gate, read_object=read)


if __name__ == "__main__":
    unittest.main()
