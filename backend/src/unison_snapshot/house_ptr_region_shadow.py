"""Experimental, audit-only House scanned PTR transaction table locator.

This module deliberately does not qualify transactions. It returns unknown when
the page topology does not unambiguously separate the transaction grid from
other ruled sections such as Filer Notes.
"""

from __future__ import annotations

import io

import numpy as np
import pdfplumber


def _clusters(points: np.ndarray) -> list[list[int]]:
    groups: list[list[int]] = []
    for point in points.tolist():
        if not groups or point > groups[-1][-1] + 1:
            groups.append([point])
        else:
            groups[-1].append(point)
    return groups


def _locate_upright_grid(image) -> dict:
    """Locate one upright transaction grid, or report unknown.

    All returned coordinates are normalized to image dimensions. The detector
    uses ruled-line topology, not OCR text or a source-specific page crop.
    """
    gray = np.asarray(image.convert("L"))
    height, width = gray.shape
    if height < 300 or width < 300:
        return {"status": "coverage_unknown", "reason": "image_too_small"}
    dark = gray < 100
    x0, x1 = int(.07 * width), int(.90 * width)
    horizontal = np.flatnonzero(dark[:, x0:x1].mean(axis=1) > .50)
    bounds = [sum(group) / len(group) for group in _clusters(horizontal)]
    # Scanned rules can appear as multiple neighboring strokes at high DPI.
    merged: list[list[float]] = []
    for y in bounds:
        if merged and (y - merged[-1][-1]) / height < .014:
            merged[-1].append(y)
        else:
            merged.append([y])
    bounds = [sum(group) / len(group) for group in merged]
    bands = []
    for upper, lower in zip(bounds, bounds[1:]):
        if (lower - upper) / height < .015:
            continue
        pad = max(2, int(.005 * height))
        interior = dark[int(upper) + pad:int(lower) - pad, x0:x1]
        if interior.size == 0:
            continue
        vertical = np.flatnonzero(interior.mean(axis=0) > .60)
        positions = [(sum(group) / len(group) + x0) / width
                     for group in _clusters(vertical)]
        bands.append({"upper": upper / height, "lower": lower / height,
                      "vertical_count": len(positions), "vertical_x": positions})
    rich = [index for index, band in enumerate(bands) if band["vertical_count"] >= 8]
    if not rich:
        return {"status": "coverage_unknown", "reason": "no_column_rich_grid",
                "bands": bands}
    runs: list[list[int]] = []
    for index in rich:
        if (runs and index == runs[-1][-1] + 1
                and bands[index]["upper"] - bands[runs[-1][-1]]["lower"] < .006):
            runs[-1].append(index)
        else:
            runs.append([index])
    candidates = []
    provisional_candidates = []
    rejections: list[str] = []
    for run in runs:
        if len(run) < 2:
            continue
        if run[0] == 0:
            rejections.append("missing_preceding_header")
            continue
        major_columns = bands[run[0] - 1]["vertical_x"]
        if len(major_columns) not in (4, 5, 6):
            rejections.append("unsupported_column_topology")
            continue
        # The compact 4-rule header has a wider asset column and omits the
        # page-left rule from this detector's x window. Its event-date cell is
        # between the second and third observed major rules.
        event_x0, event_x1 = (major_columns[1:3] if len(major_columns) == 4
                              else major_columns[2:4])
        column_family = "dense_long" if len(major_columns) == 4 else "short_grid"
        if not .04 <= event_x1 - event_x0 <= .12:
            rejections.append("invalid_event_column_width")
            continue
        heights = [bands[index]["lower"] - bands[index]["upper"] for index in run]
        # The column labels form one unusually tall band before the data rows.
        if heights[0] <= 1.5 * float(np.median(heights[1:])):
            rejections.append("no_distinct_column_header_band")
            continue
        rows = [bands[index] for index in run[1:]]
        physical = []
        if len(rows) >= 3:
            later_height = float(np.median([row["lower"] - row["upper"]
                                            for row in rows[1:]]))
            if rows[0]["lower"] - rows[0]["upper"] < .8 * later_height:
                physical.append({"band": [rows[0]["upper"], rows[0]["lower"]],
                                 "disposition": "title_legend"})
                rows = rows[1:]
        # The first major cell is the asset column in both layouts. Ignore its
        # code gutter and ruled boundaries, then classify every printed band.
        if column_family == "dense_long":
            asset_x0, asset_x1 = .11, major_columns[0] - .01
        else:
            asset_x0, asset_x1 = major_columns[0] + .055, major_columns[1] - .01
        if asset_x1 <= asset_x0:
            rejections.append("invalid_asset_column_width")
            continue
        filled = []
        seen_empty = False
        discontinuous = False
        for row in rows:
            y0 = int((row["upper"] + .004) * height)
            y1 = int((row["lower"] - .004) * height)
            ink = dark[y0:y1, int(asset_x0 * width):int(asset_x1 * width)]
            fraction = float(ink.mean()) if ink.size else 0.0
            date_ink = dark[y0:y1,
                            int((event_x0 + .008) * width):int((event_x1 - .008) * width)]
            date_fraction = float(date_ink.mean()) if date_ink.size else 0.0
            if fraction >= .006 and date_fraction >= .015:
                disposition = "data_candidate"
                if seen_empty:
                    discontinuous = True
                filled.append(row)
            elif fraction >= .006 or date_fraction >= .015:
                disposition = "unknown"
            else:
                disposition = "blank"
                seen_empty = bool(filled)
            physical.append({"band": [row["upper"], row["lower"]],
                             "disposition": disposition,
                             "asset_ink_fraction": round(fraction, 4),
                             "event_ink_fraction": round(date_fraction, 4)})
        if discontinuous:
            rejections.append("discontinuous_asset_rows")
            continue
        if not filled:
            rejections.append("no_filled_asset_rows")
            continue
        candidate = {"table_top": filled[0]["upper"] if filled else rows[0]["upper"],
                     "table_bottom": filled[-1]["lower"] if filled else rows[-1]["lower"],
                     "physical_table_top": physical[0]["band"][0],
                     "physical_table_bottom": physical[-1]["band"][1],
                     "row_bands": [[row["upper"], row["lower"]] for row in filled],
                     "physical_row_bands": physical,
                     "header_band": [bands[run[0]]["upper"], bands[run[0]]["lower"]],
                     "column_family": column_family}
        if any(item["disposition"] == "unknown" for item in physical):
            provisional_candidates.append(candidate)
        else:
            candidates.append(candidate)
    if len(candidates) + len(provisional_candidates) != 1:
        reason = (rejections[0] if len(candidates) == 0 and len(set(rejections)) == 1
                  else "ambiguous_grid_region")
        result = {"status": "coverage_unknown", "reason": reason,
                  "candidate_count": len(candidates), "rejections": rejections,
                  "bands": bands}
        return result
    selected = (candidates or provisional_candidates)[0]
    unresolved = sum(row["disposition"] == "unknown"
                     for row in selected["physical_row_bands"])
    return {"status": "located_shadow", **selected, "bands": bands,
            "slot_coverage_status": "partial_unknown" if unresolved else "classified",
            "unresolved_slot_count": unresolved}


def locate_transaction_grid(image) -> dict:
    """Locate a grid with one unambiguous page orientation.

    The returned normalized bands use the coordinate frame named in the
    result. A rotated result must be transformed before joining original PDF
    page coordinates; this shadow module does not publish those joins.
    """
    original = _locate_upright_grid(image)
    original.update({"orientation_degrees": 0, "coordinate_frame": "original_page"})
    if original["status"] == "located_shadow" or original.get("physical_row_bands"):
        return original
    rotated = []
    for angle in (90, 270):
        candidate = _locate_upright_grid(image.rotate(angle, expand=True))
        if candidate["status"] == "located_shadow":
            candidate.update({"orientation_degrees": angle,
                              "coordinate_frame": f"rotated_page_{angle}",
                              "original_page_size_pixels": list(image.size)})
            rotated.append(candidate)
    if len(rotated) == 1:
        return rotated[0]
    if len(rotated) > 1:
        original["reason"] = "ambiguous_orientation"
    return original


def inspect_pdf(pdf_bytes: bytes, *, dpi: int = 150) -> list[dict]:
    """Render an already archived PDF and inspect each page, without mutation."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return [locate_transaction_grid(page.to_image(resolution=dpi).original)
                for page in pdf.pages]
