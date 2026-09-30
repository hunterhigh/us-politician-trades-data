from pathlib import Path
import unittest


WORKFLOW = (Path(__file__).resolve().parents[2] / ".github" / "workflows" /
            "oge-fixed-shadow.yml")


class OgeFixedShadowWorkflowTests(unittest.TestCase):
    def test_replay_is_manual_read_only_and_hash_bound(self) -> None:
        content = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("  workflow_dispatch:", content)
        self.assertNotIn("  schedule:", content)
        self.assertNotIn("  workflow_run:", content)
        self.assertIn("  actions: read", content)
        self.assertIn("  contents: read", content)
        self.assertIn("if: github.ref == 'refs/heads/code'", content)
        self.assertIn("persist-credentials: false", content)
        self.assertIn("pipeline-ledger-oge-${SOURCE_RUN_ID}", content)
        self.assertIn("Prior OGE ledger does not bind", content)
        self.assertIn("Fixed OGE {name} hash differs", content)
        self.assertIn("--evidence-root", content)
        self.assertIn('--code-commit "$GITHUB_SHA"', content)
        self.assertIn("actions/upload-artifact@v4", content)
        self.assertNotIn("git push", content)
        self.assertNotIn("environment: production", content)


if __name__ == "__main__":
    unittest.main()
