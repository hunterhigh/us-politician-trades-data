set -euo pipefail

git fetch --no-tags --depth=1 --filter=blob:none origin \
  +refs/heads/main:refs/remotes/origin/main +refs/heads/state:refs/remotes/origin/state
main="$RUNNER_TEMP/health-main"
state="$RUNNER_TEMP/health-state"
git worktree add --detach --no-checkout "$main" origin/main
git -C "$main" sparse-checkout set --no-cone /manifest.json
git -C "$main" read-tree -mu HEAD
git worktree add --detach --no-checkout "$state" origin/state
git -C "$state" sparse-checkout set --cone status
git -C "$state" read-tree -mu HEAD
