import json
from pathlib import Path
import unittest

from unison_snapshot.pipeline_qa.g1_entity_source_coverage import build_g1_entity_source_coverage
from unison_snapshot.pipeline_qa.migration_inventory import InventoryError


COMMIT = "a" * 40
HASH = "b" * 64


class EntitySourceCoverageTests(unittest.TestCase):
    def _fixture(self, *, missing_senate=False):
        h = {"id": "h", "source_id": "house_clerk", "filing_id": "12345",
             "person_id": "hp", "asset_name": "H", "ticker": None,
             "ticker_mapping_basis": None}
        s = {"id": "s", "source_id": "senate_efd", "filing_id": "annual",
             "person_id": "sp", "asset_name": "S", "ticker": None,
             "ticker_mapping_basis": None}
        o = {"id": "o", "source_id": "oge", "filing_id": "wh-url:doc",
             "person_id": "op", "source_url": "https://whitehouse.gov/doc.pdf",
             "asset_name": "O", "value_low": 1, "value_high": 2,
             "report_period_end": "2025-12-31"}
        holdings = [{"id": row["id"], "source_id": row["source_id"],
                     "filing_id": row["filing_id"], "person_id": row["person_id"],
                     "source_url": row.get("source_url"), "canonical_record": row,
                     "issues": []}
                    for row in (h, s, o)]
        people = [{"id": name, "source_id": source,
                   "source_candidate_sha256": "c" * 64, "issues": []}
                  for name, source in (("hp", "house_clerk"), ("sp", "senate_efd"), ("op", "oge"))]
        inventory = {"schema_version": "pipeline-migration-inventory/v1",
                     "fixed_inputs": {"review_commit": COMMIT},
                     "records": {"reported_holdings": holdings, "people": people,
                                 "transactions": []}}
        house_path = f"house_clerk/holding_qualifications/2025/12345/{HASH}.json"
        house = {"source_sha256": HASH, "production_eligible": True, "holdings": [h]}
        senate = {"holding_count": 0 if missing_senate else 1,
                  "reported_holdings": [] if missing_senate else [s],
                  "reports": [{"document_id": "annual", "publication_state": "qualified",
                               "source_sha256": HASH}]}
        whitehouse = {"source_eligible_holding_count": 1,
                      "holdings": [{"row_id": "o", "document_id": "wh-url:doc",
                                    "source_url": "https://whitehouse.gov/doc.pdf",
                                    "asset_name": "O", "value_low": 1, "value_high": 2,
                                    "report_period_end": "2025-12-31",
                                    "source_sha256": HASH,
                                    "source_holdings_eligible": True,
                                    "row_blocking_reasons": []}]}
        objects = {(COMMIT, house_path): json.dumps(house).encode(),
                   (COMMIT, "senate_efd/annual/current.json"): json.dumps(senate).encode(),
                   (COMMIT, "whitehouse/annual/filer-reported-current.json"):
                       json.dumps(whitehouse).encode()}
        return inventory, objects

    def _run(self, fixture):
        inventory, objects = fixture
        return build_g1_entity_source_coverage(
            repo=Path("."), inventory=inventory,
            read_object=lambda _repo, commit, path: objects[(commit, path)],
            list_paths=lambda *_: [f"2025/12345/{HASH}.json"])

    def test_all_holdings_and_people_are_bound(self):
        result = self._run(self._fixture())
        self.assertEqual(result["holding_total"], 3)
        self.assertEqual(result["person_total"], 3)
        self.assertTrue(result["holdings_people_legacy_source_binding_complete"])
        self.assertFalse(result["projection_ready"])

    def test_missing_senate_holding_blocks_coverage(self):
        with self.assertRaisesRegex(InventoryError, "not all formal holdings"):
            self._run(self._fixture(missing_senate=True))


if __name__ == "__main__":
    unittest.main()
