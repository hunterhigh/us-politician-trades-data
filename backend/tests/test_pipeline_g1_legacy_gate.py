import unittest

from unison_snapshot.pipeline_qa.g1_legacy_gate import build_g1_legacy_gate
from unison_snapshot.pipeline_qa.migration_inventory import InventoryError


class G1LegacyGateTests(unittest.TestCase):
    def _reports(self):
        fixed = {"main_commit": "a" * 40, "review_commit": "b" * 40}
        inventory = {"schema_version": "pipeline-migration-inventory/v1",
                     "fixed_inputs": fixed,
                     "production_candidate_id_coverage_complete": True,
                     "market": {"verification_status": "pinned_ref_only"},
                     "records": {"transactions": [{}], "reported_holdings": [{}], "people": [{}]}}
        disposition = {"schema_version": "pipeline-g1-disposition/v1",
                       "fixed_inputs": {**fixed, "old_main_commit": "c" * 40},
                       "old_formal_id_conservation_complete": True}
        transactions = {"schema_version": "pipeline-g1-source-coverage/v1",
                        "fixed_inputs": fixed, "transaction_legacy_source_binding_complete": True,
                        "total": 1, "house_senate_level": "row", "oge_direct_level": "document",
                        "whitehouse_level": "audit"}
        entities = {"schema_version": "pipeline-g1-entity-source-coverage/v1",
                    "fixed_inputs": fixed,
                    "holdings_people_legacy_source_binding_complete": True,
                    "holding_total": 1, "person_total": 1}
        rebinding = {"schema_version": "pipeline-holdings-rebinding/v1",
                     "new_main_commit": fixed["main_commit"],
                     "old_main_commit": "c" * 40, "rebinding_complete": True}
        return inventory, disposition, transactions, entities, rebinding

    def test_legacy_binding_can_close_without_claiming_projection(self):
        result = build_g1_legacy_gate(*self._reports())
        self.assertTrue(result["g1_legacy_candidate_binding_complete"])
        self.assertFalse(result["market_page_bytes_verified"])
        self.assertFalse(result["projection_ready"])

    def test_missing_entity_binding_keeps_g1_open(self):
        reports = self._reports()
        reports[3]["holdings_people_legacy_source_binding_complete"] = False
        result = build_g1_legacy_gate(*reports)
        self.assertFalse(result["g1_legacy_candidate_binding_complete"])

    def test_mixed_review_commit_is_rejected(self):
        reports = self._reports()
        reports[3]["fixed_inputs"] = {"main_commit": "a" * 40, "review_commit": "d" * 40}
        with self.assertRaisesRegex(InventoryError, "immutable snapshot"):
            build_g1_legacy_gate(*reports)


if __name__ == "__main__":
    unittest.main()
