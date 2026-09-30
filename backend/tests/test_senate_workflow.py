from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class SenateWorkflowTests(unittest.TestCase):
    def test_electronic_shadow_is_manual_read_only_and_fixed_to_archive_commits(self):
        content = (ROOT / ".github/workflows/senate-efd-shadow.yml").read_text(
            encoding="utf-8")
        self.assertIn("  workflow_dispatch:", content)
        self.assertNotIn("  schedule:", content)
        self.assertIn("  contents: read", content)
        self.assertIn("if: github.ref == 'refs/heads/code'", content)
        self.assertIn("persist-credentials: false", content)
        self.assertIn("SHADOW_ROOT: ${{ github.workspace }}/.local/senate-efd-shadow", content)
        self.assertIn("--code-commit \"$GITHUB_SHA\"", content)
        self.assertIn("--evidence-commit \"$EVIDENCE_COMMIT\"", content)
        self.assertIn("--review-commit \"$REVIEW_COMMIT\"", content)
        self.assertIn("--document-id cce52b36-d00c-4710-a8ee-e84893fb4be1", content)
        self.assertIn("--document-id 2b076d77-6bc1-4b67-8be9-8f45a787479f", content)
        self.assertIn("all_electronic", content)
        self.assertIn('--catalog-path "$CATALOG_PATH"', content)
        self.assertIn('--identity-path "$FULL_IDENTITY_PATH"', content)
        self.assertIn("actions/upload-artifact@v4", content)
        self.assertIn("${{ env.SHADOW_ROOT }}/manifest.json", content)
        self.assertIn("${{ env.SHADOW_ROOT }}/candidate_rows.json", content)
        self.assertNotIn("git push", content)
        self.assertNotIn("environment: production", content)

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
        self.assertIn("group: disclosure-source-writer", content)
        self.assertIn("'entrypoint_count': report_batch['archived_total']", content)
        self.assertIn("'report_evidence_count': evidence_count", content)
        self.assertIn("senate_efd/paper_report_failures/*/*/{parser_version}.json", content)
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

    def test_source_waterlines_commit_before_unified_harmonization(self):
        for workflow, source in (("house-review.yml", "House"),
                                 ("senate-efd.yml", "Senate")):
            with self.subTest(source=source):
                content = (ROOT / ".github/workflows" / workflow).read_text(
                    encoding="utf-8")
                source_commit = content.index(f'review: {source} source candidate')
                harmonize = content.index('python -m unison_snapshot build-disclosure-candidate')
                unified_commit = content.index(f'review: unified {source} candidate')
                self.assertLess(source_commit, harmonize)
                self.assertLess(harmonize, unified_commit)
                self.assertIn('git -C "$REVIEW_ROOT" push origin HEAD:refs/heads/review',
                              content[source_commit:harmonize])
                self.assertNotIn('continue-on-error', content[source_commit:unified_commit])

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

    def test_history_resolution_is_bounded_and_activates_only_after_validation(self):
        content = (ROOT / ".github/workflows/senate-amendment-history-resolve.yml").read_text(
            encoding="utf-8")
        self.assertIn("--document-ids .local/senate-history-candidate-ids.json", content)
        self.assertIn("--limit 16", content)
        self.assertIn("counts.count(1) == 8 and counts.count(2) == 4", content)
        self.assertIn("resolve-senate-amendment-backfill", content)
        self.assertIn("activate-senate-amendment-supplement", content)
        self.assertIn("audit['input_transaction_count'] != 1646", content)
        self.assertIn("Historical predecessor escaped into the candidate", content)
        self.assertIn("git -C \"$EVIDENCE_ROOT\" add senate_efd/catalog senate_efd/reports", content)
        self.assertIn("HEAD:refs/heads/review", content)

    def test_paper_page_workflow_is_bounded_to_current_official_viewers(self):
        content = (ROOT / ".github/workflows/senate-paper-pages.yml").read_text(
            encoding="utf-8")
        self.assertIn("archive-senate-paper-pages", content)
        self.assertIn("--expected-documents 9", content)
        self.assertIn("--expected-pages 52", content)
        self.assertIn("git -C \"$EVIDENCE_ROOT\" add senate_efd/paper_pages", content)
        self.assertIn("extract-senate-paper-pages", content)
        self.assertIn("tesseract-ocr", content)
        self.assertIn("extraction_count'] + batch['failure_count'] != 9", content)
        self.assertIn("paper_report_failures", content)
        self.assertIn("paper_failures = len(batch['failures'])", content)
        self.assertIn("HEAD:refs/heads/review", content)
        self.assertIn("HEAD:refs/heads/state", content)
        self.assertIn("build-senate-candidate", content)
        self.assertIn("build-disclosure-candidate", content)


if __name__ == "__main__":
    unittest.main()
