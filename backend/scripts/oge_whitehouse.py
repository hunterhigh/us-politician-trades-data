"""Build White House disclosure coverage from archived official OGE pages."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.oge_whitehouse import (
    archive_direct_annual_batch, coverage_from_archive, latest_catalog_metadata,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    coverage = sub.add_parser("coverage")
    coverage.add_argument("--evidence-root", type=Path, required=True)
    coverage.add_argument("--output", type=Path, required=True)
    annual = sub.add_parser("archive-annual")
    annual.add_argument("--evidence-root", type=Path, required=True)
    annual.add_argument("--coverage", type=Path, required=True)
    annual.add_argument("--limit", type=int, default=2)
    annual.add_argument("--start-after-id")
    annual.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "coverage":
        result = coverage_from_archive(args.evidence_root,
                                       latest_catalog_metadata(args.evidence_root))
    else:
        catalog = json.loads(args.coverage.read_text(encoding="utf-8"))
        result = archive_direct_annual_batch(args.evidence_root, catalog, limit=args.limit,
                                             start_after_id=args.start_after_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()),
                      "counts": result.get("counts"),
                      "archived": result.get("archived_count"),
                      "pending": result.get("pending_count")}))


if __name__ == "__main__":
    main()
