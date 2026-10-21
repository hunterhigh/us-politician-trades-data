from pathlib import Path
import os
import shutil
import subprocess
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]


class PublishWorkflowTests(unittest.TestCase):
    def test_complete_publish_checks_sources_and_reads_back_every_frontend_mode(self):
        content = (ROOT / ".github/workflows/publish-complete.yml").read_text(
            encoding="utf-8")
        content = content.split("  publish:\n", 1)[1]
        projection = content.index("Build canonical disclosure projection from qualified sources")
        frozen_board = content.index("Freeze prior board for HTML browser checks")
        browser = content.index("Verify publication in both browser frontends")
        readiness = content.index("verify-first-launch")
        market = content.index("build_cached_candidate.py")
        preflight = content.index("Publish immutable preflight commits")
        readback = content.index("Read back immutable prepared main and market commits")
        publish = content.index("Atomically publish the verified main, market, and tags")
        self.assertLess(frozen_board, projection)
        self.assertLess(projection, market)
        self.assertLess(market, browser)
        self.assertLess(market, readiness)
        self.assertLess(readiness, preflight)
        self.assertLess(preflight, readback)
        self.assertLess(readback, browser)
        self.assertLess(readback, publish)
        stop = content.index("Exercise late failure before publication")
        self.assertLess(browser, stop)
        self.assertLess(stop, publish)
        self.assertIn("if: inputs.recovery_stop_before_publish", content)
        self.assertIn('git push --atomic origin ":$PREFLIGHT_MAIN_REF" ":$PREFLIGHT_MARKET_REF"',
                      content)
        self.assertIn("exit 86", content)
        self.assertNotIn("\n      - name:", content[publish + 1:])
        self.assertIn("verify_prepared_publication.py", content)
        self.assertIn("prepared-readback/dashboard.html", content)
        self.assertIn("person_id = 'oge:076544f8ba0638cf'", content)
        self.assertIn("ticker = 'MSFT'", content)
        self.assertIn('"$MAIN_SHA:refs/heads/main" "${market_ref[@]}"',
                      content)
        self.assertIn('"refs/tags/published/$MAIN_SHA"', content)
        self.assertIn('"refs/tags/published-market/$MARKET_SHA"', content)
        self.assertNotIn("Publish market before disclosure pointer", content)
        self.assertEqual(content.count("npm ci"), 1)
        self.assertEqual(content.count("npm run test:production"), 1)
        self.assertEqual(content.count("npm run test:latest-html"), 1)
        self.assertEqual(content.count("PREVIOUS_MAIN_ROOT: ${{ runner.temp }}/previous-main"), 1)
        self.assertIn('cp "$MAIN_ROOT/board/$board_sha.json" "$previous/board/$board_sha.json"', content)
        self.assertLess(readback, content.rindex("npm run test:latest-html"))
        self.assertLess(content.rindex("npm run test:latest-html"), publish)

    def test_browser_gate_selects_input_for_every_mode_and_propagates_failure(self):
        content = (ROOT / ".github/workflows/publish-complete.yml").read_text(encoding="utf-8")
        step = content.split("      - name: Verify publication in both browser frontends\n", 1)[1].split("      - name:", 1)[0]
        self.assertNotIn("\n        if:", step)
        self.assertIn("REQUIRE_TRUMP_SEPT_OGE: ${{ inputs.require_trump_sept_oge }}", step)
        script = textwrap.dedent(step.split("        run: |\n", 1)[1].split("        working-directory:", 1)[0])
        git_bash = Path(shutil.which("git") or "git").resolve().parent.parent / "bin/bash.exe"
        bash = str(git_bash) if os.name == "nt" and git_bash.exists() else shutil.which("bash")
        if not bash:
            self.skipTest("Bash is required to execute the Actions browser step")
        stub = '''
set -e
npm() {
  echo "npm $*|$PRODUCTION_DASHBOARD_HTML"
  if [ "$*" = "$FAIL_COMMAND" ]; then return 23; fi
}
'''
        for mode in ("", "false", "true"):
            for failure in ("", "run test:production", "run test:latest-html"):
                with self.subTest(mode=mode, failure=failure):
                    result = subprocess.run([bash, "-c", stub + script], text=True,
                        capture_output=True, env={**os.environ, "RUNNER_TEMP": "/runner temp",
                            "SKIP_PREPARED_READBACK": mode, "FAIL_COMMAND": failure})
                    self.assertEqual(result.returncode, 23 if failure else 0, result.stderr)
                    target = "complete-dashboard.html" if mode == "true" else "prepared-readback/dashboard.html"
                    commands = [line for line in result.stdout.splitlines() if line.startswith("npm ")]
                    expected = ["ci", "run test:production"]
                    if failure != "run test:production":
                        expected.append("run test:latest-html")
                    self.assertEqual(commands, [f"npm {command}|/runner temp/{target}" for command in expected])

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
        self.assertIn("github.ref == 'refs/heads/code'", job_header)
        self.assertIn("--previous-main-root", complete)
        self.assertIn("--previous-market-root", complete)
        self.assertIn('--audit "$RUNNER_TEMP/market-audit.json"', complete)
        self.assertIn('--twelve-audit "$RUNNER_TEMP/twelve-audit.json"', complete)
        self.assertIn('--checked-at "$checked_at" --limit "$TWELVE_LIMIT"', complete)
        self.assertIn('test "$TWELVE_LIMIT" -ge 0', complete)
        self.assertIn('if [ "$TWELVE_LIMIT" -gt 0 ] && [ -z "$TWELVE_KEY" ]', complete)
        self.assertIn('current_snapshot" = "bootstrap_empty"', bootstrap)
        self.assertIn('test "$INPUT_PATH" = "ingest/current.json"', bootstrap)

    def test_market_cache_uses_a_consistent_published_main_market_pair(self):
        complete = (ROOT / ".github/workflows/publish-complete.yml").read_text(
            encoding="utf-8")
        self.assertIn("cache_main_ref", complete)
        self.assertIn("cache_market_ref", complete)
        complete += (ROOT / ".github/scripts/prepare-publication-trees.sh").read_text()
        self.assertIn("cache_main_root=$cache_main_root", complete)
        self.assertIn("cache_market_root=$cache_market_root", complete)
        self.assertIn("cache_market_commit=$cache_market_ref", complete)
        self.assertIn("Cache main and market commits are not a published pair", complete)
        self.assertIn('--previous-main-root "$CACHE_MAIN_ROOT"', complete)
        self.assertIn('--previous-market-root "$CACHE_MARKET_ROOT"', complete)
        self.assertIn('--previous-market-commit "$CACHE_MARKET_COMMIT"', complete)
        self.assertIn('git merge-base --is-ancestor "$cache_market_ref" origin/market',
                      complete)

    def test_routine_publish_fetches_only_branch_tips_but_recovery_keeps_history(self):
        complete = (ROOT / ".github/workflows/publish-complete.yml").read_text(
            encoding="utf-8")
        self.assertIn("fetch-depth: 1", complete)
        self.assertNotIn("fetch-depth: 0", complete)
        complete += (ROOT / ".github/scripts/prepare-publication-trees.sh").read_text()
        self.assertIn("git fetch --no-tags --depth=1 --filter=blob:none origin", complete)
        self.assertIn("review:refs/remotes/origin/review", complete)
        self.assertIn("main:refs/remotes/origin/main", complete)
        self.assertIn("market:refs/remotes/origin/market", complete)
        self.assertIn('if [ -n "$CACHE_MAIN_OVERRIDE" ]; then', complete)
        self.assertIn("git fetch --no-tags --unshallow origin", complete)

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

    def test_market_requests_do_not_hold_publication_lock_and_publish_reloads_sources(self):
        content = (ROOT / ".github/workflows/publish-complete.yml").read_text(encoding="utf-8")
        header, jobs = content.split("jobs:", 1)
        refresh, publish = jobs.split("  publish:\n", 1)
        self.assertNotIn("concurrency:", header)
        self.assertNotIn("group: publish-snapshot", refresh)
        self.assertIn("group: publish-snapshot", publish)
        self.assertIn("build-alpaca-market-validation", refresh)
        self.assertIn("build-twelve-data-supplement", refresh)
        self.assertNotIn("build-alpaca-market-validation", publish)
        self.assertNotIn("build-twelve-data-supplement", publish)
        self.assertIn("needs.refresh.result == 'skipped'", publish)
        self.assertLess(publish.index("Prepare fixed source"), publish.index("build-disclosure-candidate"))
        self.assertLess(publish.index("build-disclosure-candidate"), publish.index("build_cached_candidate.py"))
        self.assertIn("market_ref=()", publish)
        self.assertIn('market_ref=("$MARKET_SHA:refs/heads/market")', publish)
        self.assertIn("vendor requests: 0", publish)

    def test_rollback_dry_run_verifies_prepared_tree_without_moving_refs(self):
        content = (ROOT / ".github/workflows/rollback.yml").read_text(encoding="utf-8")
        self.assertIn("dry_run:", content)
        self.assertIn("type: boolean", content)
        self.assertIn("default: false", content)
        self.assertIn("Verify prepared rollback without publishing", content)
        self.assertIn('rev-parse HEAD^', content)
        self.assertIn('rev-parse HEAD^{tree}', content)
        self.assertIn('rev-parse "${TARGET}^{tree}"', content)
        self.assertIn('test "$(git ls-remote origin refs/heads/main | cut -f1)" = "$CURRENT"',
                      content)
        self.assertIn('test "$(git ls-remote origin refs/heads/market | cut -f1)" = "$MARKET_BASE"',
                      content)
        self.assertIn("if: steps.commit.outputs.changed == 'true' && !inputs.dry_run",
                      content)


if __name__ == "__main__":
    unittest.main()
