from pathlib import Path
import re
import unittest


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "house-shadow.yml"


class HouseShadowWorkflowTests(unittest.TestCase):
    def test_shadow_is_manual_read_only_and_uploads_its_fixed_ref_artifacts(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^on:\s*\n  workflow_dispatch:")
        self.assertRegex(text, r"(?m)^permissions:\s*\n  contents: read$")
        self.assertNotRegex(text, r"(?m)^  (schedule|workflow_run|push):")
        self.assertNotIn("environment: production", text)
        self.assertNotRegex(text, r"(?m)^\s*git (?:push|worktree add|commit)\b")
        self.assertIn("persist-credentials: false", text)
        self.assertIn("git merge-base --is-ancestor \"$EVIDENCE_COMMIT\"", text)
        self.assertIn("git merge-base --is-ancestor \"$REVIEW_COMMIT\"", text)
        self.assertIn("--code-commit", text)
        self.assertIn("--evidence-commit", text)
        self.assertIn("--review-commit", text)
        self.assertIn("actions/upload-artifact@v4", text)
        self.assertIn("root / 'manifest.json'", text)
        self.assertIn("if-no-files-found: error", text)
        self.assertIn("all_archived", text)
        self.assertIn("EXPECTED_DOCUMENT_COUNT", text)
        self.assertIn("len(paths) != len(document_ids) * 2", text)


if __name__ == "__main__":
    unittest.main()
