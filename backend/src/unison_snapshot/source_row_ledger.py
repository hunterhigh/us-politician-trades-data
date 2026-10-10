"""Versioned, audit-only page ledger for source-specific layout adapters.

This records where a source page may contain data. It does not turn an OCR
observation into a qualified disclosure or a stable transaction ID.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


SCHEMA_VERSION = "source-row-ledger/v1"
COVERAGE = {"located", "non_table", "unknown"}
DISPOSITIONS = {"data_candidate", "heading", "blank", "note", "unknown"}


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


def oge_278t_shadow_pages(report: dict, *, source_id: str) -> list[dict[str, Any]]:
    """Normalize 278-T physical grids, retaining unread numbers and pages."""
    if source_id not in {"oge", "whitehouse"}:
        raise ValueError("278-T source must be OGE or White House")
    output = []
    for page in report["pages"]:
        row_state = page["row_coverage"]
        if page["coverage"] == "non_table_section":
            output.append(page_ledger(
                source_id=source_id, document_sha256=report["source_sha256"],
                page_number=page["page_number"], adapter_version="278t-page-shadow/v1",
                coverage="non_table", coverage_reason="non_table_section", slots=[],
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
