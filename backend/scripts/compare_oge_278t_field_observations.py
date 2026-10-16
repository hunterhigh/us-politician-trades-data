"""Compare saved 278-T OCR field shapes on matching physical slots only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.oge_278t_field_shadow import compare_field_observations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--left", type=Path, required=True)
    parser.add_argument("--right", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare_field_observations(
        json.loads(args.left.read_text(encoding="utf-8")),
        json.loads(args.right.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"source_sha256": result["source_sha256"],
                      "compared_page_count": result["compared_page_count"],
                      "left_only_pages": result["left_only_pages"],
                      "right_only_pages": result["right_only_pages"],
                      "summary": result["summary"]}, sort_keys=True))


if __name__ == "__main__":
    main()
