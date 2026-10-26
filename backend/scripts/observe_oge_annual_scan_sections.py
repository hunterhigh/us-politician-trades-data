"""Generate a read-only OGE scanned annual section ledger from an archived PDF."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.oge_annual_scan_sections import observe_scan_sections, observe_holding_pages
from unison_snapshot.oge_annual_runtime import controlled_scan_output_path, guard_scan_observation_output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--document-id", required=True)
    outputs = parser.add_mutually_exclusive_group(required=True)
    outputs.add_argument("--output", type=Path)
    outputs.add_argument("--review-root", type=Path,
                         help="Write the versioned observation under the controlled annual layout")
    parser.add_argument("--holding-pages", type=int, nargs="+",
                        help="Only these previously located native Parts 2/5/6 pages; no other page text/OCR")
    args = parser.parse_args()
    output = (controlled_scan_output_path(args.review_root,
        'holding_page_observations' if args.holding_pages is not None else 'section_observation',
        args.document_id,args.source_sha256) if args.review_root is not None else args.output)
    guard_scan_observation_output(output,args.pdf)
    result = (observe_holding_pages(args.pdf, source_sha256=args.source_sha256,
                                   document_id=args.document_id, page_numbers=args.holding_pages)
              if args.holding_pages is not None else
              observe_scan_sections(args.pdf, source_sha256=args.source_sha256, document_id=args.document_id))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")), encoding="utf-8")
    print(json.dumps(({"source_sha256": result["source_sha256"],
                       "observed_holding_pages": [row["page_number"] for row in result["pages"]],
                       "production_eligible_transaction_count": 0} if args.holding_pages is not None else
                     {"source_sha256": result["source_sha256"],
                      "section_pages": result["section_pages"],
                      "part7_slot_counts": result["part7_slot_counts"],
                      "production_eligible_transaction_count": 0}), sort_keys=True))


if __name__ == "__main__":
    main()
