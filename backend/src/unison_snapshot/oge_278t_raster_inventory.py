"""Independent full-page source-boundary audit, without financial field reads.

This checks the saved census against image rules, including rules outside its
search window. Agreement attests only to these horizontal boundaries. It does
not distinguish data, blank cells or notes, or establish report completeness.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from statistics import median


SCHEMA = "oge-278t-independent-raster-boundaries/v1"
STRIPS = ((110., 135.), (200., 220.), (250., 270.))
MAX_SELECTED_PAGES = 8
MAX_RENDER_PIXELS = 16_000_000
MAX_RENDER_DIMENSION = 10_000


def _raster_page(page):
    area = float(page.width) * float(page.height)
    return any(max(0., min(float(page.width), float(image["x1"])) - max(0., float(image["x0"]))) *
               max(0., min(float(page.height), float(image["bottom"])) - max(0., float(image["top"])))
               >= .75 * area for image in page.images)


def _boundaries(strips):
    # Every boundary must have independent pixel support in disjoint strips.
    # No expected row population, OCR token or printed sequence enters here.
    if (len({len(values) for values in strips}) != 1 or len(strips[0]) < 6 or
            any(max(row) - min(row) > 2. for row in zip(*strips, strict=True))):
        return []
    return [round(median(row), 1) for row in zip(*strips, strict=True)]


def audit_raster_source_inventory(path: Path, *, expected_sha256: str, census: dict,
                                   document_page_count: int | None = None) -> dict:
    """Compare source image boundaries with supplied slots; never grant eligibility.

    The bounded supported observation is an upright full-page raster with six
    or more horizontal rules witnessed in the existing 2024 form's disjoint
    x=110..135, 200..220 and 250..270 PDF-point strips. Other layouts,
    skew/disagreement and pages without source pixels remain explicitly unknown.
    Actual PDF pages are enumerated even when absent from the saved selection.
    """
    import numpy as np
    import pdfplumber
    from .oge_278t_page_coverage import _raster_rule_centers, reconcile_raster_ocr_rows

    # Reuse shape/source validation, but never use the resulting OCR mapping as
    # source evidence: the independent witness below comes only from PDF pixels.
    reconcile_raster_ocr_rows(census, [], expected_sha256=expected_sha256,
                              ocr_source_sha256=expected_sha256)
    source = Path(path)
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError("independent raster inventory source PDF hash differs")
    selected = {page["page_number"]: page for page in census["results"]}
    render_selection = set(sorted(selected)[:MAX_SELECTED_PAGES])
    limited_pages = set(selected.keys() - render_selection)
    pages = []
    with pdfplumber.open(source) as document:
        count = len(document.pages)
        if (document_page_count is not None and
                (type(document_page_count) is not int or document_page_count != count)):
            raise ValueError("independent raster inventory PDF page count differs")
        if any(number > count for number in selected):
            raise ValueError("independent raster inventory selected page is outside PDF")
        for number, page in enumerate(document.pages, 1):
            item = {"page_number": number, "status": "unknown", "reason": None,
                    "source_boundary_positions": [], "claimed_boundary_positions": [],
                    "unaccounted_source_boundaries": [], "unwitnessed_claimed_boundaries": [],
                    "ambiguous_source_boundaries": [], "ambiguous_claimed_boundaries": [],
                    "claimed_slots_crossing_source_boundaries": [],
                    "content_status": "unverified"}
            if not _raster_page(page):
                item["reason"] = "page_without_full_raster_not_assessed"
            elif number not in selected:
                item["reason"] = "raster_page_not_in_saved_inventory"
            elif number not in render_selection:
                item["reason"] = "source_inventory_render_budget_exceeded"
            elif (float(page.width) <= 270. or any(abs(float(v)) > .01 for v in page.bbox[:2]) or
                    not all(math.isfinite(float(v)) and float(v) > 0 for v in (page.width, page.height))):
                item["reason"] = "source_raster_coordinate_frame_unsupported"
            else:
                width_px, height_px = math.ceil(float(page.width)*3), math.ceil(float(page.height)*3)
                if (width_px*height_px > MAX_RENDER_PIXELS or
                        max(width_px,height_px) > MAX_RENDER_DIMENSION):
                    item["reason"] = "source_inventory_render_size_exceeded"
                    limited_pages.add(number)
                    pages.append(item)
                    continue
                gray = np.asarray(page.to_image(resolution=216, force_mediabox=True).original.convert("L"))
                strips = [_raster_rule_centers(gray, points_per_pixel=page.height/gray.shape[0],
                            x_points_per_pixel=page.width/gray.shape[1], x0=left, x1=right,
                            y_span=(0., float(page.height))) for left, right in STRIPS]
                source_edges = _boundaries(strips)
                item.update(search_y_span_pdf_points=[0., float(page.height)],
                            source_strip_spans_pdf_points=[list(pair) for pair in STRIPS],
                            source_strip_rule_counts=[len(values) for values in strips],
                            source_boundary_positions=source_edges)
                bands = selected[number]["physical_row_bands"]
                edges = sorted({float(value) for band in bands for value in band})
                item["claimed_boundary_positions"] = edges
                if not source_edges:
                    item["reason"] = "full_page_source_rule_witness_unresolved"
                elif selected[number]["status"] != "physical_slots_located":
                    item["reason"] = "saved_inventory_geometry_unknown"
                    item["unaccounted_source_boundaries"] = source_edges
                else:
                    source_matches = {value: [old for old in edges if abs(value-old) <= 2]
                                      for value in source_edges}
                    claimed_matches = {value: [real for real in source_edges if abs(value-real) <= 2]
                                       for value in edges}
                    extra = [value for value,matches in source_matches.items() if not matches]
                    missing = [value for value,matches in claimed_matches.items() if not matches]
                    ambiguous_source = [value for value,matches in source_matches.items() if len(matches)>1]
                    ambiguous_claimed = [value for value,matches in claimed_matches.items() if len(matches)>1]
                    crossing = [{"slot_index": index, "source_boundary_positions": inside}
                                for index, band in enumerate(bands, 1)
                                if (inside := [value for value in source_edges if band[0]+2 < value < band[1]-2])]
                    discontinuous = any(abs(left[1]-right[0]) > 2 for left,right in zip(bands,bands[1:]))
                    mismatch = bool(extra or missing or crossing or discontinuous or ambiguous_source or ambiguous_claimed)
                    item.update(status="source_inventory_mismatch" if mismatch else "source_boundaries_aligned",
                                reason="saved_slots_do_not_account_for_full_page_rules" if mismatch else None,
                                unaccounted_source_boundaries=extra, unwitnessed_claimed_boundaries=missing,
                                ambiguous_source_boundaries=ambiguous_source,
                                ambiguous_claimed_boundaries=ambiguous_claimed,
                                claimed_slots_crossing_source_boundaries=crossing,
                                claimed_slot_inventory_discontinuous=discontinuous)
            pages.append(item)
    selected_pages = [item for item in pages if item["page_number"] in selected]
    return {"schema_version": SCHEMA, "source_sha256": expected_sha256,
            "document_page_count": count, "selected_page_numbers": sorted(selected), "pages": pages,
            "scope": "source_horizontal_rule_inventory_only",
            "limitation": "unaccounted rules may be headers or structural borders, not necessarily missed disclosures",
            "limited_page_numbers": sorted(limited_pages),
            "all_selected_boundaries_accounted": bool(selected_pages) and all(
                item["status"] == "source_boundaries_aligned" for item in selected_pages),
            "unknown_page_numbers": [item["page_number"] for item in pages if item["status"] == "unknown"],
            "mismatched_page_numbers": [item["page_number"] for item in pages if item["status"] == "source_inventory_mismatch"],
            "report_completeness_attested": False, "qualified_transaction_count": None,
            "recognizer_started": False, "production_mutation_authorized": False}
