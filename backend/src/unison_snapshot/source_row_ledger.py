"""Versioned, audit-only page ledger for source-specific layout adapters.

This records where a source page may contain data. It does not turn an OCR
observation into a qualified disclosure or a stable transaction ID.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
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
            "coordinate_frame")})
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


def cell_observation(*, source_id: str, document_sha256: str, page_number: int,
                     adapter_version: str, slot_key: str, field: str,
                     box_px: list[int], crop_sha256: str, engine: str,
                     raw: Any, interpreted: Any = None,
                     confidence: float | None = None,
                     coordinate_frame: str = "source_page_pixels") -> dict[str, Any]:
    """Record a single read of an original cell without choosing a fact."""
    if (not source_id or not adapter_version or not slot_key or not field
            or not engine or not coordinate_frame):
        raise ValueError("cell provenance is required")
    if any(len(value) != 64 or any(ch not in "0123456789abcdef"
                                   for ch in value.lower())
           for value in (document_sha256, crop_sha256)):
        raise ValueError("cell source and crop SHA-256 are required")
    if not isinstance(page_number, int) or page_number < 1:
        raise ValueError("cell page number must be positive")
    if (len(box_px) != 4 or not all(isinstance(value, int) for value in box_px)
            or not 0 <= box_px[0] < box_px[2]
            or not 0 <= box_px[1] < box_px[3]):
        raise ValueError("invalid cell box")
    return {"schema_version": SCHEMA_VERSION, "source_id": source_id,
            "document_sha256": document_sha256.lower(), "page_number": page_number,
            "adapter_version": adapter_version, "slot_key": slot_key,
            "field": field, "box_px": box_px,
            "coordinate_frame": coordinate_frame,
            "crop_sha256": crop_sha256.lower(),
            "engine": engine, "raw": raw, "interpreted": interpreted,
            "confidence": confidence}


def page_ledger(*, source_id: str, document_sha256: str, page_number: int,
                adapter_version: str, coverage: str, slots: list[dict],
                unresolved_regions: list[list[float]] | None = None,
                coverage_reason: str | None = None,
                coordinate_frame: str = "source_page") -> dict[str, Any]:
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
    }


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


def senate_shadow_cell_observations(report: dict) -> list[dict[str, Any]]:
    """Carry all mark and date reads forward, including conflicting dates."""
    source_sha = report["entrypoint_source_sha256"]
    output = []
    for slot in report["slots"]:
        cells = slot["key_cells"]
        base = {"source_id": "senate_efd", "document_sha256": source_sha,
                "page_number": slot["page_number"],
                "adapter_version": "senate-paper-grid-shadow/v1",
                "slot_key": str(slot["grid_slot"]),
                "coordinate_frame": "archived_page_pixels_3400x4400"}
        for field in ("direction", "amount"):
            for mark in cells[field]:
                output.append(cell_observation(
                    **base, field=f"{field}.{mark['label']}",
                    box_px=mark["box_px"], crop_sha256=mark["grayscale_sha256"],
                    engine="pixel-mark-shadow/v1",
                    raw=mark["dark_sample_count"],
                    interpreted=mark["mark_signal"]))
        date = cells["date"]
        output.append(cell_observation(
            **base, field="transaction_date", box_px=date["box_px"],
            crop_sha256=date["grayscale_sha256"], engine="page-ocr-shadow/v1",
            raw=date["page_ocr_raw"], interpreted=date["page_ocr_parsed"]))
        if date["cell_ocr"] is not None:
            read = date["cell_ocr"]
            if "variants" in read:
                for variant in read["variants"].values():
                    output.append(cell_observation(
                        **base, field="transaction_date", box_px=date["box_px"],
                        crop_sha256=date["grayscale_sha256"],
                        engine=variant["engine"], raw=variant["raw"],
                        interpreted=variant["parsed"],
                        confidence=variant.get("min_confidence")))
                output.append(cell_observation(
                    **base, field="transaction_date_consensus", box_px=date["box_px"],
                    crop_sha256=date["grayscale_sha256"], engine=read["engine"],
                    raw=read["status"], interpreted=read["date"]))
            else:
                output.append(cell_observation(
                    **base, field="transaction_date", box_px=date["box_px"],
                    crop_sha256=date["grayscale_sha256"], engine=read["engine"],
                    raw=read["raw"], interpreted=date["cell_ocr_parsed"],
                    confidence=read.get("min_confidence")))
    return output


def oge_278t_shadow_pages(report: dict, *, source_id: str, archived_pdf_path=None) -> list[dict[str, Any]]:
    """Normalize 278-T physical grids, retaining unread numbers and pages."""
    if source_id not in {"oge", "whitehouse"}:
        raise ValueError("278-T source must be OGE or White House")
    output = []
    for page in report["pages"]:
        row_state = page["row_coverage"]
        if page["coverage"] == "non_table_section":
            from .oge_278t_page_roles import replay_native_privacy_role
            proof=page.get('page_role_proof')
            verified=(isinstance(proof,dict) and archived_pdf_path is not None and
                replay_native_privacy_role(archived_pdf_path,proof,source_sha256=report['source_sha256'],
                                           page_number=page['page_number']))
            output.append(page_ledger(
                source_id=source_id, document_sha256=report["source_sha256"],
                page_number=page["page_number"], adapter_version="278t-page-shadow/v1",
                coverage="non_table" if verified else "unknown",
                coverage_reason="source_replayed_native_privacy_statement" if verified else "non_table_source_proof_unavailable_or_invalid",slots=[],
                coordinate_frame="pdf_points"))
            continue
        physical = row_state in {"physical_slots_located",
                                 "physical_slots_with_unread_number"}
        bands = page["candidate_row_bands"] if physical else []
        unread = {tuple(band) for band in page.get("unresolved_row_bands", [])}
        warned = {warning["slot_index"] for warning in
                  page.get("number_sequence_warnings", [])}
        slots = [{"local_key": str(index), "bounds": band,
                  "disposition": ("unknown" if tuple(band) in unread or
                                  index in warned else "data_candidate")}
                 for index, band in enumerate(bands, 1)]
        output.append(page_ledger(
            source_id=source_id, document_sha256=report["source_sha256"],
            page_number=page["page_number"], adapter_version="278t-page-shadow/v1",
            coverage="located" if physical else "unknown", slots=slots,
            coverage_reason=None if physical else row_state,
            unresolved_regions=[] if physical else
            page.get("unresolved_row_regions", []), coordinate_frame="pdf_points"))
    return output


def house_ptr_shadow_pages(pages: list[dict], *, document_sha256: str,
                           dpi: int) -> list[dict[str, Any]]:
    """Normalize House paper row bands; provisional bands stay unresolved."""
    if dpi < 100:
        raise ValueError("House shadow DPI is required")
    output = []
    mapping = {"data_candidate": "data_candidate", "blank": "blank",
               "title_legend": "heading", "unknown": "unknown"}
    for number, page in enumerate(pages, 1):
        physical = page.get("physical_row_bands", [])
        slots = []
        for index, row in enumerate(physical, 1):
            state = mapping.get(row["disposition"])
            if state is None:
                raise ValueError("unknown House paper row disposition")
            slots.append({"local_key": str(index), "bounds": row["band"],
                          "disposition": state})
        located = page["status"] == "located_shadow"
        output.append(page_ledger(
            source_id="house_clerk", document_sha256=document_sha256,
            page_number=number, adapter_version=f"house-paper-grid-shadow/v1/{dpi}dpi",
            coverage="located" if located else "unknown", slots=slots,
            coverage_reason=None if located else page.get("reason", "unlocated_grid"),
            coordinate_frame=page.get("coordinate_frame", "original_page")))
    return output


def house_ptr_cell_shadow_ledger(report: dict) -> dict[str, list[dict[str, Any]]]:
    """Carry House physical bands and raw cells without promoting OCR values."""
    schema = report.get("schema_version")
    if schema not in {"house-ptr-cell-observation-shadow/v1",
                      "house-ptr-cell-observation-shadow/v2",
                      "house-ptr-cell-observation-shadow/v3"}:
        raise ValueError("unsupported House cell observation schema")
    if report.get("qualification_status") != "unverified_shadow_only":
        raise ValueError("House cell observations must remain unverified")
    source_sha = report["source_sha256"]
    dpi = report["render_dpi"]
    pages_out = []
    cells_out = []
    mapping = {"data_candidate": "data_candidate", "blank": "blank",
               "title_legend": "heading", "unknown": "unknown"}
    for page in report["pages"]:
        number = page["page"]
        rows = page["rows"]
        slots = []
        for index, row in enumerate(rows, 1):
            if row["physical_band_index"] != index:
                raise ValueError("House physical band order changed")
            state = mapping.get(row["disposition"])
            if state is None:
                raise ValueError("unknown House physical band disposition")
            cells = row["cells"]
            if cells is not None and page.get("cell_status") not in {
                    "raw_observed_unverified", "image_only_ocr_unavailable"}:
                raise ValueError("House cells lack an observation state")
            slots.append({"local_key": str(index), "bounds": row["band"],
                          "disposition": state,
                          "observation_ref": (cells["event_date"]["crop_sha256"]
                                              if cells else None)})
            if cells is None:
                continue
            fields = [("event_date", [cells["event_date"]]),
                      ("direction", cells["direction"]),
                      ("amount", cells["amount"])]
            if schema.endswith(("/v2", "/v3")):
                fields.extend((field, [cells[field]])
                              for field in ("asset", "notification_date"))
            if schema.endswith("/v3"):
                fields.append(("owner", [cells["owner"]]))
            for field, observations in fields:
                for observed in observations:
                    if observed["status"] != "raw_unverified" or observed["value"] is not None:
                        raise ValueError("House cell contains a promoted value")
                    cells_out.append(cell_observation(
                        source_id="house_clerk", document_sha256=source_sha,
                        page_number=number,
                        adapter_version=f"house-ptr-cell-shadow/{schema.rsplit('/', 1)[1]}/{dpi}dpi",
                        slot_key=str(index),
                        field=(field if field in {"event_date", "notification_date", "asset", "owner"}
                               else f"{field}.{observed['label']}"),
                        box_px=observed["bbox_pixels"],
                        crop_sha256=observed["crop_sha256"],
                        engine="house-ptr-ocr-cell-shadow/v1",
                        raw={"text": observed["raw_text"],
                             "reading": observed["reading"],
                             "interior_dark_fraction":
                                 observed["interior_dark_fraction"]},
                        confidence=observed["raw_ocr_confidence"],
                        coordinate_frame="rendered_source_page_pixels"))
        located = page["region_status"] == "located_shadow"
        pages_out.append(page_ledger(
            source_id="house_clerk", document_sha256=source_sha,
            page_number=number,
            adapter_version=f"house-ptr-cell-shadow/{schema.rsplit('/', 1)[1]}/{dpi}dpi",
            coverage="located" if located else "unknown",
            slots=slots,
            coverage_reason=None if located else (
                page.get("region_reason") or page.get("cell_status")
                or "unlocated_house_grid"),
            coordinate_frame=page.get("coordinate_frame") or "original_page"))
    return {"pages": pages_out, "cells": cells_out}
