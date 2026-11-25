set -euo pipefail

profiles="$RUNNER_TEMP/profiles"
mkdir -p "$profiles"
TIMEFORMAT='%3R seconds'
measure() {
  local name="$1"
  shift
  echo "Timing $name"
  if [ "$PROFILE" = true ]; then
    time python -m cProfile -o "$profiles/$name.prof" -m "$@"
  else
    time python -m "$@"
  fi
}

case "$TARGET" in
  house-select)
    measure house-select unison_snapshot house-review select --year "$(date -u +%Y)"
    ;;
  house-candidate)
    candidate="$REVIEW_ROOT/candidates/sources/house_clerk-current.json"
    measure build-house-candidate unison_snapshot build-house-candidate \
      --review-root "$REVIEW_ROOT" --evidence-root "$EVIDENCE_ROOT" \
      --save-observations \
      --state-status "$STATE_ROOT/status/house_clerk.json" --base "$candidate" --output "$candidate"
    cp "$candidate" "$REVIEW_ROOT/candidates/house-current.json"
    assembly_profile=()
    if [ "$PROFILE" = true ]; then assembly_profile=(--profile-dir "$profiles"); fi
    measure assemble-source unison_snapshot assemble-source --local-only "${assembly_profile[@]}" \
      --workspace "$RUNNER_TEMP/house-source" --source house_clerk \
      --base backend/data/empty-disclosure.json --builder-commit "$GITHUB_SHA" \
      --html-output "$RUNNER_TEMP/disclosure-current.html"
    ;;
  publish-snapshot)
    python -m unison_snapshot build-cached-candidate \
      --input "$RUNNER_TEMP/governed-disclosure.json" --main-root "$PUBLISH_MAIN" \
      --market-root "$PUBLISH_CACHE_MARKET" --market-commit "$PUBLISH_MARKET_SHA" \
      --output-dir "$RUNNER_TEMP"
    board_sha="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["board"])' "$PUBLISH_MAIN/manifest.json")"
    python -m unison_snapshot.fact_policy.publication_delta \
      --previous-board "$PUBLISH_MAIN/board/$board_sha.json" \
      --prepared "$RUNNER_TEMP/complete-candidate.json" --main-commit "$PUBLISH_MAIN_SHA" \
      --output "$RUNNER_TEMP/publication-changes.json"
    generated_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    measure build-disclosure-status unison_snapshot build-disclosure-status \
      --evidence-root "$PUBLISH_EVIDENCE" --review-root "$PUBLISH_REVIEW" \
      --input "$RUNNER_TEMP/complete-candidate.json" --code-commit "$GITHUB_SHA" \
      --previous-main-commit "$PUBLISH_MAIN_SHA" --output "$RUNNER_TEMP/disclosure-status-input.json"
    measure prepare-publication unison_snapshot prepare-publication \
      --input "$RUNNER_TEMP/complete-candidate.json" --root "$PUBLISH_MAIN" \
      --generated-at "$generated_at" --allow-market --market-commit "$PUBLISH_MARKET_SHA" \
      --market-pages-file "$RUNNER_TEMP/market-publication.json" \
      --consumer-validation-file "$RUNNER_TEMP/consumer-validation.json" \
      --disclosure-status-input "$RUNNER_TEMP/disclosure-status-input.json" \
      --publication-changes "$RUNNER_TEMP/publication-changes.json" \
      --publication-inputs "$PUBLISH_REVIEW_SHA" "$PUBLISH_STATE_SHA" "$PUBLISH_MARKET_SHA" "$GITHUB_SHA"
    ;;
  health)
    bash .github/scripts/prepare-health-trees.sh
    measure health unison_snapshot.production_health \
      --main-root "$RUNNER_TEMP/health-main" --state-root "$RUNNER_TEMP/health-state" \
      --health-root "$RUNNER_TEMP/health-output" --repository "$GITHUB_REPOSITORY" \
      --observe-publication-runs --observe-assembly --observe-source-quality \
      --smoke-client-root "$GITHUB_WORKSPACE"
    ;;
esac
