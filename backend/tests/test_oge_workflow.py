from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class OgeWorkflowTests(unittest.TestCase):
    def test_workflow_is_double_gated_and_never_automates_form_201(self):
        content = (ROOT / ".github/workflows/oge.yml").read_text(encoding="utf-8")
        self.assertIn("OGE_COLLECTION_ENABLED: ${{ vars.OGE_COLLECTION_ENABLED }}", content)
        self.assertIn("OGE_TERMS_ACKNOWLEDGED: ${{ vars.OGE_TERMS_ACKNOWLEDGED }}", content)
        self.assertIn("github.event_name == 'workflow_dispatch' ||", content)
        self.assertIn("needs.evaluate-gate.outputs.status == 'enabled'", content)
        self.assertIn("python -m unison_snapshot oge-gate", content)
        self.assertIn("python -m unison_snapshot discover-oge", content)
        self.assertIn("python -m unison_snapshot archive-oge-direct-pdfs", content)
        self.assertIn("python -m unison_snapshot extract-oge-direct-pdfs", content)
        self.assertNotIn("201 Request", content)
        self.assertNotIn("curl ", content)
        self.assertNotIn("wget ", content)

    def test_workflow_preserves_branch_boundaries_and_rebuilds_unified_candidate(self):
        content = (ROOT / ".github/workflows/oge.yml").read_text(encoding="utf-8")
        self.assertIn("git -C \"$EVIDENCE_ROOT\" add oge/catalog oge/reports", content)
        self.assertIn("/ 'oge' / 'extractions' /", content)
        self.assertIn("status/oge.json", content)
        self.assertIn("group: disclosure-source-writer", content)
        self.assertIn("python -m unison_snapshot build-oge-candidate", content)
        self.assertIn("candidates/sources/oge-current.json", content)
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

    def test_all_source_workflows_include_oge_when_available(self):
        for name in ("house-review.yml", "senate-efd.yml", "oge.yml"):
            content = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
            self.assertIn("candidates/sources/oge-current.json", content)


if __name__ == "__main__":
    unittest.main()
