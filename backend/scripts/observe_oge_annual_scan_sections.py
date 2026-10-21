"""Generate a read-only OGE scanned annual section ledger from an archived PDF."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.oge_annual_scan_sections import observe_scan_sections, observe_holding_pages


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--holding-pages", type=int, nargs="+",
                        help="Only these previously located native Parts 2/5/6 pages; no other page text/OCR")
    args = parser.parse_args()
    result = (observe_holding_pages(args.pdf, source_sha256=args.source_sha256,
                                   document_id=args.document_id, page_numbers=args.holding_pages)
              if args.holding_pages is not None else
              observe_scan_sections(args.pdf, source_sha256=args.source_sha256, document_id=args.document_id))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
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
