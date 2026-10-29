"""Source text baselines for row ownership; never repair text or field bounds."""
from __future__ import annotations

from collections import defaultdict
from math import isfinite
from pathlib import Path
import hashlib
import json

MODE = "source_text_baseline_unique_row/v1"

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
    if len(overlaps)==2:
        first,last=min(overlaps),max(overlaps)
        if page["slots"][first]["row_boundary_refs"][1] != page["slots"][last]["row_boundary_refs"][0]:
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
    if hashlib.sha256(Path(source).read_bytes()).hexdigest() != expected_sha256:
        raise ValueError("source changed during baseline proof")
    return result


def validate_native_baseline_observation(observation: dict, source_pdf_path) -> None:
    """Recompute source ownership before any field/ledger consumes row relief."""
    from .oge_278t_raster_slots import point_in_slot,box_in_slot,box_overlaps_slot
    mode=observation.get("native_baseline_mode")
    pages=observation.get("pages",[])
    has_proof=any(p.get("native_baseline_mode") is not None or "native_baseline_bindings" in p or "source_page_input_words" in p or
        any("source_baseline_row_word_indices" in s or "raw_row_crossing_word_indices" in s for s in p.get("slots",[])) for p in pages)
    if mode is None:
        if has_proof:raise ValueError("native baseline proof lacks explicit mode")
        return
    if mode!=MODE or source_pdf_path is None:
        raise ValueError("native baseline mode requires its controlled original PDF")
    sha=hashlib.sha256(Path(source_pdf_path).read_bytes()).hexdigest()
    if sha!=observation.get("source_sha256"):raise ValueError("native baseline original source SHA differs")
    canonical=lambda obj:json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False)
    for page in pages:
        if page.get("native_baseline_mode")!=MODE:raise ValueError("native baseline page mode differs")
        if not page.get("slots"):
            if any(k in page for k in ("native_baseline_bindings","source_page_input_words")):
                raise ValueError("unknown native geometry cannot claim baseline ownership")
            continue
        words=page.get("source_page_input_words")
        if not isinstance(words,list):raise ValueError("native baseline proof lacks original input words")
        expected=source_text_baseline_bindings(Path(source_pdf_path),expected_sha256=sha,
            page_number=page["page_number"],page=page,words=words)
        if canonical(expected)!=canonical(page.get("native_baseline_bindings")):
            raise ValueError("native baseline bindings differ from original TextObj")
        bindings=expected["bindings"]
        for slot in page["slots"]:
            selected=[(i,w) for i,w in enumerate(words) if point_in_slot(page,slot,
                (float(w["x0"])+float(w["x1"]))/2,(float(w["top"])+float(w["bottom"]))/2)]
            selected.sort(key=lambda item:(float(item[1]["top"]),float(item[1]["x0"])))
            if len(selected)!=len(slot["words"]):raise ValueError("native baseline local words differ from source page")
            for (index,raw),word in zip(selected,slot["words"]):
                if (type(word.get("source_page_word_index")) is not int or word["source_page_word_index"]!=index or word.get("text")!=raw["text"] or
                        word.get("bbox_pdf_points")!=[float(raw[k]) for k in ("x0","top","x1","bottom")]):
                    raise ValueError("native baseline word binding differs from raw source coordinates")
            resolved=[i for i,w in enumerate(slot["words"]) if
                bindings.get(str(w["source_page_word_index"]),{}).get("slot_index")==slot["slot_index"]]
            raw_cross=[i for i,w in enumerate(words) if box_overlaps_slot(page,slot,[w[k] for k in ("x0","top","x1","bottom")])
                and not box_in_slot(page,slot,[w[k] for k in ("x0","top","x1","bottom")])]
            unresolved=[i for i in raw_cross if str(i) not in bindings]
            if (slot.get("source_baseline_row_word_indices")!=resolved or
                    slot.get("raw_row_crossing_word_indices")!=raw_cross or
                    slot.get("unresolved_region_crossing_word_indices")!=unresolved):
                raise ValueError("native baseline raw/remaining crossing evidence differs")
