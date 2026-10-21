"""Recognize complete printed X marks anywhere inside calibrated source cells.

The older center sample remains unchanged. Only a whole, isolated crossing
component is supported here; fragmented marks, extra ink and clipping stay
unknown. This module performs no OCR and never decides transaction eligibility.
"""
import hashlib

RULE = "senate-source-complete-x/v1"


def _inner_face(gray, left, right, top, bottom):
    """Use source rule inner faces; don't discard an arbitrary margin of ink."""
    def band(seed, low, high, horizontal, a, b):
        hits = []
        for coordinate in range(max(low, seed - 8), min(high, seed + 9)):
            values = (gray.getpixel((x, coordinate)) for x in range(a, b)) if horizontal else (
                gray.getpixel((coordinate, y)) for y in range(a, b))
            if sum(v < 160 for v in values) >= .6 * (b - a):
                hits.append(coordinate)
        if (not hits or hits != list(range(hits[0], hits[-1] + 1)) or len(hits) > 8 or
                hits[0] == max(low, seed - 8) or hits[-1] == min(high, seed + 9) - 1):
            return None
        # A dithered one-pixel rule flank may be dark on alternate rows. It
        # must be adjacent to the continuous core and extend along the rule;
        # isolated dots and short strokes are never assigned to the boundary.
        for direction in (-1, 1):
            while True:
                coordinate = hits[0] - 1 if direction == -1 else hits[-1] + 1
                if coordinate < max(low, seed - 8) or coordinate >= min(high, seed + 9):
                    return None
                dark = [i for i in range(a, b) if gray.getpixel(
                    (i, coordinate) if horizontal else (coordinate, i)) < 160]
                if len(dark) < .3 * (b - a) or dark[-1] - dark[0] < .9 * (b - a):
                    break
                if direction == -1:
                    hits.insert(0, coordinate)
                else:
                    hits.append(coordinate)
                if len(hits) > 8:
                    return None
        return hits
    l = band(left, 0, gray.width, False, top + 12, bottom - 12)
    r = band(right, 0, gray.width, False, top + 12, bottom - 12)
    if l is None or r is None:
        return None
    t = band(top, 0, gray.height, True, l[-1] + 1, r[0])
    b = band(bottom, 0, gray.height, True, l[-1] + 1, r[0])
    if t is None or b is None:
        return None
    box = [l[-1] + 1, t[-1] + 1, r[0], b[0]]
    if box[0] >= box[2] or box[1] >= box[3]:
        return None
    return box, {"left": l, "right": r, "top": t, "bottom": b}


def complete_x(crop):
    """Require all dark pixels to form one interior X, retaining source geometry."""
    gray = crop.convert("L")
    points = {(x, y) for y in range(gray.height) for x in range(gray.width)
              if gray.getpixel((x, y)) < 160}
    if not points:
        return {"status": "no_dark_ink", "qualification": "not_assessed"}
    seed = next(iter(points))
    seen, stack = {seed}, [seed]
    while stack:
        x, y = stack.pop()
        for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)):
            other = (x + dx, y + dy)
            if other in points and other not in seen:
                seen.add(other)
                stack.append(other)
    if seen != points:
        return None
    left, top = min(x for x, _ in points), min(y for _, y in points)
    right, bottom = max(x for x, _ in points) + 1, max(y for _, y in points) + 1
    width, height = right - left, bottom - top
    if (not 12 <= width <= 50 or not 12 <= height <= 50 or not .65 <= width / height <= 1.5 or
            left == 0 or top == 0 or right == gray.width or bottom == gray.height):
        return None
    # Serif caps may be horizontal. All their pixels remain in the component;
    # the two-dimensional body must independently support both crossing arms.
    body = [(2 * (x - left) / (width - 1) - 1, 2 * (y - top) / (height - 1) - 1)
            for x, y in sorted(points) if top + 2 <= y < bottom - 2]
    near = sum(abs(abs(x) - abs(y)) <= .28 for x, y in body) / len(body)
    arms = [sum((x >= 0) == px and (y >= 0) == py for x, y in body) / len(body)
            for px, py in ((False, False), (True, False), (False, True), (True, True))]
    center = any(abs(x) < .2 and abs(y) < .2 for x, y in body)
    if near < .8 or min(arms) < .10 or not center:
        return None
    return {"status": "complete_x", "box_in_cell_px": [left, top, right, bottom],
            "dark_pixel_count": len(points), "body_near_diagonal_fraction": near,
            "body_arm_fractions": arms, "center_crossing_ink": center,
            "qualification": "not_assessed"}


def interpret_complete_mark_row(image, slot):
    from .senate_slots_shadow import _row_form_lines, TYPE_CELLS, AMOUNT_CELLS, _mark_count
    if slot.get("label") != "unknown" or slot.get("column_calibration") != "verified":
        return None
    gray = image.convert("L")
    lines = _row_form_lines(gray.tobytes(), slot)
    if lines is None:
        return None
    top, bottom = slot["row_bounds_px"]
    date = slot["key_cells"]["date"]
    if (date["box_px"] != [lines[3] + 10, top + 12, lines[4] - 10, bottom - 12] or
            hashlib.sha256(gray.crop(tuple(date["box_px"])).tobytes()).hexdigest() != date["grayscale_sha256"]):
        return None
    fields, cells = {}, []
    for field, bounds, labels, original in (
            ("direction", lines[:4], TYPE_CELLS, slot["type_marks"]),
            ("amount", lines[4:], AMOUNT_CELLS, slot["amount_marks"])):
        reads = slot["key_cells"][field]
        if [r["label"] for r in reads] != [label for label, _ in labels]:
            return None
        marked = []
        for (label, _), left, right, old in zip(labels, bounds, bounds[1:], reads):
            if (hashlib.sha256(gray.crop(tuple(old["box_px"])).tobytes()).hexdigest() != old["grayscale_sha256"] or
                    _mark_count(gray, *old["center_px"]) != old["dark_sample_count"] or
                    old["mark_signal"] != (old["dark_sample_count"] >= 25)):
                return None
            face = _inner_face(gray, left, right, top, bottom)
            if face is None:
                return None
            box, boundary_bands = face
            crop = gray.crop(tuple(box))
            result = complete_x(crop)
            if result is None:
                return None
            if result["status"] == "complete_x":
                marked.append(label)
            cells.append({"field": field, "label": label, "box_px": box,
                          "boundary_bands_px": boundary_bands,
                          "grayscale_sha256": hashlib.sha256(crop.tobytes()).hexdigest(),
                          "old_center_sample": old, **result})
        if len(marked) != 1 or original and original != marked:
            return None
        fields[field] = marked
    return {"rule_version": RULE, "direction_labels": fields["direction"], "amount_labels": fields["amount"],
            "original_type_marks": slot["type_marks"], "original_amount_marks": slot["amount_marks"],
            "source_column_lines_px": list(lines), "cells": cells, "qualification": "not_assessed"}
