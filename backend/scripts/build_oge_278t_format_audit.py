"""Build a local, source-bound 278-T page audit without publishing rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.oge_278t_format_audit import audit_278t_format


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--extraction", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--pages", type=int, nargs="+")
    parser.add_argument("--raster-census", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    extraction = (json.loads(args.extraction.read_text(encoding="utf-8"))
                  if args.extraction else None)
    result = audit_278t_format(
        args.pdf, extraction=extraction, page_numbers=args.pages,
        expected_sha256=args.expected_sha256,
        raster_census=args.raster_census)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")), encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "source_sha256", "page_count", "selected_page_numbers",
        "selected_row_count", "unselected_page_row_count", "pages_with_issues")},
        sort_keys=True))


if __name__ == "__main__":
    main()
