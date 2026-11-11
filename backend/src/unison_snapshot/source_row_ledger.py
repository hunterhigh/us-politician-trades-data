"""Versioned, audit-only page ledger for source-specific layout adapters.

This records where a source page may contain data. It does not turn an OCR
observation into a qualified disclosure or a stable transaction ID.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math
from typing import Any


SCHEMA_VERSION = "source-row-ledger/v1"
COVERAGE = {"located", "non_table", "unknown"}
DISPOSITIONS = {"data_candidate", "heading", "blank", "note", "unknown"}


def report_ledger(*, source_id: str, document_sha256: str,
                  adapter_version: str, page_count: int, pages: list[dict],
                  logical_rows: list[dict],
                  unresolved_data_slots: list[dict] | None = None) -> dict:
    """Link existing page slots to local logical rows, never to formal IDs.

    Missing pages stay unknown. Every data-candidate slot must be linked or
    explicitly unresolved. A split slot needs an explanation on each row;
    a continued row can reference several slots, including across pages.
    """
    if type(page_count) is not int or page_count < 1 or not adapter_version:
        raise ValueError("report page count and adapter version are required")
    if not isinstance(pages, list) or not isinstance(logical_rows, list):
        raise ValueError("report pages and logical rows must be arrays")
    # Reuse the existing provenance and slot validation rather than a second
    # definition of page coverage or physical position.
    by_page = {}
    for page in pages:
        number = page.get("page_number")
        if type(number) is not int or not 1 <= number <= page_count or number in by_page:
            raise ValueError("report page missing, duplicated or outside bounds")
        if (page.get("schema_version") != SCHEMA_VERSION or
                page.get("source_id") != source_id or
                page.get("document_sha256") != document_sha256.lower()):
            raise ValueError("report page provenance differs from report")
        checked = page_ledger(**{key: page[key] for key in (
            "source_id", "document_sha256", "page_number", "adapter_version",
            "coverage", "slots", "unresolved_regions", "coverage_reason",
            "coordinate_frame")}, non_transaction_regions=page.get('non_transaction_regions'))
        if any(page.get(key) != checked[key] for key in (
                "physical_slot_count", "disposition_counts", "slot_coverage")):
            raise ValueError("report page counts differ from physical slots")
        by_page[number] = checked
    missing_pages = [number for number in range(1, page_count + 1) if number not in by_page]
    for number in missing_pages:
        by_page[number] = page_ledger(
            source_id=source_id, document_sha256=document_sha256,
            page_number=number, adapter_version=adapter_version, coverage="unknown",
            coverage_reason="page_not_observed", slots=[])
    ordered = [by_page[number] for number in range(1, page_count + 1)]
    slots = {(page["page_number"], slot["local_key"]): slot["disposition"]
             for page in ordered for slot in page["slots"]}
    candidates = {key for key, disposition in slots.items() if disposition == "data_candidate"}

    def locator(value: dict) -> tuple[int, str]:
        number, key = value.get("page_number"), value.get("slot_key")
        if type(number) is not int or not isinstance(key, str) or (number, key) not in slots:
            raise ValueError("logical row references a missing physical slot")
        if (number, key) not in candidates:
            raise ValueError("non-data or unknown slot cannot become a logical row")
        return number, key

    owners: dict[tuple[int, str], list[dict]] = {}
    seen_rows = set()
    rows_out = []
    for row in logical_rows:
        if (not isinstance(row, dict) or set(row) - {
                "local_key", "slot_refs", "split_basis", "observation_ref"}):
            raise ValueError("logical rows contain unsupported fields or formal IDs")
        key, references = row.get("local_key"), row.get("slot_refs")
        if not isinstance(key, str) or not key or key in seen_rows:
            raise ValueError("logical row key missing or duplicated")
        if not isinstance(references, list) or not references:
            raise ValueError("logical row has no physical evidence")
        seen_rows.add(key)
        locators = []
        for reference in references:
            if not isinstance(reference, dict) or set(reference) != {"page_number", "slot_key"}:
                raise ValueError("invalid logical row slot reference")
            location = locator(reference)
            if location in locators:
                raise ValueError("logical row repeats a physical slot")
            locators.append(location)
            owners.setdefault(location, []).append(row)
        split_basis = row.get("split_basis")
        if split_basis is not None and (not isinstance(split_basis, str) or not split_basis.strip()):
            raise ValueError("slot split explanation is invalid")
        rows_out.append({**deepcopy(row), "qualification": "not_assessed"})
    if any(len(row_owners) > 1 and any(not row.get("split_basis") for row in row_owners)
           for row_owners in owners.values()):
        raise ValueError("shared physical slot requires explicit split evidence")

    unresolved = []
    blocked = set()
    for item in unresolved_data_slots or []:
        if (not isinstance(item, dict) or set(item) != {"page_number", "slot_key", "reason"}
                or not isinstance(item["reason"], str) or not item["reason"].strip()):
            raise ValueError("unresolved data slot needs a locator and reason")
        key = locator(item)
        if key in owners or key in blocked:
            raise ValueError("data slot is linked and unresolved or repeated")
        blocked.add(key)
        unresolved.append(dict(item))
    if candidates != set(owners) | blocked:
        raise ValueError("data-candidate slots lost between page and logical row")
    counts = Counter(disposition for disposition in slots.values())
    unknown_pages = [page["page_number"] for page in ordered if page["coverage"] == "unknown"]
    return {"schema_version": "source-report-ledger/v1",
            "source_id": source_id, "document_sha256": document_sha256.lower(),
            "adapter_version": adapter_version, "page_count": page_count,
            "pages": ordered, "unobserved_pages": missing_pages,
            "coverage_unknown_pages": unknown_pages,
            "observed_physical_slot_count": len(slots),
            "physical_slot_count": None if unknown_pages else len(slots),
            "disposition_counts": dict(sorted(counts.items())),
            "logical_row_count": len(rows_out), "logical_rows": rows_out,
            "linked_data_slot_count": len(owners),
            "unresolved_data_slots": unresolved,
            "relation_coverage": "partial_unknown" if
                unknown_pages or blocked or counts["unknown"] else "accounted",
            "qualified_transaction_count": None,
            "production_mutation_authorized": False}


def page_ledger(*, source_id: str, document_sha256: str, page_number: int,
                adapter_version: str, coverage: str, slots: list[dict],
                unresolved_regions: list[list[float]] | None = None,
                coverage_reason: str | None = None,
                coordinate_frame: str = "source_page",
                non_transaction_regions: list[dict] | None = None) -> dict[str, Any]:
    """Build a page account with explicit unknowns and per-slot conservation.

    Slot IDs are local to this adapter version; downstream candidate IDs must
    be established separately from original evidence and fact relations.
    """
    if not source_id or not adapter_version or not coordinate_frame:
        raise ValueError("source and adapter version are required")
    if len(document_sha256) != 64 or any(ch not in "0123456789abcdef"
                                          for ch in document_sha256.lower()):
        raise ValueError("document SHA-256 is required")
    if not isinstance(page_number, int) or page_number < 1:
        raise ValueError("page number must be positive")
    if coverage not in COVERAGE:
        raise ValueError("invalid page coverage")
    if coverage == "non_table" and slots:
        raise ValueError("non-table page cannot claim data slots")
    regions = unresolved_regions or []
    if coverage == "located" and regions:
        raise ValueError("located page cannot hide unresolved regions")
    if coverage == "unknown" and not (coverage_reason or regions):
        raise ValueError("unknown coverage needs a reason or unresolved region")
    if coverage == "non_table" and not coverage_reason:
        raise ValueError("non-table classification needs its basis")
    for region in regions:
        if len(region) != 2 or not 0 <= region[0] < region[1]:
            raise ValueError("invalid unresolved region")

    seen = set()
    normalized = []
    for slot in slots:
        key = slot.get("local_key")
        state = slot.get("disposition")
        bounds = slot.get("bounds")
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError("slot key missing or duplicated")
        seen.add(key)
        if state not in DISPOSITIONS:
            raise ValueError("every slot needs a disposition")
        if (not isinstance(bounds, list) or len(bounds) != 2 or
                not all(isinstance(value, (int, float)) for value in bounds) or
                not 0 <= bounds[0] < bounds[1]):
            raise ValueError("every slot needs ordered page bounds")
        normalized.append({"local_key": key, "bounds": bounds,
                           "disposition": state,
                           "observation_ref": slot.get("observation_ref")})
    counts = Counter(slot["disposition"] for slot in normalized)
    if sum(counts.values()) != len(normalized):
        raise ValueError("physical slot conservation failed")
    role_regions = [] if non_transaction_regions is None else non_transaction_regions
    if not isinstance(role_regions, list) or len(role_regions) > 8:
        raise ValueError('non-transaction region inventory is invalid')
    seen_regions = set()
    for proof in role_regions:
        box = proof.get('region_bbox_pdf_points') if isinstance(proof, dict) else None
        if (not isinstance(box, list) or len(box) != 4 or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in box) or
                not 0 <= box[0] < box[2] or not 0 <= box[1] < box[3] or
                proof.get('source_sha256') != document_sha256.lower() or
                proof.get('page_number') != page_number or proof.get('status') != 'observed' or
                proof.get('coordinate_frame') != coordinate_frame or
                proof.get('financial_fact_qualification') is not False or
                proof.get('page_coverage') != 'partial' or
                not isinstance(proof.get('role'), str) or not proof['role'] or
                tuple(box) in seen_regions):
            raise ValueError('non-transaction region source binding is invalid')
        seen_regions.add(tuple(box))
        if any(slot['disposition'] in {'data_candidate', 'unknown'} and
               slot['bounds'][0] < box[3] and slot['bounds'][1] > box[1] for slot in normalized):
            raise ValueError('non-transaction region overlaps an unresolved or data slot')
    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": source_id,
        "document_sha256": document_sha256.lower(),
        "page_number": page_number,
        "adapter_version": adapter_version,
        "coordinate_frame": coordinate_frame,
        "coverage": coverage,
        "coverage_reason": coverage_reason,
        "slot_coverage": "partial_unknown" if counts["unknown"] else "classified",
        "physical_slot_count": len(normalized),
        "disposition_counts": dict(sorted(counts.items())),
        "unresolved_regions": regions,
        "slots": normalized,
        "qualified_transaction_count": None,
        **({'non_transaction_regions': deepcopy(role_regions)} if role_regions else {}),
    }


def add_non_transaction_region(page, proof):
    """Attach a caller-replayed region without reclassifying any source slot."""
    box = proof.get('region_bbox_pdf_points') if isinstance(proof, dict) else None
    if not isinstance(box, list) or len(box) != 4:
        raise ValueError('non-transaction region has no source bounds')
    if proof in page.get('non_transaction_regions', []):
        return deepcopy(page)
    if any(slot['disposition'] in {'data_candidate', 'unknown'} and
           slot['bounds'][0] < box[3] and slot['bounds'][1] > box[1] for slot in page['slots']):
        return None
    regions = deepcopy(page['unresolved_regions'])
    # These profiles explain source-defined full-width regions. Subtract only
    # those vertical extents; remaining gaps and unknown areas stay unknown.
    if box[0] == 0 and proof.get('role') in {
            'fixed_native_summary_of_contents_2019', 'native_referential_endnotes'}:
        remainder = []
        for top, bottom in regions:
            if top < box[1]:
                remainder.append([top, min(bottom, box[1])])
            if bottom > box[3]:
                remainder.append([max(top, box[3]), bottom])
        regions = remainder
    return page_ledger(**{key: page[key] for key in (
        'source_id', 'document_sha256', 'page_number', 'adapter_version',
        'coverage', 'slots', 'coverage_reason', 'coordinate_frame')},
        unresolved_regions=regions,
        non_transaction_regions=[*page.get('non_transaction_regions', []), proof])


def senate_shadow_pages(report: dict) -> list[dict[str, Any]]:
    """Normalize classified Senate paper slots without granting eligibility."""
    source_sha = report["entrypoint_source_sha256"]
    observed_by_page: dict[int, list[dict]] = {}
    for slot in report["slots"]:
        observed_by_page.setdefault(slot["page_number"], []).append(slot)
    output = []
    mapping = {"data_observed": "data_candidate",
               "heading_candidate": "heading", "blank_appearance": "blank",
               "note_candidate": "note", "unknown": "unknown"}
    for page in report["pages"]:
        number = page["page_number"]
        observations = observed_by_page.get(number, [])
        if len(observations) != page["physical_grid_slot_count"]:
            raise ValueError("Senate shadow page count disagrees with slots")
        slots = []
        for row in observations:
            disposition = mapping.get(row["label"])
            if disposition is None:
                raise ValueError("unknown Senate shadow label")
            slots.append({"local_key": str(row["grid_slot"]),
                          "bounds": row["row_bounds_px"],
                          "disposition": disposition,
                          "observation_ref": row["row_crop_sha256"]})
        output.append(page_ledger(
            source_id="senate_efd", document_sha256=source_sha,
            page_number=number, adapter_version="senate-paper-grid-shadow/v1",
            coverage="located", slots=slots,
            coordinate_frame="archived_page_pixels_3400x4400"))
    if sum(page["physical_slot_count"] for page in output) != report["physical_grid_slot_count"]:
        raise ValueError("Senate shadow report count disagrees with pages")
    return output


