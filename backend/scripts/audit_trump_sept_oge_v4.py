"""Audit the fixed September OGE v3 -> v4 sale-OCR correction, row by row.

The only accepted change to a source row is recognizing the exact raw type
``salo`` as a sale.  Corrected rows retain their original six OCR cells even
when they move from quarantine to the transaction array.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from unison_snapshot.oge_reports import (
    EXTRACTION_SCHEMA, TRUMP_SEPT_2026_DOCUMENT_ID,
    TRUMP_SEPT_2026_PARSER_VERSION, TRUMP_SEPT_2026_SECOND_PASS_VERSION,
    TRUMP_SEPT_2026_SOURCE_SHA256,
    TRUMP_SEPT_2026_SOURCE_URL,
)


V4_PARSER_VERSION = TRUMP_SEPT_2026_SECOND_PASS_VERSION
V3_TRANSACTIONS = 228
V3_QUARANTINED = 925
V4_TRANSACTIONS = 352
V4_QUARANTINED = 801
CORRECTED_SALO = 259
PROMOTED_SALO = 124


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


def _indexed_rows(value: dict, label: str) -> tuple[dict[str, dict], dict[str, str]]:
    rows: dict[str, dict] = {}
    disposition: dict[str, str] = {}
    for bucket in ("transactions", "quarantined"):
        for row in value[bucket]:
            if not isinstance(row, dict):
                raise ValueError(f"{label} contains a non-object row")
            identifier = row.get("extraction_id")
            if (not isinstance(identifier, str) or
                    not identifier.startswith("oge-278t:") or
                    identifier in rows):
                raise ValueError(f"{label} has a missing or repeated extraction ID")
            rows[identifier] = row
            disposition[identifier] = bucket
    return rows, disposition


def _expected_correction(old: dict, *, promoted: bool) -> dict:
    if (old.get("transaction_type_raw") != "salo" or
            old.get("transaction_type") is not None or
            not isinstance(old.get("reasons"), list) or
            "transaction_type_unsupported" not in old["reasons"] or
            not isinstance(old.get("cells"), list) or
            len(old["cells"]) != 6):
        raise ValueError("September OGE v3 salo row is not a supported correction input")
    expected = deepcopy(old)
    expected["transaction_type"] = "sale"
    expected["type_ocr_correction"] = {
        "basis": "fixed_source_salo_ocr_normalization",
        "raw_type": "salo",
        "resolved_type": "sale",
        "original_reasons": list(old["reasons"]),
    }
    remaining = sorted(set(old["reasons"]) - {"transaction_type_unsupported"})
    if promoted:
        if remaining:
            raise ValueError("September OGE v4 promoted a row with other quarantine reasons")
        del expected["reasons"]
    else:
        if not remaining:
            raise ValueError("September OGE v4 kept a fully repaired row in quarantine")
        expected["reasons"] = remaining
    return expected


def audit(review_root: Path) -> dict:
    directory = (review_root / "oge" / "extractions" /
                 TRUMP_SEPT_2026_DOCUMENT_ID / TRUMP_SEPT_2026_SOURCE_SHA256)
    v3 = _read(directory / (TRUMP_SEPT_2026_PARSER_VERSION.replace("/", "-") + ".json"),
               TRUMP_SEPT_2026_PARSER_VERSION)
    v4 = _read(directory / (V4_PARSER_VERSION.replace("/", "-") + ".json"),
               V4_PARSER_VERSION)
    if (len(v3["transactions"]) != V3_TRANSACTIONS or
            len(v3["quarantined"]) != V3_QUARANTINED or
            len(v4["transactions"]) != V4_TRANSACTIONS or
            len(v4["quarantined"]) != V4_QUARANTINED):
        raise ValueError("September OGE v3/v4 row counts do not match the fixed replay")
    old_header = {key: value for key, value in v3.items()
                  if key not in {"parser_version", "transactions", "quarantined"}}
    new_header = {key: value for key, value in v4.items()
                  if key not in {"parser_version", "transactions", "quarantined"}}
    if old_header != new_header:
        raise ValueError("September OGE v4 changed a document field beyond parser version")
    old_rows, old_bucket = _indexed_rows(v3, "v3")
    new_rows, new_bucket = _indexed_rows(v4, "v4")
    if old_rows.keys() != new_rows.keys():
        raise ValueError("September OGE v4 added or lost an extraction ID")

    original_transaction_ids = [row["extraction_id"] for row in v3["transactions"]]
    if [row["extraction_id"] for row in v4["transactions"]
            if row["extraction_id"] in set(original_transaction_ids)] != original_transaction_ids:
        raise ValueError("September OGE v4 reordered an original qualified row")
    repaired = promoted = 0
    for identifier, old in old_rows.items():
        current = new_rows[identifier]
        if old_bucket[identifier] == "transactions":
            if new_bucket[identifier] != "transactions" or current != old:
                raise ValueError("September OGE v4 changed an original qualified row")
            continue
        if old.get("transaction_type_raw") != "salo":
            if new_bucket[identifier] != "quarantined" or current != old:
                raise ValueError("September OGE v4 changed a non-salo quarantined row")
            continue
        is_promoted = new_bucket[identifier] == "transactions"
        expected = _expected_correction(old, promoted=is_promoted)
        if current != expected:
            raise ValueError("September OGE v4 changed a salo row beyond type and reasons")
        repaired += 1
        promoted += is_promoted
    if repaired != CORRECTED_SALO or promoted != PROMOTED_SALO:
        raise ValueError("September OGE v4 sale correction counts do not close")
    return {
        "document_id": TRUMP_SEPT_2026_DOCUMENT_ID,
        "source_sha256": TRUMP_SEPT_2026_SOURCE_SHA256,
        "v3_transaction_count": V3_TRANSACTIONS,
        "v3_quarantined_count": V3_QUARANTINED,
        "v4_transaction_count": V4_TRANSACTIONS,
        "v4_quarantined_count": V4_QUARANTINED,
        "corrected_salo_count": repaired,
        "newly_promoted_count": promoted,
        "row_conservation_complete": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-root", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.review_root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
