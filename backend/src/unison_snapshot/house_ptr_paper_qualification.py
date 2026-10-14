"""Carry scanned House PTR observations into the existing qualification queue.

This bridge deliberately emits no formal transactions. A located paper row is
not a verified filing fact, and its physical slot key is never a formal ID.
"""

from __future__ import annotations

import re

from .house_ptr import OWNER_CODES, QUALIFICATION_SCHEMA
from .house_ptr_amount_header import evaluate_amount_header_pages
from .house_ptr_key_fields_shadow import read_house_ptr_key_fields
from .house_ptr_section_context import relate_house_ptr_sections
from .house_ptr_duplicate_observation import observe_repeated_trade_signatures
from .house_ptr_paper_temporal import (PARSER_VERSION, audit_paper_temporal_relations,
                                      validate_paper_source_metadata)


def _pending_transaction_projection(result: dict, metadata: dict) -> dict:
    """Shape observed paper slots for the builder without assigning formal IDs."""
    document_id, source_sha, _, _ = validate_paper_source_metadata(metadata)
    if result["document_id"] != document_id or result["source_sha256"] != source_sha:
        raise ValueError("House paper projection source does not match qualification")
    projected = []
    for row in result["quarantined"]:
        fields = row.get("raw_fields")
        if not isinstance(fields, dict):
            continue
        amount = row.get("amount_range_observation")
        projected.append({
            "observation_key": row["observation_key"],
            "evidence": row["evidence"],
            "person_id": result["identity"].get("person_id"),
            "filing_id": document_id,
            "source_url": metadata["source_url"],
            "filed_at": f"{metadata['filed_date']}T00:00:00Z",
            "owner": OWNER_CODES.get(fields.get("owner_code")),
            "asset_name": fields.get("asset_raw") or None,
            "transaction_date": fields.get("event_date"),
            "transaction_type": fields.get("direction_mark"),
            "amount_letter": fields.get("amount_letter"),
            "amount_low": amount.get("low") if isinstance(amount, dict) else None,
            "amount_high": amount.get("high") if isinstance(amount, dict) else None,
            "amount_kind": amount.get("kind") if isinstance(amount, dict) else None,
            "field_status": {
                "owner": "ocr_unverified" if fields.get("owner_code") else "unreadable",
                "asset_name": "ocr_unverified" if fields.get("asset_raw") else "unreadable",
                "transaction_date": fields.get("event_date_status"),
                "transaction_type": fields.get("direction_status"),
                "amount": "printed_bounds_unverified_transaction" if amount else
                          fields.get("amount_status", "unresolved"),
            },
            "blocking_reasons": row["reasons"],
        })
    return {"schema_version": "house-paper-pending-transaction-projection/v1",
            "status": "unverified_pending", "source_sha256": source_sha,
            "document_id": document_id, "row_count": len(projected),
            "rows": projected}


def qualify_paper_observation(observation: dict, metadata: dict,
                              identity: dict, *, amount_header: dict | None = None,
                              peer_metadata: list[dict] | None = None) -> dict:
    """Account for every physical row while blocking unverified promotion."""
    source_sha = observation.get("source_sha256")
    if (not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha)
            or metadata.get("sha256") != source_sha):
        raise ValueError("House paper observation and archived source hashes differ")
    document_id = metadata.get("document_id")
    if (not isinstance(document_id, str) or not document_id
            or identity.get("document_id") != document_id):
        raise ValueError("House paper identity does not match the archived document")
    header_ranges = None
    header_evidence = None
    if amount_header is not None:
        if (amount_header.get("source_sha256") != source_sha or
                amount_header.get("page_count") != len(observation["pages"])):
            raise ValueError("House paper amount header does not match the observation")
        checked = evaluate_amount_header_pages(source_sha, amount_header.get("pages"))
        if (checked["status"] != amount_header.get("status") or
                checked["ranges"] != amount_header.get("ranges")):
            raise ValueError("House paper amount header interpretation changed")
        header_ranges = checked["ranges"]
        header_evidence = checked
    reading = read_house_ptr_key_fields(observation)
    section_context = relate_house_ptr_sections(observation)
    duplicates = observe_repeated_trade_signatures(reading, section_context)
    repeated_by_slot = {}
    for group in duplicates["groups"]:
        slots = [(item["page"], item["physical_band_index"])
                 for item in group["slots"]]
        for slot in slots:
            repeated_by_slot[slot] = [
                {"page": other[0], "physical_band_index": other[1]}
                for other in slots if other != slot]
    read_by_slot = {(row["page"], row["physical_band_index"]): row
                    for row in reading["rows"]}
    section_by_slot = {(row["page"], row["physical_band_index"]): row
                       for row in section_context["data_rows"]}
    heading_by_slot = {(row["page"], row["physical_band_index"]): row
                       for row in section_context["sections"]}
    if len(read_by_slot) != reading["data_candidate_count"]:
        raise ValueError("House paper reading has duplicate physical slots")
    quarantined = []
    structural_observations = []
    physical_counts = {"data_candidate": 0, "unknown": 0, "blank": 0}
    seen = set()
    for page in observation["pages"]:
        page_number = page.get("page")
        for physical in page["rows"]:
            band_index = physical.get("physical_band_index")
            slot = (page_number, band_index)
            if (type(page_number) is not int or type(band_index) is not int
                    or page_number < 1 or band_index < 1 or slot in seen):
                raise ValueError("House paper physical slot is invalid or duplicated")
            seen.add(slot)
            disposition = physical.get("disposition")
            if disposition not in physical_counts:
                raise ValueError("House paper physical disposition is unknown")
            physical_counts[disposition] += 1
            if disposition == "blank":
                continue
            if slot in heading_by_slot:
                structural_observations.append(heading_by_slot[slot])
                continue
            key = f"paper-slot:{source_sha}:{page_number}:{band_index}"
            reasons = (["paper_source_fields_unverified"] if disposition == "data_candidate"
                       else ["physical_row_disposition_unresolved"])
            read = read_by_slot.get(slot)
            if disposition == "data_candidate":
                if read is None:
                    raise ValueError("House paper data slot has no key-field reading")
                reasons.extend(read["blocking_reasons"])
            elif read is not None:
                raise ValueError("House paper non-data slot has a key-field reading")
            amount_observation = None
            if read and read["fields"] and header_ranges:
                fields = read["fields"]
                spouse_mark = fields["spouse_dc_over_1000000_mark"]
                letter = fields["amount_letter"]
                if spouse_mark is True and letter is None:
                    letter = "K"
                elif spouse_mark is True:
                    letter = None
                if letter in header_ranges:
                    amount_observation = {
                        "status": "printed_bounds_unverified_transaction",
                        "letter": letter, **header_ranges[letter],
                        "header_source_sha256": source_sha,
                    }
            quarantined.append({
                "extraction_id": key, "observation_key": key,
                "reasons": sorted(set(reasons)), "source_sha256": source_sha,
                "evidence": {"page": page_number, "physical_band_index": band_index,
                             "band_normalized": physical.get("band")},
                "raw_fields": read["fields"] if read else None,
                "account_section_observation": section_by_slot.get(slot),
                "possible_repeated_with": repeated_by_slot.get(slot, []),
                "amount_range_observation": amount_observation,
            })
    if physical_counts["data_candidate"] != reading["data_candidate_count"]:
        raise ValueError("House paper physical census and key-field reading differ")
    if (len(quarantined) + len(structural_observations) + physical_counts["blank"]
            != section_context["physical_slot_count"]):
        raise ValueError("House paper section and qualification census differ")
    if not quarantined:
        raise ValueError("House paper has no observed nonblank rows")
    document_reasons = ["paper_source_fields_unverified"]
    if section_context["unclassified_unknown_count"]:
        document_reasons.append("physical_row_disposition_unresolved")
    result = {
        "schema_version": QUALIFICATION_SCHEMA,
        "source_sha256": source_sha, "parser_version": PARSER_VERSION,
        "document_id": document_id, "identity": identity,
        "document_disposition": {"status": "paper_observation_unverified",
                                 "physical_counts": physical_counts,
                                 "section_heading_count": len(structural_observations),
                                 "amount_header_status": amount_header.get("status")
                                 if amount_header else "not_observed"},
        "amount_header_observation": header_evidence,
        "section_context_observation": section_context,
        "duplicate_observation": duplicates,
        "structural_observations": structural_observations,
        "transactions": [], "quarantined": quarantined, "advisories": [],
        "qualification": {
            "method": "paper_observation_blocked",
            "status": "quarantined_rows", "qualified_count": 0,
            "quarantined_count": len(quarantined), "advisory_count": 0,
            "zero_transaction_document_count": 0,
            "document_reasons": document_reasons, "production_eligible": False,
        },
    }
    result["pending_transaction_projection"] = _pending_transaction_projection(result, metadata)
    if peer_metadata is not None:
        result["temporal_relation_observation"] = audit_paper_temporal_relations(
            result, metadata, peer_metadata)
    return result
