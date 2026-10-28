"""Materialize filer-attributed White House review rows without publication gates."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from unison_snapshot.whitehouse_reported import build_filer_reported_index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-root", type=Path, required=True)
    args = parser.parse_args(argv)
    path = args.review_root / "whitehouse/coverage-current.json"
    try:
        raw = path.read_bytes()
        coverage = json.loads(raw)
        result = build_filer_reported_index(
            coverage, args.review_root, coverage_sha256=hashlib.sha256(raw).hexdigest())
        target = args.review_root / "whitehouse/filer-reported-current.json"
        target.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")), encoding="utf-8")
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"White House filer-reported index failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({key: result[key] for key in ("report_count", "record_count",
                                                 "record_counts_by_source_field")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
