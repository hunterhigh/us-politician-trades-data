"""Promote the fixed, source-bound paper PTR rows after slot observation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from unison_snapshot.senate_paper import PAPER_EXTRACTION_SCHEMA, PAPER_OCR_RULE_VERSION
from unison_snapshot.senate_reports import PARSER_VERSION


DOCUMENT_ID = "068274e1-4b7a-4453-a242-563dde4c10d8"
AUDIT_SHA256 = "123eb6f68e316449f3f2919f49514554e3ab0e567fdaa972073cbbdb9323a009"
SOURCE_SHA256 = "107c79b1276ae99ef9321547420e2303790fcc3cb110f43bf64e4c3ddb34bb6c"
SOURCE_URL = f"https://efdsearch.senate.gov/search/view/paper/{DOCUMENT_ID}/"
AMOUNTS = {(1001, 15000): "$1,001 - $15,000",
           (15001, 50000): "$15,001 - $50,000",
           (50001, 100000): "$50,001 - $100,000",
           (100001, 250000): "$100,001 - $250,000",
           (250001, 500000): "$250,001 - $500,000"}


def qualify(batch: dict, pending: dict, audit: dict, manifest: dict) -> dict:
    """Replace one failed report with 36 reviewed rows; retain other failures."""
    if (batch.get("parser_version") != PARSER_VERSION or
            batch.get("report_count") != 9 or
            pending.get("schema_version") != "senate-paper-pending-slot-projection/v3" or
            audit.get("schema_version") != "senate-paper-first-report-frontend-gates/v3" or
            audit.get("document_id") != DOCUMENT_ID or audit.get("row_count") != 36 or
            audit.get("report_context", {}).get("entrypoint_sha256") != SOURCE_SHA256 or
            audit["report_context"].get("cover_receipt_date") != "2026-02-12" or
            audit["report_context"].get("frontend_count_policy") != "official_disclosure_rows" or
            audit["report_context"].get("identity", {}).get("person_id") != "senate:B001277" or
            audit["report_context"].get("amendment_checkbox", {}).get("observation") != "no_mark_detected" or
            audit["report_context"].get("verified_supplement_target") is not False or
            manifest.get("document_id") != DOCUMENT_ID or
            manifest.get("entrypoint_source_sha256") != SOURCE_SHA256 or
            manifest.get("evidence_complete") is not True):
        raise ValueError("fixed Senate paper source qualification changed")
    failures = [item for item in batch["failures"] if item["document_id"] == DOCUMENT_ID]
    if (len(failures) != 1 or failures[0]["source_sha256"] != SOURCE_SHA256 or
            any(item["document_id"] == DOCUMENT_ID for item in batch["extractions"])):
        raise ValueError("fixed paper report has no unique failed parser disposition")
    pending_rows = {(
        item["source_slot"]["page_number"], item["source_slot"]["grid_slot"]): item
        for item in pending["pending_rows"]
        if item["source_slot"]["document_id"] == DOCUMENT_ID}
    if len(pending_rows) != 36:
        raise ValueError("fixed paper source slots are incomplete or repeated")
    page_hashes = {item["page_number"]: item["sha256"] for item in manifest["pages"]}
    if len(page_hashes) != manifest["page_count"]:
        raise ValueError("fixed paper manifest pages repeat")
    rows = []
    ids = set()
    for item in audit["rows"]:
        page, grid = item["page_number"], item["grid_slot"]
        source = pending_rows.pop((page, grid), None)
        projection = item["frontend_projection_preview"]
        if (source is None or source["source_slot"]["entrypoint_source_sha256"] != SOURCE_SHA256 or
                source["source_slot"]["page_sha256"] != page_hashes.get(page) or
                source["source_slot"]["row_crop_sha256"] != item["row_crop_sha256"] or
                item["page_sha256"] != page_hashes.get(page) or
                source.get("account_heading_visual_raw") != item["account_heading_visual_raw"] or
                source.get("legacy_extraction_failed") is not True or
                source.get("legacy_extraction_report_row_count") != 0 or
                item.get("missing_frontend_fields") != ["id", "verification_status"] or
                projection.get("filing_id") != DOCUMENT_ID or
                projection.get("source_url") != SOURCE_URL or
                projection.get("filed_at") != "2026-02-12T00:00:00Z" or
                projection.get("person_id") != "senate:B001277" or
                projection.get("owner") != "Spouse" or
                projection.get("instrument_type") is not None or
                projection.get("asset_name") !=
                f"{item['asset_text_without_code_observed']} (Account: {item['account_heading_visual_raw']})"):
            raise ValueError("fixed paper row differs from verified source slot")
        amount = AMOUNTS.get((projection["amount_low"], projection["amount_high"]))
        if amount is None or projection["transaction_type"] not in {"purchase", "sale"}:
            raise ValueError("fixed paper row amount or direction changed")
        fingerprint = "|".join((SOURCE_SHA256, str(page), page_hashes[page],
                                str(grid), item["row_crop_sha256"]))
        row_id = "senate-ptr:" + hashlib.sha256(fingerprint.encode("ascii")).hexdigest()[:24]
        if row_id in ids:
            raise ValueError("fixed paper source slot ID collision")
        ids.add(row_id)
        rows.append({"extraction_id": row_id, "row_number": grid,
                     "page_number": page,
                     "transaction_date": projection["transaction_date"],
                     "owner_raw": "Spouse", "ticker_raw": None,
                     "asset_name_raw": projection["asset_name"],
                     "asset_type_raw": None,
                     "transaction_type_raw": projection["transaction_type"].title(),
                     "transaction_type": projection["transaction_type"],
                     "amount_raw": amount, "comment_raw": None,
                     "qualification_status": "eligible", "quarantine_reasons": [],
                     "qualification_basis": "fixed_source_visual_review_v1",
                     "source_slot": source["source_slot"]})
    if pending_rows or len(rows) != 36:
        raise ValueError("fixed paper source rows did not conserve")
    extraction = {
        "schema_version": PAPER_EXTRACTION_SCHEMA,
        "parser_version": PARSER_VERSION,
        "paper_rule_version": PAPER_OCR_RULE_VERSION,
        "source_id": "senate_efd", "document_id": DOCUMENT_ID,
        "source_url": SOURCE_URL, "source_sha256": SOURCE_SHA256,
        "filer_name": "RICHARD BLUMENTHAL",
        "portal_listed_date": "2026-02-12", "report_label_date": "2026-02-12",
        "report_amendment_number": None, "report_title_date": "2026-02-12",
        "catalog_title_date_matches": True, "filed_at_raw": "Filed 02/12/2026",
        "document_disposition": "transactions_parsed",
        "evidence_complete": True,
        "page_manifest_sha256": manifest["manifest_sha256"],
        "page_count": manifest["page_count"],
        "transactions": rows, "eligible_transaction_count": 36,
        "quarantined_transaction_count": 0,
        "qualification_basis": "fixed_source_visual_review_v1",
        "frontend_gate_audit_sha256": AUDIT_SHA256,
    }
    result = dict(batch)
    result["extractions"] = [*batch["extractions"], extraction]
    result["failures"] = [item for item in batch["failures"]
                          if item["document_id"] != DOCUMENT_ID]
    result["extraction_count"] += 1
    result["failure_count"] -= 1
    result["transaction_count"] += 36
    result["eligible_transaction_count"] += 36
    if (result["extraction_count"] + result["failure_count"] != result["report_count"]):
        raise ValueError("paper report dispositions do not conserve")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("batch", "pending", "audit", "manifest", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    raw_audit = args.audit.read_bytes()
    if hashlib.sha256(raw_audit).hexdigest() != AUDIT_SHA256:
        raise ValueError("reviewed frontend gate audit SHA changed")
    read = lambda path: json.loads(path.read_text(encoding="utf-8"))
    result = qualify(read(args.batch), read(args.pending), json.loads(raw_audit),
                     read(args.manifest))
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
