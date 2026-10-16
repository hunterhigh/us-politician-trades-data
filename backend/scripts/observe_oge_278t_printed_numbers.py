"""Conserve printed row numbers over saved 2024 OGE grid observations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.oge_278t_printed_number_shadow import observe_printed_number_sequence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slots", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = observe_printed_number_sequence([
        json.loads(path.read_text(encoding="utf-8")) for path in args.slots])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"source_sha256": result["source_sha256"],
                      "summary": result["summary"]}, sort_keys=True))


if __name__ == "__main__":
    main()
