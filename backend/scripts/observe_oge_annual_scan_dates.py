"""Replay six annual-catalog cover date blocks, including one amendment letter."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_annual_scan_dates import (cover_observations,
                                                    observe_scan_date_blocks)


def observe_fixed_batch(evidence_root: Path) -> dict:
    results = []
    for document_id, (sha, _, _) in sorted(cover_observations().items()):
        pdf = (evidence_root / "oge" / "annual" / "reports" /
               document_id / f"{sha}.pdf")
        try:
            results.append(observe_scan_date_blocks(
                pdf, source_sha256=sha, document_id=document_id))
        except (OSError, ValueError, OgeCatalogError) as error:
            results.append({"source_document_id": document_id,
                            "source_sha256": sha, "status": "coverage_unknown",
                            "reason": str(error), "agency_receipt_verified": False,
                            "filed_at": None, "part7_filing_date_unverified": True,
                            "visual_transcription_is_automatic_fact": False,
                            "production_eligible_transaction_count": 0})
    return {"schema_version": "oge-278e-scan-date-batch/v1",
            "source_scope": "six_fixed_annual_catalog_entries_including_amendment_letter",
            "reports": results,
            "verified_agency_receipt_count": sum(
                result["agency_receipt_verified"] for result in results),
            "production_eligible_transaction_count": 0}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = observe_fixed_batch(args.evidence_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"reports": len(result["reports"]),
                      "verified_agency_receipt_count": 0,
                      "production_eligible_transaction_count": 0}))


if __name__ == "__main__":
    main()
