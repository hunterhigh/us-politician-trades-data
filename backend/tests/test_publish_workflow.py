from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class PublishWorkflowTests(unittest.TestCase):
    def test_complete_publish_checks_sources_and_reads_back_every_frontend_mode(self):
        content = (ROOT / ".github/workflows/publish-complete.yml").read_text(
            encoding="utf-8")
        readiness = content.index("verify-first-launch")
        market = content.index("build-alpaca-market-validation")
        publish = content.index("Atomically publish complete main snapshot")
        readback = content.index("Read back the published main and market commits")
        self.assertLess(readiness, market)
        self.assertLess(publish, readback)
        for mode in ("dashboard", "search", "person", "ticker"):
            self.assertIn(f"fetch_selection {mode}", content)
        self.assertIn("snapshot_commit'] != expected", content)
        self.assertIn("readback-dashboard.html", content)
        self.assertIn("npm run test:production", content)

    def test_scheduler_is_opt_in_and_bootstrap_cannot_downgrade_main(self):
        complete = (ROOT / ".github/workflows/publish-complete.yml").read_text(
            encoding="utf-8")
        bootstrap = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        self.assertIn("ENABLED: ${{ vars.COMPLETE_PUBLICATION_ENABLED }}", complete)
        self.assertIn('if: github.event_name == \'schedule\'', complete)
        self.assertIn('run: test "$ENABLED" = "true"', complete)
        self.assertIn('current_snapshot" = "bootstrap_empty"', bootstrap)
        self.assertIn('test "$INPUT_PATH" = "ingest/current.json"', bootstrap)


if __name__ == "__main__":
    unittest.main()
