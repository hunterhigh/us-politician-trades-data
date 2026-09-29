from pathlib import Path
import unittest


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "pipeline-shadow-bundle.yml"


class ShadowBundleWorkflowTests(unittest.TestCase):
    def test_manual_read_only_bundle_consumes_all_three_source_artifacts(self):
        content = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("  workflow_dispatch:", content)
        self.assertNotIn("  schedule:", content)
        self.assertNotIn("  workflow_run:", content)
        self.assertIn("  actions: read", content)
        self.assertIn("  contents: read", content)
        self.assertIn("if: github.ref == 'refs/heads/code'", content)
        self.assertIn("persist-credentials: false", content)
        self.assertNotIn("git push", content)
        self.assertNotIn("environment: production", content)
        for source in ("oge", "house", "senate"):
            self.assertIn(f"--run-dir .local/shadow/{source}", content)
        self.assertIn('pipeline-ledger-oge-${OGE_RUN_ID}', content)
        self.assertIn('house-shadow-${HOUSE_RUN_ID}-${HOUSE_RUN_ATTEMPT}', content)
        self.assertIn('senate-efd-shadow-${SENATE_RUN_ID}-${SENATE_RUN_ATTEMPT}', content)
        self.assertIn('gh run download', content)
        self.assertIn('--code-commit "$GITHUB_SHA"', content)
        self.assertIn('actions/upload-artifact@v4', content)


if __name__ == "__main__":
    unittest.main()
