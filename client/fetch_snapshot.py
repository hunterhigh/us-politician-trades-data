#!/usr/bin/env python3
"""Fetch and process one snapshot directly from a fixed public GitHub repository."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

# Allow this checked-in entry point to run directly from any working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = PROJECT_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from unison_snapshot.legacy import load
from snapshot_repo import HTTPTransport, PublicSnapshotError, PublicSnapshotRepository


def write_atomic(path: Path, value: dict) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["dashboard", "search", "person", "ticker"])
    parser.add_argument("--value")
    parser.add_argument("--owner", default=os.environ.get("POLITICIAN_DATA_GITHUB_OWNER"))
    parser.add_argument("--repo", default=os.environ.get("POLITICIAN_DATA_GITHUB_REPO"))
    parser.add_argument("--ref", choices=["main", "demo"], default="main")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    if not args.owner or not args.repo:
        parser.error("configure --owner/--repo or POLITICIAN_DATA_GITHUB_OWNER/REPO")
    if args.mode in {"person", "ticker"} and not args.value:
        parser.error(f"{args.mode} requires --value")
    try:
        repository = PublicSnapshotRepository(args.owner, args.repo, ref=args.ref,
            transport=HTTPTransport(os.environ.get("GITHUB_TOKEN"), timeout=args.timeout))
        selected = repository.fetch(args.mode, args.value)
        processed = load("process_snapshot").build_snapshot(selected.snapshot)
        processed["meta"]["snapshot_commit"] = selected.commit
        write_atomic(args.output, processed)
    except (PublicSnapshotError, ValueError, OSError) as exc:
        parser.exit(2, f"snapshot error: {exc}\n")
    print(json.dumps({"snapshot_id": processed["meta"]["snapshot_id"],
                      "snapshot_commit": selected.commit, "output": str(args.output.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
