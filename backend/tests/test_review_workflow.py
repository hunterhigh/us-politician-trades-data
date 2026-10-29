from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ReviewWorkflowTests(unittest.TestCase):
    def test_replay_uses_explicit_archived_source_identity_and_shared_publication(self):
        content=(ROOT/'.github/workflows/replay.yml').read_text(encoding='utf-8')
        self.assertIn('--document-id "$DOCUMENT_ID" --source-sha256 "$SOURCE_SHA256"',content)
        self.assertIn('uses: ./.github/actions/publish-data',content)
        self.assertIn('uses: ./.github/workflows/whitehouse.yml',content)
        self.assertNotIn('refs/heads/main',content)
        self.assertNotIn('DOCUMENT_ID: wh-url:',content)
    def test_candidate_rebuild_uses_normal_qualification_and_conservation(self) -> None:
        content = (ROOT / ".github/workflows/whitehouse.yml").read_text(
            encoding="utf-8")
        self.assertIn('overlay-whitehouse-annual-candidate', content)
        self.assertIn('python -m unison_snapshot oge-review reconcile', content)
        self.assertNotIn("refs/heads/main", content)
        self.assertNotIn("refs/heads/market", content)
        self.assertIn("python -m unison_snapshot assemble-source", content)

    def test_every_review_writer_uses_one_concurrency_lock(self):
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            content = path.read_text(encoding="utf-8")
            if any(marker in content for marker in ("HEAD:refs/heads/review", "./.github/actions/publish-data", "unison_snapshot assemble-source", "unison_snapshot replay-senate-history")):
                self.assertIn("group: disclosure-source-writer", content, path.name)
                self.assertNotIn("group: disclosure-review-writer", content, path.name)

    def test_candidate_rebuild_keeps_normal_release_readiness(self):
        content = (ROOT / ".github/workflows/whitehouse.yml").read_text(
            encoding="utf-8")
        self.assertIn("disclosure-candidate-before.json", content)
        self.assertIn("oge-candidate-before.json", content)
        self.assertIn("--verify-readiness", content)
        self.assertNotIn("audit_trump_", content)


if __name__ == "__main__":
    unittest.main()
