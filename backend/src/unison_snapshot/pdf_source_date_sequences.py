"""Narrow source glyph evidence for an auxiliary inserted day digit.

Raw source text and auxiliary OCR are never rewritten. This producer can show
that a complete source Date contains one slash and one nine, not an extra one.
Role, full-field geometry and visibility must still bind at the consumer.
"""
from __future__ import annotations
import hashlib
import io
import json
import re
from pathlib import Path
from .pdf_source_glyphs import cff_cid_to_gid
from .pdf_text_object_words import observe_pdf_text_objects

RULE="source-date-single-slash-nine-sequence/v1"
PARAMETERS={"em_pixels":256,"alpha_threshold":128,"slash_slope_absolute":[.15,.65],
    "slash_residual_over_height_max":.08,"slash_width_over_height_max":.65,
    "nine_width_over_height":[.4,.9],"nine_hole_center_y":[.1,.55],"nine_hole_area_over_box_min":.02,
    "source_matrix_storage_precision_points":.001}
_DATE=re.compile(r"(\d{1,2})/(\d{1,2})/(20\d{2})")


def glyph_sequence_shape(alpha) -> dict:
    """Conservative ink topology. Input is original glyph alpha, positive font Y."""
    import cv2
    import numpy as np
    mask=(np.flipud(np.asarray(alpha))>=PARAMETERS["alpha_threshold"]).astype("uint8")
    if mask.ndim!=2:raise ValueError("source glyph alpha must have two dimensions")
    ys,xs=np.nonzero(mask)
    if not len(xs):return {"supports_single_slash":False,"supports_single_nine":False,"reason":"no_source_ink"}
    mask=mask[ys.min():ys.max()+1,xs.min():xs.max()+1];height,width=mask.shape
    count,labels,stats,_=cv2.connectedComponentsWithStats(mask,8)
    areas=stats[1:,cv2.CC_STAT_AREA];largest=int(np.argmax(areas))+1;main=(labels==largest).astype("uint8")
    contours,hierarchy=cv2.findContours(main,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE);holes=[]
    if hierarchy is not None:
        for contour,level in zip(contours,hierarchy[0]):
            moments=cv2.moments(contour)
            if level[3]>=0 and moments["m00"]>2:
                holes.append({"area_over_box":float(cv2.contourArea(contour)/(width*height)),
                    "center_y_over_height":float(moments["m01"]/moments["m00"]/height)})
    pairs=[(y,float(np.flatnonzero(main[y]).mean())) for y in range(height) if main[y].any()]
    if len(pairs)<8:return {"supports_single_slash":False,"supports_single_nine":False,"reason":"too_few_source_scanlines"}
    yy,xx=np.asarray(pairs).T;slope,offset=np.polyfit(yy,xx,1)
    residual=float(np.std(xx-(slope*yy+offset)))/height
    single=bool(len(areas)==1)
    ratio=width/height
    result={"ink_size":[width,height],"component_count":int(count-1),"holes":holes,
        "width_over_height":ratio,"center_slope":float(slope),"center_residual_over_height":residual}
    result["supports_single_slash"]=bool(single and not holes and
        PARAMETERS["slash_slope_absolute"][0]<=abs(slope)<=PARAMETERS["slash_slope_absolute"][1] and
        residual<=PARAMETERS["slash_residual_over_height_max"] and ratio<=PARAMETERS["slash_width_over_height_max"])
    result["supports_single_nine"]=bool(single and len(holes)==1 and
        PARAMETERS["nine_width_over_height"][0]<=ratio<=PARAMETERS["nine_width_over_height"][1] and
        PARAMETERS["nine_hole_center_y"][0]<=holes[0]["center_y_over_height"]<=PARAMETERS["nine_hole_center_y"][1] and
        holes[0]["area_over_box"]>=PARAMETERS["nine_hole_area_over_box_min"])
    return result


def compare_auxiliary_date(record: dict, auxiliary_text: str) -> dict:
    """Explain only an extra day-one contradicted by a proved source sequence."""
    base={"rule":RULE,"raw_source_text":record.get("raw_text"),"raw_auxiliary_text":auxiliary_text,
        "status":"not_resolved","raw_text_modified":False}
    if record.get("status")!="source_slash_nine_supported" or not isinstance(auxiliary_text,str):return base
    source=_DATE.fullmatch(record["raw_text"].strip());aux=_DATE.fullmatch(auxiliary_text.strip())
    if source is None or aux is None:return base
    if (int(source[2])==9 and int(aux[2])==19 and source[1]==aux[1] and source[3]==aux[3]):
        return {**base,"status":"auxiliary_extra_day_one_contradicted_by_source_glyph_sequence",
            "source_text":record["raw_text"].strip(),"source_object_index":record["source_object_index"],
            "source_glyph_indices":record["slash_nine_glyph_indices"],
            "requires_bound_date_role":True,"requires_source_visibility":True}
    return base


def _content_and_fonts(data: bytes, page_number: int):
    from pdfminer.pdfdevice import PDFTextDevice
    from pdfminer.pdfinterp import PDFPageInterpreter,PDFResourceManager
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdftypes import resolve1
    class Device(PDFTextDevice):
        def __init__(self,manager):super().__init__(manager);self.rows=[];self.current=None
        def render_string(self,state,sequence,ncs=None,graphicstate=None):
            row={"font":str(state.font.fontname),"matrix":list(state.matrix),"ctm":list(self.ctm),
                 "linematrix":list(state.linematrix),"rise":state.rise,"font_size":state.fontsize,"glyphs":[]}
            self.current=row
            super().render_string(state,sequence,ncs,graphicstate)
            row["raw_text"]="".join(g["raw_unicode"] or "\ufffd" for g in row["glyphs"])
            self.rows.append(row);self.current=None
        def render_char(self,matrix,font,fontsize,scaling,rise,cid,ncs,graphicstate):
            try:text=font.to_unichr(cid)
            except Exception:text=None
            advance=font.char_width(cid)*fontsize*scaling
            self.current["glyphs"].append({"cid":cid,"raw_unicode":text,"source_matrix":list(matrix),"advance_pdf_points":advance})
            return advance
    page=next(PDFPage.get_pages(io.BytesIO(data),pagenos={page_number-1}));fonts={}
    for resource,reference in resolve1(page.resources.get("Font",{})).items():
        dictionary=resolve1(reference)
        if str(dictionary.get("Encoding"))!="/'Identity-H'":continue
        descendants=resolve1(dictionary.get("DescendantFonts",[]))
        if len(descendants)!=1:continue
        descriptor=resolve1(resolve1(descendants[0]).get("FontDescriptor",{}));program=resolve1(descriptor.get("FontFile3"));cmap=resolve1(dictionary.get("ToUnicode"))
        if program is None or cmap is None:continue
        raw=program.get_data();name=dictionary["BaseFont"].name.removesuffix("-Identity-H")
        fonts.setdefault(name,[]).append({"data":raw,"font_resource":str(resource),
            "font_program_sha256":hashlib.sha256(raw).hexdigest(),"to_unicode_sha256":hashlib.sha256(cmap.get_data()).hexdigest()})
    manager=PDFResourceManager();device=Device(manager);PDFPageInterpreter(manager,device).process_page(page)
    return device.rows,fonts


def observe_source_date_sequences(source: Path, *, expected_sha256: str, page_number: int) -> dict:
    """Recompute complete original Date objects, CIDs, positions and glyph alpha."""
    import numpy as np
    import pymupdf
    observation=observe_pdf_text_objects(source,expected_sha256=expected_sha256,page_number=page_number)
    data=Path(source).read_bytes();contents,fonts=_content_and_fonts(data,page_number);records=[];glyph_cache={}
    for obj in observation["objects"]:
        raw=obj["raw_text"];match=_DATE.fullmatch(raw.strip())
        if match is None:continue
        base={"source_object_index":obj["source_object_index"],"raw_text":raw,
              "bbox_displayed_mediabox":obj["bbox_displayed_mediabox"],"matrix_pdf_bottom_left":obj["matrix_pdf_bottom_left"],
              "font_base_name":obj["font_base_name"],"status":"unsupported"}
        matches=[c for c in contents if c["font"]==obj["font_base_name"] and c["raw_text"]==raw and
            abs(c["font_size"]-obj["font_size"])<.0001 and all(abs(a-b)<=PARAMETERS["source_matrix_storage_precision_points"]
                for a,b in zip(c["matrix"],obj["matrix_pdf_bottom_left"]))]
        if obj["exclusion_reasons"] or obj["observation_issues"] or len(matches)!=1 or len(fonts.get(obj["font_base_name"],[]))!=1:
            records.append({**base,"reason":"source_content_or_font_not_uniquely_bound"});continue
        content=matches[0];font=fonts[obj["font_base_name"]][0];glyphs=content["glyphs"]
        base.update(glyphs=glyphs,source_content_matrix=content["matrix"],font_program_sha256=font["font_program_sha256"],
            to_unicode_sha256=font["to_unicode_sha256"],font_resource=font["font_resource"])
        if content["ctm"]!=[1,0,0,1,0,0] or content["linematrix"]!=[0,0] or content["rise"]!=0:
            records.append({**base,"reason":"unsupported_content_transform"});continue
        offset=0;spans=[]
        for g in glyphs:
            spans.append([offset,offset+len(g["raw_unicode"] or "")]);offset=spans[-1][1]
        # No Unicode edit: require each relevant visible mark to be one atomic source glyph.
        stripped_left=len(raw)-len(raw.lstrip());slash_offset=stripped_left+len(match[1]);nine_offset=slash_offset+1
        indices=[i for i,span in enumerate(spans) if span==[slash_offset,slash_offset+1]]
        day=[i for i,span in enumerate(spans) if span==[nine_offset,nine_offset+1]]
        if match[2]!='9' or len(indices)!=1 or len(day)!=1 or day[0]!=indices[0]+1:
            records.append({**base,"reason":"date_is_not_atomic_slash_followed_by_single_day_nine"});continue
        first,second=indices[0],day[0]
        if glyphs[first]["raw_unicode"]!='/' or glyphs[second]["raw_unicode"]!='9':
            records.append({**base,"reason":"atomic_source_unicode_does_not_match_marks"});continue
        try:mapping=cff_cid_to_gid(font["data"])
        except (ValueError,KeyError,IndexError):
            records.append({**base,"reason":"unsupported_CID_charset"});continue
        if any(glyphs[i]["cid"] not in mapping for i in (first,second)):
            records.append({**base,"reason":"source_CID_absent_from_font_charset"});continue
        evidence=[]
        for index in (first,second):
            item=glyphs[index];key=(font["font_program_sha256"],item["cid"])
            if key not in glyph_cache:
                embedded=pymupdf.Font(fontbuffer=font["data"]);gid=mapping[item["cid"]]
                pix=pymupdf.Pixmap(pymupdf.mupdf.fz_render_glyph_pixmap(embedded.this,gid,
                    pymupdf.mupdf.FzMatrix(256,0,0,256,0,0),pymupdf.mupdf.FzIrect(-2000,-2000,2000,2000),8))
                alpha=np.frombuffer(pix.samples,dtype="uint8").reshape(pix.height,pix.width)
                clipped=pix.x<=-2000 or pix.y<=-2000 or pix.x+pix.width>=2000 or pix.y+pix.height>=2000
                glyph_cache[key]={"gid":gid,"glyph_pixel_bbox":[pix.x,pix.y,pix.width,pix.height],
                    "glyph_alpha_sha256":hashlib.sha256(alpha.tobytes()).hexdigest(),
                    "measurements":glyph_sequence_shape(alpha) if not clipped else {"supports_single_slash":False,"supports_single_nine":False,"reason":"glyph_clipped"}}
            evidence.append({"glyph_index":index,**item,**glyph_cache[key]})
        # Positions are raw drawing positions: no missing, reordered or backward glyph is allowed.
        ordered=all(b["source_matrix"][4]>a["source_matrix"][4] and a["advance_pdf_points"]>0 for a,b in zip(glyphs,glyphs[1:]))
        supported=ordered and evidence[0]["measurements"]["supports_single_slash"] and evidence[1]["measurements"]["supports_single_nine"]
        records.append({**base,"status":"source_slash_nine_supported" if supported else "unsupported",
            "slash_nine_glyph_indices":[first,second],"slash_nine_glyphs":evidence,"complete_source_glyph_ordered":ordered,
            "reason":None if supported else "source_marks_not_individually_proved"})
    if hashlib.sha256(Path(source).read_bytes()).hexdigest()!=expected_sha256:raise ValueError("source changed during date sequence observation")
    return {"schema_version":"pdf-source-date-sequences/v1","source_sha256":expected_sha256,"page_number":page_number,
        "rule":RULE,"parameters":json.loads(json.dumps(PARAMETERS)),"observations":records,
        "source_text_manifest_sha256":observation["text_object_manifest_sha256"],"raw_text_modified":False,"new_ocr_calls":0,
        "requires_confirmed_date_field_and_source_visibility":True}
