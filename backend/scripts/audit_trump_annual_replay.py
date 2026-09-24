"""Fail closed on the fixed Trump annual-report replay and row disposition."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

from unison_snapshot.codec import encode
from unison_snapshot.oge_278e_public import (
    TRUMP_2025_PAGE_COUNT,
    TRUMP_2025_PARSER_VERSION,
    TRUMP_2025_SOURCE_SHA256,
    TRUMP_2025_SOURCE_URL,
)


SCHEMA = "whitehouse-trump-annual-replay-audit/v1"
LEGACY_TREE = "c57ecdcd0e780fa7dc256fae6d0fe728735c8f09"
LEGACY_AUDIT_SHA256 = "291eec853889ebcb102187a7f876fcd624270f33f0bcc5108dd42c90611a995b"
EXPECTED_EXTRACTION_SHA256 = "f44d6c7de59f16111a39a88d7993969e7ba609d08da8201fa36c605268d29547"
EXPECTED_COUNTS = {
    "holdings": 3999,
    "transactions": 6759,
    "excluded": 21,
    "quarantined": 17320,
}
EXPECTED_PART6_COUNTS = {
    "holdings": 3998,
    "transactions": 0,
    "excluded": 4,
    "quarantined": 2320,
}
EXPECTED_PART6_NUMERIC_HOLDING_COUNT = 2075
EXPECTED_PART6_SYNTHETIC_HOLDING_COUNT = 1923
EXPECTED_PART7_COUNTS = {
    "holdings": 0,
    "transactions": 6759,
    "excluded": 0,
    "quarantined": 14526,
}
EXPECTED_PART7_TRANSACTION_TYPES = {"purchase": 4754, "sale": 2005}
EXPECTED_PART7_PAGE_RANGE = (159, 845)
EXPECTED_PART7_QUARANTINE_REASONS = {
    "row_number_ocr_unreadable": 442,
    "transaction_amount_unreadable_or_open": 713,
    "transaction_date_unreadable_or_outside_period": 630,
    "transaction_ocr_confidence_below_threshold": 14201,
    "transaction_type_unreadable": 274,
}


def _rows(extraction: dict, name: str) -> list[dict]:
    rows = extraction.get(name)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Trump replay {name} disposition is invalid")
    return rows


def audit_replay(extraction: dict, *, extraction_sha256: str,
                 legacy_tree: str, legacy_audit_sha256: str) -> dict:
    if extraction_sha256 != EXPECTED_EXTRACTION_SHA256:
        raise ValueError("Trump v8 fixed extraction bytes changed")
    if (extraction.get("source_url") != TRUMP_2025_SOURCE_URL or
            extraction.get("source_sha256") != TRUMP_2025_SOURCE_SHA256 or
            extraction.get("parser_version") != TRUMP_2025_PARSER_VERSION or
            extraction.get("page_count") != TRUMP_2025_PAGE_COUNT):
        raise ValueError("Trump replay is not bound to the fixed source and parser")
    if legacy_tree != LEGACY_TREE or legacy_audit_sha256 != LEGACY_AUDIT_SHA256:
        raise ValueError("Trump replay legacy checkpoint identity changed")
    checkpoint = extraction.get("ocr_checkpoint")
    if (not isinstance(checkpoint, dict) or
            checkpoint.get("reused_shard_count") != 38 or
            checkpoint.get("reused_parser_version") !=
            "whitehouse-278e-hybrid-geometry/v5" or
            checkpoint.get("created_shard_count") != 0 or
            checkpoint.get("pending_page_count") != 0 or
            checkpoint.get("completed_page_count") != TRUMP_2025_PAGE_COUNT):
        raise ValueError("Trump replay did not reuse the complete read-only v5 geometry")
    if (extraction.get("source_row_census_status") != "ocr_detected_rows_only" or
            extraction.get("source_row_census_complete") is not False):
        raise ValueError("Trump replay overstates its OCR-detected row census")

    dispositions = {name: _rows(extraction, name) for name in EXPECTED_COUNTS}
    counts = {name: len(rows) for name, rows in dispositions.items()}
    if counts != EXPECTED_COUNTS:
        raise ValueError(f"Trump replay disposition counts changed: {counts}")
    printed = extraction.get("printed_row_count")
    if printed != sum(counts.values()) or printed != 28099:
        raise ValueError("Trump replay row conservation failed")

    part6 = {name: [row for row in rows if row.get("section") == "part6"]
             for name, rows in dispositions.items()}
    part6_counts = {name: len(rows) for name, rows in part6.items()}
    if part6_counts != EXPECTED_PART6_COUNTS:
        raise ValueError(f"Trump Part 6 disposition counts changed: {part6_counts}")
    part6_rows = [row for rows in part6.values() for row in rows]
    locators = [row.get("source_row_locator") for row in part6_rows]
    scopes = [row.get("account_scope") for row in part6_rows]
    if (any(not isinstance(value, str) or not value for value in locators) or
            any(not isinstance(value, str) or not value for value in scopes)):
        raise ValueError("Trump Part 6 row locator or account scope is missing")
    locator_counts = Counter(locators)
    duplicate_locator_excess = sum(count - 1 for count in locator_counts.values())
    if duplicate_locator_excess:
        raise ValueError("Trump Part 6 physical row locator is duplicated")

    holding_keys = [(row.get("account_scope"), row.get("source_row_locator"))
                    for row in part6["holdings"]]
    qualified_holding_duplicate_count = len(holding_keys) - len(set(holding_keys))
    if qualified_holding_duplicate_count:
        raise ValueError("Trump qualified Part 6 holdings contain duplicate evidence")
    numeric_holding_count = sum(
        1 for row in part6["holdings"]
        if re.fullmatch(r"\d+(?:\.\d+)?", str(row.get("row_number", ""))))
    synthetic_holding_count = sum(
        1 for row in part6["holdings"]
        if str(row.get("row_number", "")).startswith("ocr-p"))
    if (numeric_holding_count != EXPECTED_PART6_NUMERIC_HOLDING_COUNT or
            synthetic_holding_count != EXPECTED_PART6_SYNTHETIC_HOLDING_COUNT or
            numeric_holding_count + synthetic_holding_count !=
            len(part6["holdings"])):
        raise ValueError("Trump Part 6 printed/synthetic holding census changed")

    part7 = {name: [row for row in rows if row.get("section") == "part7"]
             for name, rows in dispositions.items()}
    part7_counts = {name: len(rows) for name, rows in part7.items()}
    if part7_counts != EXPECTED_PART7_COUNTS:
        raise ValueError(f"Trump Part 7 disposition counts changed: {part7_counts}")
    part7_rows = [row for rows in part7.values() for row in rows]
    part7_locators = [row.get("source_row_locator") for row in part7_rows]
    part7_scopes = [row.get("account_scope") for row in part7_rows]
    if (any(not isinstance(value, str) or not value for value in part7_locators) or
            any(not isinstance(value, str) or not value for value in part7_scopes)):
        raise ValueError("Trump Part 7 row locator or account scope is missing")
    if len(part7_locators) != len(set(part7_locators)):
        raise ValueError("Trump Part 7 physical row locator is duplicated")
    part7_pages = [row.get("page_number") for row in part7_rows]
    if (any(type(value) is not int for value in part7_pages) or
            (min(part7_pages), max(part7_pages)) != EXPECTED_PART7_PAGE_RANGE):
        raise ValueError("Trump Part 7 page range changed")
    part7_types = Counter(row.get("transaction_type")
                          for row in part7["transactions"])
    if dict(part7_types) != EXPECTED_PART7_TRANSACTION_TYPES:
        raise ValueError("Trump Part 7 transaction type counts changed")
    part7_reasons = Counter(reason for row in part7["quarantined"]
                            for reason in row.get("reasons", []))
    if dict(part7_reasons) != EXPECTED_PART7_QUARANTINE_REASONS:
        raise ValueError("Trump Part 7 quarantine reason counts changed")

    printed_keys = Counter((row.get("account_scope"), row.get("row_number"))
                           for row in part6_rows)
    account_row_number_collision_excess = sum(
        count - 1 for count in printed_keys.values() if count > 1)
    scopes_by_row_number: dict[str, set[str]] = defaultdict(set)
    for row in part6_rows:
        scopes_by_row_number[str(row.get("row_number"))].add(row["account_scope"])
    cross_account_repeated_row_number_count = sum(
        1 for values in scopes_by_row_number.values() if len(values) > 1)

    canonical_rows = [encode(row) for rows in dispositions.values() for row in rows]
    exact_json_duplicate_count = len(canonical_rows) - len(set(canonical_rows))
    if exact_json_duplicate_count:
        raise ValueError("Trump replay contains exact duplicate disposition rows")
    reasons = Counter(reason for row in dispositions["quarantined"]
                      for reason in row.get("reasons", []))
    return {
        "schema_version": SCHEMA,
        "source_url": TRUMP_2025_SOURCE_URL,
        "source_sha256": TRUMP_2025_SOURCE_SHA256,
        "parser_version": TRUMP_2025_PARSER_VERSION,
        "extraction_sha256": extraction_sha256,
        "page_count": TRUMP_2025_PAGE_COUNT,
        "printed_row_count": printed,
        "disposition_counts": counts,
        "part6_disposition_counts": part6_counts,
        "part6_physical_row_count": len(part6_rows),
        "part6_unique_source_row_locator_count": len(locator_counts),
        "part6_duplicate_source_row_locator_excess": duplicate_locator_excess,
        "part6_account_row_number_collision_excess":
            account_row_number_collision_excess,
        "part6_cross_account_repeated_row_number_count":
            cross_account_repeated_row_number_count,
        "qualified_part6_holding_duplicate_count":
            qualified_holding_duplicate_count,
        "part6_numeric_printed_holding_count": numeric_holding_count,
        "part6_synthetic_row_number_holding_count": synthetic_holding_count,
        "part7_disposition_counts": part7_counts,
        "part7_page_range": list(EXPECTED_PART7_PAGE_RANGE),
        "part7_transaction_type_counts": dict(sorted(part7_types.items())),
        "part7_quarantine_reason_counts": dict(sorted(part7_reasons.items())),
        "part7_unique_source_row_locator_count": len(set(part7_locators)),
        "exact_json_duplicate_count": exact_json_duplicate_count,
        "quarantine_reason_counts": dict(sorted(reasons.items())),
        "source_row_census_status": extraction["source_row_census_status"],
        "source_row_census_complete": extraction["source_row_census_complete"],
        "legacy_checkpoint_tree": legacy_tree,
        "legacy_checkpoint_audit_sha256": legacy_audit_sha256,
        "legacy_checkpoint_changed_blob_count": 0,
        "legacy_checkpoint_shard_count": 38,
        "legacy_checkpoint_total_bytes": 72380635,
        "legacy_checkpoint_ocr_engine": "tesseract 5.3.4",
        "ocr_checkpoint": checkpoint,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--legacy-tree", required=True)
    parser.add_argument("--legacy-audit-sha256", required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    extraction = json.loads(raw)
    audit = audit_replay(
        extraction, extraction_sha256=hashlib.sha256(raw).hexdigest(),
        legacy_tree=args.legacy_tree,
        legacy_audit_sha256=args.legacy_audit_sha256)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encode(audit))
    print(json.dumps(audit, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
