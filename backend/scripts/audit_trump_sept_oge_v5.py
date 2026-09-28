"""Audit the fixed September OGE v4 -> v5 structural row recovery.

The v5 replay must preserve every v4 transaction object, add four omitted
physical rows, promote two source-bound page-12 rows, and remove one OCR header
artifact.  No other v4 extraction row may change.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from unison_snapshot.oge_reports import (
    EXTRACTION_SCHEMA, TRUMP_SEPT_2026_DOCUMENT_ID,
    TRUMP_SEPT_2026_SECOND_PASS_VERSION,
    TRUMP_SEPT_2026_SOURCE_SHA256, TRUMP_SEPT_2026_SOURCE_URL,
    TRUMP_SEPT_2026_STRUCTURAL_PASS_VERSION,
)


V4_TRANSACTIONS = 352
V4_QUARANTINED = 801
V5_TRANSACTIONS = 358
V5_QUARANTINED = 798
FAKE_HEADER_ID = "oge-278t:97438cd2efe2ba17cc80ccb4"
CORRECTED_IDS = {
    "oge-278t:fac97da8e5025227ad0b9139": {
        "row_number": 337,
        "asset_name": "ARTIVION INC",
        "transaction_type": "sale",
    },
    "oge-278t:8b3bb35e34734a1102b4e41e": {
        "row_number": 338,
        "asset_name": "WENDYS CO CLASS A",
        "transaction_type": "sale",
    },
}
RECOVERED_ROWS = [
    {
        "extraction_id": "oge-278t:55c5e492c058bc46ef81d643",
        "page_number": 4, "row_number": 67, "owner": "Self",
        "asset_name": "NATERA INC", "ticker": None,
        "transaction_type_raw": "purchase", "transaction_type": "purchase",
        "transaction_date": "2026-07-17", "late_notification_raw": "No",
        "amount_raw": "$1,001 - $15,000", "amount_low": 1001,
        "amount_high": 15000,
    },
    {
        "extraction_id": "oge-278t:8fce140b9c3fca8985296c35",
        "page_number": 4, "row_number": 68, "owner": "Self",
        "asset_name": "DEXCOM INC", "ticker": None,
        "transaction_type_raw": "purchase", "transaction_type": "purchase",
        "transaction_date": "2026-07-17", "late_notification_raw": "No",
        "amount_raw": "$1,001 - $15,000", "amount_low": 1001,
        "amount_high": 15000,
    },
    {
        "extraction_id": "oge-278t:c4493bc63478b78bdd77113b",
        "page_number": 9, "row_number": 232, "owner": "Self",
        "asset_name": "MORGAN STANLEY", "ticker": None,
        "transaction_type_raw": "sale", "transaction_type": "sale",
        "transaction_date": "2026-07-31", "late_notification_raw": "no",
        "amount_raw": "$50,001 - $100,000", "amount_low": 50001,
        "amount_high": 100000,
    },
    {
        "extraction_id": "oge-278t:faffe360fe76ed247b8c9921",
        "page_number": 9, "row_number": 233, "owner": "Self",
        "asset_name": "BOOKING HLDGS INC", "ticker": None,
        "transaction_type_raw": "sale", "transaction_type": "sale",
        "transaction_date": "2026-07-31", "late_notification_raw": "no",
        "amount_raw": "$50,001 - $100,000", "amount_low": 50001,
        "amount_high": 100000,
    },
]


def _read(path: Path, version: str) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or
            value.get("schema_version") != EXTRACTION_SCHEMA or
            value.get("parser_version") != version or
            value.get("document_id") != TRUMP_SEPT_2026_DOCUMENT_ID or
            value.get("source_url") != TRUMP_SEPT_2026_SOURCE_URL or
            value.get("source_sha256") != TRUMP_SEPT_2026_SOURCE_SHA256 or
            not isinstance(value.get("transactions"), list) or
            not isinstance(value.get("quarantined"), list)):
        raise ValueError(f"September OGE {version} extraction is not bound to the fixed source")
    return value


def _index(value: dict, label: str) -> tuple[dict[str, dict], dict[str, str]]:
    rows: dict[str, dict] = {}
    buckets: dict[str, str] = {}
    for bucket in ("transactions", "quarantined"):
        for row in value[bucket]:
            identifier = row.get("extraction_id") if isinstance(row, dict) else None
            if (not isinstance(identifier, str) or
                    not identifier.startswith("oge-278t:") or identifier in rows):
                raise ValueError(f"{label} has a missing or repeated extraction ID")
            rows[identifier] = row
            buckets[identifier] = bucket
    return rows, buckets


def _corrected_row(old: dict, resolved: dict) -> dict:
    if not isinstance(old.get("reasons"), list):
        raise ValueError("September OGE v4 correction input is not quarantined")
    expected = deepcopy(old)
    del expected["reasons"]
    expected.update(resolved)
    expected["source_bound_row_correction"] = {
        "basis": "fixed_source_visual_table_alignment",
        "original_row_number": old["row_number"],
        "original_asset_name": old["asset_name"],
        "original_transaction_type_raw": old["transaction_type_raw"],
        "resolved_row_number": resolved["row_number"],
        "resolved_asset_name": resolved["asset_name"],
        "resolved_transaction_type": resolved["transaction_type"],
    }
    return expected


def audit(review_root: Path) -> dict:
    directory = (review_root / "oge" / "extractions" /
                 TRUMP_SEPT_2026_DOCUMENT_ID / TRUMP_SEPT_2026_SOURCE_SHA256)
    v4 = _read(directory / "oge-278t-pdf-v4.json",
               TRUMP_SEPT_2026_SECOND_PASS_VERSION)
    v5 = _read(directory / "oge-278t-pdf-v5.json",
               TRUMP_SEPT_2026_STRUCTURAL_PASS_VERSION)
    if (len(v4["transactions"]) != V4_TRANSACTIONS or
            len(v4["quarantined"]) != V4_QUARANTINED or
            len(v5["transactions"]) != V5_TRANSACTIONS or
            len(v5["quarantined"]) != V5_QUARANTINED):
        raise ValueError("September OGE v4/v5 row counts do not match the fixed replay")
    old_header = {key: value for key, value in v4.items()
                  if key not in {"parser_version", "transactions", "quarantined"}}
    new_header = {key: value for key, value in v5.items()
                  if key not in {"parser_version", "transactions", "quarantined"}}
    if old_header != new_header:
        raise ValueError("September OGE v5 changed a document field beyond parser version")

    old_rows, old_bucket = _index(v4, "v4")
    new_rows, new_bucket = _index(v5, "v5")
    recovered_by_id = {row["extraction_id"]: row for row in RECOVERED_ROWS}
    expected_ids = (set(old_rows) - {FAKE_HEADER_ID}) | set(recovered_by_id)
    if set(new_rows) != expected_ids:
        raise ValueError("September OGE v5 added or lost an unexpected extraction ID")

    for identifier, old in old_rows.items():
        if old_bucket[identifier] == "transactions":
            if new_bucket.get(identifier) != "transactions" or new_rows[identifier] != old:
                raise ValueError("September OGE v5 changed an existing v4 transaction")
        elif identifier == FAKE_HEADER_ID:
            if identifier in new_rows:
                raise ValueError("September OGE v5 retained the page-34 header artifact")
        elif identifier in CORRECTED_IDS:
            expected = _corrected_row(old, CORRECTED_IDS[identifier])
            if new_bucket.get(identifier) != "transactions" or new_rows[identifier] != expected:
                raise ValueError("September OGE v5 row alignment correction changed")
        elif new_bucket.get(identifier) != "quarantined" or new_rows[identifier] != old:
            raise ValueError("September OGE v5 changed an unrelated quarantine row")

    for identifier, base in recovered_by_id.items():
        expected = {
            **base,
            "source_bound_row_recovery": {
                "basis": "fixed_source_visual_table_row_recovery",
                "page_number": base["page_number"],
                "printed_row_number": base["row_number"],
            },
        }
        if new_bucket.get(identifier) != "transactions" or new_rows[identifier] != expected:
            raise ValueError("September OGE v5 recovered row evidence changed")

    original_ids = [row["extraction_id"] for row in v4["transactions"]]
    if [row["extraction_id"] for row in v5["transactions"][:V4_TRANSACTIONS]] != original_ids:
        raise ValueError("September OGE v5 reordered an existing v4 transaction")
    return {
        "document_id": TRUMP_SEPT_2026_DOCUMENT_ID,
        "source_sha256": TRUMP_SEPT_2026_SOURCE_SHA256,
        "v4_transaction_count": V4_TRANSACTIONS,
        "v4_quarantined_count": V4_QUARANTINED,
        "v5_transaction_count": V5_TRANSACTIONS,
        "v5_quarantined_count": V5_QUARANTINED,
        "preserved_v4_transaction_count": V4_TRANSACTIONS,
        "recovered_missing_row_count": len(RECOVERED_ROWS),
        "promoted_aligned_row_count": len(CORRECTED_IDS),
        "removed_header_artifact_count": 1,
        "disposition_entry_count": V5_TRANSACTIONS + V5_QUARANTINED,
        "disposition_entry_conservation_complete": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-root", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.review_root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
