"""Write a read-only Senate paper slot sidecar from the current archived batch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.senate_paper_observation import compare_archived_batch, project_pending_slots


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--docs-root", required=True, type=Path)
    parser.add_argument("--extraction-batch", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pending-output", type=Path)
    args = parser.parse_args()
    batch = json.loads(args.extraction_batch.read_text(encoding="utf-8"))
    result = compare_archived_batch(args.evidence_root, args.docs_root, batch)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")) + "\n", encoding="utf-8")
    if args.pending_output is not None:
        pending = project_pending_slots(result)
        args.pending_output.parent.mkdir(parents=True, exist_ok=True)
        args.pending_output.write_text(json.dumps(pending, ensure_ascii=False,
                                                 sort_keys=True, separators=(",", ":")) + "\n",
                                       encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "report_count", "observed_report_count", "coverage_unknown_report_count",
        "physical_grid_slot_count", "observed_page_count", "unobserved_page_count",
        "label_counts")}, sort_keys=True))


if __name__ == "__main__":
    main()
