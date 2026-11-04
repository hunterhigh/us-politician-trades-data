#!/usr/bin/env bash
set -euo pipefail

[[ "$PREFLIGHT_RUN_ID" =~ ^[0-9]+$ ]]
refs=("refs/heads/preflight/$PREFLIGHT_RUN_ID/main" "refs/heads/preflight/$PREFLIGHT_RUN_ID/market")
expected=("$PREFLIGHT_MAIN_SHA" "$PREFLIGHT_MARKET_SHA")
leases=()
deletions=()
for index in 0 1; do
  ref="${refs[$index]}"
  sha="${expected[$index]}"
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]]
  current="$(git ls-remote origin "$ref" | cut -f1)"
  if [ -z "$current" ]; then
    continue
  fi
  if [ "$current" != "$sha" ]; then
    echo "Preflight ref changed; refusing to delete $ref" >&2
    exit 1
  fi
  leases+=("--force-with-lease=$ref:$sha")
  deletions+=(":$ref")
done
if [ "${#deletions[@]}" -gt 0 ]; then
  git push --atomic "${leases[@]}" origin "${deletions[@]}"
fi
