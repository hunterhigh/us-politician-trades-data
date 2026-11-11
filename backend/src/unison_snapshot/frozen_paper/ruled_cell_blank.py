"""Whole-cell blank observations from independently witnessed straight rules.

No target-cell contour chooses what is erased. Unsupported frames and every
unexplained dark pixel remain unknown, rather than becoming a blank fact.
"""
from __future__ import annotations

import hashlib


def inspect_ruled_cell_blank(image, box: list[int], *, horizontal_witness_spans: list,
                             vertical_witness_spans: list, rule_profile: str = "straight_thin/v1") -> dict:
    """Observe a calibrated source cell; never OCR, repair ink or infer an owner.

    Default straight_thin/v1 uses ink <100 and rules <=6 pixels and <=1/8
    cell size. Explicit straight_wide/v2 uses Senate's existing ink <160,
    a complete 41-pixel search window, and both <=12-pixel and <=1/8-cell
    thickness limits. Both require the target's predicted print core to exist.
    Each axis needs at least two
    disjoint spans outside the target cell. Only their common dark footprint
    defines the possible print mask; the actual target frame must also close.
    """
    import numpy as np

    if rule_profile not in {"straight_thin/v1", "straight_wide/v2"}:
        raise ValueError("Unsupported independent rule profile")
    wide = rule_profile == "straight_wide/v2"
    radius, maximum, margin = (20, 12, 24) if wide else (8, 6, 12)
    threshold = 160 if wide else 100
    unknown = {"status":"unknown", "measurement":("independent_source_wide_rule_footprint/v2" if wide else
                                                  "independent_source_rule_footprint/v1"),
               "black_pixel_threshold_exclusive":threshold, "unexplained_dark_pixel_count":None}
    if (not isinstance(box, list) or len(box) != 4 or
            any(type(v) is not int for v in box) or
            not margin <= box[0] < box[2] <= image.width-margin or
            not margin <= box[1] < box[3] <= image.height-margin):
        return {**unknown,"reason":"source_cell_window_unsupported"}
    x0,y0,x1,y1 = box
    gray = np.asarray(image.convert("L"))
    dark = gray < threshold
    target = dark[y0:y1,x0:x1]
    base = {**unknown,"cell_bbox_pixels":list(box),
        "source_cell_grayscale_sha256":hashlib.sha256(gray[y0:y1,x0:x1].tobytes()).hexdigest(),
        "observed_dark_pixel_count":int(target.sum())}
    if not target.any():
        return {**base,"status":"blank","measurement":"whole_source_cell_no_dark_pixels/v1",
                "unexplained_dark_pixel_count":0,"removed_rule_pixel_count":0}

    def spans(values, limit, low, high):
        selected=[]
        for pair in values:
            if (not isinstance(pair,(list,tuple)) or len(pair)!=2 or
                    any(type(v) is not int for v in pair)):
                return []
            a,b=pair
            if not (0<=a<b<=limit and b-a>=20 and (b<=low or a>=high)):
                continue
            selected.append((a+5,b-5))
        selected.sort()
        if len(selected)<2 or any(b>c for (a,b),(c,d) in zip(selected,selected[1:])):
            return []
        return selected

    horizontal = spans(horizontal_witness_spans,image.width,x0,x1)
    vertical = spans(vertical_witness_spans,image.height,y0,y1)
    if not horizontal or not vertical:
        return {**base,"reason":"independent_rule_witnesses_missing"}
    evidence=[]

    def rule(axis, edge, witnesses):
        indices=np.arange(edge-radius,edge+radius+1)
        supported=np.ones(len(indices),dtype=bool)
        for a,b in witnesses:
            samples=dark[indices,a:b] if axis=="horizontal" else dark[a:b,indices].T
            supported &= samples.all(axis=1)
        pixels=indices[supported]
        groups=[g.tolist() for g in np.split(pixels,np.where(np.diff(pixels)>1)[0]+1) if len(g)]
        evidence.append({"axis":axis,"calibrated_edge_pixel":edge,
                         "outside_cell_witness_spans":[list(s) for s in witnesses],
                         "common_dark_pixel_groups":groups})
        # The explicit wide profile must observe both ends of the print band;
        # a clipped search window cannot establish its true thickness.
        if wide and len(groups)==1 and (groups[0][0]==indices[0] or groups[0][-1]==indices[-1]):
            return None
        return groups[0] if len(groups)==1 else None

    left,right=(rule("vertical",edge,vertical) for edge in (x0,x1))
    top,bottom=(rule("horizontal",edge,horizontal) for edge in (y0,y1))
    base["independent_rule_witnesses"]=evidence
    if any(g is None for g in (left,right,top,bottom)):
        return {**base,"reason":"source_rule_footprint_missing_or_ambiguous"}
    if (any(len(g)>min(maximum,max(1,(x1-x0)//8)) for g in (left,right)) or
            any(len(g)>min(maximum,max(1,(y1-y0)//8)) for g in (top,bottom))):
        return {**base,"reason":"source_rule_footprint_too_thick"}
    if not (left[-1]+1<right[0] and top[-1]+1<bottom[0]):
        return {**base,"reason":"source_rule_footprints_overlap"}
    # Require the predicted frame itself. A missing line may not be filled in
    # by extrapolation, and target ink may not enlarge a witnessed footprint.
    if (not dark[top,left[0]:right[-1]+1].all() or
            not dark[bottom,left[0]:right[-1]+1].all() or
            not dark[top[0]:bottom[-1]+1,left].all() or
            not dark[top[0]:bottom[-1]+1,right].all()):
        return {**base,"reason":"target_source_frame_not_continuous"}
    mask=np.zeros_like(target)
    for ys in (top,bottom):
        for y in ys:
            if y0<=y<y1:
                mask[y-y0,max(x0,left[0])-x0:min(x1,right[-1]+1)-x0]=True
    for xs in (left,right):
        for x in xs:
            if x0<=x<x1:
                mask[max(y0,top[0])-y0:min(y1,bottom[-1]+1)-y0,x-x0]=True
    count=int((target & ~mask).sum())
    return {**base,"status":"blank" if count==0 else "unknown",
            "reason":None if count==0 else "unexplained_source_cell_ink",
            "unexplained_dark_pixel_count":count,"removed_rule_pixel_count":int((target & mask).sum()),
            "source_rule_mask_sha256":hashlib.sha256(mask.tobytes()).hexdigest(),
            "target_ink_used_to_estimate_rules":False}
