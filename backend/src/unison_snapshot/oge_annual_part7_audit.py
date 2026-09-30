"""Source-bound, read-only Part 7 audit for a pinned OGE annual report.

This audit records evidence and unresolved boundaries. It never qualifies rows
for publication or treats absence from a frozen candidate as global de-duplication.
"""

from __future__ import annotations

import re


SCHEMA = "oge-278e-part7-shadow-audit/v1"


def audit_annual_part7(extraction: dict, evidence: dict) -> dict:
    if (extraction.get("source_sha256") != evidence.get("annual_source_sha256") or
            extraction.get("parser_version") != "oge-278e-tables/v2" or
            extraction.get("form_type") != "278e" or
            extraction.get("report_type") != "Annual" or
            extraction.get("filing_date") is not None):
        raise ValueError("annual source, parser, or filing-date boundary mismatch")
    if (evidence.get("cover", {}).get("filer_certified_on") != "2026-06-23" or
            evidence["cover"].get("oge_received_on") != extraction.get("oge_received_on") or
            evidence.get("official_278t_coverage_complete") is not False):
        raise ValueError("cover or 278-T coverage evidence is not pinned")
    if any(snapshot.get("vance_transaction_count") != 0 for snapshot in
           evidence.get("fixed_candidate_snapshots", [])):
        raise ValueError("frozen candidate evidence changed")
    snapshots = evidence.get("fixed_candidate_snapshots", [])
    if (len(snapshots) != 2 or {item.get("ref") for item in snapshots} != {"main", "review"} or
            any(item.get("person_id") != "oge:a16b930d2cac6095" or
                not re.fullmatch(r"[0-9a-f]{40}", item.get("commit", ""))
                for item in snapshots)):
        raise ValueError("frozen candidate scope is incomplete")
    index = evidence.get("white_house_index", {})
    if (not re.fullmatch(r"[0-9a-f]{64}", index.get("page_sha256", "")) or
            index.get("vance_annual_link_count") != 2 or
            index.get("vance_278t_link_count") != 0):
        raise ValueError("white house index scope is incomplete")
    expected = evidence.get("printed_part7_rows", [])
    actual = extraction.get("transactions", [])
    if len(expected) != 10 or len(actual) != 10:
        raise ValueError("Part 7 row count differs from printed census")
    if {row.get("number") for row in expected} != {str(number) for number in range(1, 11)}:
        raise ValueError("fixed visual transcription numbers are not unique 1-10")
    by_number = {row["row_number"]: row for row in actual}
    if set(by_number) != {str(number) for number in range(1, 11)}:
        raise ValueError("Part 7 printed numbers are not unique 1-10")
    rows = []
    for printed in expected:
        number = printed["number"]
        parsed = by_number[number]
        fields = ("asset_name", "transaction_type", "transaction_date",
                  "amount_low", "amount_high")
        if (printed.get("page_number") != 11 or parsed.get("page_number") != 11 or
                any(printed.get(field) != parsed.get(field) for field in fields)):
            raise ValueError(f"Part 7 printed row {number} differs from extraction")
        rows.append({
            "number": number, "page_number": 11,
            "asset_name": parsed["asset_name"],
            "transaction_type": parsed["transaction_type"],
            "transaction_date": parsed["transaction_date"],
            "amount_low": parsed["amount_low"],
            "amount_high": parsed["amount_high"],
            "annual_printed_row_status": "matched_fixed_visual_transcription",
            "fixed_candidate_overlap_count": 0,
            "official_278t_duplicate_status": "unresolved",
            "official_filing_date_status": "unverified",
            "shadow_disposition": "quarantined",
        })
    return {
        "schema_version": SCHEMA,
        "annual_source_sha256": extraction["source_sha256"],
        "annual_source_url": extraction["source_url"],
        "parser_version": extraction["parser_version"],
        "white_house_annual_source_sha256": evidence["white_house_annual_source_sha256"],
        "cover": evidence["cover"],
        "fixed_candidate_snapshots": evidence["fixed_candidate_snapshots"],
        "white_house_index": evidence["white_house_index"],
        "official_278t_coverage_complete": False,
        "annual_report_filing_date": None,
        "part7_printed_row_count": 10,
        "rows": rows,
        "production_eligible_row_count": 0,
        "report_complete": False,
    }
