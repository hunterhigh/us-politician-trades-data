"""Summarize a saved House paper cell observation without OCR or publication."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.house_ptr_format_audit import build_house_ptr_format_audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists")
    observation = json.loads(args.observation.read_text(encoding="utf-8"))
    result = build_house_ptr_format_audit(observation)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "source_sha256", "page_count", "physical_slot_count",
        "coverage_unknown_page_count", "raw_fields_complete_unverified_count")},
        sort_keys=True))


if __name__ == "__main__":
    main()
