from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class SenateWorkflowTests(unittest.TestCase):
    def test_catalog_collection_is_double_gated_and_default_closed(self):
        content = (ROOT / ".github/workflows/senate-efd.yml").read_text(encoding="utf-8")
        self.assertIn("SENATE_EFD_COLLECTION_ENABLED: ${{ vars.SENATE_EFD_COLLECTION_ENABLED }}", content)
        self.assertIn("SENATE_EFD_TERMS_ACKNOWLEDGED: ${{ vars.SENATE_EFD_TERMS_ACKNOWLEDGED }}", content)
        self.assertIn("needs: evaluate-gate", content)
        self.assertIn("needs.evaluate-gate.outputs.status == 'enabled'", content)
        self.assertIn("python -m unison_snapshot senate-efd-gate", content)
        self.assertIn("python -m unison_snapshot discover-senate-efd", content)
        self.assertNotIn("curl ", content)
        self.assertNotIn("wget ", content)

    def test_catalog_respects_evidence_review_and_state_boundaries(self):
        content = (ROOT / ".github/workflows/senate-efd.yml").read_text(encoding="utf-8")
        self.assertIn("senate_efd/catalog", content)
        self.assertIn("/ 'discoveries' /", content)
        self.assertIn("/ 'identities' /", content)
        self.assertIn("python -m unison_snapshot match-senate-catalog", content)
        self.assertIn("python -m unison_snapshot archive-senate-report-entrypoints", content)
        self.assertIn("python -m unison_snapshot extract-senate-report-entrypoints", content)
        self.assertIn("senate_efd/reports", content)
        self.assertIn("include-hidden-files: true", content)
        self.assertIn("identity_roster_sha256", content)
        self.assertIn("status/senate_efd.json", content)
        self.assertIn("group: disclosure-source-writer", content)
        self.assertIn("group: disclosure-review-writer", content)
        self.assertIn("'entrypoint_count': report_batch['archived_total']", content)
        self.assertIn("'report_evidence_count': evidence_count", content)
        self.assertIn(".local/senate-efd-state.json", content)
        self.assertIn("python -m unison_snapshot build-senate-candidate", content)
        self.assertIn("candidates/sources/senate_efd-current.json", content)
        self.assertIn("senate_efd/qualifications/", content)
        self.assertIn("python -m unison_snapshot build-disclosure-candidate", content)
        self.assertIn("--harmonize-cutoffs", content)
        self.assertIn("candidates/disclosure-current.json", content)
        self.assertIn("--html-output", content)

    def test_house_and_senate_runs_both_rebuild_the_unified_candidate(self):
        senate = (ROOT / ".github/workflows/senate-efd.yml").read_text(encoding="utf-8")
        house = (ROOT / ".github/workflows/house-review.yml").read_text(encoding="utf-8")
        for content in (house, senate):
            self.assertIn("candidates/sources/house_clerk-current.json", content)
            self.assertIn("candidates/sources/senate_efd-current.json", content)
            self.assertIn("--harmonize-cutoffs", content)
            self.assertIn("disclosure_candidate.json", content)

    def test_roster_refresh_preserves_catalog_gate_and_status(self):
        content = (ROOT / ".github/workflows/senate-roster.yml").read_text(encoding="utf-8")
        self.assertIn("gate = prior.get('gate'", content)
        self.assertIn("for key in ('catalog', 'reports')", content)
        self.assertIn("'collection_enabled': gate['collection_enabled']", content)
        self.assertIn("'terms_acknowledged': gate['terms_acknowledged']", content)

    def test_history_plan_archives_only_catalog_and_does_not_mutate_candidate_state(self):
        content = (ROOT / ".github/workflows/senate-amendment-history-plan.yml").read_text(
            encoding="utf-8")
        self.assertIn("python -m unison_snapshot plan-senate-amendment-backfill", content)
        self.assertIn("git -C \"$EVIDENCE_ROOT\" add senate_efd/catalog", content)
        self.assertIn("Require a unique predecessor for every planned target", content)
        self.assertNotIn("archive-senate-report-entrypoints", content)
        self.assertNotIn("HEAD:refs/heads/state", content)
        self.assertNotIn("HEAD:refs/heads/review", content)


if __name__ == "__main__":
    unittest.main()
