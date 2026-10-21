"""Source text baselines for row ownership; never repair text or field bounds."""
from __future__ import annotations

from collections import defaultdict
from math import isfinite
from pathlib import Path

from .pdf_text_object_words import observe_pdf_text_objects


def _key(text, box):
    return text, tuple(box)


def _assignment(record: dict, page: dict) -> dict | None:
    from .oge_278t_raster_slots import _slot_line_coefficients

    if (record["exclusion_reasons"] or record["observation_issues"] or
            record["text_render_mode"] != 0 or
            any(c in record["raw_text"] for c in "\r\n\t")):
        return None
    a, b, c, d, e, f = record["matrix_pdf_bottom_left"]
    if not all(isfinite(v) for v in (a,b,c,d,e,f)) or a <= 0 or c != 0 or d <= 0:
        return None
    box = record["bbox_displayed_mediabox"]
    x0, top, x1, bottom = box
    media = record["source_mediabox_pdf_bottom_left"]
    # Project the entire original ink x extent onto the original text baseline.
    # This is deliberately NOT called a typographic advance measurement.
    baseline = [[x, media[3] - (f + (x + media[0] - e)*b/a)] for x in (x0,x1)]
    center = [(x0+x1)/2, (top+bottom)/2]
    coefficients = [_slot_line_coefficients(page,slot,0,6) for slot in page["slots"]]

    def inside(points, coefficient):
        vertical, horizontal = coefficient
        return all(vertical[0][0]+vertical[0][1]*y <= x < vertical[1][0]+vertical[1][1]*y
                   and horizontal[0][0]+horizontal[0][1]*x <= y < horizontal[1][0]+horizontal[1][1]*x
                   for x,y in points)

    owners = [i for i,co in enumerate(coefficients) if inside(baseline+[center],co)]
    if len(owners) != 1:
        return None
    owner = owners[0]
    # Do not rescue a multi-row text object or text reaching an unobserved area.
    horizontal = [co[1] for co in coefficients]
    overlaps = [i for i,edges in enumerate(horizontal) if
                top < max(edges[1][0]+edges[1][1]*x for x in (x0,x1)) and
                bottom > min(edges[0][0]+edges[0][1]*x for x in (x0,x1))]
    if (not 1 <= len(overlaps) <= 2 or owner not in overlaps or
            any(abs(i-owner)>1 for i in overlaps)):
        return None
    envelope_top = horizontal[min(overlaps)][0]
    envelope_bottom = horizontal[max(overlaps)][1]
    if (top < max(envelope_top[0]+envelope_top[1]*x for x in (x0,x1)) or
            bottom > min(envelope_bottom[0]+envelope_bottom[1]*x for x in (x0,x1))):
        return None
    # The horizontal field is still determined by the COMPLETE original box.
    # A baseline can never move text from another financial column.
    columns = []
    corners = [(x,y) for x in (x0,x1) for y in (top,bottom)]
    for column in range(6):
        vertical,_ = _slot_line_coefficients(page,page["slots"][owner],column,column+1)
        if all(vertical[0][0]+vertical[0][1]*y <= x < vertical[1][0]+vertical[1][1]*y for x,y in corners):
            columns.append(column)
    if len(columns) != 1:
        return None
    return {"slot_index": page["slots"][owner]["slot_index"], "column_index": columns[0],
            "baseline_segment_over_original_ink_x_extent": baseline,
            "bbox_center_pdf_points": center, "overlapping_observed_slots": [i+1 for i in overlaps]}


def source_text_baseline_bindings(source: Path, *, expected_sha256: str,
                                 page_number: int, page: dict, words: list[dict]) -> dict:
    """Recompute native provenance; OCR words and unsupported pages get no rescue.

    The input words are never altered. Only exact raw-string/bbox matches to an
    active original TextObj can gain a row binding. Source clipping, transparency
    resources and a later object overlapping the text conservatively disable it.
    """
    import pdfplumber
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    import ctypes
    from pdfminer.pdfinterp import PDFContentParser
    from pdfminer.psparser import PSKeyword, PSEOF

    result = {"schema_version": "source-text-baseline-row-bindings/v1",
              "source_sha256": expected_sha256, "page_number": page_number,
              "bindings": {}, "qualification": "not_assessed"}
    if page.get("geometry_mode") != "source_pdf_dual_axis/v1" or not page.get("slots"):
        return result
    native = observe_pdf_text_objects(source,expected_sha256=expected_sha256,page_number=page_number)
    with pdfplumber.open(source) as document:
        original = document.pages[page_number-1].page_obj
        if original.resources.get("ExtGState"):
            return {**result,"unsupported_reason":"source_graphics_state_not_supported"}
        parser = PDFContentParser(original.contents)
        while True:
            try:
                _, token = parser.nextobject()
            except PSEOF:
                break
            if isinstance(token,PSKeyword) and token.name in (b"W",b"W*"):
                return {**result,"unsupported_reason":"source_clipping_not_supported"}
    lookup = defaultdict(list)
    records = native["objects"]
    all_bounds = [(o["source_object_index"],o["bounds_pdf_bottom_left"]) for o in records+native["nontext_objects"]]
    with pdfium.PdfDocument(source) as document:
        original = document[page_number-1]
        objects = list(original.get_objects(max_depth=1))
        for record in records:
            index = record["source_object_index"]
            box = record["bounds_pdf_bottom_left"]
            if any(j>index and max(box[0],b[0]) < min(box[2],b[2]) and
                   max(box[1],b[1]) < min(box[3],b[3]) for j,b in all_bounds):
                continue
            colors = [ctypes.c_uint() for _ in range(4)]
            if (not raw.FPDFPageObj_GetFillColor(objects[index],*colors) or
                    colors[3].value != 255 or all(v.value==255 for v in colors[:3])):
                continue
            record = {**record,"source_mediabox_pdf_bottom_left":native["source_mediabox_pdf_bottom_left"]}
            assignment = _assignment(record,page)
            if assignment:
                lookup[_key(record["raw_text"],record["bbox_displayed_mediabox"])].append({
                    **assignment,"source_object_index":index,
                    "matrix_pdf_bottom_left":record["matrix_pdf_bottom_left"],
                    "source_object_manifest_sha256":native["text_object_manifest_sha256"],
                    "basis":"exact_original_text_object_baseline_and_center_same_unique_row/v1"})
        original.close()
    for index,word in enumerate(words):
        box = [float(word[k]) for k in ("x0","top","x1","bottom")]
        matches = lookup.get(_key(word["text"],box),[])
        if len(matches)==1:
            result["bindings"][str(index)] = matches[0]
    return result
