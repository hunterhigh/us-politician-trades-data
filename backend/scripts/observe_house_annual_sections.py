"""Write a House annual Schedule A/B sidecar from archived PDF and extraction."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.house_annual_sections import observe_archived_annual_pdf


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--extraction", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    extraction = json.loads(args.extraction.read_text(encoding="utf-8"))
    observation = observe_archived_annual_pdf(args.pdf, extraction)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(observation, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"schedule_a_rows": observation["schedule_a_extraction_row_count"],
                      "schedule_a_parent_leaf_rows": observation["schedule_a_parent_leaf_row_count"],
                      "schedule_b_date_anchored_rows": observation["schedule_b_date_anchored_row_count"],
                      "production_eligible_transactions": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
