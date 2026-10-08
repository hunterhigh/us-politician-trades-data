from copy import deepcopy
from pathlib import Path
import unittest

from unison_snapshot.pipeline_qa.projection_issues import collect_projection_issues


class ProjectionIssuesTests(unittest.TestCase):
    def fixture(self):
        row = {"id": "tx-1", "person_id": "person-1", "source_id": "oge",
               "asset_name": "Example", "ticker": None, "ticker_mapping_basis": None}
        candidate = {"meta": {"is_demo": False, "data_cutoff_at": "2026-10-07T23:59:59Z"},
                     "people": [{"id": "person-1"}], "transactions": [row],
                     "reported_holdings": [], "source_health": [{"source_id": "oge"}]}
        board = {key: deepcopy(candidate[key]) for key in
                 ("people", "transactions", "reported_holdings")}
        board["transactions"][0].update(ticker="EX", ticker_mapping_basis="official_symbol")
        return candidate, board

    def test_existing_market_enrichment_is_not_a_source_issue(self):
        candidate, board = self.fixture()
        report = collect_projection_issues(governed=candidate, reference=deepcopy(candidate),
                                           previous_board=board)
        self.assertEqual(report["issue_count"], 0)
        self.assertFalse(report["publication_authorized"])

    def test_collects_changes_without_preempting_candidate_construction(self):
        candidate, board = self.fixture()
        reference = deepcopy(candidate)
        candidate["transactions"][0]["asset_name"] = "Changed"
        candidate["transactions"].append({"id": "tx-2", "source_id": "oge"})
        report = collect_projection_issues(governed=candidate, reference=reference,
                                           previous_board=board)
        self.assertEqual(report["counts"]["transactions"]["new_from_previous"], 1)
        self.assertEqual({item["reason"] for item in report["issues"]},
                         {"new_since_review_candidate", "changed_since_review_candidate",
                          "changed_from_previous_publication"})
        self.assertTrue(report["targeted_review_deferred_until_after_construction"])

    def test_workflow_builds_projection_before_market_and_reviews_later(self):
        workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/publish-complete.yml")
        content = workflow.read_text(encoding="utf-8")
        projection = content.index("name: Build canonical disclosure projection from qualified sources")
        market = content.index("name: Build licensed five-array candidate")
        browser = content.index("name: Verify complete candidate in the frozen browser frontend")
        issues = content.index("name: Record projection issues for final targeted review")
        readiness = content.index("name: Verify first-launch disclosure completeness")
        publication = content.index("name: Prepare and commit market branch")
        self.assertLess(projection, market)
        self.assertLess(market, browser)
        self.assertLess(browser, issues)
        self.assertLess(issues, readiness)
        self.assertLess(readiness, publication)
        self.assertIn('--input "$RUNNER_TEMP/governed-disclosure.json"', content)


if __name__ == "__main__":
    unittest.main()
