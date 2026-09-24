from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class TrumpReplayWorkflowTests(unittest.TestCase):
    def test_replay_is_fixed_read_only_and_review_scoped(self):
        content = (ROOT / ".github/workflows/whitehouse-trump-annual-replay.yml").read_text(
            encoding="utf-8")
        self.assertIn("group: disclosure-source-writer", content)
        self.assertIn("wh-url:0c14d3849ca60768024e470b", content)
        self.assertIn("1cc7951c6f72fab008e921903c9a1d03d41a9910239f954e208b501d608553a3",
                      content)
        self.assertIn("c57ecdcd0e780fa7dc256fae6d0fe728735c8f09", content)
        self.assertIn("audit_trump_ocr_checkpoints.py", content)
        self.assertIn("audit_trump_annual_replay.py", content)
        self.assertIn("TARGET_PARSER_SLUG: whitehouse-278e-hybrid-geometry-v8", content)
        self.assertIn("test ! -e \"$REVIEW_ROOT/$target_checkpoint_path\"", content)
        self.assertIn("status --porcelain --untracked-files=all", content)
        self.assertNotIn("refs/heads/evidence", content)

    def test_candidate_rebuild_requires_fixed_annual_transaction_total(self) -> None:
        content = (ROOT / ".github/workflows/whitehouse-candidate-rebuild.yml").read_text(
            encoding="utf-8")
        self.assertIn("annual_transaction_count != 6759", content)
        self.assertNotIn("refs/heads/main", content)
        self.assertNotIn("refs/heads/market", content)
        self.assertIn("HEAD:refs/heads/review", content)

    def test_every_review_writer_uses_one_concurrency_lock(self):
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            content = path.read_text(encoding="utf-8")
            if "HEAD:refs/heads/review" in content:
                self.assertIn("group: disclosure-source-writer", content, path.name)
                self.assertNotIn("group: disclosure-review-writer", content, path.name)

    def test_candidate_rebuild_audits_only_additive_trump_annual_facts(self):
        content = (ROOT / ".github/workflows/whitehouse-candidate-rebuild.yml").read_text(
            encoding="utf-8")
        self.assertIn("disclosure-candidate-before.json", content)
        self.assertIn("oge-candidate-before.json", content)
        self.assertIn("audit_trump_candidate_transition.py", content)
        self.assertIn("candidate-transition-current.json", content)


if __name__ == "__main__":
    unittest.main()
