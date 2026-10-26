"""Recognize complete printed X marks anywhere inside calibrated source cells.

The older center sample remains unchanged. v1 requires one isolated crossing;
v2 also separates directly witnessed rule flanks. Explicit v3 amount recovery
retains disconnected ink in a closed source face and applies the same crossing
geometry; it never invents missing strokes. Unexplained ink stays unknown.
This module performs no OCR and never decides transaction eligibility.
"""
import hashlib

RULE = "senate-source-complete-x/v1"
BOUNDARY_RULE = "senate-source-complete-x-boundary/v2"
ENCLOSED_RULE = "senate-source-enclosed-amount-x/v3"
INDEPENDENT_BOUNDARY_RULE = "senate-source-independent-rule-boundary/v4"


def _independent_blank_boundary(gray, slot, lines, left, right):
    """Only outside-cell print witnesses may explain a failed unmarked frame.

    This supports continuous axis-aligned bands up to 12 pixels and 1/8 cell
    size. It neither fits curves nor uses target contours to expand the mask.
    """
    from .ruled_cell_blank import inspect_ruled_cell_blank
    top,bottom = slot['row_bounds_px']
    height = bottom-top
    horizontal = [[a,b] for a,b in zip(lines,lines[1:])
                  if b<=left or a>=right]
    vertical = [[top-height+20,top-20],[bottom+20,bottom+height-20]]
    result = inspect_ruled_cell_blank(gray,[left,top,right,bottom],
        horizontal_witness_spans=horizontal,vertical_witness_spans=vertical,
        rule_profile='straight_wide/v2')
    if result['status'] != 'blank':
        return None
    return {'status':'independently_witnessed_blank','qualification':'not_assessed',
            'independent_blank_observation':result}


def _source_rule_trace_result(gray, box, bands):
    """Separate small inner-face flanks actually touching a proved rule core.

    No arbitrary edge strip or interior noise is removed. Whole X-sized
    components, including any touching a rule, remain untouched and uncertain.
    """
    from PIL import Image
    crop=gray.crop(tuple(box));points={(x,y) for y in range(crop.height) for x in range(crop.width) if crop.getpixel((x,y))<160}
    pending=set(points);removed=[]
    while pending:
        seed=pending.pop();group=[seed];stack=[seed]
        while stack:
            x,y=stack.pop()
            for yy in range(y-1,y+2):
                for xx in range(x-1,x+2):
                    if (xx,yy) in pending:pending.remove((xx,yy));group.append((xx,yy));stack.append((xx,yy))
        left,top=min(x for x,y in group),min(y for x,y in group);right,bottom=max(x for x,y in group)+1,max(y for x,y in group)+1
        if right-left>=12 and bottom-top>=12:continue
        witnesses=[]
        all_points_on_flank=True
        for x,y in group:
            point_witnesses=[]
            for yy in range(y-1,y+2):
                for xx in range(x-1,x+2):
                    if 0<=xx<crop.width and 0<=yy<crop.height:continue
                    sx,sy=xx+box[0],yy+box[1]
                    if (0<=sx<gray.width and 0<=sy<gray.height and gray.getpixel((sx,sy))<160 and
                            (sx in bands['left'] or sx in bands['right'] or sy in bands['top'] or sy in bands['bottom'])):
                        point_witnesses.append([sx,sy])
            if not point_witnesses:all_points_on_flank=False
            witnesses.extend(point_witnesses)
        if witnesses and all_points_on_flank:
            removed.append({'box_in_cell_px':[left,top,right,bottom],'source_dark_pixel_count':len(group),'source_rule_core_witnesses_px':[list(point) for point in sorted(set(map(tuple,witnesses)))]})
            points.difference_update(group)
    if not removed:return complete_x(crop)
    analysis=Image.new('L',crop.size,255)
    for x,y in points:analysis.putpixel((x,y),crop.getpixel((x,y)))
    result=complete_x(analysis)
    if result is None:return None
    if result['status']=='no_dark_ink':result['status']='source_rule_traces_only'
    return {**result,'source_rule_trace_components':removed,'original_crop_pixels_retained_in_receipt':True,
            'source_financial_ink_not_changed':True}


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


def complete_x(crop, *, allow_fragments=False):
    """Check every dark pixel against interior X geometry; never join fragments."""
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
    if seen != points and not allow_fragments:
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
    return {"status": "fragmented_x" if seen != points else "complete_x", "box_in_cell_px": [left, top, right, bottom],
            "dark_pixel_count": len(points), "body_near_diagonal_fraction": near,
            "body_arm_fractions": arms, "center_crossing_ink": center,
            "qualification": "not_assessed"}


def interpret_enclosed_amount_row(image, slot):
    """Recover only a missing amount, retaining a source-verified old direction.

    The old unique center-sample direction is not reinterpreted. Every other
    direction cell must be empty, and all amount cells are examined in full.
    Fragmented amount ink must satisfy the same geometric X checks as v1;
    disconnected pixels are retained and no strokes are invented.
    """
    from .senate_paper_cell_faces import enclosed_cell_ink
    from .senate_slots_shadow import _row_form_lines, TYPE_CELLS, AMOUNT_CELLS, _mark_count
    if (slot.get('label') != 'unknown' or slot.get('column_calibration') != 'verified' or
            len(slot.get('type_marks', [])) != 1 or slot.get('amount_marks')):
        return None
    gray = image.convert('L')
    lines = _row_form_lines(gray.tobytes(), slot)
    if lines is None:
        return None
    top, bottom = slot['row_bounds_px']
    date = slot['key_cells']['date']
    if (date['box_px'] != [lines[3]+10, top+12, lines[4]-10, bottom-12] or
            hashlib.sha256(gray.crop(tuple(date['box_px'])).tobytes()).hexdigest() != date['grayscale_sha256']):
        return None
    cells, amounts = [], []
    for field, bounds, labels in [('direction', lines[:4], TYPE_CELLS), ('amount', lines[4:], AMOUNT_CELLS)]:
        old_reads = slot['key_cells'][field]
        if [r['label'] for r in old_reads] != [label for label, _ in labels]:
            return None
        for (label, _), left, right, old in zip(labels, bounds, bounds[1:], old_reads):
            if (hashlib.sha256(gray.crop(tuple(old['box_px'])).tobytes()).hexdigest() != old['grayscale_sha256'] or
                    _mark_count(gray, *old['center_px']) != old['dark_sample_count'] or
                    old['mark_signal'] != (old['dark_sample_count'] >= 25)):
                return None
            observed = enclosed_cell_ink(gray, left, right, top, bottom)
            if observed is None:
                return None
            ink, proof = observed
            if field == 'direction' and label == slot['type_marks'][0]:
                if not old['mark_signal']:
                    return None
                result = {'status': 'original_unique_center_mark_retained'}
            else:
                result = complete_x(ink, allow_fragments=field == 'amount')
                if result is None or field == 'direction' and result['status'] != 'no_dark_ink':
                    return None
            if field == 'amount' and result['status'] in {'complete_x', 'fragmented_x'}:
                amounts.append(label)
            cells.append({'field': field, 'label': label, 'old_center_sample': old, **proof, **result})
    if len(amounts) != 1:
        return None
    return {'rule_version': ENCLOSED_RULE, 'direction_labels': slot['type_marks'], 'amount_labels': amounts,
            'original_type_marks': slot['type_marks'], 'original_amount_marks': [], 'cells': cells,
            'source_column_lines_px': list(lines), 'qualification': 'not_assessed'}


def interpret_complete_mark_row(image, slot, *, resolve_boundary_traces=False,
                                independent_blank_boundaries=False):
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
            box, boundary_bands = face if face else (None,None)
            crop = gray.crop(tuple(box)) if box is not None else None
            result = ((_source_rule_trace_result(gray,box,boundary_bands) if resolve_boundary_traces else complete_x(crop))
                      if face else None)
            if independent_blank_boundaries and not old['mark_signal'] and (
                    result is None or result['status'] != 'complete_x'):
                result = _independent_blank_boundary(gray,slot,lines,left,right)
            if result is None:
                return None
            if result["status"] == "complete_x":
                marked.append(label)
            cells.append({"field": field, "label": label, "box_px": box,
                          "boundary_bands_px": boundary_bands,
                          "grayscale_sha256": hashlib.sha256(crop.tobytes()).hexdigest() if crop else None,
                          "old_center_sample": old, **result})
        if len(marked) != 1 or original and original != marked:
            return None
        fields[field] = marked
    return {"rule_version": INDEPENDENT_BOUNDARY_RULE if independent_blank_boundaries else BOUNDARY_RULE if resolve_boundary_traces else RULE, "direction_labels": fields["direction"], "amount_labels": fields["amount"],
            "original_type_marks": slot["type_marks"], "original_amount_marks": slot["amount_marks"],
            "source_column_lines_px": list(lines), "cells": cells, "qualification": "not_assessed"}
