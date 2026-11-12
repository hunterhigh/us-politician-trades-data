"""Run bounded House annual sidecar lane after the normal holding parser."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.house_annual_section_batch import run_section_batch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--review-root", required=True, type=Path)
    parser.add_argument("--new-metadata-list", required=True, type=Path)
    parser.add_argument("--old-limit", type=int, default=2)
    args = parser.parse_args()
    metadata = [Path(line) for line in args.new_metadata_list.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    result = run_section_batch(args.evidence_root, args.review_root, metadata,
                               old_limit=args.old_limit)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
