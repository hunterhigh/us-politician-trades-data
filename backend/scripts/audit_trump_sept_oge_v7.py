"""Audit fixed Trump September OGE v6 -> v7 geometric row recovery."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from unison_snapshot.oge_reports import (
    EXTRACTION_SCHEMA, TRUMP_SEPT_2026_DOCUMENT_ID,
    TRUMP_SEPT_2026_GEOMETRY_PASS_VERSION,
    TRUMP_SEPT_2026_PAGE7_PASS_VERSION,
    TRUMP_SEPT_2026_SOURCE_SHA256, TRUMP_SEPT_2026_SOURCE_URL,
)


EXPECTED_LOCATORS = {
    *((23, number) for number in (
        704, 706, 707, 708, 709, 710, 711, 712, 714, 715, 716, 717,
        718, 719, 720, 721, 722, 723, 724, 725, 727,
    )),
    (31, 959), (31, 960), (31, 961),
}
GEOMETRY_PAGES = {2, 23, 31}


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
    v6 = _read(directory / "oge-278t-pdf-v6.json", TRUMP_SEPT_2026_PAGE7_PASS_VERSION)
    v7 = _read(directory / "oge-278t-pdf-v7.json", TRUMP_SEPT_2026_GEOMETRY_PASS_VERSION)
    if (len(v6["transactions"]) != 383 or len(v6["quarantined"]) != 773 or
            len(v7["transactions"]) != 407 or len(v7["quarantined"]) != 749):
        raise ValueError("September OGE v6/v7 disposition counts changed")
    if ({key: value for key, value in v6.items()
         if key not in {"parser_version", "transactions", "quarantined"}} !=
            {key: value for key, value in v7.items()
             if key not in {"parser_version", "transactions", "quarantined"}}):
        raise ValueError("September OGE v7 changed document-level evidence")
    if v7["transactions"][:383] != v6["transactions"]:
        raise ValueError("September OGE v7 changed or reordered a v6 transaction")

    v6_ids = [row.get("extraction_id") for row in v6["transactions"] + v6["quarantined"]]
    v7_ids = [row.get("extraction_id") for row in v7["transactions"] + v7["quarantined"]]
    if (len(v6_ids) != 1156 or len(set(v6_ids)) != 1156 or
            len(v7_ids) != 1156 or len(set(v7_ids)) != 1156 or set(v6_ids) != set(v7_ids)):
        raise ValueError("September OGE v7 extraction IDs are not conserved")

    old_quarantine = {row["extraction_id"]: row for row in v6["quarantined"]}
    new_rows = v7["transactions"][383:]
    promoted_locators = {(row.get("page_number"), row.get("row_number")) for row in new_rows}
    if promoted_locators != EXPECTED_LOCATORS:
        raise ValueError("September OGE v7 geometry promotion locators changed")
    promoted_ids = {new.get("extraction_id") for new in new_rows}
    v7_quarantine = {row.get("extraction_id"): row for row in v7["quarantined"]}
    expected_remaining = []
    for old in v6["quarantined"]:
        if old.get("extraction_id") in promoted_ids:
            continue
        expected = old
        if old.get("page_number") == 2 and old.get("row_number") is None:
            raw_cells = old.get("cells")
            raw_label = raw_cells[0] if isinstance(raw_cells, list) and raw_cells else ""
            labels = re.findall(r"(?<!\d)\d{1,4}(?!\d)", raw_label)
            if len(labels) != 1:
                raise ValueError("September OGE v6 page-2 row label is not recoverable")
            aligned_number = int(labels[0])
            actual = v7_quarantine.get(old.get("extraction_id"), {})
            proof = actual.get("geometry_row_alignment")
            if (not isinstance(proof, dict) or
                    proof.get("basis") != "fixed_source_pdf_row_order_and_label_v7" or
                    proof.get("source_sha256") != TRUMP_SEPT_2026_SOURCE_SHA256 or
                    proof.get("page_number") != 2 or
                    proof.get("printed_row_number") != aligned_number or
                    proof.get("original_row_number") is not None or
                    proof.get("original_row_label") != raw_label or
                    proof.get("original_reasons") != old.get("reasons") or
                    proof.get("original_cells") != raw_cells):
                raise ValueError("September OGE v7 row-number repair lacks source-bound proof")
            expected = dict(old)
            expected["row_number"] = aligned_number
            expected["geometry_row_alignment"] = proof
            expected["reasons"] = [reason for reason in old.get("reasons", [])
                                   if reason != "row_number_invalid"]
        expected_remaining.append(expected)
    if v7["quarantined"] != expected_remaining:
        raise ValueError("September OGE v7 changed an unrelated quarantine row")

    for row in new_rows:
        original = old_quarantine.get(row.get("extraction_id"))
        proof = row.get("geometry_recovery")
        if (original is None or not isinstance(proof, dict) or
                proof.get("basis") != "fixed_source_pdf_word_geometry_v7" or
                proof.get("source_sha256") != TRUMP_SEPT_2026_SOURCE_SHA256 or
                proof.get("page_number") != row.get("page_number") or
                proof.get("printed_row_number") != row.get("row_number") or
                proof.get("original_cells") != original.get("cells") or
                row.get("cells") != original.get("cells") or
                not row.get("asset_name") or row.get("transaction_type") not in {"purchase", "sale"} or
                not row.get("transaction_date") or type(row.get("amount_low")) is not int or
                type(row.get("amount_high")) is not int):
            raise ValueError("September OGE v7 promoted row lacks a valid source-bound proof")

    for page_number in GEOMETRY_PAGES:
        rows = [row for row in v7["transactions"] + v7["quarantined"]
                if row.get("page_number") == page_number]
        locators = [row.get("row_number") for row in rows]
        expected_rows = {2: set(range(1, 34)), 23: set(range(694, 728)),
                         31: set(range(959, 992))}[page_number]
        if (len(locators) != len(set(locators)) or set(locators) != expected_rows):
            raise ValueError(f"September OGE v7 page {page_number} row locators changed")

    return {
        "document_id": TRUMP_SEPT_2026_DOCUMENT_ID,
        "source_sha256": TRUMP_SEPT_2026_SOURCE_SHA256,
        "v6_transaction_count": len(v6["transactions"]),
        "v7_transaction_count": len(v7["transactions"]),
        "v6_quarantined_count": len(v6["quarantined"]),
        "v7_quarantined_count": len(v7["quarantined"]),
        "promoted_geometry_count": len(new_rows),
        "promoted_locators": [list(locator) for locator in sorted(promoted_locators)],
        "geometry_pages_disposition_count": sum(
            1 for row in v7["transactions"] + v7["quarantined"]
            if row.get("page_number") in GEOMETRY_PAGES),
        "extraction_id_conservation_complete": True,
        "legacy_v6_transactions_unchanged": True,
        "unrelated_quarantine_unchanged": True,
        "quarantined_row_number_alignments": sum(
            1 for row in v7["quarantined"] if row.get("geometry_row_alignment")),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.review_root), sort_keys=True))


if __name__ == "__main__":
    main()
