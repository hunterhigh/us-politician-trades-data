"""Static safety checks for the House source-to-review run handoff."""
from pathlib import Path
import unittest


WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


class HouseSourceHandoffWorkflowTests(unittest.TestCase):
    def test_source_exports_exact_published_commits_after_state_write(self):
        text = (WORKFLOWS / "house-state.yml").read_text(encoding="utf-8")
        self.assertIn("if: github.ref == 'refs/heads/code'", text)
        self.assertLess(text.index("- name: Publish state branch"),
                        text.index("- name: Record immutable House source handoff"))
        self.assertIn('git -C "$EVIDENCE_ROOT" rev-parse HEAD', text)
        self.assertIn('git -C "$RUNNER_TEMP/house-state" rev-parse HEAD', text)
        self.assertIn("'schema_version': 'house-source-handoff/v1'", text)
        self.assertIn("name: house-source-handoff-${{ github.run_id }}", text)
        self.assertIn("if-no-files-found: error", text)

    def test_event_review_uses_trigger_run_artifact_and_fixed_commits(self):
        text = (WORKFLOWS / "house-review.yml").read_text(encoding="utf-8")
        self.assertIn("actions: read", text)
        self.assertIn("github.event.workflow_run.head_branch == 'code'", text)
        self.assertIn("github.event.workflow_run.head_repository.full_name == github.repository", text)
        self.assertIn("run-id: ${{ github.event.workflow_run.id }}", text)
        self.assertIn("value.get('source_run_id') != os.environ['SOURCE_RUN_ID']", text)
        self.assertIn("value.get('code_commit') != os.environ['SOURCE_CODE_COMMIT']", text)
        self.assertIn('git fetch --no-tags origin "$PINNED_EVIDENCE"', text)
        self.assertIn('git worktree add --detach "$state_root" "$PINNED_STATE"', text)
        self.assertIn("if: github.event_name == 'workflow_run'", text)


if __name__ == "__main__":
    unittest.main()
