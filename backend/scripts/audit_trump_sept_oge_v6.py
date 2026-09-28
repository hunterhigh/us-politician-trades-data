"""Audit the fixed September OGE v5 -> v6 page-7 disposition change.

The source PDF's page 7 visibly contains one transaction at each printed
number 166-198.  V6 changes only the 25 v5 quarantined positions on that
page; it neither reinterprets other pages nor claims the rest of the report
is complete.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from unison_snapshot.oge_reports import (
    EXTRACTION_SCHEMA, TRUMP_SEPT_2026_DOCUMENT_ID,
    TRUMP_SEPT_2026_PAGE7_PASS_VERSION,
    TRUMP_SEPT_2026_SOURCE_SHA256, TRUMP_SEPT_2026_SOURCE_URL,
    TRUMP_SEPT_2026_STRUCTURAL_PASS_VERSION,
)


PAGE7_IDS = {
    168: "oge-278t:2d10674fb4edd6281d71c093",
    171: "oge-278t:064972d2b35aa0ecb7afcac7",
    172: "oge-278t:4092d776ea20223d8fd66db2",
    173: "oge-278t:48c9e48142ef368c5e39257f",
    177: "oge-278t:6ecc20e5caf44564da48f847",
    178: "oge-278t:608461166207f9b9f333241d",
    179: "oge-278t:d85eb1033dcb46f6eae29dac",
    180: "oge-278t:7c8ffd7b6242a58c7d1a5032",
    181: "oge-278t:956775223c4e229d063b5e3b",
    182: "oge-278t:8aac3a79323a7895873e39e8",
    183: "oge-278t:b943958da9dc84e6a68f9be8",
    184: "oge-278t:0d28d77025535d2a2e153796",
    185: "oge-278t:2fb6cb5849c64fb3638c3281",
    186: "oge-278t:c4baa50416748b28c6a9a4f4",
    187: "oge-278t:4cedc372692338cbdc10c413",
    188: "oge-278t:3a91de09bb8ecf07895a2bff",
    189: "oge-278t:f36c5e73803e09c2e922f9b2",
    190: "oge-278t:405a6b7a46fadfe90305bc1a",
    191: "oge-278t:6c61768be2be46745375f278",
    192: "oge-278t:29692e04a5589bb6a7e5a9cf",
    193: "oge-278t:d2fb936c42e3ac4f9131fd80",
    194: "oge-278t:a7b9036a2d9aff70f4e806c4",
    195: "oge-278t:aa271fa48fca255468def560",
    196: "oge-278t:73b3a93109bb2e3f9e54255d",
    197: "oge-278t:3cd37932c7dfcbc3af1f64e9",
}

SPECIAL_FIELDS = {
    168: {"amount_low": 250001, "amount_high": 500000},
    171: {"transaction_date": "2026-07-08", "late_notification_raw": "Yes"},
    177: {"row_number": 177, "transaction_type": "purchase",
          "late_notification_raw": "Yes", "amount_low": 1000001,
          "amount_high": 5000000},
    191: {"row_number": 191, "asset_name": "WATSCO INC CLASS A",
          "transaction_type": "purchase"},
}


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
        raise ValueError(f"September OGE {version} extraction is not source-bound")
    return value


def audit(review_root: Path) -> dict:
    directory = (review_root / "oge" / "extractions" /
                 TRUMP_SEPT_2026_DOCUMENT_ID / TRUMP_SEPT_2026_SOURCE_SHA256)
    v5 = _read(directory / "oge-278t-pdf-v5.json",
               TRUMP_SEPT_2026_STRUCTURAL_PASS_VERSION)
    v6 = _read(directory / "oge-278t-pdf-v6.json",
               TRUMP_SEPT_2026_PAGE7_PASS_VERSION)
    if (len(v5["transactions"]) != 358 or len(v5["quarantined"]) != 798 or
            len(v6["transactions"]) != 383 or len(v6["quarantined"]) != 773):
        raise ValueError("September OGE v5/v6 disposition counts changed")
    if ({key: value for key, value in v5.items()
         if key not in {"parser_version", "transactions", "quarantined"}} !=
            {key: value for key, value in v6.items()
             if key not in {"parser_version", "transactions", "quarantined"}}):
        raise ValueError("September OGE v6 changed a document field")
    if v6["transactions"][:358] != v5["transactions"]:
        raise ValueError("September OGE v6 changed or reordered a v5 transaction")

    old_quarantine = v5["quarantined"]
    old_ids = [row.get("extraction_id") for row in
               v5["transactions"] + old_quarantine]
    if any(not isinstance(identifier, str) for identifier in old_ids) or \
            len(old_ids) != len(set(old_ids)):
        raise ValueError("September OGE v5 extraction IDs are not unique")
    target_ids = set(PAGE7_IDS.values())
    page7_quarantine = [row for row in old_quarantine
                        if row.get("page_number") == 7]
    if {row.get("extraction_id") for row in page7_quarantine} != target_ids or \
            len(page7_quarantine) != 25:
        raise ValueError("September OGE v5 page-7 target set changed")
    expected_remaining = [row for row in old_quarantine
                          if row.get("extraction_id") not in target_ids]
    if v6["quarantined"] != expected_remaining:
        raise ValueError("September OGE v6 changed an unrelated quarantine row")

    original_by_id = {row["extraction_id"]: row for row in page7_quarantine}
    new_rows = v6["transactions"][358:]
    if [row.get("extraction_id") for row in new_rows] != \
            [PAGE7_IDS[number] for number in sorted(PAGE7_IDS)]:
        raise ValueError("September OGE v6 changed promoted ID or order")
    for number, identifier in sorted(PAGE7_IDS.items()):
        original = original_by_id[identifier]
        resolved = SPECIAL_FIELDS.get(number, {"transaction_type": "purchase"})
        expected = deepcopy(original)
        reasons = expected.pop("reasons")
        expected.update(resolved)
        expected["source_bound_page7_correction"] = {
            "basis": "fixed_source_visual_page7_table",
            "page_number": 7,
            "printed_row_number": number,
            "original_reasons": reasons,
            "resolved_fields": resolved,
        }
        row = new_rows[sorted(PAGE7_IDS).index(number)]
        if row != expected or row.get("page_number") != 7 or \
                row.get("row_number") != number:
            raise ValueError(f"September OGE v6 row #{number} correction changed")

    all_v6 = v6["transactions"] + v6["quarantined"]
    if [row.get("extraction_id") for row in all_v6] != \
            [row.get("extraction_id") for row in v5["transactions"]] + \
            [PAGE7_IDS[number] for number in sorted(PAGE7_IDS)] + \
            [row.get("extraction_id") for row in expected_remaining]:
        raise ValueError("September OGE v6 extraction ID conservation failed")
    for number in range(166, 199):
        matches = [row for row in all_v6 if row.get("row_number") == number]
        if len(matches) != 1 or matches[0].get("page_number") != 7:
            raise ValueError("September OGE v6 page-7 printed positions are not unique")
    if {row.get("row_number") for row in all_v6
            if row.get("page_number") == 7} != set(range(166, 199)):
        raise ValueError("September OGE v6 page-7 printed positions are incomplete")
    return {
        "document_id": TRUMP_SEPT_2026_DOCUMENT_ID,
        "source_sha256": TRUMP_SEPT_2026_SOURCE_SHA256,
        "v5_transaction_count": 358,
        "v5_quarantined_count": 798,
        "v6_transaction_count": 383,
        "v6_quarantined_count": 773,
        "preserved_v5_transaction_count": 358,
        "promoted_page7_count": 25,
        "preserved_other_quarantine_count": 773,
        "page7_printed_position_count": 33,
        "disposition_entry_count": 1156,
        "page7_disposition_conservation_complete": True,
        "promoted_rows": [
            {"extraction_id": row["extraction_id"],
             "page_number": row["page_number"],
             "row_number": row["row_number"],
             "asset_name": row["asset_name"],
             "transaction_date": row["transaction_date"]}
            for row in new_rows
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-root", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.review_root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
