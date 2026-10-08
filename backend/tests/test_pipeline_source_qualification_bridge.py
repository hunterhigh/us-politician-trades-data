import json
from pathlib import Path
import unittest

from unison_snapshot.pipeline_qa.g1_source_coverage import build_g1_source_coverage
from unison_snapshot.pipeline_qa.migration_inventory import InventoryError
from unison_snapshot.pipeline_qa.oge_qualification_bridge import build_oge_qualification_bridge
from unison_snapshot.pipeline_qa.source_qualification_bridge import build_source_qualification_bridge


COMMIT = "a" * 40
SOURCE_HASH = "b" * 64


class SourceQualificationBridgeTests(unittest.TestCase):
    def _inventory(self):
        house = {"id": "h", "source_id": "house_clerk", "filing_id": "12345",
                 "source_url": "https://house.gov/12345.pdf", "verification_status": "official_matched"}
        senate = {"id": "s", "source_id": "senate_efd", "filing_id": "paper-id",
                  "source_url": "https://efdsearch.senate.gov/search/view/paper/paper-id/",
                  "verification_status": "official_matched"}
        direct = {"id": "o", "source_id": "oge", "filing_id": "doc",
                  "source_url": "https://oge.gov/doc.pdf", "verification_status": "official_matched"}
        wh = {"id": "w", "source_id": "oge", "filing_id": "wh-url:doc",
              "source_url": "https://whitehouse.gov/doc.pdf", "verification_status": "official_matched"}
        records = [{"id": row["id"], "source_id": row["source_id"],
                    "filing_id": row["filing_id"], "source_url": row["source_url"],
                    "canonical_record": row, "issues": []}
                   for row in (house, senate, direct, wh)]
        return {"schema_version": "pipeline-migration-inventory/v1",
                "fixed_inputs": {"review_commit": COMMIT},
                "records": {"transactions": records}}

    def _house_senate(self, inventory, *, bad_house=False):
        house_row = inventory["records"]["transactions"][0]["canonical_record"]
        house_decision = {"document_id": "12345", "source_sha256": SOURCE_HASH,
                          "qualification": {"method": "deterministic_automatic_rules",
                                            "production_eligible": not bad_house,
                                            "qualified_count": 1},
                          "transactions": [house_row]}
        senate_decision = {"candidate_transaction_count": 1, "qualified_rows": [
            {"transaction_id": "s", "document_id": "paper-id",
             "source_url": "https://efdsearch.senate.gov/search/view/paper/paper-id/",
             "source_sha256": SOURCE_HASH}]}
        path = f"house_clerk/qualifications/2026/12345/{SOURCE_HASH}.json"
        objects = {(COMMIT, path): json.dumps(house_decision).encode(),
                   (COMMIT, "senate_efd/qualifications/current.json"): json.dumps(senate_decision).encode()}
        return build_source_qualification_bridge(
            repo=Path("."), inventory=inventory,
            read_object=lambda _repo, commit, name: objects[(commit, name)],
            list_paths=lambda *_: [f"2026/12345/{SOURCE_HASH}.json"])

    def _oge(self, inventory, *, wrong_count=False):
        overlay = {"schema_version": "pipeline-legacy-channel-overlay/v1",
                   "fixed_inputs": inventory["fixed_inputs"],
                   "groups": {"whitehouse_url_fixed_document_continuity": ["w"],
                              "oge_124_fixed_id_continuity": ["o"]},
                   "whitehouse_document_counts": {"wh-url:doc": 1},
                   "historical_audit_fixtures": {"whitehouse_sha256": "c" * 64}}
        decision = {"source_id": "oge", "reports": [
            {"document_id": "doc", "promoted_count": 2 if wrong_count else 1,
             "document_reasons": []}]}
        return build_oge_qualification_bridge(
            repo=Path("."), inventory=inventory, overlay=overlay,
            read_object=lambda *_: json.dumps(decision).encode())

    def test_all_formal_ids_have_source_binding_and_paper_is_explicit(self):
        inventory = self._inventory()
        house_senate = self._house_senate(inventory)
        oge = self._oge(inventory)
        result = build_g1_source_coverage(inventory, house_senate, oge)
        self.assertEqual(result["total"], 4)
        self.assertEqual(house_senate["senate_paper_ids"], ["s"])
        self.assertTrue(result["transaction_legacy_source_binding_complete"])
        self.assertFalse(result["projection_ready"])

    def test_house_ineligible_row_is_rejected(self):
        with self.assertRaisesRegex(InventoryError, "ineligible rows"):
            self._house_senate(self._inventory(), bad_house=True)

    def test_oge_document_count_mismatch_is_rejected(self):
        with self.assertRaisesRegex(InventoryError, "promotion counts"):
            self._oge(self._inventory(), wrong_count=True)

    def test_source_coverage_rejects_missing_record(self):
        inventory = self._inventory()
        house_senate = self._house_senate(inventory)
        oge = self._oge(inventory)
        oge["records"] = []
        with self.assertRaisesRegex(InventoryError, "source-qualified ID set"):
            build_g1_source_coverage(inventory, house_senate, oge)


if __name__ == "__main__":
    unittest.main()
