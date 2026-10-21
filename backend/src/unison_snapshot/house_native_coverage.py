"""Bounded source-page accounting for the native Clerk PTR template.

Geometry and words are saved observations, not independent authenticity proofs.
Only the recognized header, alternating source bands and closing section grammar
can account a page. Financial fields and formal IDs remain the PTR parser's job.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re

from .source_row_ledger import page_ledger, report_ledger

GEOMETRY_SCHEMA = "house-native-page-geometry/v1"
BASIS = "house-native-template-page-census/v1"


def _clean(value):
    return " ".join(str(value).replace("\x00", "").split())


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def observe_native_geometry(page, source_sha: str, page_number: int) -> dict:
    """Save existing PDF primitives without changing original word coordinates."""
    def box(item):
        return [float(item[key]) for key in ("x0", "top", "x1", "bottom")]
    return {"schema_version": GEOMETRY_SCHEMA, "source_sha256": source_sha,
        "page_number": page_number, "width": float(page.width), "height": float(page.height),
        "lines": [box(line) for line in page.lines],
        "rects": [box(r) for r in page.rects],
        "curves": [box(c) for c in page.curves],
        "images": [box(i) for i in page.images]}


def _geometry(page, source_sha, number):
    g = page.get("native_page_geometry")
    if not isinstance(g, dict) or g.get("schema_version") != GEOMETRY_SCHEMA:
        return None
    if (g.get("source_sha256") != source_sha or type(g.get("page_number")) is not int
            or g.get("page_number") != number or
            g.get("width") != page["width"] or g.get("height") != page["height"]):
        return None
    for kind in ("lines", "rects", "curves", "images"):
        if not isinstance(g.get(kind), list):
            return None
        for b in g[kind]:
            if (not isinstance(b, list) or len(b) != 4 or
                    any(type(v) not in (int, float) or not math.isfinite(v) for v in b) or
                    not 0 <= b[0] <= b[2] <= page["width"] or
                    not 0 <= b[1] <= b[3] <= page["height"]):
                return None
    # This bounded template has no independent line primitives. An unhandled
    # line (including a four-line empty table) is an unresolved source region,
    # never evidence of an empty page. Missing old arrays also failed above.
    if g["lines"]:
        return None
    return g


def _lines(words):
    result = []
    for w in sorted(words, key=lambda w: (float(w["top"]), float(w["x0"]))):
        if not result or abs(float(w["top"]) - float(result[-1][0]["top"])) > 2:
            result.append([])
        result[-1].append(w)
    return result


def _text(words):
    return _clean(" ".join(str(w["text"]) for w in sorted(words,
        key=lambda w: (round(float(w["top"]), 1), float(w["x0"])))))


def _in(w, b, pad=0.8):
    # Glyph bounds, not just first-character anchors, must fit a source region.
    return (b[0]-pad <= float(w["x0"]) and float(w.get("x1", w["x0"])) <= b[2]+pad
        and b[1]-pad <= float(w["top"]) and
        float(w.get("bottom", w["top"]+w.get("size", 9))) <= b[3]+pad)


def _financial(words):
    return any("$" in str(w["text"]) or re.fullmatch(r"\d{2}/\d{2}/\d{4}",
        _clean(w["text"])) or re.fullmatch(r"\[[A-Z0-9]{2}[\]}]", _clean(w["text"]))
        for w in words)


def _table_page(page, geometry, rows):
    width, height, words = page["width"], page["height"], page["words"]
    # The official column roles establish the table region; no date locates it.
    groups = {}
    for b in geometry["rects"]:
        if b[2]-b[0] > 20 and b[3]-b[1] > 20:
            groups.setdefault((round(b[1], 2), round(b[3], 2)), []).append(b)
    headers = []
    expected = ["ID", "Owner", "Asset", "Transaction Type", "Date", "Notification Date", "Amount", "Cap. Gains > $200?"]
    for boxes in groups.values():
        boxes = sorted(boxes, key=lambda b: b[0])
        if len(boxes) != 8 or any(abs(a[2]-b[0]) > 1 for a,b in zip(boxes, boxes[1:])):
            continue
        if [_text([w for w in words if _in(w,b)]) for b in boxes] == expected:
            headers.append(boxes)
    if len(headers) != 1:
        return None
    cells = headers[0];left, right = cells[0][0], cells[-1][2]
    header_top, header_bottom = cells[0][1], cells[0][3]
    # Thin outer bars retain boundaries absent from merged white-fill cells.
    sides = {}
    for side in (left, right):
        spans = sorted(set((round(b[1],2), round(b[3],2)) for b in geometry["rects"]
            if b[2]-b[0] <= 1.5 and abs(b[0]-side) <= 1 and b[3]-b[1] > 10
            and b[1] >= header_bottom-.8))
        sides[side] = spans
    bands = sides[left]
    if (not bands or len(bands)%2 or len(bands) != len(sides[right]) or
            any(abs(a-c)>1 or abs(b-d)>1 for (a,b),(c,d) in zip(bands,sides[right])) or
            abs(bands[0][0]-header_bottom)>1 or
            any(abs(a[1]-b[0])>1 for a,b in zip(bands,bands[1:]))):
        return None
    source_status = []
    for line in _lines(words):
        if re.fullmatch(r"(?:F|Filing) (?:S|Status): (?:New|Amended)", _text(line), re.I):
            source_status.append(float(line[0]["top"]))
    if len(rows)*2 != len(bands) or len(source_status) != len(rows):
        return None
    slots, mapping, assigned = [], {}, set()
    for pair, ((top,bottom),(detail_top,detail_bottom)) in enumerate(zip(bands[::2],bands[1::2]),1):
        matched = [(index,row) for index,row in rows
            if top <= row["evidence"]["bbox_points"][1]+2 < bottom]
        if len(matched) != 1 or len([t for t in source_status if detail_top <= t < detail_bottom]) != 1:
            return None
        index,row = matched[0]
        if len(row["evidence"]["segments"]) != 1:
            return None
        main_box = [left,top,right,bottom];detail_box = [cells[2][0],detail_top,right,detail_bottom]
        main = [w for w in words if _in(w,main_box)]
        detail = [w for w in words if _in(w,detail_box)]
        # A second financial line, not a wrapped asset/amount, is not consumed
        # as prose. Existing anchors alone cannot certify these source words.
        row_y = row["evidence"]["bbox_points"][1]+2
        for w in main:
            if abs(w["top"]-row_y)>2 and not (
                _in(w,[cells[2][0],top,cells[2][2],bottom]) or
                _in(w,[cells[6][0],top,cells[6][2],bottom])):
                return None
        details = _lines(detail)
        if not details or any(not re.match(r"^(?:(?:F|Filing) (?:S|Status): (?:New|Amended)|(?:S|Subholding) (?:O|Of): .+|(?:L|Location): .+|(?:D|Description): .+)$", _text(line), re.I) for line in details):
            return None
        # Every row has exactly one observed status detail, not a keyword count.
        refs=[]
        for name,bounds in (("main",[top,bottom]),("detail",[detail_top,detail_bottom])):
            key=f"row-{index}-{name}"
            slots.append({"local_key":key,"bounds":bounds,"disposition":"data_candidate",
                "observation_ref":row["extraction_id"]})
            refs.append(key)
        mapping[index]=refs
        assigned.update(id(w) for w in main+detail)
    # Classify the official masthead/filer block and header, never arbitrary
    # leftovers. The template's visible title/labels are source observations.
    before=[w for w in words if w["top"] < header_top]
    lines=[_text(line) for line in _lines(before)]
    if _financial(before):
        return None
    if not (any(text in {"P T R", "Periodic Transaction Report"} for text in lines)
            and any(re.fullmatch(r"Filing ID #[0-9]+", text) for text in lines)
            and any(text.startswith("Name: ") for text in lines)
            and any(text == "Status: Member" for text in lines)
            and any(re.fullmatch(r"State/District: [A-Z]{2}[0-9]{2}", text) for text in lines)):
        return None
    for line in _lines(before):
        text=_text(line)
        if not (text in {"P T R","Periodic Transaction Report","F I","Filer Information","T","Transactions","Status: Member"}
            or re.fullmatch(r"Filing ID #[0-9]+",text) or text.startswith("Name: ")
            or re.fullmatch(r"State/District: [A-Z]{2}[0-9]{2}",text)
            or (text.startswith("Clerk of the House of Representatives") and "Legislative Resource Center" in text)):
            return None
    assigned.update(id(w) for w in before)
    assigned.update(id(w) for w in words if _in(w,[left,header_top,right,header_bottom]))
    if any(id(w) not in assigned for w in words):
        return None
    # Curves are known capital-gains controls only in a main band's final cell.
    if any(not any(_box_inside(b,[cells[7][0],t,right,z]) for t,z in bands[::2]) for b in geometry["curves"]):
        return None
    if len(geometry["images"]) != 1 or any(not _box_inside(b,[.08*width,.08*height,.19*width,.16*height]) for b in geometry["images"]):
        return None
    # All cell/background primitives must land on the observed column/band
    # boundaries, not merely somewhere inside the table's bounding rectangle.
    x_edges=[b[0] for b in cells]+[right]
    y_edges=[header_top,header_bottom]+[edge for band in bands for edge in band]
    def aligned(value,edges):
        return any(abs(value-edge)<=1 for edge in edges)
    for b in geometry["rects"]:
        if b[1] >= header_top-.8 and not (
            aligned(b[0],x_edges) and aligned(b[2],x_edges)
            and aligned(b[1],y_edges) and aligned(b[3],y_edges)):
            return None
    # Extra vector tables/boxes outside the observed transaction enclosure
    # are not ignored just because their text extractor returned no words.
    if any(not (_box_inside(b,[left,header_top,right,bands[-1][1]]) or
        (b[3]-b[1] <= 1.5 and b[1] < header_top and b[2]-b[0] > .5*width))
        for b in geometry["rects"]):
        return None
    slots.insert(0,{"local_key":"native-table-header","bounds":[header_top,header_bottom],
        "disposition":"heading","observation_ref":f"{BASIS}:header:{_digest(cells)}"})
    return slots,mapping


def _box_inside(b, region, pad=.8):
    return region[0]-pad <= b[0] <= b[2] <= region[2]+pad and region[1]-pad <= b[1] <= b[3] <= region[3]+pad


def _closing_page(page, geometry):
    words=page["words"];lines=_lines(words)
    headings=[(line,_text(line)) for line in lines if all(11.5 <= w.get("size",0) <= 12.5 for w in line)]
    roles=[text for _,text in headings]
    if roles not in (["I V D","C","I P O","C S"],
        ["Investment Vehicle Details","Comments","Initial Public Offering","Certification and Signature"]):
        return False
    tops=[line[0]["top"] for line,_ in headings]
    regions=[[],[],[],[],[]]
    for line in lines:
        if any(line is heading for heading,_ in headings):
            continue
        y=line[0]["top"]
        bucket=sum(y>top for top in tops)
        regions[bucket].extend(line)
    footnote=_text(regions[0])
    if footnote != "* For the complete list of asset type abbreviations, please visit https://fd.house.gov/reference/asset-type-codes.aspx.":
        return False
    # Known account details/comments can contain prose, but not unassigned
    # numeric financial fields. Any extra transaction/table remains unknown.
    if not regions[1] or not regions[2] or _financial(regions[1]+regions[2]):
        return False
    if _text(regions[3]) != "Yes No":
        return False
    certification=[_text(line) for line in _lines(regions[4])]
    if len(certification)!=3 or not (certification[0] == "I CERTIFY that the statements I have made on the attached Periodic Transaction Report are true, complete, and correct to the best of"
        and certification[1] == "my knowledge and belief. Further, I CERTIFY that I have disclosed all transactions as required by the STOCK Act."
        and re.fullmatch(r"Digitally Signed: .+, [0-9]{2}/[0-9]{2}/[0-9]{4}",certification[2])):
        return False
    if geometry["images"]:
        return False
    # Only source controls/bullets and account-detail enclosure belong here;
    # another table/border or drawing is not silently called a comment.
    if any(not any(_box_inside(b,region) for region in (
        [0,tops[0],page["width"],tops[1]],
        [0,tops[2],.20*page["width"],tops[3]],
        [0,tops[3],.08*page["width"],max(w.get("bottom",w["top"]+9) for w in regions[4])]))
        for b in geometry["curves"]):
        return False
    enclosures=[b for b in geometry["rects"] if b[3]-b[1]>2]
    if (len(enclosures)!=2 or any(abs(a-b)>1 for a,b in zip(*enclosures))
            or any(not _box_inside(b,[0,tops[0],page["width"],tops[1]]) for b in enclosures)
            or any(not _in(w,enclosures[0]) for w in regions[1])):
        return False
    footnote_box=[min(w["x0"] for w in regions[0]), min(w["top"] for w in regions[0]),
        max(w.get("x1",w["x0"]) for w in regions[0]),
        max(w.get("bottom",w["top"]+w.get("size",9)) for w in regions[0])]
    if any(not _box_inside(b,footnote_box) for b in geometry["rects"] if b[3]-b[1]<=2):
        return False
    return True


def apply_native_coverage(source_sha, pages, rows, fallback):
    """Refine only source-observed recognized pages; all others stay unknown."""
    observed=deepcopy(fallback["pages"]);logical=deepcopy(fallback["logical_rows"])
    changed=False
    for number,page in enumerate(pages,1):
        geometry=_geometry(page,source_sha,number)
        if geometry is None:
            continue
        local=[(i,r) for i,r in enumerate(rows,1) if r["evidence"]["page"]==number]
        result=_table_page(page,geometry,local) if local else None
        if result is not None:
            slots,mapping=result
            observed[number-1]=page_ledger(source_id="house_clerk",document_sha256=source_sha,
                page_number=number,adapter_version=fallback["adapter_version"],coverage="located",
                coverage_reason=f"{BASIS}:header_bands_status_and_word_roles:{_digest(geometry)}",
                coordinate_frame="source_page_pdf_points",slots=slots)
            for index,keys in mapping.items():
                logical[index-1]["slot_refs"]=[{"page_number":number,"slot_key":key} for key in keys]
            changed=True
        elif not local and _closing_page(page,geometry):
            observed[number-1]=page_ledger(source_id="house_clerk",document_sha256=source_sha,
                page_number=number,adapter_version=fallback["adapter_version"],coverage="non_table",
                coverage_reason=f"{BASIS}:observed_closing_section_roles:{_digest([page['words'],geometry])}",
                coordinate_frame="source_page_pdf_points",slots=[])
            changed=True
    if not changed:
        return fallback
    # Rebuild validates that no old data slot is lost or dangling.
    for row in logical:
        row.pop("qualification", None)
    return report_ledger(source_id="house_clerk",document_sha256=source_sha,
        adapter_version=fallback["adapter_version"],page_count=len(pages),pages=observed,logical_rows=logical)
