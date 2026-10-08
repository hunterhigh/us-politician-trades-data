import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from unison_snapshot.pipeline_qa.legacy_channel_overlay import build_legacy_channel_overlay
from unison_snapshot.pipeline_qa.migration_inventory import InventoryError


class LegacyChannelOverlayTests(unittest.TestCase):
    def _run(self, *, bad_url=False, missing_oge=False):
        records = [
            {"id": "wh", "source_id": "oge", "filing_id": "wh-url:a",
             "source_url": "https://whitehouse.gov/a.pdf" if not bad_url else "other", "issues": []},
            {"id": "oge", "source_id": "oge", "filing_id": "f1", "issues": []},
            {"id": "paper", "source_id": "senate_efd",
             "source_url": "https://efdsearch.senate.gov/search/view/paper/p1/", "issues": []},
            {"id": "house", "source_id": "house_clerk", "issues": []},
        ]
        if missing_oge:
            records = [row for row in records if row["id"] != "oge"]
        inventory = {"schema_version": "pipeline-migration-inventory/v1",
                     "fixed_inputs": {"main_commit": "a" * 40},
                     "records": {"transactions": records}}
        wh = {"wh_url_row_count": 1, "documents": [{"document_id": "wh-url:a",
               "source_url": "https://whitehouse.gov/a.pdf", "candidate_row_count": 1}]}
        oge = {"candidate_row_count": 1, "documents": [
            {"candidate_transaction_ids": ["oge"], "candidate_row_count": 1}]}
        with TemporaryDirectory() as directory:
            wh_path = Path(directory) / "wh.json"
            oge_path = Path(directory) / "oge.json"
            wh_path.write_text(json.dumps(wh))
            oge_path.write_text(json.dumps(oge))
            return build_legacy_channel_overlay(inventory, whitehouse_audit=wh_path,
                                                oge_124_audit=oge_path)

    def test_conserves_current_ids_without_claiming_source_replay(self):
        result = self._run()
        self.assertEqual(result["counts"], {
            "whitehouse_url_fixed_document_continuity": 1,
            "oge_124_fixed_id_continuity": 1,
            "senate_paper_source_binding_required": 1,
            "other_current_candidate_ids": 1,
        })
        self.assertFalse(result["historical_source_replay_performed"])
        self.assertFalse(result["g1_source_binding_complete"])

    def test_source_url_mismatch_fails_closed(self):
        with self.assertRaisesRegex(InventoryError, "White House current candidate differs"):
            self._run(bad_url=True)

    def test_missing_oge_id_fails_closed(self):
        with self.assertRaisesRegex(InventoryError, "duplicate or missing"):
            self._run(missing_oge=True)


if __name__ == "__main__":
    unittest.main()
