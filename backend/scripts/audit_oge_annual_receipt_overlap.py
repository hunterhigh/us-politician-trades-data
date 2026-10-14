"""Replay the fixed OGE annual receipt/278-T blocker matrix without promotion."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.oge_annual_receipt_overlap import audit_receipt_overlap


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--date-batch", type=Path, required=True)
    parser.add_argument("--harris-part7", type=Path, required=True)
    parser.add_argument("--fixed-metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    date_batch = json.loads(args.date_batch.read_text(encoding="utf-8"))
    result = audit_receipt_overlap(
        args.catalog.read_bytes(), date_batch["reports"],
        json.loads(args.harris_part7.read_text(encoding="utf-8")),
        json.loads(args.fixed_metadata.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"receipt_verified": result["verified_agency_receipt_count"],
                      "harris_part7_pending": result["harris_part7_filled_row_count"],
                      "harris_278t_catalog_candidates": result[
                          "harris_same_filer_278t_catalog_count"],
                      "production_eligible": result[
                          "production_eligible_transaction_count"]}))


if __name__ == "__main__":
    main()
