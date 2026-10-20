"""Bounded, read-only OCR observations inside confirmed raster 278-T slots."""

from __future__ import annotations

import hashlib
import json
from math import isfinite
import re
from pathlib import Path
from statistics import median

from .ocr_geometry import ocr_pdf_pages
from .oge_278t_page_shadow import (probe_oge_2024_alternate_bands,
                                   probe_raster_row_bands, _raster_rule_centers)


SCHEMA = "oge-278t-raster-slot-observation/v2"
MAX_PAGES_PER_RUN = 8
_COLUMNS = ("printed_number", "description", "type", "transaction_date",
            "notification", "amount")


def column_edges_at_y(page: dict, y: float) -> list[float]:
    """Evaluate source-PDF column lines; absence of a model keeps legacy x's."""
    edges = page["column_edges_pdf_points"]
    model = page.get("column_line_model")
    if model is None:
        return edges
    slopes = model.get("slopes_dx_per_dy")
    reference = model.get("reference_y_pdf_points")
    domain = model.get("valid_y_span_pdf_points")
    if (model.get("schema_version") != "oge-278t-column-lines/v1" or
            not isinstance(slopes, list) or len(slopes) != 7 or
            len(edges) != 7 or type(reference) not in (int, float) or not isfinite(reference) or
            type(y) not in (int, float) or not isfinite(y) or
            any(type(v) not in (int, float) or not isfinite(v) or abs(v) > .015 for v in slopes) or
            any(type(v) not in (int, float) or not isfinite(v) for v in edges) or
            model.get("coordinate_frame") != "displayed_mediabox" or
            page.get("render_coordinate_frame") != "displayed_mediabox" or
            not isinstance(domain, list) or len(domain) != 2 or
            any(type(v) not in (int,float) or not isfinite(v) for v in domain) or
            not domain[0] <= y <= domain[1]):
        raise ValueError("invalid source-PDF column line model")
    sections = model.get("regional_observations")
    if (not domain[0] < domain[1] or max(slopes)-min(slopes) > .008 or
            not isinstance(sections,list) or len(sections) != 3):
        raise ValueError("column line model lacks bounded regional evidence")
    ys = []
    previous = domain[0]
    for section in sections:
        span, positions = section.get("y_span_pdf_points"), section.get("edges")
        if (not isinstance(span,list) or len(span) != 2 or
                any(type(v) not in (int,float) or not isfinite(v) for v in span) or
                not previous <= span[0] < span[1] <= domain[1] or
                not isinstance(positions,list) or len(positions) != 7 or
                any(type(v) not in (int,float) or not isfinite(v) for v in positions) or
                any(a >= b for a,b in zip(positions,positions[1:]))):
            raise ValueError("column line model regional evidence is invalid")
        previous = span[1]
        ys.append((span[0]+span[1])/2)
    expected_ref = sum(ys)/3
    denominator = sum((v-expected_ref)**2 for v in ys)
    expected_edges = [sum(sec["edges"][i] for sec in sections)/3 for i in range(7)]
    expected_slopes = [sum((v-expected_ref)*(sec["edges"][i]-expected_edges[i])
                      for v,sec in zip(ys,sections))/denominator for i in range(7)]
    residual = max(abs(sec["edges"][i]-edges[i]-slopes[i]*(v-reference))
                   for i in range(7) for v,sec in zip(ys,sections))
    if (abs(reference-expected_ref) > 1e-6 or residual > .75 or
            any(abs(a-b) > 1e-6 for a,b in zip(edges,expected_edges)) or
            any(abs(a-b) > 1e-6 for a,b in zip(slopes,expected_slopes))):
        raise ValueError("column line model differs from fitted regional evidence")
    observed = page.get("column_geometry_observation", {}).get("column_line_model")
    if observed is not None and observed != model:
        raise ValueError("column line model differs from saved geometry evidence")
    result = [x + slope * (y-reference) for x, slope in zip(edges, slopes)]
    if any(a >= b for a,b in zip(result, result[1:])):
        raise ValueError("source-PDF column lines cross")
    return result


def column_crop_bounds(page: dict, start: int, end: int, top: float, bottom: float,
                       left_pad: float = .6, right_pad: float = 1.5) -> tuple[float, float]:
    """Rectangle enclosing a sloped column; evidence still checks actual lines."""
    domain = (page.get("column_line_model") or {}).get("valid_y_span_pdf_points")
    if domain:
        top,bottom = max(top,domain[0]),min(bottom,domain[1])
    a, b = column_edges_at_y(page, top), column_edges_at_y(page, bottom)
    return min(a[start], b[start])+left_pad, max(a[end], b[end])-right_pad


def column_geometry_sha256(page: dict) -> str:
    return hashlib.sha256(json.dumps({"edges": page.get("column_edges_pdf_points"),
        "model": page.get("column_line_model"), "frame": page.get("render_coordinate_frame")},
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _column_profile_matches(edges: list[float]) -> bool:
    if len(edges) != 7 or not 80 <= edges[0] <= 120 or not 675 <= edges[-1] <= 710:
        return False
    return all(abs((right - left) - target) <= limit
               for left, right, target, limit in zip(
                   edges[:-1], edges[1:], (45, 310, 51, 57, 47, 82),
                   (12, 18, 12, 12, 12, 18), strict=True))


def _rule_pixel_confirmation(gray, *, x: float, top: float, bottom: float,
                             scale_x: float, scale_y: float) -> dict:
    """Require one unbroken dark path plus majority thin-line support per third.

    A path may move one pixel horizontally per scanline, but cannot bridge a
    blank scanline. Text, horizontal rules or a dark block alone do not satisfy
    both requirements. No OCR or expected transaction count is involved.
    """
    import numpy as np

    left, right = int((x - 3) * scale_x), int((x + 3) * scale_x)
    first, last = int(top * scale_y), int(bottom * scale_y)
    if not (0 <= left < right <= gray.shape[1] and 0 <= first < last <= gray.shape[0]):
        return {"status": "unknown", "reason": "pixel_window_outside_page"}
    mask = gray[first:last, left:right] < 160
    reach = mask[0].copy()
    continuous_height = 1 if reach.any() else 0
    if continuous_height:
        for row in mask[1:]:
            reach = (reach | np.r_[False, reach[:-1]] | np.r_[reach[1:], False]) & row
            if not reach.any():
                break
            continuous_height += 1
    width = mask.sum(axis=1)
    runs = mask[:, 0].astype(int) + (~mask[:, :-1] & mask[:, 1:]).sum(axis=1)
    thin = (width >= 1) & (width <= max(1, int(2 * scale_x))) & (runs == 1)
    supports = [round(float(part.mean()), 6) for part in np.array_split(thin, 3)]
    confirmed = continuous_height == len(mask) and all(value > .5 for value in supports)
    return {"status": "confirmed" if confirmed else "unknown",
            "reason": None if confirmed else "no_continuous_unique_thin_rule",
            "x_pdf_points": x, "checked_span_pdf_points": [top, bottom],
            "continuous_path_scanlines": continuous_height, "checked_scanlines": len(mask),
            "thin_rule_support_by_third": supports}


def _oge_2024_column_geometry(page) -> dict:
    """Locate seven same-page rules; corroborate short vectors from page pixels.

    The original strong-vector path is unchanged. The restricted fallback
    never supplies a missing x from the template or another page: all seven
    candidates must already have vector evidence and match this form profile.
    """
    lines = sorted((float(line["x0"]), abs(float(line["y1"]) - float(line["y0"])),
                    float(line["top"]), float(line["bottom"]))
                   for line in page.lines
                   if abs(float(line["x1"]) - float(line["x0"])) <= 1 and
                   abs(float(line["y1"]) - float(line["y0"])) >= 15)
    groups = []
    for x, length, top, bottom in lines:
        if groups and x - groups[-1][-1][0] <= 2.5:
            groups[-1].append((x, length, top, bottom))
        else:
            groups.append([(x, length, top, bottom)])
    def edge(group):
        return round(sum(x * length for x, length, _, _ in group) /
                     sum(length for _, length, _, _ in group), 1)

    strong = [group for group in groups if sum(line[1] for line in group) >= 200]
    edges = [edge(group) for group in strong]
    if _column_profile_matches(edges):
        return {"status": "confirmed", "basis": "strong_vector_rules",
                "edges_pdf_points": edges, "weak_rule_observations": []}
    unknown = {"status": "unknown", "basis": None, "edges_pdf_points": None,
               "weak_rule_observations": []}
    candidates = [group for group in groups if sum(line[1] for line in group) >= 40]
    edges = [edge(group) for group in candidates]
    if len(strong) < 5 or not _column_profile_matches(edges):
        return {**unknown, "reason": "insufficient_same_page_vector_profile"}
    # pdfplumber lines and to_image share displayed page coordinates, including
    # /Rotate. A cropped origin would need an additional transform; leave it unknown.
    if tuple(page.bbox) != (0, 0, page.width, page.height):
        return {**unknown, "reason": "unsupported_raster_coordinate_frame"}
    top = max(min(line[2] for line in group) for group in strong) + 40
    bottom = min(max(line[3] for line in group) for group in strong) - 10
    if bottom - top < 120:
        return {**unknown, "reason": "insufficient_common_grid_span"}
    weak = [group for group in candidates if sum(line[1] for line in group) < 200]
    gaps = []
    for group in weak:
        cursor, uncovered = top, []
        for _, _, start, end in sorted(group, key=lambda line: line[2]):
            start, end = max(top, start), min(bottom, end)
            if start >= end:
                continue
            if start > cursor:
                uncovered.append([cursor, start])
            cursor = max(cursor, end)
        if bottom > cursor:
            uncovered.append([cursor, bottom])
        gap = max(uncovered, key=lambda span: span[1] - span[0], default=[top, top])
        if gap[1] - gap[0] < 40:
            return {**unknown, "reason": "no_independent_pixel_witness_span"}
        gaps.append(gap)
    import numpy as np

    gray = np.asarray(page.to_image(resolution=216, force_mediabox=True).original.convert("L"))
    scale_x, scale_y = gray.shape[1] / page.width, gray.shape[0] / page.height
    confirmations = [
        {**_rule_pixel_confirmation(gray, x=edge(group), top=top, bottom=bottom,
                                   scale_x=scale_x, scale_y=scale_y),
         "vector_length_pdf_points": round(sum(line[1] for line in group), 3),
         "vector_uncovered_span_pdf_points": gap}
        for group, gap in zip(weak, gaps, strict=True)]
    if not confirmations or any(item["status"] != "confirmed" for item in confirmations):
        return {**unknown, "reason": "weak_vector_not_confirmed_by_pixels",
                "weak_rule_observations": confirmations}
    return {"status": "confirmed", "basis": "same_page_vector_and_pixel_rules",
            "edges_pdf_points": edges, "weak_rule_observations": confirmations}


def _oge_2024_column_edges(page) -> list[float] | None:
    return _oge_2024_column_geometry(page)["edges_pdf_points"]


def _scanline_intervals(selected, *, first: int, scale_y: float) -> list[dict]:
    """Losslessly group observed scanlines; these intervals grant no support."""
    intervals = []
    for offset, value in enumerate(selected):
        if not value:
            continue
        row = first + offset
        if intervals and row == intervals[-1][1]:
            intervals[-1][1] = row + 1
        else:
            intervals.append([row, row + 1])
    return [{"scanline_range": [a,b], "scanline_count": b-a,
             "y_span_pdf_points": [a/scale_y,b/scale_y]} for a,b in intervals]


def _image_rule_witness(gray, *, x, top, bottom, scale_x, scale_y,
                         slope=0., reference_y=0., require_fixed_x=False) -> dict:
    """Trace a scanned rule, allowing <= .75pt blank gaps, not missing segments.

    This is separate from the unchanged strict vector corroboration rule.
    Every scanline must connect within 1pt laterally, and each third needs
    majority unique thin-rule support. Horizontal intersections are not proof.
    """
    import numpy as np
    left, right = int((x - 3) * scale_x), int((x + 3) * scale_x)
    first, last = int(top * scale_y), int(bottom * scale_y)
    if not (0 <= left < right <= gray.shape[1] and 0 <= first < last <= gray.shape[0]):
        return {"status": "unknown", "reason": "pixel_window_outside_page",
                "failure_reasons": ["witness_window_outside_page"]}
    if slope:
        centers = x + slope * (np.arange(first,last)/scale_y-reference_y)
        starts = ((centers-3)*scale_x).astype(int)
        width = right-left
        if starts.min() < 0 or starts.max()+width > gray.shape[1]:
            return {"status": "unknown", "reason": "pixel_window_outside_page",
                "failure_reasons": ["witness_window_outside_page"]}
        window = gray[np.arange(first,last)[:,None], starts[:,None]+np.arange(width)]
    else:
        window = gray[first:last, left:right]
    mask = window < 160
    if mask.size == 0:
        return {"status": "unknown", "reason": "empty_rule_window",
                "failure_reasons": ["empty_witness_window"]}
    max_gap = max(1, int(.75 * scale_y))
    jump = max(1, int(scale_x))
    # The state is consecutive blank scanlines since the last connected pixel.
    state = np.where(mask[0], 0, max_gap + 1)
    connected = bool((state == 0).any())
    first_disconnected = None if connected else first
    for scanline, row in enumerate(mask[1:], start=first+1):
        nearby = state.copy()
        for shift in range(1, jump + 1):
            nearby[shift:] = np.minimum(nearby[shift:], state[:-shift])
            nearby[:-shift] = np.minimum(nearby[:-shift], state[shift:])
        state = np.where(row & (nearby <= max_gap), 0, state + 1)
        if not (state <= max_gap).any():
            connected = False
            if first_disconnected is None:
                first_disconnected = scanline
            break
    width = mask.sum(axis=1)
    runs = mask[:, 0].astype(int) + (~mask[:, :-1] & mask[:, 1:]).sum(axis=1)
    thin = (width >= 1) & (width <= max(1, int(2 * scale_x))) & (runs == 1)
    thirds = [round(float(part.mean()), 6) for part in np.array_split(thin, 3)]
    # Connectivity inside a broad window does not prove the assumed line.
    # Bound every observed thin center, including short bends which regional
    # medians would hide. A linear model uses the same .75pt residual bound.
    positions = (mask @ np.arange(mask.shape[1])) / np.maximum(width, 1)
    observed = positions + (starts if slope else left)
    expected = (x + slope*(np.arange(first,last)/scale_y-reference_y))*scale_x
    deviation = float(np.max(np.abs(observed[thin]-expected[thin])))/scale_x if thin.any() else None
    allowed_deviation = 1/scale_x if require_fixed_x else .75
    axis_span = float(np.ptp(observed[thin])) if require_fixed_x and thin.any() else None
    confirmed = (connected and bool((state == 0).any()) and all(v > .5 for v in thirds)
                 and deviation is not None and deviation <= allowed_deviation)
    # Explain all failures without changing any acceptance rule. In particular,
    # an empty black mask is not proof that the source image has no gray line.
    blank = ~mask.any(axis=1)
    off_track = thin & (np.abs(observed-expected)/scale_x > allowed_deviation)
    reasons = []
    if not connected:
        reasons.append("line_connection_interrupted")
    elif not bool((state == 0).any()):
        reasons.append("line_endpoint_without_black_support")
    if not all(v > .5 for v in thirds):
        reasons.append("thin_line_support_insufficient")
    if deviation is None:
        reasons.append("no_thin_center_observed")
    elif deviation > allowed_deviation:
        reasons.append("line_center_off_trajectory")
    blank_intervals = _scanline_intervals(blank, first=first, scale_y=scale_y)
    off_track_intervals = _scanline_intervals(off_track, first=first, scale_y=scale_y)
    # Sample first/middle/last rows per failed interval; all interval extents
    # and counts remain complete. Samples are raw pixels, not guessed causes.
    samples = []
    for kind, intervals in (("black_mask_empty",blank_intervals),
                            ("thin_center_off_trajectory",off_track_intervals)):
        for interval in intervals:
            a,b = interval["scanline_range"]
            for scanline in sorted({a,(a+b-1)//2,b-1}):
                offset = scanline-first
                samples.append({"kind":kind, "scanline":scanline,
                    "y_pdf_points":scanline/scale_y,
                    "expected_x_pdf_points":float(expected[offset]/scale_x),
                    "observed_thin_center_x_pdf_points":float(observed[offset]/scale_x) if thin[offset] else None,
                    "window_x0_pdf_points":float((starts[offset] if slope else left)/scale_x),
                    "gray_values":window[offset].tolist()})
    return {"status": "confirmed" if confirmed else "unknown",
            "failure_reasons": reasons,
            "first_disconnected_scanline": first_disconnected,
            "first_disconnected_y_pdf_points": first_disconnected/scale_y if first_disconnected is not None else None,
            "black_mask_empty_intervals": blank_intervals,
            "off_trajectory_intervals": off_track_intervals,
            "multiple_stroke_intervals": _scanline_intervals(runs > 1, first=first, scale_y=scale_y),
            "failure_pixel_samples": samples,
            "failure_pixel_sampling": "first_middle_last_scanline_per_interval",
            "black_pixel_threshold_exclusive": 160,
            **({"axis_center_span_pixels": axis_span} if require_fixed_x else {}),
            "x_pdf_points": round(x, 3), "span_pdf_points": [top, bottom],
            "max_blank_gap_scanlines": max_gap, "max_lateral_step_pixels": jump,
            "maximum_center_deviation_pdf_points": deviation,
            "allowed_center_deviation_pdf_points": allowed_deviation,
            "thin_rule_support_by_third": thirds}


def _image_columns_sampleable(edges: list[float], *, page_width: float,
                              scale_x: float) -> bool:
    """Require seven visible lines and a pixel interior for every column.

    Width is not a form identifier. The same-page rules remain candidates
    until the existing form title and per-column header gate confirms roles.
    Three points on each side are needed by the vertical/horizontal witnesses.
    """
    return (len(edges) == 7 and
            all(type(x) in (int, float) and isfinite(x) for x in edges) and
            3 <= edges[0] < edges[-1] <= page_width-3 and
            all((b-a-6)*scale_x >= 1 for a,b in zip(edges, edges[1:])))


def _horizontal_rule_support(gray, y, edges, sx, sy):
    return [round(float((gray[max(0,int((y-2)*sy)):int((y+2)*sy),
                        int((a+3)*sx):int((b-3)*sx)] < 160).any(axis=0).mean()), 6)
            for a,b in zip(edges, edges[1:])]


def _joint_horizontal_partition(gray, rules, edges, *, sx, sy, fit=None):
    """Keep unmatched boundaries as barriers; never delete one to join rows."""
    from itertools import product
    if len(rules) != 3 or any(len(row) > 128 for row in rules):
        return {"status":"unknown","reason":"horizontal_candidate_inventory_outside_bounds",
                "evidence":{"strip_rule_positions":rules}}
    triples = [indices for indices in product(*(range(len(row)) for row in rules))
               if max(rules[c][i] for c,i in enumerate(indices))-
                  min(rules[c][i] for c,i in enumerate(indices)) <= 2]
    used = {(c,i):sum(t[c] == i for t in triples) for c,row in enumerate(rules) for i in range(len(row))}
    unique = [t for t in triples if all(used[c,i] == 1 for c,i in enumerate(t))]
    centers = [round(median(rules[c][i] for c,i in enumerate(t)),1) for t in unique]
    ordered = sorted(zip(centers,unique))
    centers, unique = [x[0] for x in ordered], [x[1] for x in ordered]
    evidence = {"schema_version":"oge-278t-joint-horizontal-boundaries/v1",
        "strip_rule_positions":rules,"matched_triples":[list(t) for t in unique],
        "matched_centers_pdf_points":centers,"unmatched_candidates":[],"six_column_candidate_positions":[]}
    if len(centers) < 6 or any(any(a[c] >= b[c] for c in range(3)) for a,b in zip(unique,unique[1:])):
        return {"status":"unknown","reason":"horizontal_matching_missing_or_ambiguous","evidence":evidence}
    assigned = {(c,i) for t in unique for c,i in enumerate(t)}
    barriers = []
    for c,row in enumerate(rules):
        for i,y in enumerate(row):
            if (c,i) not in assigned:
                item={"kind":"ambiguous_strip_candidate" if used[c,i] else "unmatched_strip_candidate",
                      "strip_index":c,"candidate_index":i,"y_pdf_points":y}
                evidence["unmatched_candidates"].append(item);barriers.append(item)
    gaps=[b-a for a,b in zip(centers,centers[1:])];typical=median(gaps)
    if not 8 <= typical <= 25 or centers[-1]-centers[0] < 200:
        return {"status":"unknown","reason":"image_row_profile_unknown","evidence":evidence}
    # A real broken divider can survive only in a right-hand column. Inventory
    # all six interiors using the SAME detector, not just the three seed strips.
    shape={"column_edges_pdf_points":edges,"column_line_model":fit["model"] if fit else None,
           "render_coordinate_frame":"displayed_mediabox"}
    domain=[centers[0],centers[-1]]
    end_edges=[column_edges_at_y(shape,y) for y in domain]
    for c in range(6):
        left=max(row[c] for row in end_edges)+3;right=min(row[c+1] for row in end_edges)-3
        if right <= left or int(right*sx) <= int(left*sx):
            return {"status":"unknown","reason":"column_interior_not_sampleable","evidence":evidence}
        candidates=_raster_rule_centers(gray,points_per_pixel=1/sy,x_points_per_pixel=1/sx,x0=left,x1=right)
        evidence["six_column_candidate_positions"].append(candidates)
        for i,y in enumerate(candidates):
            matches=[j for j,center in enumerate(centers) if abs(center-y) <= 2]
            if len(matches) != 1:
                barriers.append({"kind":"unmatched_column_candidate","column_index":c,
                                 "candidate_index":i,"y_pdf_points":y})
    crossings=[_horizontal_rule_support(gray,y,column_edges_at_y(shape,y),sx,sy) for y in centers]
    supported=[all(isfinite(v) and v >= .95 for v in row) for row in crossings]
    bands,unresolved,intervals=[],[],[]
    for i,(a,b) in enumerate(zip(centers,centers[1:])):
        local=[item for item in barriers if a-2 <= item["y_pdf_points"] <= b+2]
        reasons=[]
        if not supported[i] or not supported[i+1]:reasons.append("horizontal_rule_not_across_all_columns")
        if not 8 <= b-a <= min(25,typical*1.5):reasons.append("row_spacing_outside_existing_bounds")
        if local:reasons.append("unresolved_boundary_inside_interval")
        intervals.append({"band_pdf_points":[a,b],"status":"unknown" if reasons else "supported",
                          "reasons":reasons,"boundary_candidates":local})
        (unresolved if reasons else bands).append([a,b])
    evidence.update(boundary_barriers=barriers,intervals=intervals,
                    horizontal_rule_support_by_column=crossings)
    if not bands:return {"status":"unknown","reason":"no_supported_local_row_interval","evidence":evidence}
    return {"status":"candidate","centers":centers,"bands":bands,
            "unresolved_regions":unresolved,"crossings":crossings,"evidence":evidence}


def _oge_2024_axis_image_grid(page, *, fit=None, partial_rows=False) -> dict:
    """Find same-page image lines, pending independent OCR header role checks.

    No expected x, row count, printed number or neighbouring page fills a gap.
    Page-local sampling bounds permit variable column widths and placement.
    """
    import numpy as np
    unknown = {"status": "unknown", "basis": "image_rules",
               "edges_pdf_points": None, "weak_rule_observations": []}
    if tuple(page.bbox) != (0, 0, page.width, page.height):
        return {**unknown, "reason": "unsupported_raster_coordinate_frame"}
    image = page.to_image(resolution=216, force_mediabox=True).original.convert("L")
    gray = np.asarray(image)
    sx, sy = gray.shape[1] / page.width, gray.shape[0] / page.height
    mask = gray < 160
    expanded = mask.copy()
    radius = max(1, int(sx))
    for shift in range(1, radius + 1):
        expanded[:, shift:] |= mask[:, :-shift]
        expanded[:, :-shift] |= mask[:, shift:]
    hits = np.flatnonzero(expanded.mean(axis=0) > .60)
    groups = []
    for pixel in hits:
        x = float(pixel) / sx
        if not 3 <= x <= page.width-3:
            continue
        if groups and x - groups[-1][-1] <= 2.5:
            groups[-1].append(x)
        else:
            groups.append([x])
    edges = fit["edges"] if fit else [round(float(median(group)), 3) for group in groups]
    if (not _image_columns_sampleable(edges, page_width=page.width, scale_x=sx) or
            (not fit and any(group[-1] - group[0] > 6 for group in groups))):
        return {**unknown, "reason": "image_column_profile_unknown",
                "candidate_edges_pdf_points": edges}
    # Three disjoint strips in number/description columns independently count
    # horizontal rules. They are derived from observed boundaries, not a row total.
    strips = [(edges[0] + .30*(edges[1]-edges[0]), edges[0] + .65*(edges[1]-edges[0]))]
    strips += [(edges[1] + f*(edges[2]-edges[1]),
                edges[1] + f*(edges[2]-edges[1]) + min(15, .15*(edges[2]-edges[1]))) for f in (.20, .40)]
    rules = [_raster_rule_centers(gray, points_per_pixel=1/sy, x_points_per_pixel=1/sx,
                                 x0=a, x1=b) for a,b in strips]
    partial = None
    if partial_rows:
        partial=_joint_horizontal_partition(gray,rules,edges,sx=sx,sy=sy,fit=fit)
        if partial["status"] != "candidate":
            return {**unknown,"reason":partial["reason"],"joint_horizontal_evidence":partial["evidence"]}
        centers=partial["centers"]
    elif (len(rules[0]) < 6 or len({len(row) for row in rules}) != 1 or
            any(max(row)-min(row) > 2 for row in zip(*rules))):
        return {**unknown, "reason": "image_horizontal_rules_disagree",
                "strip_rule_counts": [len(row) for row in rules]}
    else:
        centers = [round(median(row), 1) for row in zip(*rules)]
    gaps = [b-a for a,b in zip(centers, centers[1:])]
    typical = median(gaps)
    if not partial_rows and (not 8 <= typical <= 25 or any(not 8 <= gap <= min(25, typical*1.5) for gap in gaps)
            or centers[-1]-centers[0] < 200):
        return {**unknown, "reason": "image_row_profile_unknown"}
    crossings = []
    for y in centers:
        local_edges = column_edges_at_y({"column_edges_pdf_points": edges,
                                       "column_line_model": fit["model"], "render_coordinate_frame":"displayed_mediabox"}, y) if fit else edges
        if not _image_columns_sampleable(local_edges, page_width=page.width, scale_x=sx):
            return {**unknown, "reason": "column_widths_outside_supported_domain"}
        support = _horizontal_rule_support(gray,y,local_edges,sx,sy)
        crossings.append(support)
    if not partial_rows and any(not isfinite(value) or value < .95 for row in crossings for value in row):
        return {**unknown, "reason": "image_horizontal_rule_not_across_columns",
                "horizontal_rule_support_by_column": crossings}
    if fit:
        # Include the existing 3pt header-box overhang in the witnessed domain.
        fit["model"]["valid_y_span_pdf_points"] = [max(50,centers[0]-23),centers[-1]]
        if any(section["y_span_pdf_points"][0] < fit["model"]["valid_y_span_pdf_points"][0] or
               section["y_span_pdf_points"][1] > centers[-1]
               for section in fit["model"]["regional_observations"]):
            return {**unknown,"reason":"regional_evidence_outside_table_domain"}
    if fit:
        modeled = {"column_edges_pdf_points":edges,"column_line_model":fit["model"],
                   "render_coordinate_frame":"displayed_mediabox"}
        for endpoint in fit["model"]["valid_y_span_pdf_points"]:
            local = column_edges_at_y(modeled,endpoint)
            if not _image_columns_sampleable(local, page_width=page.width, scale_x=sx):
                return {**unknown,"reason":"column_widths_outside_supported_domain"}
    # Each census strip must remain in its own column throughout the table.
    # Measuring adjacent cells cannot substitute for independent strip evidence.
    endpoints = ([column_edges_at_y(modeled, y) for y in fit["model"]["valid_y_span_pdf_points"]]
                 if fit else [edges])
    for (left, right), column in zip(strips, (0, 1, 1), strict=True):
        if (not max(row[column] for row in endpoints) <= left < right <=
                min(row[column+1] for row in endpoints) or
                int(right*sx) <= int(left*sx)):
            return {**unknown, "reason": "image_strip_outside_observed_column"}
    if strips[1][1] >= strips[2][0]:
        return {**unknown, "reason": "image_strips_overlap"}
    top, bottom = (fit["model"]["valid_y_span_pdf_points"][0] if fit else centers[0]+2), (centers[-1] if fit else centers[-1]-2)
    witnesses = [_image_rule_witness(gray, x=x, top=top, bottom=bottom,
                                     scale_x=sx, scale_y=sy,
                                     slope=fit["model"]["slopes_dx_per_dy"][i] if fit else 0.,
                                     reference_y=fit["model"]["reference_y_pdf_points"] if fit else 0.,
                                     require_fixed_x=not bool(fit))
                 for i,x in enumerate(edges)]
    if any(item["status"] != "confirmed" for item in witnesses):
        return {**unknown, "reason": "image_vertical_rule_not_connected",
                "weak_rule_observations": witnesses}
    return {**({"column_line_model": fit["model"]} if fit else {}),
            "status": "candidate", "basis": "same_page_image_grid",
            "layout_profile": "oge_2024_variable_column_widths",
            "edges_pdf_points": edges, "weak_rule_observations": witnesses,
            "physical_row_bands": partial["bands"] if partial else [[a,b] for a,b in zip(centers, centers[1:])],
            **({"partial_row_geometry":True,"unresolved_regions":partial["unresolved_regions"],
                "joint_horizontal_evidence":partial["evidence"],"whole_page_physical_slot_count":None} if partial else {}),
            "horizontal_rule_strips_pdf_points": strips, "strip_rule_positions": rules,
            "horizontal_rule_support_by_column": crossings,
            "header_band_pdf_points": [max(50, centers[0]-(20 if fit else 60)), centers[0]],
            "render_coordinate_frame": "displayed_mediabox",
            "render_page_bbox": list(page.bbox), "render_rotation": page.rotation,
            "render_source_cropbox": list(page.cropbox),
            "render_pdf_points_per_pixel": [1/sx, 1/sy],
            "render_pixel_size": [image.width, image.height]}


def _oge_2024_image_grid_search(page, *, partial_rows=False) -> dict:
    """Try the legacy axis path first, then fit observed lines in three regions."""
    axis = _oge_2024_axis_image_grid(page,partial_rows=partial_rows)
    if axis["status"] == "candidate" or axis.get("reason") == "unsupported_raster_coordinate_frame":
        return axis
    import numpy as np
    gray = np.asarray(page.to_image(resolution=216, force_mediabox=True).original.convert("L"))
    sx, sy = gray.shape[1]/page.width, gray.shape[0]/page.height
    sections = []
    for low, high in ((.20,.40),(.45,.65),(.70,.90)):
        top, bottom = page.height*low, page.height*high
        mask = gray[int(top*sy):int(bottom*sy)] < 160
        expanded = mask.copy()
        for shift in range(1,max(1,int(sx))+1):
            expanded[:,shift:] |= mask[:,:-shift]
            expanded[:,:-shift] |= mask[:,shift:]
        groups = []
        for pixel in np.flatnonzero(expanded.mean(axis=0) > .70):
            x = float(pixel)/sx
            if not 3 <= x <= page.width-3:
                continue
            if groups and x-groups[-1][-1] <= 2.5:
                groups[-1].append(x)
            else:
                groups.append([x])
        positions = [float(median(group)) for group in groups]
        if not 7 <= len(positions) <= 14 or any(group[-1]-group[0] > 6 for group in groups):
            return {**axis, "drift_fallback_reason": "regional_columns_missing_or_ambiguous",
                    "regional_column_counts": [len(item["edges"]) for item in sections]+[len(positions)],
                    "regional_candidate_line_observations": sections + [
                        {"y_span_pdf_points": [top,bottom], "edges": positions}],
                    "candidate_window_count": None}
        sections.append({"y_span_pdf_points": [top,bottom], "edges": positions})
    counts = [len(section["edges"]) for section in sections]
    if len(set(counts)) != 1:
        return {**axis, "drift_fallback_reason": "regional_column_counts_disagree",
                "regional_column_counts": counts,
                "regional_candidate_line_observations": sections, "candidate_window_count": None}
    # Scan-image borders may add outside lines. Never skip an internal line:
    # test consecutive seven-line windows and require a unique fully witnessed
    # six-column grid. Multiple plausible grids remain explicitly ambiguous.
    candidates, failures = [], []
    diagnostics = {"regional_candidate_line_observations": sections,
                   "candidate_window_count": counts[0]-6}
    for start in range(counts[0]-6):
        selected = [{**section, "edges": section["edges"][start:start+7]} for section in sections]
        ys = [(item["y_span_pdf_points"][0]+item["y_span_pdf_points"][1])/2 for item in selected]
        reference = sum(ys)/3
        denominator = sum((y-reference)**2 for y in ys)
        edges = [sum(item["edges"][i] for item in selected)/3 for i in range(7)]
        slopes = [sum((y-reference)*(item["edges"][i]-edges[i]) for y,item in zip(ys,selected))/denominator
                  for i in range(7)]
        residual = max(abs(item["edges"][i]-(edges[i]+slopes[i]*(y-reference)))
                       for i in range(7) for y,item in zip(ys,selected))
        if max(abs(s) for s in slopes) > .015 or max(slopes)-min(slopes) > .008 or residual > .75:
            failures.append({"status":"unknown", "reason":"regional_lines_do_not_fit_bounded_model",
                             "candidate_start_index": start})
            continue
        model = {"schema_version": "oge-278t-column-lines/v1", "reference_y_pdf_points": reference,
                 "slopes_dx_per_dy": slopes, "regional_observations": selected,
                 "maximum_fit_residual_pdf_points": residual, "coordinate_frame": "displayed_mediabox",
                 "valid_y_span_pdf_points": [0.,page.height]}
        fitted = _oge_2024_axis_image_grid(page, fit={"edges": edges, "model": model},partial_rows=partial_rows)
        fitted = {**fitted, "candidate_start_index": start}
        if fitted["status"] == "candidate":candidates.append(fitted)
        else:failures.append(fitted)
    if len(candidates) == 1:
        result = candidates[0]
        if counts[0] > 7:
            result = {**result, **diagnostics, "candidate_window_failures": failures,
                      "unique_supported_grid_count": 1}
        return result
    if candidates:
        return {**axis, **diagnostics, "drift_fallback_reason": "multiple_supported_image_grids",
                "supported_grid_count": len(candidates), "supported_image_grids": candidates,
                "candidate_window_failures": failures}
    return {**axis, **diagnostics, **({"drift_fallback_observation": failures[0]} if counts[0] == 7 else
                                     {"candidate_window_failures": failures})}


def _oge_2024_image_grid(page, *, allow_partial_rows=False) -> dict:
    strict=_oge_2024_image_grid_search(page)
    if not allow_partial_rows:return strict
    partial=_oge_2024_image_grid_search(page,partial_rows=True)
    return {**partial,"strict_geometry_observation":strict}


def _header_role_evidence(words: list[dict], geometry: dict) -> dict:
    """Recognize fixed form labels only; fuzzy labels never correct a fact."""
    def near(a, b):
        if a == b:
            return True
        if abs(len(a)-len(b)) > 1:
            return False
        return any(a[:i]+a[i+1:] == b for i in range(len(a))) if len(a)>len(b) else (
            any(b[:i]+b[i+1:] == a for i in range(len(b))) if len(b)>len(a) else
            sum(x != y for x,y in zip(a,b)) == 1)
    top, bottom = geometry["header_band_pdf_points"]
    labels = {}
    shape = {"column_edges_pdf_points": geometry["edges_pdf_points"],
             "column_line_model": geometry.get("column_line_model"),
             "render_coordinate_frame": geometry.get("render_coordinate_frame")}
    for column, label in enumerate(("description", "type", "date", "notification", "amount"), 1):
        hits = []
        for i,w in enumerate(words):
            center = (w["top"]+w["bottom"])/2
            if not (top <= center < bottom and w["top"] >= top-3 and w["bottom"] <= bottom+3):
                continue
            if not near(re.sub(r"[^a-z]", "", w["text"].lower()),label):
                continue
            domain = (geometry.get("column_line_model") or {}).get("valid_y_span_pdf_points")
            if domain and not domain[0] <= w["top"] < w["bottom"] <= domain[1]:
                continue
            local = column_edges_at_y(shape, center)
            a,b = column_edges_at_y(shape,w["top"]),column_edges_at_y(shape,w["bottom"])
            if (top <= center < bottom and w["top"] >= top-3 and w["bottom"] <= bottom+3
                    and local[column] <= (w["x0"]+w["x1"])/2 < local[column+1]
                    and w["x0"] >= max(a[column],b[column])-2
                    and w["x1"] <= min(a[column+1],b[column+1])+2
                    and near(re.sub(r"[^a-z]", "", w["text"].lower()),label)):
                hits.append(i)
        labels[label] = hits
    title = [i for i,w in enumerate(words) if w["bottom"] < top and
             re.sub(r"[^a-z0-9]", "", w["text"].lower()) in {"oge", "form", "2024", "278t", "2787"}]
    title_groups = []
    for i in sorted(title, key=lambda i: words[i]["top"]):
        if title_groups and words[i]["top"]-words[title_groups[-1][0]]["top"] <= 3:
            title_groups[-1].append(i)
        else:
            title_groups.append([i])
    coherent_titles = []
    for group in title_groups:
        ordered = sorted(group, key=lambda i: words[i]["x0"])
        values = [re.sub(r"[^a-z0-9]", "", words[i]["text"].lower()) for i in ordered]
        if (len(values) == 4 and values[:2] == ["oge", "form"] and
                values[2] in {"278t", "2787"} and values[3] == "2024"):
            coherent_titles.append(ordered)
    confirmed = all(len(hits) == 1 for hits in labels.values()) and len(coherent_titles) == 1
    if len(coherent_titles) == 1:
        title = coherent_titles[0]
    indices = sorted(set(title + [i for hits in labels.values() for i in hits]))
    return {"status": "confirmed" if confirmed else "unknown", "label_word_indices": labels,
            "title_word_indices": title, "observed_words": [{"index":i, **words[i]} for i in indices],
            "maximum_label_character_edits": 1, "fact_resolution": "not_assessed"}


def _partial_header_geometry(words: list[dict], geometry: dict) -> tuple[dict,dict]:
    """Bind a unique header to adjacent observed rules, never a preferred row count."""
    evidence=geometry["joint_horizontal_evidence"]
    centers=evidence["matched_centers_pdf_points"]
    crossings=evidence["horizontal_rule_support_by_column"]
    bands=[[geometry["header_band_pdf_points"][0],centers[0]],
           *[[a,b] for a,b in zip(centers,centers[1:])]]
    confirmed=[]
    for i,band in enumerate(bands):
        if not all(isfinite(v) and v>=.95 for v in crossings[i]):continue
        candidate={**geometry,"header_band_pdf_points":band}
        header=_header_role_evidence(words,candidate)
        if header["status"] == "confirmed":confirmed.append((i,candidate,header))
    if len(confirmed) != 1:
        return geometry,{"status":"unknown","reason":"partial_header_not_unique",
                         "confirmed_header_candidate_count":len(confirmed)}
    i,result,header=confirmed[0];bottom=result["header_band_pdf_points"][1]
    result={**result,"physical_row_bands":[b for b in result["physical_row_bands"] if b[0]>=bottom],
        "unresolved_regions":[[max(bottom,a),b] for a,b in result["unresolved_regions"] if b>bottom],
        "header_boundary_match_index":i}
    if not result["physical_row_bands"]:
        return result,{**header,"status":"unknown","reason":"no_supported_row_below_header"}
    return result,header


def _observe_slot_columns(words: list[dict], edges: list[float] | None, *, page: dict | None = None) -> dict:
    """Assign saved words by same-page columns, retaining out-of-column words."""
    if edges is None:
        return {"column_observation": None, "boundary_crossing_word_count": None,
                "unassigned_column_word_count": None}
    if len(edges) != 7 or any(left >= right for left, right in zip(edges, edges[1:])):
        raise ValueError("six-column observation requires seven ordered boundaries")
    by_column: dict[str, list[int]] = {name: [] for name in _COLUMNS}
    crossings = assigned = 0
    outside_domain = []
    for index, word in enumerate(words):
        x0, top, x1, bottom = word["bbox_pdf_points"]
        local = column_edges_at_y(page, (top+bottom)/2) if page else edges
        for name, left, right in zip(_COLUMNS, local[:-1], local[1:], strict=True):
            if left <= (x0 + x1) / 2 < right:
                by_column[name].append(index)
                assigned += 1
                break
        domain = (page.get("column_line_model") or {}).get("valid_y_span_pdf_points") if page else None
        if domain and not domain[0] <= top < bottom <= domain[1]:
            outside_domain.append(index)
            top,bottom = max(top,domain[0]),min(bottom,domain[1])
        a,b = (column_edges_at_y(page,top),column_edges_at_y(page,bottom)) if page else (edges,edges)
        crossings += any(x0 < max(left,right) and x1 > min(left,right)
                         for left,right in zip(a[1:-1],b[1:-1]))
    return {**({"column_model_outside_word_indices": outside_domain} if page and page.get("column_line_model") else {}),
            "column_observation": {
                name: {"raw_ocr_text": " ".join(words[index]["text"] for index in indices),
                       "word_indices": indices} for name, indices in by_column.items()},
            "boundary_crossing_word_count": crossings,
            "unassigned_column_word_count": len(words) - assigned}


def census_with_2024_fallback(pdf_path: Path, *, expected_sha256: str,
                              page_numbers: list[int], allow_partial_rows=False) -> tuple[dict, dict[int, list[float] | None]]:
    """Count raster slots, retrying only independently confirmed 2024 grids."""
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError("pdfplumber is required for 278-T grid observation") from None
    source = Path(pdf_path)
    census = probe_raster_row_bands(
        source, expected_sha256=expected_sha256, page_numbers=page_numbers)
    with pdfplumber.open(source) as document:
        geometry = {number: _oge_2024_column_geometry(document.pages[number - 1])
                    for number in page_numbers}
        for number, item in list(geometry.items()):
            if item["status"] == "unknown":
                image_grid = _oge_2024_image_grid(document.pages[number - 1],allow_partial_rows=allow_partial_rows)
                if image_grid["status"] == "candidate":
                    geometry[number] = image_grid
                else:
                    geometry[number] = {**item, "image_fallback_observation": image_grid}
        edges_by_page = {number: item["edges_pdf_points"] if item["status"] == "confirmed" else None
                         for number, item in geometry.items()}
    retry_pages = [page["page_number"] for page in census["results"]
                   if page["status"] != "physical_slots_located" and
                   edges_by_page[page["page_number"]] is not None]
    recovered = {}
    for start in range(0, len(retry_pages), MAX_PAGES_PER_RUN):
        fallback = probe_oge_2024_alternate_bands(
            source, expected_sha256=expected_sha256,
            page_numbers=retry_pages[start:start + MAX_PAGES_PER_RUN])
        recovered.update({page["page_number"]: page for page in fallback["results"]
                          if page["status"] == "physical_slots_located"})
    census["results"] = [recovered.get(page["page_number"], page)
                         for page in census["results"]]
    for page in census["results"]:
        page["column_geometry_observation"] = geometry[page["page_number"]]
        image_grid = geometry[page["page_number"]]
        if image_grid.get("basis") == "same_page_image_grid":
            # Other census consumers must not count a pending template as a
            # confirmed grid. Only observe_raster_slots can check its OCR header.
            page.update(status="coverage_unknown", physical_row_bands=[],
                        row_detection_basis="same_page_image_grid_pending_header")
    return census, edges_by_page


def observe_raster_slots(pdf_path: Path, *, expected_sha256: str,
                         page_numbers: list[int],
                         executable: str | None = None,
                         resolution: int = 200,
                         saved_ocr_pages: dict | None = None,
                         saved_ocr_engine: str | None = None,
                         allow_partial_rows: bool = False) -> dict:
    """Retain OCR words by independently located physical slot.

    OCR words are observations only.  A slot without recognized words may be
    blank or unreadable, and a slot with words is not yet a transaction.
    """
    if (not page_numbers or len(page_numbers) > MAX_PAGES_PER_RUN or
            any(type(number) is not int or number < 1 for number in page_numbers) or
            len(set(page_numbers)) != len(page_numbers)):
        raise ValueError("278-T raster observation needs 1-8 distinct pages")
    if type(resolution) is not int or not 150 <= resolution <= 400:
        raise ValueError("278-T raster observation DPI is outside bounds")
    source = Path(pdf_path)
    if type(allow_partial_rows) is not bool or (allow_partial_rows and saved_ocr_pages is None):
        raise ValueError("partial row geometry requires explicit saved OCR pages")
    actual_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError("278-T raster observation source SHA-256 differs")
    census, edges_by_page = census_with_2024_fallback(
        source, expected_sha256=expected_sha256, page_numbers=page_numbers,
        **({"allow_partial_rows":True} if allow_partial_rows else {}))
    import pdfplumber
    with pdfplumber.open(source) as document:
        known_pages = [page["page_number"] for page in census["results"]
                       if page["status"] == "physical_slots_located" or
                       page["column_geometry_observation"].get("status") == "candidate"]
        if saved_ocr_pages is not None:
            if (not isinstance(saved_ocr_pages, dict) or set(saved_ocr_pages) != set(page_numbers) or
                    not isinstance(saved_ocr_engine, str) or not saved_ocr_engine):
                raise ValueError("saved OCR pages differ from selected geometry pages")
            for number in page_numbers:
                actual, saved = document.pages[number - 1], saved_ocr_pages[number]
                if (tuple(actual.cropbox) != tuple(actual.mediabox) or
                        float(saved.width) != float(actual.width) or float(saved.height) != float(actual.height)):
                    raise ValueError("saved OCR coordinate frame cannot be bound to original page")
            ocr_pages, engine = [saved_ocr_pages[number] for number in known_pages], saved_ocr_engine
        else:
            ocr_pages, engine = (ocr_pdf_pages(
            document, page_numbers=known_pages, max_pages=MAX_PAGES_PER_RUN,
            resolution=resolution, executable=executable, force_mediabox=True)
            if known_pages else ([], None))
    ocr_by_page = dict(zip(known_pages, ocr_pages, strict=True))
    pages = []
    for page in census["results"]:
        number = page["page_number"]
        image_candidate = page["column_geometry_observation"].get("status") == "candidate"
        if page["status"] != "physical_slots_located" and not image_candidate:
            pages.append({"page_number": number,
                          "coverage": "physical_slots_unknown",
                          "physical_slot_count": None,
                          "column_geometry_observation": page["column_geometry_observation"],
                          "slots": [], "ocr_word_count": None})
            continue
        words = ocr_by_page[number].extract_words()
        geometry = page["column_geometry_observation"]
        if geometry.get("basis") == "same_page_image_grid":
            if geometry.get("partial_row_geometry"):
                geometry,header=_partial_header_geometry(words,geometry)
            else:
                header = _header_role_evidence(words, geometry)
            geometry = {**geometry, "header_role_evidence": header,
                        "status": header["status"]}
            if header["status"] != "confirmed":
                pages.append({"page_number": number, "coverage": "physical_slots_unknown",
                              "physical_slot_count": None, "slots": [],
                              "column_geometry_observation": geometry,
                              "unbound_page_ocr_words": words, "ocr_word_count": len(words),
                              "render_coordinate_frame": "displayed_mediabox"})
                continue
        column_edges = geometry["edges_pdf_points"] if image_candidate else edges_by_page[number]
        bands = geometry["physical_row_bands"] if image_candidate else page["physical_row_bands"]
        partial=geometry.get("partial_row_geometry",False)
        column_page = {"column_edges_pdf_points": column_edges,
                       "column_line_model": geometry.get("column_line_model"),
             "render_coordinate_frame": geometry.get("render_coordinate_frame")}
        slots = []
        assigned: set[int] = set()
        for index, band in enumerate(bands, start=1):
            top, bottom = band
            selected = [
                (position, word) for position, word in enumerate(words)
                if top <= (float(word["top"]) + float(word["bottom"])) / 2 < bottom]
            assigned.update(position for position, _ in selected)
            ordered = sorted((word for _, word in selected),
                             key=lambda word: (float(word["top"]), float(word["x0"])))
            observations = [{"text": str(word["text"]),
                             "bbox_pdf_points": [round(float(word[key]), 2)
                                                 for key in ("x0", "top", "x1", "bottom")],
                             "confidence": round(float(word["ocr_confidence"]), 2)}
                            for word in ordered]
            slots.append({"slot_index": index,
                          "band_pdf_points": band,
                          "ocr_status": ("words_observed" if observations else
                                         "no_words_observed_unknown_content"),
                          "raw_ocr_text": " ".join(item["text"] for item in observations),
                          **_observe_slot_columns(observations, column_edges, page=column_page),
                          **({"unresolved_region_crossing_word_indices":[i for i,w in enumerate(words)
                              if max(float(w["top"]),top) < min(float(w["bottom"]),bottom) and any(
                                  max(float(w["top"]),r[0]) < min(float(w["bottom"]),r[1])
                                  for r in geometry["unresolved_regions"])]} if partial else {}),
                          "words": observations})
        pages.append({"page_number": number,
                      "coverage": "physical_slots_partial" if partial else "physical_slots_located",
                      "row_detection_basis": ("same_page_image_grid_header_confirmed" if image_candidate else
                          page.get("row_detection_basis", "three_strip_grid")),
                      "column_geometry": ("oge_2024_six_column_grid" if column_edges
                                          else "unknown"),
                      "column_edges_pdf_points": column_edges,
                      "column_geometry_observation": geometry,
                      **({"column_line_model": geometry["column_line_model"]}
                         if "column_line_model" in geometry else {}),
                      "render_coordinate_frame": "displayed_mediabox",
                      "physical_slot_count": None if partial else len(slots),
                      **({"partial_row_geometry":True,"known_physical_slot_count":len(slots),
                          "whole_page_physical_slot_count":None,"unresolved_regions":geometry["unresolved_regions"],
                          "unbound_page_ocr_words":[{"word_index":i,**word} for i,word in enumerate(words) if i not in assigned]}
                         if partial else {}),
                      "slots": slots,
                      "ocr_word_count": len(words),
                      "words_outside_slots": len(words) - len(assigned)})
    return {"schema_version": SCHEMA,
            "source_sha256": actual_sha256,
            "selected_page_numbers": page_numbers,
            "ocr_engine": engine,
            "ocr_resolution_dpi": resolution,
            "render_coordinate_frame": "displayed_mediabox",
            "confirmed_physical_slot_count": sum(
                page.get("known_physical_slot_count",page["physical_slot_count"]) or 0 for page in pages),
            "slots_with_ocr_words": sum(
                slot["ocr_status"] == "words_observed"
                for page in pages for slot in page["slots"]),
            "pages_with_column_geometry": sum(
                page.get("column_geometry") == "oge_2024_six_column_grid"
                for page in pages),
            "slots_with_column_observation": sum(
                slot["column_observation"] is not None
                for page in pages for slot in page["slots"]),
            "pages": pages,
            "production_mutation_authorized": False}
