from copy import deepcopy
import unittest
from unittest.mock import patch

from unison_snapshot.pipeline_qa.migration_inventory import InventoryError
from unison_snapshot.pipeline_qa.publication_delta import build_publication_delta

MAIN = "a" * 40
REVIEW = "b" * 40
SOURCE_HASH = "c" * 64
OFFICIAL_URL = "https://www.whitehouse.gov/wp-content/uploads/index.html"


class PublicationDeltaTests(unittest.TestCase):
    def fixture(self):
        person = {"id": "person", "disclosure_authority": "oge", "display_name": "Person"}
        holding = {"id": "old-holding", "filing_id": "old-report", "person_id": "person",
                   "source_id": "oge", "source": "OGE", "source_url": "https://www.whitehouse.gov/old.pdf",
                   "verification_status": "official_matched", "owner": "Self", "asset_name": "Example",
                   "report_period_end": "2025-12-31", "filed_at": "2026-01-01T00:00:00Z",
                   "value_low": 1000, "value_high": 1500, "change_from_prior": None,
                   "instrument_type": "Stock"}
        previous = {"people": [person], "transactions": [], "reported_holdings": [holding]}
        unified = {"meta": {"is_demo": False, "data_cutoff_at": "2026-01-01T23:59:59Z"},
                   "people": [person], "transactions": [], "reported_holdings": [deepcopy(holding)]}
        prepared = deepcopy(unified)
        prepared["security_market_data"] = []
        prepared["source_health"] = []
        sources = {name: {"meta": {"is_demo": False}, "people": [], "transactions": [],
                          "reported_holdings": []} for name in ("house_clerk", "oge", "senate_efd")}
        sources["oge"]["people"] = [person]
        sources["oge"]["reported_holdings"] = [deepcopy(holding)]
        return previous, unified, prepared, sources

    def run_gate(self, values, transitions=None, **kwargs):
        previous, unified, prepared, sources = values
        return build_publication_delta(previous_board=previous, unified=unified,
            prepared=prepared, sources=sources, transitions=transitions,
            main_commit=MAIN, review_commit=REVIEW, **kwargs)

    def test_unchanged_facts_make_no_duplicate_change_rows(self):
        report = self.run_gate(self.fixture())
        self.assertEqual(report["changes"], [])
        self.assertEqual(report["counts"]["reported_holdings"]["unchanged"], 1)
        self.assertEqual(report["transition_verification_level"], "not_applicable_no_rekeys")

    def test_new_source_fact_is_reported_once(self):
        values = self.fixture()
        new = {**values[1]["reported_holdings"][0], "id": "new-fact", "asset_name": "Other"}
        values[1]["reported_holdings"].append(new)
        values[2]["reported_holdings"].append(deepcopy(new))
        values[3]["oge"]["reported_holdings"].append(deepcopy(new))
        report = self.run_gate(values)
        self.assertEqual(report["counts"]["reported_holdings"]["new"], 1)
        self.assertEqual(report["changes"], [{"entity": "reported_holdings", "kind": "new",
                                               "new_id": "new-fact", "source_id": "oge"}])

    def test_evidenced_rekey_is_one_canonical_fact(self):
        values = self.fixture()
        updated = {**values[1]["reported_holdings"][0], "id": "new-holding",
                   "filing_id": "replacement-report", "source_url": "https://www.whitehouse.gov/new.pdf"}
        values[1]["reported_holdings"] = [updated]
        values[2]["reported_holdings"] = [deepcopy(updated)]
        values[3]["oge"]["reported_holdings"] = [deepcopy(updated)]
        decision = {"schema_version": "pipeline-fact-transitions/v1", "from_main_commit": MAIN,
                    "to_review_commit": REVIEW, "evidence_commit": "d" * 40,
                    "records": [{"entity": "reported_holdings",
                    "kind": "rekey", "old_id": "old-holding", "new_id": "new-holding",
                    "reason": "official_index_replacement",
                    "evidence_url": "https://www.whitehouse.gov/new.pdf",
                    "source_sha256": SOURCE_HASH}]}
        def archived(repo, commit, url, read_object, list_paths):
            return {"sha256": SOURCE_HASH, "filer_name_from_label": "Person",
                    "report_year_from_label": 2026, "url": url}
        with patch("unison_snapshot.pipeline_qa.publication_delta._archive", side_effect=archived), \
             patch("unison_snapshot.pipeline_qa.publication_delta._official_index_replacement", return_value=True):
            report = self.run_gate(values, decision, repo="repo", evidence_commit="d" * 40)
            self.assertEqual(report["counts"]["reported_holdings"]["rekey"], 1)
            self.assertEqual(report["counts"]["reported_holdings"]["new"], 0)
            self.assertEqual(len(report["changes"]), 1)
            self.assertEqual(report["transition_verification_level"],
                             "archived_official_index_bytes_for_rekeys")
            decision["from_main_commit"] = "e" * 40
            with self.assertRaises(InventoryError):
                self.run_gate(values, decision, repo="repo", evidence_commit="d" * 40)

    def test_unverified_transition_and_malformed_id_fail(self):
        values = self.fixture()
        values[1]["reported_holdings"] = []
        values[2]["reported_holdings"] = []
        values[3]["oge"]["reported_holdings"] = []
        spec = {"schema_version": "pipeline-fact-transitions/v1", "from_main_commit": MAIN,
                "to_review_commit": REVIEW, "evidence_commit": "d" * 40,
                "records": [{"entity": "reported_holdings", "kind": "withdrawn",
                             "old_id": [], "new_id": None, "reason": "official_withdrawal",
                             "evidence_url": OFFICIAL_URL, "source_sha256": SOURCE_HASH}]}
        with self.assertRaises(InventoryError):
            self.run_gate(values, spec, repo="repo", evidence_commit="d" * 40)
        spec["records"][0]["old_id"] = "old-holding"
        with self.assertRaises(InventoryError):
            self.run_gate(values, spec, repo="repo", evidence_commit="d" * 40)

    def test_unexplained_removal_or_changed_field_fails(self):
        values = self.fixture()
        values[1]["reported_holdings"] = []
        values[2]["reported_holdings"] = []
        values[3]["oge"]["reported_holdings"] = []
        with self.assertRaises(InventoryError):
            self.run_gate(values)
        values = self.fixture()
        for row in (values[1]["reported_holdings"][0], values[2]["reported_holdings"][0],
                    values[3]["oge"]["reported_holdings"][0]):
            row["value_low"] = 2000
        with self.assertRaises(InventoryError):
            self.run_gate(values)

    def test_source_drift_and_simulated_record_fail(self):
        values = self.fixture()
        values[3]["oge"]["reported_holdings"][0]["value_low"] = 9
        with self.assertRaises(InventoryError):
            self.run_gate(values)
        values = self.fixture()
        values[2]["reported_holdings"][0]["verification_status"] = "simulated"
        with self.assertRaises(InventoryError):
            self.run_gate(values)


if __name__ == "__main__":
    unittest.main()
