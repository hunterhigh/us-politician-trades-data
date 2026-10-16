"""Probe the date column of saved 2024 OGE 278-T physical slots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.oge_278t_date_column_shadow import observe_date_column


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--slots", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = observe_date_column(
        args.pdf, slots_observation=json.loads(args.slots.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"source_sha256": result["source_sha256"],
                      "summary": result["summary"]}, sort_keys=True))


if __name__ == "__main__":
    main()
