import hashlib
import json
from pathlib import Path
import unittest

from unison_snapshot.pipeline_qa.g1_disposition import build_g1_disposition
from unison_snapshot.pipeline_qa.migration_inventory import InventoryError


OLD = "a" * 40
NEW = "b" * 40


class G1DispositionTests(unittest.TestCase):
    def _fixture(self, *, remove_old=False):
        transaction = {"id": "t1", "source_id": "house_clerk"}
        old_board = {"people": [], "transactions": [transaction], "reported_holdings": []}
        raw = json.dumps(old_board).encode()
        digest = hashlib.sha256(raw).hexdigest()
        objects = {(OLD, "manifest.json"): json.dumps({"board": digest}).encode(),
                   (OLD, f"board/{digest}.json"): raw}
        records = [] if remove_old else [{"canonical_record": transaction}]
        inventory = {"schema_version": "pipeline-migration-inventory/v1",
                     "fixed_inputs": {"main_commit": NEW},
                     "records": {"people": [], "transactions": records, "reported_holdings": []}}
        overlay = {"schema_version": "pipeline-legacy-channel-overlay/v1",
                   "fixed_inputs": inventory["fixed_inputs"],
                   "groups": {"other_current_candidate_ids": [] if remove_old else ["t1"]}}
        rebinding = {"schema_version": "pipeline-holdings-rebinding/v1",
                     "old_main_commit": OLD, "new_main_commit": NEW,
                     "pairs": [], "rebinding_complete": True}
        return objects, inventory, overlay, rebinding

    def _run(self, fixture):
        objects, inventory, overlay, rebinding = fixture
        return build_g1_disposition(repo=Path("."), old_main_commit=OLD,
            inventory=inventory, overlay=overlay, rebinding=rebinding,
            read_object=lambda _repo, commit, path: objects[(commit, path)])

    def test_old_transaction_is_retained_and_projection_stays_closed(self):
        result = self._run(self._fixture())
        self.assertTrue(result["old_formal_id_conservation_complete"])
        self.assertEqual(result["counts"]["retained_old_transactions"], 1)
        self.assertFalse(result["projection_ready"])

    def test_missing_old_transaction_is_reported(self):
        result = self._run(self._fixture(remove_old=True))
        self.assertEqual(result["issues"], ["old_transaction_loss_or_change"])
        self.assertFalse(result["old_formal_id_conservation_complete"])

    def test_mismatched_rebinding_commit_is_rejected(self):
        fixture = self._fixture()
        fixture[3]["new_main_commit"] = "c" * 40
        with self.assertRaisesRegex(InventoryError, "immutable commits"):
            self._run(fixture)


if __name__ == "__main__":
    unittest.main()
