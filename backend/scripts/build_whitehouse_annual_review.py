"""Materialize source-qualified and blocked White House annual holdings in review."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from unison_snapshot.whitehouse_annual_review import build_annual_review


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-root", required=True, type=Path)
    args = parser.parse_args()
    root = args.review_root
    raw = (root / "whitehouse/coverage-current.json").read_bytes()
    result = build_annual_review(json.loads(raw), root,
                                 coverage_sha256=hashlib.sha256(raw).hexdigest())
    target = root / "whitehouse/annual/filer-reported-current.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")), encoding="utf-8")
    print(json.dumps({key: result[key] for key in
                      ("report_count", "holding_count", "owner_counts",
                       "source_eligible_report_count", "source_eligible_holding_count")},
                     sort_keys=True))


if __name__ == "__main__":
    main()
