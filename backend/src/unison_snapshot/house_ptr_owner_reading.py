"""Source-component crops for an independent saved paper Owner reading.

The producer requires an explicitly supplied recognizer. Normal materialization
only re-renders the bound PDF and checks saved evidence; it never starts OCR.
"""
from __future__ import annotations
import hashlib
import io
import json
import re

MODE="source_binary_owner_components/v1"


def _sha(image):
    stream=io.BytesIO();image.save(stream,format="PNG")
    return hashlib.sha256(stream.getvalue()).hexdigest()


def _cluster(values):
    import numpy as np
    groups=np.split(values,np.where(np.diff(values)>1)[0]+1)
    return [list(map(int,g)) for g in groups if len(g)]


def _geometry(image,page,row):
    """Use other rows/columns to classify printing, never Owner ink density."""
    import numpy as np
    from PIL import Image, ImageDraw
    from .house_ptr_source_glyphs import _components
    a=np.asarray(image.convert("L"))<100
    box=row["cells"]["owner"]["bbox_pixels"];x0,y0,x1,y1=box
    corridors=[];witnesses=[]
    for edge in (x0,x1):
        supports=[]
        for other in page["rows"]:
            cell=(other.get("cells") or {}).get("owner")
            if other["physical_band_index"]==row["physical_band_index"] or not cell:continue
            b=cell["bbox_pixels"]
            if b[0]!=x0 or b[2]!=x1 or b[3]-b[1]<=10:continue
            values=np.flatnonzero(a[b[1]+5:b[3]-5,edge-8:edge+9].mean(axis=0)>.78)+edge-8
            groups=_cluster(values)
            if len(groups)!=1:continue
            supports+=groups[0]
            witnesses.append({"axis":"vertical","edge":edge,"other_band":other["physical_band_index"],"pixels":groups[0]})
        if len([w for w in witnesses if w["axis"]=="vertical" and w["edge"]==edge])<2:return None
        if max(supports)-min(supports)+1 > (x1-x0)/8:return None
        corridors.append([min(supports),max(supports)])
    for edge in (y0,y1):
        supports=[];count=0
        for name in ("asset","event_date","notification_date"):
            cell=row["cells"].get(name)
            if not cell:continue
            b=cell["bbox_pixels"]
            if b[2]-b[0]<=10:continue
            values=np.flatnonzero(a[edge-8:edge+9,b[0]+5:b[2]-5].mean(axis=1)>.78)+edge-8
            groups=_cluster(values)
            if len(groups)!=1:continue
            supports+=groups[0];count+=1
            witnesses.append({"axis":"horizontal","edge":edge,"other_column":name,"pixels":groups[0]})
        if count<2 or max(supports)-min(supports)+1 > (y1-y0)/8:return None
        corridors.append([min(supports),max(supports)])
    left,right,top,bottom=corridors
    interior=[left[1]+1,top[1]+1,right[0],bottom[0]]
    if not (x0<interior[0]<interior[2]<x1 and y0<interior[1]<interior[3]<y1):return None
    # Actual target frame must close: independent print witnesses do not turn
    # a missing target border into a known cell.
    bounds=[left[0]-2,top[0]-2,right[1]+3,bottom[1]+3]
    if min(bounds)<0 or bounds[2]>image.width or bounds[3]>image.height:return None
    crop=(~a[bounds[1]:bounds[3],bounds[0]:bounds[2]]*255).astype("uint8")
    test=Image.fromarray(crop).copy()
    whites=np.argwhere(~a[interior[1]:interior[3],interior[0]:interior[2]])
    if not len(whites):return None
    sy,sx=whites[0];seed=(int(sx+interior[0]-bounds[0]),int(sy+interior[1]-bounds[1]))
    ImageDraw.floodfill(test,seed,128,thresh=0)
    face=np.asarray(test)==128
    if not face.any() or face[0].any() or face[-1].any() or face[:,0].any() or face[:,-1].any():return None
    residual=a[y0:y1,x0:x1].copy()
    for low,high in corridors[:2]:residual[:,max(0,low-x0):min(x1-x0,high-x0+1)]=False
    for low,high in corridors[2:]:residual[max(0,low-y0):min(y1-y0,high-y0+1),:]=False
    labels,stats=_components(residual)
    parts=sorted([list(map(int,v[:4])) for v in stats])
    if len(parts)!=2:return None
    for x,y,w,h in parts:
        if not (x+x0>interior[0] and x+w+x0<interior[2] and y+y0>interior[1] and y+h+y0<interior[3]):return None
    if min(v[1]+v[3] for v in parts)-max(v[1] for v in parts)<min(v[3] for v in parts)/2:return None
    component_box=[min(v[0] for v in parts)-2,min(v[1] for v in parts)-2,
        max(v[0]+v[2] for v in parts)+2,max(v[1]+v[3] for v in parts)+2]
    actual=image.crop(tuple(box)).crop(tuple(component_box))
    return {"owner_crop_sha256":_sha(image.crop(tuple(box))),"source_component_bboxes_in_cell":parts,
        "source_crop_in_cell":component_box,"component_crop_sha256":_sha(actual),
        "crop_padding_pixels":2,"print_corridors_pixels":corridors,"print_witnesses":witnesses,
        "target_frame_closed":True,"unassigned_content_component_count":0},actual


def _inputs(pdf_bytes,observation,selections):
    import numpy as np
    import pdfplumber
    import pypdfium2 as pdfium
    from .house_ptr_fixed_source_core import check_source_cells
    if observation.get("render_dpi")!=200 or hashlib.sha256(pdf_bytes).hexdigest()!=observation.get("source_sha256"):
        raise ValueError("House owner reading source mismatch")
    check_source_cells(pdf_bytes,observation,page_count=len(observation["pages"]),allow_unobserved=True,verify_crop_hashes=True)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf,pdfium.PdfDocument(pdf_bytes) as raster:
        for page in observation["pages"]:
            number=page["page"]
            selected=[v for v in selections if v[0]==number]
            if not selected:continue
            native_page=pdf.pages[number-1];images=native_page.images
            if native_page.chars or len(images)!=1:continue
            info=images[0]
            if (info.get("bits")!=1 or info["x0"]!=0 or info["top"]!=0 or
                abs(info["x1"]-native_page.width)>.001 or abs(info["bottom"]-native_page.height)>.001):continue
            objects=[v for v in raster[number-1].get_objects() if isinstance(v,pdfium.PdfImage)]
            if len(objects)!=1:continue
            native=objects[0].get_bitmap().to_pil().convert("L")
            image=native_page.to_image(resolution=200).original
            if native.size!=image.size or not np.array_equal(np.asarray(native),np.asarray(image.convert("L"))):continue
            if not np.isin(np.asarray(native),[0,255]).all():continue
            for row in page.get("rows",[]):
                key=(number,row["physical_band_index"])
                if key not in selected:continue
                geometry=_geometry(image,page,row)
                if geometry is None:continue
                proof,crop=geometry
                proof.update(page=number,physical_band_index=row["physical_band_index"],field="owner",reading_mode=MODE,
                    source_sha256=observation["source_sha256"],first_ocr_raw=row["cells"]["owner"]["raw_text"],
                    primary_ocr_engine=page.get("ocr_engine"),native_image_size=list(native.size),
                    native_pixels_sha256=hashlib.sha256(np.asarray(native).tobytes()).hexdigest())
                yield key,proof,crop


def observe_owner_component_readings(pdf_bytes,observation,selections,*,recognize,engine,engine_version):
    """Explicit bounded producer; caller owns the installed independent engine."""
    if (not isinstance(selections,list) or not 0<len(selections)<=3 or len(set(selections))!=len(selections)
            or engine!="rapidocr-onnxruntime" or not isinstance(engine_version,str)):
        raise ValueError("House independent owner selection/engine invalid")
    readings=[]
    for key,proof,crop in _inputs(pdf_bytes,observation,selections):
        raw,score=recognize(crop)
        readings.append({**proof,"second_ocr_engine":engine,"second_engine_version":engine_version,
            "second_ocr_raw":raw,"second_recognition_score":score})
    return {"schema_version":"house-paper-key-field-reread/v1","source_sha256":observation["source_sha256"],
        "qualification_status":"unverified_shadow_only","rereads":readings}


def verified_owner_readings(pdf_bytes,observation,field_reread):
    """Recompute all source geometry and ignore any self-reported acceptance."""
    if field_reread is None:return {}
    selected=[v for v in field_reread.get("rereads",[]) if v.get("field")=="owner"]
    if not selected:return {}
    by_key={(v.get("page"),v.get("physical_band_index")):v for v in selected}
    if len(by_key)!=len(selected):raise ValueError("House owner reading duplicated")
    accepted={}
    for key,proof,crop in _inputs(pdf_bytes,observation,list(by_key)):
        saved=by_key[key]
        if any(saved.get(k)!=v for k,v in proof.items()):continue
        if (saved.get("second_ocr_engine")!="rapidocr-onnxruntime" or
            not isinstance(proof.get("primary_ocr_engine"),str) or
            not proof["primary_ocr_engine"].casefold().startswith("tesseract ") or
            not isinstance(saved.get("second_engine_version"),str) or not saved["second_engine_version"].strip()):
            continue
        raw=saved.get("second_ocr_raw")
        code=re.sub(r"[^A-Za-z]","",proof["first_ocr_raw"])
        score=saved.get("second_recognition_score")
        if (code not in {"SP","DC","JT"} or raw!=code or type(score) not in {float,int} or not .9<=score<=1):continue
        accepted[key]={**proof,"second_ocr_engine":saved["second_ocr_engine"],
            "second_engine_version":saved["second_engine_version"],"second_ocr_raw":raw,
            "second_recognition_score":score,"owner_code":code,
            "decision":"independent_owner_reading_matches_source_components"}
    return accepted
