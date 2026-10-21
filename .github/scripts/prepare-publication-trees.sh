set -euo pipefail
git fetch --no-tags --depth=1 origin \
  review:refs/remotes/origin/review \
  main:refs/remotes/origin/main \
  market:refs/remotes/origin/market \
  evidence:refs/remotes/origin/evidence
review_root="$RUNNER_TEMP/complete-review"
evidence_root="$RUNNER_TEMP/complete-evidence"
market_root="$RUNNER_TEMP/complete-market"
main_root="$RUNNER_TEMP/complete-main"
cache_main_root="$RUNNER_TEMP/cache-main"
cache_market_root="$RUNNER_TEMP/cache-market"
git worktree add --detach "$review_root" origin/review
git worktree add --detach "$evidence_root" origin/evidence
git worktree add --detach "$market_root" origin/market
git worktree add --detach "$main_root" origin/main
if [ -n "$CACHE_MAIN_OVERRIDE" ]; then
  # Historical cache recovery is intentionally allowed to pay for
  # full main/market history. Routine publication keeps every
  # checkout shallow because it only needs the current tips.
  git fetch --no-tags --unshallow origin \
    main:refs/remotes/origin/main \
    market:refs/remotes/origin/market
  cache_main_ref="$CACHE_MAIN_OVERRIDE"
  cache_market_ref="$CACHE_MARKET_OVERRIDE"
else
  cache_main_ref="$(git rev-parse origin/main)"
  cache_market_ref="$(git show "$cache_main_ref:manifest.json" | python -c \
    'import json,sys; print(json.load(sys.stdin)["market_commit"])')"
  if [ "$cache_market_ref" != "$(git rev-parse origin/market)" ]; then
    git fetch --no-tags --unshallow origin \
      main:refs/remotes/origin/main \
      market:refs/remotes/origin/market
  fi
fi
git cat-file -e "$cache_main_ref^{commit}"
git cat-file -e "$cache_market_ref^{commit}"
git merge-base --is-ancestor "$cache_main_ref" origin/main
git merge-base --is-ancestor "$cache_market_ref" origin/market
git worktree add --detach "$cache_main_root" "$cache_main_ref"
git worktree add --detach "$cache_market_root" "$cache_market_ref"
referenced_market="$(python -c \
  'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["market_commit"])' \
  "$cache_main_root/manifest.json")"
test "$referenced_market" = "$(git -C "$cache_market_root" rev-parse HEAD)" || {
  echo "Cache main and market commits are not a published pair." >&2
  exit 2
}
echo "review_root=$review_root" >> "$GITHUB_OUTPUT"
echo "evidence_root=$evidence_root" >> "$GITHUB_OUTPUT"
echo "review_commit=$(git -C "$review_root" rev-parse HEAD)" >> "$GITHUB_OUTPUT"
echo "evidence_commit=$(git -C "$evidence_root" rev-parse HEAD)" >> "$GITHUB_OUTPUT"
echo "market_root=$market_root" >> "$GITHUB_OUTPUT"
echo "main_root=$main_root" >> "$GITHUB_OUTPUT"
echo "cache_main_root=$cache_main_root" >> "$GITHUB_OUTPUT"
echo "cache_market_root=$cache_market_root" >> "$GITHUB_OUTPUT"
echo "cache_market_commit=$cache_market_ref" >> "$GITHUB_OUTPUT"
echo "market_base=$(git -C "$market_root" rev-parse HEAD)" >> "$GITHUB_OUTPUT"
echo "main_base=$(git -C "$main_root" rev-parse HEAD)" >> "$GITHUB_OUTPUT"
