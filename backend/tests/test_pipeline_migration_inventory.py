import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from unison_snapshot.pipeline_qa.migration_inventory import build_inventory, InventoryError


MAIN = "a" * 40
REVIEW = "b" * 40
MARKET = "c" * 40


class MigrationInventoryTests(unittest.TestCase):
    def _fixture(self, *, candidate_ticker=None, source_ticker=None, missing_source=False):
        transaction = {"id": "t1", "source_id": "oge", "filing_id": "f1",
                       "person_id": "p1", "source_url": "https://example.org/f1",
                       "ticker": "ABC", "ticker_mapping_basis": "market_rule",
                       "verification_status": "official_matched"}
        candidate = {**transaction, "ticker": candidate_ticker,
                     "ticker_mapping_basis": None if candidate_ticker is None else "market_rule"}
        source = {**candidate, "ticker": source_ticker,
                  "ticker_mapping_basis": None if source_ticker is None else "market_rule"}
        board = {"people": [], "transactions": [transaction],
                 "reported_holdings": [], "security_market_data": []}
        board_raw = json.dumps(board).encode()
        board_sha = hashlib.sha256(board_raw).hexdigest()
        manifest = {"is_demo": False, "board": board_sha, "market_commit": MARKET,
                    "source_health": [], "market_pages": [],
                    "coverage": {"market_ticker_count": 0}}
        unified = {"meta": {"is_demo": False}, "people": [],
                   "transactions": [candidate], "reported_holdings": []}
        objects = {
            (MAIN, "manifest.json"): json.dumps(manifest).encode(),
            (MAIN, f"board/{board_sha}.json"): board_raw,
            (REVIEW, "candidates/disclosure-current.json"): json.dumps(unified).encode(),
        }
        for source_id in ("house_clerk", "oge", "senate_efd"):
            source_rows = [] if source_id != "oge" or missing_source else [source]
            payload = {"meta": {"is_demo": False}, "people": [],
                       "transactions": source_rows, "reported_holdings": []}
            objects[(REVIEW, f"candidates/sources/{source_id}-current.json")] = json.dumps(payload).encode()
        return objects

    def _run(self, objects, *, verify_market_pages=False):
        with TemporaryDirectory() as directory:
            html = Path(directory) / "baseline.html"
            html.write_bytes(b"latest html")
            with patch("unison_snapshot.pipeline_qa.migration_inventory.HTML_SHA256",
                       hashlib.sha256(html.read_bytes()).hexdigest()):
                return build_inventory(
                    repo=Path(directory), main_commit=MAIN, review_commit=REVIEW,
                    market_commit=MARKET, html_path=html,
                    read_object=lambda _repo, commit, path: objects[(commit, path)],
                    verify_market_pages=verify_market_pages,
                )

    def test_market_enrichment_is_recorded_without_claiming_projection(self):
        result = self._run(self._fixture())
        row = result["records"]["transactions"][0]
        self.assertEqual(row["disposition"], "legacy_qualified_binding")
        self.assertEqual(row["unified_difference_fields"], ["ticker", "ticker_mapping_basis"])
        self.assertEqual(row["canonical_record"]["ticker"], "ABC")
        self.assertTrue(result["production_candidate_id_coverage_complete"])
        self.assertFalse(result["projection_ready"])

    def test_missing_source_candidate_blocks_record(self):
        result = self._run(self._fixture(missing_source=True))
        self.assertEqual(result["records"]["transactions"][0]["issues"],
                         ["missing_source_candidate"])
        self.assertFalse(result["production_candidate_id_coverage_complete"])

    def test_unexplained_field_change_blocks_record(self):
        objects = self._fixture(candidate_ticker="ABC", source_ticker="ABC")
        unified = json.loads(objects[(REVIEW, "candidates/disclosure-current.json")])
        unified["transactions"][0]["filing_id"] = "other"
        objects[(REVIEW, "candidates/disclosure-current.json")] = json.dumps(unified).encode()
        result = self._run(objects)
        self.assertIn("unexplained_unified_difference", result["records"]["transactions"][0]["issues"])

    def test_board_hash_and_html_hash_are_enforced(self):
        objects = self._fixture()
        board_key = next(key for key in objects if key[1].startswith("board/"))
        objects[board_key] += b" "
        with self.assertRaisesRegex(InventoryError, "board content hash mismatch"):
            self._run(objects)
        with TemporaryDirectory() as directory:
            html = Path(directory) / "other.html"
            html.write_bytes(b"different")
            with self.assertRaisesRegex(InventoryError, "HTML baseline hash changed"):
                build_inventory(repo=Path(directory), main_commit=MAIN,
                                review_commit=REVIEW, market_commit=MARKET,
                                html_path=html, read_object=lambda *_: b"{}")

    def test_market_page_is_pinned_and_counted(self):
        objects = self._fixture()
        row = {"ticker": "ABC", "source_id": "alpaca_sip_eod",
               "price_history": [{"date": "2026-01-01", "close": 1}]}
        raw = json.dumps({"security_market_data": [row]}).encode()
        digest = hashlib.sha256(raw).hexdigest()
        objects[(MARKET, f"market-pages/{digest}.json")] = raw
        manifest = json.loads(objects[(MAIN, "manifest.json")])
        manifest["market_pages"] = [digest]
        manifest["coverage"]["market_ticker_count"] = 1
        objects[(MAIN, "manifest.json")] = json.dumps(manifest).encode()
        result = self._run(objects, verify_market_pages=True)
        self.assertEqual(result["market"]["records"][0]["first_price_date"], "2026-01-01")
        objects[(MARKET, f"market-pages/{digest}.json")] += b" "
        with self.assertRaisesRegex(InventoryError, "market page content hash mismatch"):
            self._run(objects, verify_market_pages=True)


if __name__ == "__main__":
    unittest.main()
