"""Generate a read-only OGE scanned annual section ledger from an archived PDF."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.oge_annual_scan_sections import observe_scan_sections


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = observe_scan_sections(args.pdf, source_sha256=args.source_sha256,
                                   document_id=args.document_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"source_sha256": result["source_sha256"],
                      "section_pages": result["section_pages"],
                      "part7_slot_counts": result["part7_slot_counts"],
                      "production_eligible_transaction_count": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
