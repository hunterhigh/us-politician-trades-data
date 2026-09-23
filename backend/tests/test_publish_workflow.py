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
        # Environment-level vars are unavailable while a job-level if is evaluated.
        job_header = complete.split("jobs:", 1)[1].split("environment: production", 1)[0]
        self.assertNotIn("COMPLETE_PUBLICATION_ENABLED", job_header)
        self.assertIn("if: github.ref == 'refs/heads/code'", job_header)
        self.assertIn("--previous-main-root", complete)
        self.assertIn("--previous-market-root", complete)
        self.assertIn('--audit "$RUNNER_TEMP/market-audit.json"', complete)
        self.assertIn('--twelve-audit "$RUNNER_TEMP/twelve-audit.json"', complete)
        self.assertIn('--checked-at "$checked_at" --limit "$TWELVE_LIMIT"', complete)
        self.assertIn('current_snapshot" = "bootstrap_empty"', bootstrap)
        self.assertIn('test "$INPUT_PATH" = "ingest/current.json"', bootstrap)

    def test_market_cache_uses_a_consistent_published_main_market_pair(self):
        complete = (ROOT / ".github/workflows/publish-complete.yml").read_text(
            encoding="utf-8")
        self.assertIn("cache_main_ref", complete)
        self.assertIn("cache_market_ref", complete)
        self.assertIn("cache_main_root=$cache_main_root", complete)
        self.assertIn("cache_market_root=$cache_market_root", complete)
        self.assertIn("cache_market_commit=$cache_market_ref", complete)
        self.assertIn("Cache main and market commits are not a published pair", complete)
        self.assertIn('--previous-main-root "$CACHE_MAIN_ROOT"', complete)
        self.assertIn('--previous-market-root "$CACHE_MARKET_ROOT"', complete)
        self.assertIn('--previous-market-commit "$CACHE_MARKET_COMMIT"', complete)
        self.assertIn('git merge-base --is-ancestor "$cache_market_ref" origin/market',
                      complete)

    def test_twelve_data_summaries_distinguish_batch_and_cumulative_counts(self):
        complete = (ROOT / ".github/workflows/publish-complete.yml").read_text(
            encoding="utf-8")
        audit = (ROOT / ".github/workflows/audit-twelve-data.yml").read_text(
            encoding="utf-8")
        for content in (complete, audit):
            self.assertIn("new_accepted_count", content)
            self.assertIn("cached_accepted_count", content)
            self.assertIn("backlog_count", content)
            self.assertIn("deduplicated_row_count", content)


if __name__ == "__main__":
    unittest.main()
