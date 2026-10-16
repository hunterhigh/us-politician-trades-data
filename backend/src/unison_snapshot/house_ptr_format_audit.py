"""Reconcile saved House paper observations by format, without qualification.

This consumes an existing source-bound cell observation. It does not render a
PDF, run OCR, create transaction IDs, or change the production parser.
"""

from __future__ import annotations

from collections import Counter

from .house_ptr_key_fields_shadow import read_house_ptr_key_fields
from .house_ptr_section_context import relate_house_ptr_sections
from .source_row_ledger import house_ptr_cell_shadow_ledger, page_ledger, report_ledger


def build_house_ptr_format_audit(observation: dict) -> dict:
    """Account for every page and physical band in one saved House PTR read."""
    if observation.get("schema_version") != "house-ptr-cell-observation-shadow/v3":
        raise ValueError("House format audit requires owner-aware cell observations")
    if observation.get("qualification_status") != "unverified_shadow_only":
        raise ValueError("House format audit cannot consume promoted observations")
    pages = observation.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("House format audit requires observed pages")
    if [page.get("page") for page in pages] != list(range(1, len(pages) + 1)):
        raise ValueError("House format audit page order is incomplete")

    ledger = house_ptr_cell_shadow_ledger(observation)
    readings = read_house_ptr_key_fields(observation)
    sections = relate_house_ptr_sections(observation)
    source_sha = observation["source_sha256"]
    if not (readings["source_sha256"] == sections["source_sha256"] == source_sha):
        raise ValueError("House format audit source identity changed")

    physical = {(page["page_number"], slot["local_key"]): slot["disposition"]
                for page in ledger["pages"] for slot in page["slots"]}
    if len(physical) != sum(page["physical_slot_count"] for page in ledger["pages"]):
        raise ValueError("House format audit has duplicate physical slots")
    data_keys = {(row["page"], str(row["physical_band_index"]))
                 for row in readings["rows"]}
    section_keys = {(row["page"], str(row["physical_band_index"]))
                    for row in sections["data_rows"]}
    physical_data_keys = {key for key, disposition in physical.items()
                          if disposition == "data_candidate"}
    if data_keys != section_keys or data_keys != physical_data_keys:
        raise ValueError("House format audit lost a data candidate")
    heading_keys = {(row["page"], str(row["physical_band_index"]))
                    for row in sections["sections"]}
    unknown_keys = {(row["page"], str(row["physical_band_index"]))
                    for row in sections["unclassified_unknown_rows"]}
    if heading_keys & unknown_keys or heading_keys | unknown_keys != {
            key for key, disposition in physical.items() if disposition == "unknown"}:
        raise ValueError("House format audit lost an unknown band")
    legend_keys = {(row["page"], str(row["physical_band_index"]))
                   for row in sections.get("printed_legend_rows", [])}
    if legend_keys != {key for key, disposition in physical.items()
                       if disposition == "heading"}:
        raise ValueError("House format audit lost a printed legend")

    # Section interpretation resolves some originally unknown bands as headings.
    # Carry that classification and every data-slot link into the common report
    # contract; it remains separate from production qualification.
    interpreted_pages = []
    for page in ledger["pages"]:
        interpreted_slots = [
            {**slot, "disposition": "heading" if
             (page["page_number"], slot["local_key"]) in heading_keys
             else slot["disposition"]} for slot in page["slots"]]
        interpreted_pages.append(page_ledger(**{
            key: page[key] for key in (
                "source_id", "document_sha256", "page_number", "adapter_version",
                "coverage", "unresolved_regions", "coverage_reason", "coordinate_frame")},
            slots=interpreted_slots))
    report = report_ledger(
        source_id="house_clerk", document_sha256=source_sha,
        adapter_version="house-ptr-format-audit/v1", page_count=len(pages),
        pages=interpreted_pages, logical_rows=[
            {"local_key": f"p{row['page']}:band{row['physical_band_index']}",
             "slot_refs": [{"page_number": row["page"],
                            "slot_key": str(row["physical_band_index"])}],
             "observation_ref": f"field_readings/rows/{index}"}
            for index, row in enumerate(readings["rows"])])

    page_audits = []
    for page in ledger["pages"]:
        number = page["page_number"]
        page_readings = [row for row in readings["rows"] if row["page"] == number]
        page_sections = [row for row in sections["data_rows"] if row["page"] == number]
        counts = Counter(slot["disposition"] for slot in page["slots"])
        blockers = Counter(reason for row in page_readings
                           for reason in row["blocking_reasons"])
        page_audits.append({
            "page": number, "coverage": page["coverage"],
            "coverage_reason": page["coverage_reason"],
            "physical_slot_count": page["physical_slot_count"],
            "disposition_counts": dict(sorted(counts.items())),
            "data_candidate_count": len(page_readings),
            "section_heading_candidate_count": sum(
                key[0] == number for key in heading_keys),
            "unclassified_unknown_count": sum(
                key[0] == number for key in unknown_keys),
            "raw_fields_complete_unverified_count": sum(
                row["status"] == "raw_fields_present_unverified"
                for row in page_readings),
            "section_context_unresolved_count": sum(
                row["section_key"] is None for row in page_sections),
            "blocking_reason_counts": dict(sorted(blockers.items())),
            "blocked_field_rows": [
                {"page": number, "physical_band_index": row["physical_band_index"],
                 "blocking_reasons": row["blocking_reasons"],
                 "source_field_evidence": row.get("source_field_evidence")}
                for row in page_readings if row["blocking_reasons"]],
        })

    counts = Counter(slot["disposition"] for page in ledger["pages"]
                     for slot in page["slots"])
    return {
        "schema_version": "house-ptr-format-audit/v1",
        "source_sha256": source_sha,
        "source_report_ledger": report,
        "field_readings": readings,
        "qualification_status": "unverified_shadow_only",
        "production_transaction_change": 0,
        "page_count": len(page_audits),
        "physical_slot_count": len(physical),
        "disposition_counts": dict(sorted(counts.items())),
        "section_heading_candidate_count": len(heading_keys),
        "unclassified_unknown_count": len(unknown_keys),
        "printed_legend_count": len(legend_keys),
        "raw_fields_complete_unverified_count": sum(
            row["status"] == "raw_fields_present_unverified"
            for row in readings["rows"]),
        "blocked_field_row_count": sum(bool(row["blocking_reasons"])
                                       for row in readings["rows"]),
        "section_context_unresolved_count": sum(
            row["section_key"] is None for row in sections["data_rows"]),
        "coverage_unknown_page_count": sum(
            page["coverage"] == "unknown" for page in ledger["pages"]),
        "pages": page_audits,
    }
