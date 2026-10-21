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


def _header_cells(page, geometry):
    groups = {}
    for b in geometry["rects"]:
        if b[2]-b[0] > 20 and b[3]-b[1] > 20:
            groups.setdefault((round(b[1], 2), round(b[3], 2)), []).append(b)
    expected = ["ID", "Owner", "Asset", "Transaction Type", "Date", "Notification Date", "Amount", "Cap. Gains > $200?"]
    headers = []
    for boxes in groups.values():
        boxes = sorted(boxes, key=lambda b: b[0])
        if len(boxes) != 8 or any(abs(a[2]-b[0]) > 1 for a,b in zip(boxes, boxes[1:])):
            continue
        if [_text([w for w in page["words"] if _in(w,b)]) for b in boxes] == expected:
            headers.append(boxes)
    return headers[0] if len(headers) == 1 else None


def _segment(row, number):
    return [s["bbox_points"] for s in row["evidence"]["segments"] if s["page"]==number]


def _source_status_unique(row, pages):
    statuses=[]
    for number,page in enumerate(pages,1):
        for b in _segment(row,number):
            for line in _lines([w for w in page["words"] if _in(w,b)]):
                text=_text(line)
                if re.fullmatch(r"(?:F|Filing) (?:S|Status): (?:New|Amended)",text,re.I):
                    statuses.append(text.rsplit(":",1)[1].strip())
    return len(statuses)==1 and statuses[0].casefold()==str(row.get("filing_status","")).casefold()


def _table_page(page, geometry, rows, *, number, all_pages):
    width, height, words = page["width"], page["height"], page["words"]
    cells=_header_cells(page,geometry)
    if cells is None:
        return None
    left,right=cells[0][0],cells[-1][2]
    header_top,header_bottom=cells[0][1],cells[0][3]
    sides={}
    for side in (left,right):
        sides[side]=sorted(set((round(b[1],2),round(b[3],2)) for b in geometry["rects"]
            if b[2]-b[0]<=1.5 and abs(b[0]-side)<=1 and b[3]-b[1]>10
            and b[3]>header_bottom+1))
    raw_bands=sides[left]
    if (not raw_bands or len(raw_bands)!=len(sides[right]) or
            any(abs(a-c)>1 or abs(b-d)>1 for (a,b),(c,d) in zip(raw_bands,sides[right]))):
        return None
    # A repeated header overpaints the top of a continuing native cell. Keep
    # the original primitives in cache, clip only their role-accounting spans.
    # Overlapping spans form one source block only if all of its words bind to
    # the same existing logical row below; adjacent bands remain separate.
    bands=[]
    for top,bottom in raw_bands:
        top=max(top,header_bottom)
        if bands and top<bands[-1][1]-.8:
            bands[-1][1]=max(bottom,bands[-1][1])
        else:
            bands.append([top,bottom])
    if abs(bands[0][0]-header_bottom)>1 or any(abs(a[1]-b[0])>1 for a,b in zip(bands,bands[1:])):
        return None
    body=[w for w in words if w["top"]>=header_bottom-.8]
    ownership={};owned={index:[] for index,_ in rows}
    for w in body:
        matches=[index for index,row in rows if any(_in(w,b) for b in _segment(row,number))]
        if len(matches)!=1:
            return None
        ownership[id(w)]=matches[0];owned[matches[0]].append(w)
    for index,row in rows:
        if (not owned[index] or not _source_status_unique(row,all_pages) or
                (number>row["evidence"]["page"] and not any(w.get("size",9)>=8.8 for w in owned[index]))):
            return None
        detail_kind=None
        primary_top=row["evidence"]["bbox_points"][1]+2
        for line in _lines(owned[index]):
            text=_text(line)
            if all(w.get("size",9)<8.8 for w in line):
                # Notes have their own source detail-band witness. A prior D:
                # cannot turn a later aligned financial row into free prose.
                detail_bands=[(t,z) for t,z in bands if all(_in(w,[left,t,right,z]) for w in line)
                    and any(re.match(r"^(?:(?:F|Filing) (?:S|Status):|(?:S|Subholding) (?:O|Of):|(?:L|Location):|(?:D|Description):)",_text(other),re.I)
                        for other in _lines([w for w in owned[index] if _in(w,[left,t,right,z])]))]
                has_amount=any(cells[6][0]<=w["x0"]<cells[6][2] and "$" in str(w["text"]) for w in line)
                has_direction=any(cells[3][0]<=w["x0"]<cells[3][2] and re.fullmatch(r"[PSE]",_clean(w["text"]),re.I) for w in line)
                has_event=any(cells[4][0]<=w["x0"]<cells[4][2] and re.fullmatch(r"\d{2}/\d{2}/\d{4}",_clean(w["text"])) for w in line)
                if (not detail_bands or not cells[2][0]-.8<=min(w["x0"] for w in line)<cells[2][2]
                        or (has_amount and (has_direction or has_event))):
                    return None
                match=re.match(r"^(?:(F|Filing) (S|Status): (New|Amended)|(S|Subholding) (O|Of): .+|(L|Location): .+|(D|Description): .+)$",text,re.I)
                if match:
                    detail_kind=("description" if re.match(r"^(?:D|Description):",text,re.I) else "other")
                elif detail_kind!="description":
                    return None
            elif number==row["evidence"]["page"] and abs(line[0]["top"]-primary_top)<=2:
                continue
            elif any(not (_in(w,[cells[2][0],header_bottom,cells[2][2],bands[-1][1]]) or
                          _in(w,[cells[6][0],header_bottom,cells[6][2],bands[-1][1]])) for w in line):
                return None
    slots=[];mapping={};assigned=set();band_owners=[]
    for band_index,(top,bottom) in enumerate(bands,1):
        band_words=[w for w in body if _in(w,[left,top,right,bottom])]
        indices={ownership[id(w)] for w in band_words}
        if len(indices)!=1:
            return None
        index=next(iter(indices));row=dict(rows)[index]
        key=f"row-{index}-source-band-{band_index}"
        slots.append({"local_key":key,"bounds":[top,bottom],"disposition":"data_candidate",
            "observation_ref":row["extraction_id"]})
        mapping.setdefault(index,[]).append(key);assigned.update(id(w) for w in band_words)
        band_owners.append((top,bottom,index))
    if set(mapping)!=set(owned) or any(id(w) not in assigned for w in body):
        return None
    before=[w for w in words if w["top"]<header_top]
    lines=[_text(line) for line in _lines(before)]
    if number==1:
        if _financial(before) or not (any(text in {"P T R","Periodic Transaction Report"} for text in lines)
            and any(re.fullmatch(r"Filing ID #[0-9]+",text) for text in lines)
            and any(text.startswith("Name: ") for text in lines)
            and "Status: Member" in lines
            and any(re.fullmatch(r"State/District: [A-Z]{2}[0-9]{2}",text) for text in lines)):
            return None
        for text in lines:
            if not (text in {"P T R","Periodic Transaction Report","F I","Filer Information","T","Transactions","Status: Member"}
                or re.fullmatch(r"Filing ID #[0-9]+",text) or text.startswith("Name: ")
                or re.fullmatch(r"State/District: [A-Z]{2}[0-9]{2}",text)
                or (text.startswith("Clerk of the House of Representatives") and "Legislative Resource Center" in text)):
                return None
        if len(geometry["images"])!=1 or any(not _box_inside(b,[.08*width,.08*height,.19*width,.16*height]) for b in geometry["images"]):
            return None
    elif before or geometry["images"]:
        return None
    assigned.update(id(w) for w in before)
    assigned.update(id(w) for w in words if _in(w,[left,header_top,right,header_bottom]))
    if any(id(w) not in assigned for w in words):
        return None
    # A capital-gains control may straddle an overpainted repeated header.
    # Its unchanged source box must fit an original source cell span, whose
    # visible continuation was uniquely bound above; do not move its pixels.
    controls={}
    for b in geometry["curves"]:
        owners=set()
        for t,z in raw_bands:
            if _box_inside(b,[cells[7][0],t,right,z]):
                owners.update(index for a,c,index in band_owners if min(c,z)-max(a,max(t,header_bottom))>.8)
        if len(owners)!=1:
            return None
        controls.setdefault(next(iter(owners)),[]).append(b)
    for boxes in controls.values():
        # One printed control can contain nested outline/checkmark paths. A
        # second spatially distinct control is not another arbitrary curve.
        enclosing=[b for b in boxes if all(_box_inside(other,b,pad=.05) for other in boxes)]
        if not enclosing or any(b[2]-b[0]>12 or b[3]-b[1]>12 or
                               abs((b[2]-b[0])-(b[3]-b[1]))>.8 for b in enclosing):
            return None
    x_edges=[b[0] for b in cells]+[right]
    y_edges=[header_top,header_bottom]+[edge for band in raw_bands for edge in band]
    def aligned(value,edges):return any(abs(value-edge)<=1 for edge in edges)
    for b in geometry["rects"]:
        if b[1]>=header_top-.8 and not (aligned(b[0],x_edges) and aligned(b[2],x_edges)
                and aligned(b[1],y_edges) and aligned(b[3],y_edges)):
            return None
        if not (_box_inside(b,[left,header_top,right,bands[-1][1]]) or
                (b[3]-b[1]<=1.5 and b[1]<header_top and b[2]-b[0]>.5*width)):
            return None
    slots.insert(0,{"local_key":"native-table-header","bounds":[header_top,header_bottom],
        "disposition":"heading","observation_ref":f"{BASIS}:header:{_digest(cells)}"})
    return slots,mapping


def _box_inside(b, region, pad=.8):
    return region[0]-pad <= b[0] <= b[2] <= region[2]+pad and region[1]-pad <= b[1] <= b[3] <= region[3]+pad


def _closing_page(page, geometry):
    words=page["words"];lines=_lines(words)
    headings=[(line,_text(line)) for line in lines if all(11.5 <= w.get("size",0) <= 12.5 for w in line)]
    aliases={"I V D":"vehicle", "Investment Vehicle Details":"vehicle",
        "C":"comments", "Comments":"comments", "I P O":"ipo",
        "Initial Public Offering":"ipo", "C S":"certification",
        "Certification and Signature":"certification"}
    roles=[aliases.get(text) for _,text in headings]
    if roles not in (["vehicle","comments","ipo","certification"],
                     ["vehicle","ipo","certification"]):
        return False
    tops={role:line[0]["top"] for role,(line,_) in zip(roles,headings)}
    regions={role:[] for role in ["footnote",*roles]}
    for line in lines:
        if any(line is heading for heading,_ in headings):
            continue
        preceding=[role for role in roles if line[0]["top"]>tops[role]]
        regions[preceding[-1] if preceding else "footnote"].extend(line)
    footnote=_text(regions["footnote"])
    if footnote != "* For the complete list of asset type abbreviations, please visit https://fd.house.gov/reference/asset-type-codes.aspx.":
        return False
    # The official native template omits an empty Comments section entirely.
    # Explicit present-but-empty Comments likewise has no unassigned words;
    # no other closing heading may disappear or change order.
    if not regions["vehicle"] or _financial(regions["vehicle"]+regions.get("comments",[])):
        return False
    if _text(regions["ipo"]) != "Yes No":
        return False
    certification=[_text(line) for line in _lines(regions["certification"])]
    if len(certification)!=3 or not (certification[0] == "I CERTIFY that the statements I have made on the attached Periodic Transaction Report are true, complete, and correct to the best of"
        and certification[1] == "my knowledge and belief. Further, I CERTIFY that I have disclosed all transactions as required by the STOCK Act."
        and re.fullmatch(r"Digitally Signed: .+, [0-9]{2}/[0-9]{2}/[0-9]{4}",certification[2])):
        return False
    if geometry["images"]:
        return False
    vehicle_end=tops.get("comments",tops["ipo"])
    # Only source controls/bullets and account-detail enclosure belong here;
    # another table/border or drawing is not silently called a comment.
    if any(not any(_box_inside(b,region) for region in (
        [0,tops["vehicle"],page["width"],vehicle_end],
        [0,tops["ipo"],.20*page["width"],tops["certification"]],
        [0,tops["certification"],.08*page["width"],max(w.get("bottom",w["top"]+9) for w in regions["certification"])]))
        for b in geometry["curves"]):
        return False
    enclosures=[b for b in geometry["rects"] if b[3]-b[1]>2]
    if (len(enclosures)!=2 or any(abs(a-b)>1 for a,b in zip(*enclosures))
            or any(not _box_inside(b,[0,tops["vehicle"],page["width"],vehicle_end]) for b in enclosures)
            or any(not _in(w,enclosures[0]) for w in regions["vehicle"])):
        return False
    footnote_box=[min(w["x0"] for w in regions["footnote"]), min(w["top"] for w in regions["footnote"]),
        max(w.get("x1",w["x0"]) for w in regions["footnote"]),
        max(w.get("bottom",w["top"]+w.get("size",9)) for w in regions["footnote"])]
    if any(not _box_inside(b,footnote_box) for b in geometry["rects"] if b[3]-b[1]<=2):
        return False
    return True


def _split_closing_region(page, geometry):
    footnotes=[line for line in _lines(page["words"]) if _text(line)==
        "* For the complete list of asset type abbreviations, please visit https://fd.house.gov/reference/asset-type-codes.aspx."]
    if not footnotes:
        return (page,geometry,None,None)
    if len(footnotes)!=1:
        return None
    top=footnotes[0][0]["top"]
    table={**page,"words":[w for w in page["words"] if w["top"]<top-.8]}
    closing={**page,"words":[w for w in page["words"] if w["top"]>=top-.8]}
    table_g=deepcopy(geometry);closing_g=deepcopy(geometry)
    for kind in ("lines","rects","curves","images"):
        table_g[kind]=[];closing_g[kind]=[]
        for b in geometry[kind]:
            table_role=b[3]<=top+.8;closing_role=b[1]>=top-.8
            if int(table_role)+int(closing_role)!=1:
                return None
            (table_g[kind] if table_role else closing_g[kind]).append(b)
    return table,table_g,closing,closing_g


def apply_native_coverage(source_sha, pages, rows, fallback):
    """Compose table/closing roles without losing multi-page logical refs."""
    observed=deepcopy(fallback["pages"]);logical=deepcopy(fallback["logical_rows"])
    changed=False
    for number,page in enumerate(pages,1):
        geometry=_geometry(page,source_sha,number)
        if geometry is None:
            continue
        local=[(i,r) for i,r in enumerate(rows,1) if _segment(r,number)]
        if local:
            pieces=_split_closing_region(page,geometry)
            if pieces is None:
                continue
            table,table_g,closing,closing_g=pieces
            if closing is not None and not _closing_page(closing,closing_g):
                continue
            result=_table_page(table,table_g,local,number=number,all_pages=pages)
            if result is None:
                continue
            slots,mapping=result
            if closing is not None:
                slots.append({"local_key":"native-closing-sections","bounds":[
                    min(w["top"] for w in closing["words"]),
                    max(w.get("bottom",w["top"]+w.get("size",9)) for w in closing["words"])],
                    "disposition":"note","observation_ref":f"{BASIS}:observed_closing_section_roles:{_digest([closing['words'],closing_g])}"})
            observed[number-1]=page_ledger(source_id="house_clerk",document_sha256=source_sha,
                page_number=number,adapter_version=fallback["adapter_version"],coverage="located",
                coverage_reason=f"{BASIS}:header_bands_status_and_word_roles:{_digest(geometry)}",
                coordinate_frame="source_page_pdf_points",slots=slots)
            for index,keys in mapping.items():
                # Replace only this page's old refs. A later continuation must
                # append its own source bands, never erase the first page.
                logical[index-1]["slot_refs"]=[r for r in logical[index-1]["slot_refs"] if r["page_number"]!=number]+[
                    {"page_number":number,"slot_key":key} for key in keys]
            changed=True
        elif _closing_page(page,geometry):
            observed[number-1]=page_ledger(source_id="house_clerk",document_sha256=source_sha,
                page_number=number,adapter_version=fallback["adapter_version"],coverage="non_table",
                coverage_reason=f"{BASIS}:observed_closing_section_roles:{_digest([page['words'],geometry])}",
                coordinate_frame="source_page_pdf_points",slots=[])
            changed=True
    if not changed:
        return fallback
    for row in logical:row.pop("qualification",None)
    return report_ledger(source_id="house_clerk",document_sha256=source_sha,
        adapter_version=fallback["adapter_version"],page_count=len(pages),pages=observed,logical_rows=logical)
