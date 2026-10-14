"""Read-only receipt and 278-T overlap blockers for fixed scanned OGE annuals."""
from __future__ import annotations

import hashlib
import json
import re

from .oge_annual_scan_dates import FIXED_COVERS


SCHEMA = "oge-278e-fixed-receipt-overlap-audit/v1"
HARRIS_ID = "1462ab8c5542e5e7852589b0007899fc"


def _possible_harris_name(value: object) -> bool:
    if not isinstance(value, str):
        return False
    tokens = set(re.findall(r"[a-z]+", value.casefold()))
    return {"kamala", "harris"} <= tokens


def audit_receipt_overlap(catalog_payload: bytes, date_reports: list[dict],
                          part7: dict, fixed_metadata: dict) -> dict:
    """Classify observed dates and links; never infer a filing date or de-dup."""
    catalog_snapshot_sha256 = hashlib.sha256(catalog_payload).hexdigest()
    catalog = json.loads(catalog_payload)
    if (catalog.get("schema_version") != "oge-whitehouse-coverage/v1" or
            catalog.get("records_total") != catalog.get("catalog_rows_covered") or
            catalog.get("catalog_rows_covered") != 16746 or
            catalog.get("catalog_sha256") != fixed_metadata.get("official_catalog_sha256") or
            catalog_snapshot_sha256 != fixed_metadata.get("catalog_snapshot_sha256") or
            fixed_metadata.get("schema_version") != "oge-278e-fixed-receipt-metadata/v1"):
        raise ValueError("fixed official catalog scope or metadata changed")
    listed = {row.get("source_document_id"): row for row in catalog["reports"]}
    dates = {row.get("source_document_id"): row for row in date_reports}
    metadata = {row.get("source_document_id"): row for row in fixed_metadata["reports"]}
    if set(dates) != set(FIXED_COVERS) or set(metadata) != set(FIXED_COVERS):
        raise ValueError("six scanned annual reports are not uniquely accounted for")
    matrix = []
    for document_id, (source_sha, cover_kind, fixed_dates) in sorted(FIXED_COVERS.items()):
        entry, observed, original = listed.get(document_id), dates[document_id], metadata[document_id]
        date_values = observed.get("dates") or {
            role: block.get("date") for role, block in
            observed.get("date_blocks", {}).items()}
        if (entry is None or entry.get("document_type") != "278e_annual" or
                entry.get("access_method") != "direct_pdf" or
                observed.get("source_sha256") != source_sha or
                original.get("source_sha256") != source_sha or
                original.get("catalog_entry_id") != entry.get("catalog_entry_id") or
                original.get("catalog_added_date") != entry.get("catalog_added_date") or
                original.get("document_url") != entry.get("document_url") or
                original.get("document_type") != entry.get("document_type") or
                original.get("amended_label") != entry.get("amended_label") or
                observed.get("cover_kind") != cover_kind or
                observed.get("agency_receipt_verified") is not False or
                observed.get("part7_filing_date_unverified") is not True or
                date_values != {**fixed_dates, "agency_receipt": None}):
            raise ValueError(f"annual source or receipt evidence drift: {document_id}")
        pdf_info = original.get("pdf_metadata", {})
        if not isinstance(pdf_info, dict) or any(
                "receiv" in key.lower() or "receipt" in key.lower()
                for key in pdf_info):
            raise ValueError("PDF metadata requires separate receipt review")
        matrix.append({
            "source_document_id": document_id, "source_sha256": source_sha,
            "catalog_entry_id": entry["catalog_entry_id"],
            "cover_kind": cover_kind,
            "catalog_added_date": entry["catalog_added_date"],
            "catalog_date_semantics": "entry_added_to_public_index_not_agency_receipt",
            "pdf_creation_date": pdf_info.get("CreationDate"),
            "pdf_modified_date": pdf_info.get("ModDate"),
            "pdf_date_semantics": "file_processing_timestamps_not_agency_receipt",
            "visual_date_roles": date_values,
            "agency_receipt_status": "unverified",
            "filed_at": None,
            "annual_part7_filing_date_unverified": True,
            "production_eligible_transaction_count": 0,
        })
    harris_source = FIXED_COVERS[HARRIS_ID][0]
    if (part7.get("source_document_id") != HARRIS_ID or
            part7.get("source_sha256") != harris_source or
            len(part7.get("part7_pages", [])) != 5):
        raise ValueError("Harris Part 7 source or page scope changed")
    slots = [dict(row, page_number=page["page_number"])
             for page in part7["part7_pages"] for row in page["rows"]]
    if (len(slots) != 100 or
            [page["page_number"] for page in part7["part7_pages"]] != [9, 10, 11, 12, 13] or
            sum(row["classification"] == "filled_transaction" for row in slots) != 95 or
            sum(row["classification"] == "blank_template" for row in slots) != 5 or
            len({(row["page_number"], row["number"]) for row in slots}) != 100):
        raise ValueError("Harris Part 7 physical row census changed")
    same_filer_278t = [row for row in catalog["reports"]
                       if row.get("document_type") == "278t" and
                       _possible_harris_name(row.get("filer_name"))]
    pending = []
    for row in slots:
        if row["classification"] != "filled_transaction":
            continue
        pending.append({
            "source_document_id": HARRIS_ID,
            "source_sha256": harris_source,
            "page_number": row["page_number"],
            "printed_number": row["number"],
            "source_row_sha256": hashlib.sha256(
                "\n".join(row["raw_lines"]).encode("utf-8")).hexdigest(),
            "same_filer_278t_catalog_candidate_count": len(same_filer_278t),
            "overlap_status": "unresolved_no_source_pair" if not same_filer_278t else
                              "unresolved_candidates_present",
            "deduplicated": False, "production_eligible": False,
        })
    return {
        "schema_version": SCHEMA,
        "official_catalog_sha256": catalog["catalog_sha256"],
        "catalog_snapshot_sha256": catalog_snapshot_sha256,
        "catalog_rows_covered": catalog["catalog_rows_covered"],
        "catalog_selected_report_count": len(catalog["reports"]),
        "catalog_selected_278t_count": sum(
            row.get("document_type") == "278t" for row in catalog["reports"]),
        "catalog_scope": "fixed_oge_public_index_white_house_and_vice_president_agencies",
        "catalog_absence_proves_no_other_filing": False,
        "receipt_matrix": matrix,
        "verified_agency_receipt_count": 0,
        "harris_part7_filled_row_count": len(pending),
        "harris_same_filer_278t_catalog_count": len(same_filer_278t),
        "harris_catalog_name_search": "kamala_and_harris_tokens_any_order",
        "harris_278t_overlap_status": "unresolved",
        "harris_pending_rows": pending,
        "safe_next_scope": "source_bound_shadow_observation_only",
        "production_eligible_transaction_count": 0,
    }
