from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class OgeWorkflowTests(unittest.TestCase):
    def test_workflow_is_double_gated_and_never_automates_form_201(self):
        content = (ROOT / ".github/workflows/oge.yml").read_text(encoding="utf-8")
        self.assertIn("OGE_COLLECTION_ENABLED: ${{ vars.OGE_COLLECTION_ENABLED }}", content)
        self.assertIn("OGE_TERMS_ACKNOWLEDGED: ${{ vars.OGE_TERMS_ACKNOWLEDGED }}", content)
        # Environment variables are available only after this job starts.
        gate_job = content.split("  evaluate-gate:", 1)[1].split("  collect-direct-reports:", 1)[0]
        self.assertNotIn("    if: ${{ github.event_name", gate_job)
        self.assertIn("environment: production", gate_job)
        self.assertIn("needs.evaluate-gate.outputs.status == 'enabled'", content)
        self.assertIn("python -m unison_snapshot oge-gate", content)
        self.assertIn("python -m unison_snapshot discover-oge", content)
        self.assertIn("python -m unison_snapshot archive-oge-direct-pdfs", content)
        self.assertIn("python -m unison_snapshot extract-oge-direct-pdfs", content)
        self.assertIn('--reuse-root "$REVIEW_ROOT/oge/extractions"', content)
        self.assertIn("'reused_extraction_count':", content)
        self.assertIn("'parsed_extraction_count':", content)
        self.assertEqual(content.count("fetch-depth: 1"), 3)
        self.assertNotIn("fetch-depth: 0", content)
        self.assertNotIn("201 Request", content)
        self.assertNotIn("curl ", content)
        self.assertNotIn("wget ", content)

    def test_workflow_preserves_branch_boundaries_and_rebuilds_unified_candidate(self):
        content = (ROOT / ".github/workflows/oge.yml").read_text(encoding="utf-8")
        self.assertIn("git -C \"$EVIDENCE_ROOT\" add oge/catalog oge/reports", content)
        self.assertIn("/ 'oge' / 'extractions' /", content)
        self.assertIn("value['parser_version'].replace('/', '-')", content)
        self.assertNotIn("'oge-278t-pdf-v1.json'", content)
        self.assertIn("status/oge.json", content)
        self.assertIn("group: disclosure-source-writer", content)
        self.assertIn("python -m unison_snapshot build-oge-candidate", content)
        self.assertIn("python backend/scripts/audit_trump_sept_oge_v5.py", content)
        self.assertIn("python backend/scripts/audit_trump_sept_oge_v6.py", content)
        self.assertIn("python backend/scripts/audit_trump_sept_oge_v7.py", content)
        self.assertIn('--catalog-history-dir "$REVIEW_ROOT/oge/discoveries"', content)
        self.assertIn('--extractions-dir "$REVIEW_ROOT/oge/extractions"', content)
        self.assertIn("'retained_historical_direct_count':", content)
        self.assertIn("'whitehouse_public_extraction_failure_count':", content)
        self.assertIn("candidates/sources/oge-current.json", content)
        self.assertIn("python backend/scripts/build_whitehouse_278t_candidate.py", content)
        self.assertIn("python backend/scripts/overlay_whitehouse_annual_candidate.py", content)
        self.assertIn("whitehouse/qualifications/candidate-current.json", content)
        self.assertIn("whitehouse/annual/candidate-current.json", content)
        self.assertIn("'whitehouse_public_qualified_holding_count':", content)
        self.assertIn("'qualified_holding_count': len(combined['reported_holdings'])", content)
        self.assertLess(content.index("python -m unison_snapshot build-oge-candidate"),
                        content.index("python backend/scripts/build_whitehouse_278t_candidate.py"))
        self.assertLess(content.index("python backend/scripts/build_whitehouse_278t_candidate.py"),
                        content.index("python backend/scripts/overlay_whitehouse_annual_candidate.py"))
        self.assertLess(content.index("python backend/scripts/overlay_whitehouse_annual_candidate.py"),
                        content.index("python -m unison_snapshot build-disclosure-candidate"))
        self.assertIn("python -m unison_snapshot build-disclosure-candidate", content)
        self.assertIn("--harmonize-cutoffs", content)
        self.assertIn("candidates/disclosure-current.json", content)

    def test_collection_failure_is_written_to_source_state(self):
        content = (ROOT / ".github/workflows/oge.yml").read_text(encoding="utf-8")
        self.assertIn("id: collect", content)
        self.assertIn("id: publish_evidence", content)
        self.assertIn("if: ${{ always() }}", content)
        self.assertIn("COLLECT_OUTCOME: ${{ steps.collect.outcome }}", content)
        self.assertIn("EVIDENCE_OUTCOME: ${{ steps.publish_evidence.outcome }}", content)
        self.assertIn("'status': 'partial' if succeeded else 'failed'", content)
        self.assertIn("'workflow_run_url': os.environ['WORKFLOW_RUN_URL']", content)

    def test_successful_unified_build_updates_and_commits_disclosure_status(self):
        content = (ROOT / ".github/workflows/oge.yml").read_text(encoding="utf-8")
        build = content.index("python -m unison_snapshot build-disclosure-candidate")
        status = content.index("(review / 'status/disclosure_candidate.json').write_text(")
        staged = content.index("status/disclosure_cutoff.json status/disclosure_candidate.json")
        self.assertLess(build, status)
        self.assertLess(status, staged)
        self.assertIn('BUILDER_COMMIT="${{ github.sha }}" python', content[build:status])
        for field in ("'schema_version': 'disclosure-candidate-run/v1'",
                      "'builder_commit': os.environ['BUILDER_COMMIT']",
                      "'data_cutoff_at': candidate['meta']['data_cutoff_at']",
                      "'people_count': len(candidate['people'])",
                      "'transaction_count': len(candidate['transactions'])",
                      "'reported_holding_count': len(candidate['reported_holdings'])",
                      "'cutoff_audit': 'status/disclosure_cutoff.json'"):
            self.assertIn(field, content[build:staged])
        self.assertNotIn("continue-on-error", content[build:staged])

    def test_all_source_workflows_include_oge_when_available(self):
        for name in ("house-review.yml", "senate-efd.yml", "oge.yml"):
            content = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
            self.assertIn("candidates/sources/oge-current.json", content)

    def test_archived_whitehouse_candidate_rebuild_is_manual_and_review_only(self):
        content = (ROOT / ".github/workflows/whitehouse-candidate-rebuild.yml").read_text(
            encoding="utf-8")
        self.assertIn("workflow_dispatch:", content)
        self.assertNotIn("  schedule:", content)
        self.assertIn("group: disclosure-source-writer", content)
        self.assertIn("environment: production", content)
        self.assertIn("python backend/scripts/build_whitehouse_278t_candidate.py", content)
        self.assertIn("python -m unison_snapshot verify-first-launch", content)
        self.assertLess(content.index("status['qualified_holding_count']"),
                        content.index("python -m unison_snapshot verify-first-launch"))
        self.assertIn("git -C \"$REVIEW_ROOT\" push origin HEAD:refs/heads/review", content)
        self.assertNotIn("HEAD:refs/heads/main", content)


if __name__ == "__main__":
    unittest.main()
