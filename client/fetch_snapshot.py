#!/usr/bin/env python3
"""Deprecated public client. Production data is available only through the token skill."""
import argparse
import json
import os
from pathlib import Path
import tempfile


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
    parser.add_argument("--expected-commit", help="Require the live head to equal this frozen commit")
    parser.add_argument("--status-output", type=Path, help="Save a typed operational result alongside the output")
    args = parser.parse_args()
    if not args.owner or not args.repo:
        parser.error("configure --owner/--repo or POLITICIAN_DATA_GITHUB_OWNER/REPO")
    if args.mode in {"person", "ticker"} and not args.value:
        parser.error(f"{args.mode} requires --value")
    message = ('Public data reads have stopped: this repository is intentionally private. '
        'This client is deprecated; use the existing token-authenticated skill. '
        'Setting GITHUB_TOKEN on this legacy client does not enable private data downloads.')
    if args.status_output:
        write_atomic(args.status_output, {'status': 'deprecated', 'detail': message,
            'expected_commit': args.expected_commit})
    parser.exit(2, message + '\n')


if __name__ == "__main__":
    raise SystemExit(main())
