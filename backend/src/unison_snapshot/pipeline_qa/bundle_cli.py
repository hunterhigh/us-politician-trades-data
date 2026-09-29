"""CLI for assembling fixed source runs into one read-only shadow bundle."""
from __future__ import annotations

import argparse
import json
import sys

from .bundle import BundleInputError, build_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify and combine immutable source run artifacts into an offline shadow bundle.")
    parser.add_argument("--run-dir", action="append", required=True,
                        help="Source run directory containing manifest.json and its declared row artifact")
    parser.add_argument("--code-commit", required=True, help="Exact shared 40-character source code SHA")
    parser.add_argument("--run-id", required=True, help="Stable ID for this combined shadow run")
    parser.add_argument("--trigger", choices=("schedule", "workflow_run", "workflow_dispatch", "replay", "local"),
                        default="local", help="How this bundle invocation was started")
    parser.add_argument("--output-dir", required=True, help="New or empty local output directory")
    args = parser.parse_args(argv)
    try:
        manifest = build_bundle(args.run_dir, args.output_dir,
                                run_id=args.run_id, code_commit=args.code_commit,
                                trigger=args.trigger)
    except (BundleInputError, OSError) as exc:
        print(f"Shadow bundle failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"run_id": manifest["run_id"], "source_scope": manifest["source_scope"],
                      "documents": manifest["counts"]["discovered_documents"],
                      "rows": manifest["accounted_rows"], "output_dir": args.output_dir},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
