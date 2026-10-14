"""Physical Part 7 slot and pending transaction ledger for a pinned OGE 278e.

The independently inspected page census supplies the physical slots.  The
existing annual parser supplies observations, not publication authority.
"""
from __future__ import annotations

import hashlib
import re

from .oge_annual_part7_audit import audit_annual_part7
from .oge_annual_ledger import _validate_source


SCHEMA = "oge-278e-part7-slot-ledger/v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def observe_annual_part7(extraction: dict, evidence: dict, page_census: dict) -> dict:
    """Account for all printed Part 7 slots without promoting annual trades."""
    _validate_source(extraction)
    audit = audit_annual_part7(extraction, evidence)
    if (page_census.get("schema_version") != "oge-278e-page-census/v1" or
            page_census.get("source_sha256") != extraction["source_sha256"] or
            page_census.get("method") != "independent_rendered_page_and_text_line_inspection"):
        raise ValueError("independent Part 7 page census is not source-bound")
    page = page_census.get("pages", {}).get("part7", {})
    page_sha = page.get("page_text_sha256")
    if page.get("page") != 11 or not isinstance(page_sha, str) or not _SHA.fullmatch(page_sha):
        raise ValueError("Part 7 page identity is incomplete")
    filled = page.get("filled_rows")
    blank = page.get("blank_template_numbers")
    if (not isinstance(filled, list) or not isinstance(blank, list) or
            len(filled) != 10 or blank != list(range(11, 21)) or
            [item.get("number") for item in filled] !=
            [str(number) for number in range(1, 11)] or
            any(item.get("collection") != "transactions" or
                item.get("index") != number - 1
                for number, item in enumerate(filled, 1))):
        raise ValueError("Part 7 physical slot census does not reconcile")
    if [row["row_number"] for row in extraction["transactions"]] != [
            str(number) for number in range(1, 11)]:
        raise ValueError("Part 7 parser rows do not map one-to-one to physical slots")

    by_number = {row["number"]: row for row in audit["rows"]}
    slots = []
    pending = []
    for number in range(1, 21):
        slot_id = "oge-part7-slot:" + hashlib.sha256(
            f"{extraction['source_sha256']}|11|{number}".encode("ascii")
        ).hexdigest()[:24]
        base = {"slot_id": slot_id, "page_number": 11,
                "printed_number": str(number),
                "source_page_text_sha256": page_sha}
        if number > 10:
            slots.append({**base, "classification": "blank_template",
                          "pending_transaction_id": None})
            continue
        observed = by_number[str(number)]
        parsed = extraction["transactions"][number - 1]
        transaction_id = "oge-part7-pending:" + hashlib.sha256(
            f"{slot_id}|transaction".encode("ascii")
        ).hexdigest()[:24]
        slots.append({**base, "classification": "filled_transaction",
                      "pending_transaction_id": transaction_id})
        pending.append({
            "pending_transaction_id": transaction_id,
            "physical_slot_id": slot_id,
            "page_number": 11,
            "printed_number": str(number),
            "asset_name": observed["asset_name"],
            "transaction_type": observed["transaction_type"],
            "transaction_date": observed["transaction_date"],
            "amount_low": observed["amount_low"],
            "amount_high": observed["amount_high"],
            "raw_type": parsed["raw_type"],
            "raw_date": parsed["raw_date"],
            "raw_amount": parsed["raw_amount"],
            "field_evidence": "matched_fixed_visual_transcription",
            "annual_filing_date_status": "unverified",
            "official_278t_duplicate_status": "unresolved",
            "disposition": "quarantined",
        })
    return {
        "schema_version": SCHEMA,
        "source_id": "oge",
        "source_document_id": extraction["source_document_id"],
        "source_url": extraction["source_url"],
        "source_sha256": extraction["source_sha256"],
        "evidence_archive_path": extraction["evidence_archive_path"],
        "parser_version": extraction["parser_version"],
        "section": "part7",
        "page_number": 11,
        "page_text_sha256": page_sha,
        "physical_slot_count": 20,
        "filled_transaction_count": len(pending),
        "blank_template_count": 10,
        "unaccounted_slot_count": 0,
        "production_eligible_transaction_count": 0,
        "slots": slots,
        "pending_transactions": pending,
        "unresolved_links": ["annual_filing_date", "official_278t_overlap",
                             "white_house_annual_duplicate_relation"],
        "publication_status": "shadow_only_not_promoted",
    }
