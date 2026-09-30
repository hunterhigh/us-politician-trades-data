"""Read-only reconciliation of the frozen White House 278e annual report."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import re


SCHEMA = "whitehouse-278e-fixed-candidate-replay/v1"
DOCUMENT_ID = "wh-url:0c14d3849ca60768024e470b"
PDF_SHA256 = "1cc7951c6f72fab008e921903c9a1d03d41a9910239f954e208b501d608553a3"
EXTRACTION_SHA256 = "f44d6c7de59f16111a39a88d7993969e7ba609d08da8201fa36c605268d29547"
PARSER_VERSION = "whitehouse-278e-hybrid-geometry/v8"
EXPECTED_COUNTS = {"holdings": 3999, "transactions": 6759,
                   "excluded": 21, "quarantined": 17320}
SOURCE_FIELDS = ("asset_name", "owner", "transaction_type", "transaction_date",
                 "amount_low", "amount_high")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def replay_annual_candidate(candidate_bytes: bytes, extraction_bytes: bytes,
                            document_audit: dict, *, candidate_blob: str,
                            extraction_blob: str, checkpoint_tree: str) -> dict:
    """Compare every fixed source transaction to the existing candidate ID."""
    if (document_audit.get("schema_version") !=
            "whitehouse-wh-url-document-audit/v1" or
            document_audit.get("candidate_sha256") != _sha(candidate_bytes) or
            _blob(candidate_bytes) != candidate_blob or
            _sha(extraction_bytes) != EXTRACTION_SHA256 or
            _blob(extraction_bytes) != extraction_blob or
            checkpoint_tree != "c57ecdcd0e780fa7dc256fae6d0fe728735c8f09"):
        raise ValueError("annual replay is not bound to the frozen candidate and OCR")
    docs = [doc for doc in document_audit.get("documents", [])
            if doc.get("document_id") == DOCUMENT_ID]
    if (len(docs) != 1 or docs[0].get("candidate_row_count") !=
            EXPECTED_COUNTS["transactions"] or
            docs[0].get("archive_pdf_sha256_from_path") != PDF_SHA256 or
            docs[0].get("document_type_from_public_label") != "278e_annual"):
        raise ValueError("annual document census changed")
    doc = docs[0]
    extraction = json.loads(extraction_bytes)
    candidate = json.loads(candidate_bytes)
    if (extraction.get("source_sha256") != PDF_SHA256 or
            extraction.get("source_url") != doc["source_url"] or
            extraction.get("parser_version") != PARSER_VERSION or
            extraction.get("page_count") != 927):
        raise ValueError("annual extraction source or parser changed")
    counts = {name: len(extraction[name]) for name in EXPECTED_COUNTS}
    if (counts != EXPECTED_COUNTS or
            extraction.get("printed_row_count") != sum(counts.values())):
        raise ValueError("annual source row conservation failed")
    checkpoint = extraction.get("ocr_checkpoint", {})
    if (checkpoint.get("reused_shard_count") != 38 or
            checkpoint.get("created_shard_count") != 0 or
            checkpoint.get("pending_page_count") != 0 or
            checkpoint.get("completed_page_count") != 927):
        raise ValueError("annual OCR checkpoint reuse changed")
    part7 = [row for name in EXPECTED_COUNTS for row in extraction[name]
             if row.get("section") == "part7"]
    locators = [row.get("source_row_locator") for row in part7]
    if (len(locators) != len(set(locators)) or
            any(not isinstance(locator, str) or
                re.fullmatch(r"p[1-9]\d*-y[1-9]\d*", locator) is None
                for locator in locators)):
        raise ValueError("annual Part 7 physical locators are invalid")
    by_id: dict[str, dict] = {}
    for row in candidate["transactions"]:
        if row.get("filing_id") != DOCUMENT_ID:
            continue
        row_id = row.get("id")
        if not isinstance(row_id, str) or row_id in by_id:
            raise ValueError("annual candidate ID missing or duplicated")
        by_id[row_id] = row
    if len(by_id) != EXPECTED_COUNTS["transactions"]:
        raise ValueError("annual candidate count changed")
    binding_lines = []
    conflicts = Counter()
    seen = set()
    for row in extraction["transactions"]:
        if row.get("section") != "part7":
            raise ValueError("annual transaction outside Part 7")
        locator = row["source_row_locator"]
        row_id = "wh-annual-tx:" + hashlib.sha256(
            f"{PDF_SHA256}|part7|{locator}".encode()).hexdigest()[:24]
        if row_id in seen:
            raise ValueError("annual source identity collides")
        seen.add(row_id)
        existing = by_id.get(row_id)
        if existing is None:
            conflicts["candidate_id_missing"] += 1
            continue
        for field in SOURCE_FIELDS:
            if existing.get(field) != row.get(field):
                conflicts[field] += 1
        for field, value in (("filing_id", DOCUMENT_ID),
                             ("person_id", doc["person_id"]),
                             ("source_url", doc["source_url"]),
                             ("filed_at", "2026-06-29T00:00:00Z"),
                             ("source_id", "oge"),
                             ("verification_status", "official_matched")):
            if existing.get(field) != value:
                conflicts[field] += 1
        row_bytes = json.dumps(row, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode()
        binding_lines.append(f"{locator}\t{row_id}\t{_sha(row_bytes)}")
    conflicts["candidate_without_extraction"] = len(set(by_id) - seen)
    if any(conflicts.values()):
        raise ValueError(f"annual candidate/source field conflicts: {dict(conflicts)}")
    annual = list(by_id.values())
    ptr = [row for row in candidate["transactions"]
           if str(row.get("filing_id", "")).startswith("wh-url:") and
           row.get("filing_id") != DOCUMENT_ID]
    def coarse(row: dict) -> tuple:
        return (row.get("person_id"), row.get("transaction_date"),
                row.get("transaction_type"), row.get("amount_low"),
                row.get("amount_high"))
    coarse_ptr = {coarse(row) for row in ptr}
    coarse_matches = sum(coarse(row) in coarse_ptr for row in annual)
    return {
        "schema_version": SCHEMA,
        "candidate_commit": document_audit["candidate_commit"],
        "candidate_sha256": _sha(candidate_bytes),
        "evidence_commit": document_audit["evidence_commit"],
        "document_id": DOCUMENT_ID, "source_sha256": PDF_SHA256,
        "parser_version": PARSER_VERSION,
        "extraction_sha256": _sha(extraction_bytes),
        "disposition_counts": counts,
        "printed_row_count": sum(counts.values()),
        "part7_transaction_count": len(seen),
        "part7_quarantine_count": sum(
            row.get("section") == "part7" for row in extraction["quarantined"]),
        "part7_unique_locator_count": len(set(locators)),
        "existing_candidate_id_count": len(by_id),
        "candidate_source_field_conflict_count": 0,
        "candidate_binding_sha256": _sha(("\n".join(sorted(binding_lines)) + "\n").encode()),
        "annual_rows_with_coarse_periodic_candidate": coarse_matches,
        "coarse_periodic_matches_are_duplicates": False,
        "source_row_census_complete": extraction.get("source_row_census_complete"),
        "production_mutation_authorized": False,
    }
