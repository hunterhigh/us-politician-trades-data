"""Qualify the source-reviewed portion of one archived House paper PTR."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.house_ptr_fixed_paper import qualify_fixed_house_paper_paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--review-root", required=True, type=Path)
    parser.add_argument("--source-review", required=True, type=Path)
    args = parser.parse_args()
    result = qualify_fixed_house_paper_paths(
        args.evidence_root, args.review_root, args.source_review)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
