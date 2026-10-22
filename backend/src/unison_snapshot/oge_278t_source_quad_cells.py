"""Replay saved field-cell TSV against source-rendered quads, without new OCR."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path

MODE = "source_quad_cells/v1"
DETECTION_QUAD_MODE = "mapped_detection_quadrilateral/v1"
NUMBER_CONTENT_MODE = "source_quad_isolated_number_ink/v1"
_FIELDS = {"printed_number":0,"type":2,"transaction_date":3,"amount":4}
_LAYERS = {"full_source":"all_image","source_text_objects_auxiliary":"text_image"}


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _render(source,sha,number,resolution,cache):
    key=(str(Path(source).resolve()),sha,number,resolution)
    if key not in cache:
        from .pdf_text_objects import render_pdf_text_objects
        cache[key]=render_pdf_text_objects(source,expected_sha256=sha,page_number=number,resolution=resolution)
    return cache[key]


def _number_content_pixels(pixels, *, padding):
    """Locate all ink separate from the grid, without painting any source pixel.

    This bounded 300dpi mode supports narrow printed-number cells. A component
    touching the grid corridor cannot be interpreted as content. Extra ink is
    retained, never discarded as a small component or recognition noise.
    """
    import cv2
    import numpy as np
    ink=(pixels<100).astype('uint8')
    count,labels,stats,_=cv2.connectedComponentsWithStats(ink,8)
    height,width=pixels.shape;frames=[];content=[];proof=[]
    for label in range(1,count):
        yy,xx=np.where(labels==label)
        inset=np.minimum.reduce([xx-padding,width-padding-1-xx,yy-padding,height-padding-1-yy])
        x,y,w,h,area=map(int,stats[label])
        frame=bool(inset.max()<=3 and (w>=.75*(width-2*padding) or h>=.75*(height-2*padding)))
        proof.append({'bbox_pixels':[x,y,x+w,y+h],'area_pixels':area,'frame':frame})
        if frame:frames.append(label)
        else:
            if inset.min()<=3:raise ValueError('number ink touches an unresolved frame component')
            content.append(label)
    if not frames or not content:raise ValueError('number source lacks separate grid and content')
    yy,xx=np.where(np.isin(labels,content))
    box=[int(xx.min())-2,int(yy.min())-2,int(xx.max())+3,int(yy.max())+3]
    return box,pixels[box[1]:box[3],box[0]:box[2]].copy(),proof


def source_quad_cell(rendered, page, slot, field, *, layer, padding=20, content_crop_mode=None):
    """Derive exact perspective crop and mapping from the real PDFium canvas."""
    import numpy as np
    import cv2
    from .oge_278t_raster_slots import slot_quad
    if field not in _FIELDS or layer not in _LAYERS or type(padding) is not int or padding != 20:
        raise ValueError("unsupported source quad field/layer/padding")
    receipt=rendered["receipt"];a=receipt["pixel_edge_to_source_pdf_affine"]
    media=receipt["source_mediabox_pdf_bottom_left"]
    if receipt.get("source_rotation_degrees") != 0:
        raise ValueError("source quad displayed rotation is unsupported")
    frame=page.get("column_geometry_observation",{}).get("render_page_bbox")
    # PDFium exposes float32 page boxes; pdfplumber retains decimal PDF values.
    # This is a coordinate metadata precision check, never a word/quad overhang.
    expected_frame=[0,0,media[2]-media[0],media[3]-media[1]]
    if frame is not None and (len(frame)!=4 or any(abs(a-b)>1e-4 for a,b in zip(frame,expected_frame))):
        raise ValueError("source quad display frame differs from original MediaBox")
    affine=np.array([[a[0],a[2],a[4]],[a[1],a[3],a[5]],[0.,0.,1.]])
    inverse=np.linalg.inv(affine);column=_FIELDS[field]
    quad=slot_quad(page,slot,column,6 if field=="amount" else column+1)
    points=[]
    for x,y in quad:
        p=inverse@np.array([x+media[0],media[3]-y,1.])
        points.append(p[:2]/p[2])
    points=np.array(points,dtype="float32")
    width=round(max(np.linalg.norm(points[1]-points[0]),np.linalg.norm(points[2]-points[3])))
    height=round(max(np.linalg.norm(points[3]-points[0]),np.linalg.norm(points[2]-points[1])))
    if width < 2 or height < 2:raise ValueError("source quad cell has no usable area")
    matrix=cv2.getPerspectiveTransform(points,np.array([[0,0],[width-1,0],[width-1,height-1],[0,height-1]],dtype="float32"))
    pixels=np.pad(cv2.warpPerspective(np.asarray(rendered[_LAYERS[layer]]),matrix,(width,height),borderValue=255),padding,constant_values=255)
    content_proof={}
    if content_crop_mode is not None:
        if content_crop_mode!=NUMBER_CONTENT_MODE or field!='printed_number' or layer!='full_source' or receipt['resolution']!=300:
            raise ValueError('isolated number crop requires 300dpi original full-source number field')
        full_sha=hashlib.sha256(pixels.tobytes()).hexdigest()
        box,pixels,components=_number_content_pixels(pixels,padding=padding)
        # Preserve exactly the original two-pixel context of the saved crop.
        # The matrix maps the new unpadded content coordinates; TSV stays raw.
        matrix=np.array([[1,0,padding-box[0]-2],[0,1,padding-box[1]-2],[0,0,1.]])@matrix
        padding=2
        content_proof={'content_crop_mode':content_crop_mode,'content_crop_box_in_full_cell_pixels':box,
                       'full_cell_pixel_sha256':full_sha,'source_component_observations':components}
    return {"quad_pdf_points":quad,"source_pixel_to_cell_matrix":matrix.tolist(),
        "source_crop_pixel_sha256":hashlib.sha256(pixels.tobytes()).hexdigest(),
        "crop_pixel_size":[pixels.shape[1],pixels.shape[0]],"padding":padding,**content_proof},pixels


def observe_saved_source_quad_cells(source_pdf_path: Path, *, slots_observation: dict,
        field: str, saved_pages: list[dict], layer: str, resolution: int = 300,
        ocr_engine: str, render_cache: dict | None = None,
        detection_geometry_mode: str | None = None, content_crop_mode: str | None = None) -> dict:
    """Validate saved recipes/TSV and reconstruct normal secondary observations.

    Source path is supplied by the controlled caller, never taken from a read.
    Text-object selection is auxiliary and cannot establish full-source coverage.
    Optional saved-page selected_slot_indices binds sparse crops explicitly;
    unselected slots are placeholders, never additional reads or OCR votes.
    """
    import numpy as np
    from .oge_278t_field_shadow import _validate_geometry_input,_secondary_slot_geometry,_date_shape,_amount_detection_reading,_ordered_amount_shape
    from .oge_278t_raster_slots import column_geometry_sha256,DUAL_MODE
    from .ocr_geometry import words_from_tesseract_tsv
    if detection_geometry_mode not in (None, DETECTION_QUAD_MODE):
        raise ValueError("unknown source quad detection geometry mode")
    if content_crop_mode not in (None,NUMBER_CONTENT_MODE):raise ValueError('unknown source content crop mode')
    _validate_geometry_input(slots_observation)
    if (field not in _FIELDS or layer not in _LAYERS or type(resolution) is not int or not 200 <= resolution <= 400
            or not isinstance(ocr_engine,str) or not ocr_engine.strip()):
        raise ValueError("source quad read parameters are invalid")
    sha=hashlib.sha256(Path(source_pdf_path).read_bytes()).hexdigest()
    if sha != slots_observation["source_sha256"]:raise ValueError("source quad PDF SHA differs")
    pages={p["page_number"]:p for p in slots_observation["pages"]}
    if (not isinstance(saved_pages,list) or not 1 <= len(saved_pages) <= 8 or
            len({p.get("page_number") for p in saved_pages}) != len(saved_pages)):
        raise ValueError("source quad pages are missing/repeated")
    cache={} if render_cache is None else render_cache
    output=[];count=0
    for saved in saved_pages:
        number=saved["page_number"];page=pages.get(number)
        if page is None or page.get("geometry_mode") != DUAL_MODE:
            raise ValueError("source quad reads require the bound dual-axis page")
        rendered=_render(source_pdf_path,sha,number,resolution,cache)
        if canonical_sha(rendered["receipt"]) != canonical_sha(saved.get("render_receipt")):
            raise ValueError("source quad render receipt differs from original PDF")
        cells=saved.get("cells");base={s["slot_index"]:s for s in page["slots"]}
        selection=saved.get("selected_slot_indices")
        if selection is not None and (not isinstance(selection,list) or not selection or
                any(type(i) is not int for i in selection) or selection!=sorted(set(selection)) or
                not set(selection)<=set(base)):
            raise ValueError("source quad selected slots are invalid")
        selected=set(base) if selection is None else set(selection)
        if (not isinstance(cells,list) or len(cells)!=len(selected) or
                any(not isinstance(c,dict) or type(c.get("slot_index")) is not int for c in cells) or
                {c.get("slot_index") for c in cells}!=selected):
            raise ValueError("source quad cells differ from selected bound physical slots")
        bound_cells={c["slot_index"]:c for c in cells}
        result=[]
        indices=list(base) if selection is not None else [c["slot_index"] for c in cells]
        for index in indices:
            slot=base[index]
            if index not in selected:
                result.append({"slot_index":index,**_secondary_slot_geometry(page,slot),
                    "band_pdf_points":slot["band_pdf_points"],
                    "page_raw_ocr_text":" ".join(slot["column_observation"][name]["raw_ocr_text"] for name in (("notification","amount") if field=="amount" else (field,))),
                    "column_raw_ocr_text":"", "column_shape":None if field=="type" else
                        {"status":"unknown","value":None,"reason":"slot_not_selected"},
                    "column_words" if field=="type" else "column_detections":[],
                    "observation_status":"not_selected"})
                continue
            cell=bound_cells[index];recipe=cell.get("recipe")
            expected,pixels=source_quad_cell(rendered,page,slot,field,layer=layer,content_crop_mode=content_crop_mode)
            if (not isinstance(recipe,dict) or recipe.get("physical_index")!=index-1 or recipe.get("column")!=_FIELDS[field]
                    or any(canonical_sha(recipe.get(k))!=canonical_sha(expected[k]) for k in
                        ("quad_pdf_points","source_pixel_to_cell_matrix","source_crop_pixel_sha256","padding"))):
                raise ValueError("source quad crop/transform differs from original PDF")
            if content_crop_mode and any(canonical_sha(recipe.get(k))!=canonical_sha(v) for k,v in expected.items()):
                raise ValueError('number content crop source proof differs')
            tsv=cell.get("tsv_utf8")
            if not isinstance(tsv,str) or "\ufffd" in tsv:
                raise ValueError("source quad cell lacks unaltered valid original TSV")
            raw=tsv.encode("utf-8");tsv_sha=hashlib.sha256(raw).hexdigest()
            if recipe.get("tsv_sha256")!=tsv_sha:raise ValueError("source quad TSV hash differs")
            words=words_from_tesseract_tsv(raw,points_per_pixel=1.)
            inverse=np.linalg.inv(np.array(expected["source_pixel_to_cell_matrix"]))
            a=rendered["receipt"]["pixel_edge_to_source_pdf_affine"];media=rendered["receipt"]["source_mediabox_pdf_bottom_left"]
            detections=[]
            for word in words:
                x,y,x1,y1=word["x0"],word["top"],word["x1"],word["bottom"]
                if not (0<=x<x1<=pixels.shape[1] and 0<=y<y1<=pixels.shape[0]):raise ValueError("source quad TSV word exceeds crop")
                corners=[]
                for u,v in ((x,y),(x1,y),(x1,y1),(x,y1)):
                    p=inverse@np.array([u-expected["padding"],v-expected["padding"],1.]);px,py=p[0]/p[2],p[1]/p[2]
                    sx=a[0]*px+a[2]*py+a[4];sy=a[1]*px+a[3]*py+a[5]
                    corners.append((float(sx-media[0]),float(media[3]-sy)))
                detections.append({"text":word["text"],"confidence":word["ocr_confidence"],
                    "bbox_pdf_points":[min(p[0] for p in corners),min(p[1] for p in corners),max(p[0] for p in corners),max(p[1] for p in corners)]})
                if detection_geometry_mode:
                    # Keep the old axis-aligned envelope and the actual mapped
                    # quadrilateral separately. Under a projective transform an
                    # enclosing horizontal box can cross a slanted row even
                    # when all original corners are inside the field.
                    detections[-1].update(quad_pdf_points=[list(p) for p in corners],
                        rectified_bbox_pixels=[x,y,x1,y1],
                        source_quad_inner_pixel_bounds=[expected['padding'],expected['padding'],
                            pixels.shape[1]-expected['padding']-1,pixels.shape[0]-expected['padding']-1])
            text=" ".join(d["text"] for d in detections)
            if field=="type":shape=text.casefold() if len(detections)==1 and text.casefold() in {"purchase","sale","exchange"} else None
            elif field=="amount":
                amount_reading=_amount_detection_reading(detections,page=page,source_slot=slot)
                shape=_ordered_amount_shape(amount_reading)
            elif field=="printed_number":
                value=int(text) if len(detections)==1 and re.fullmatch(r"[1-9][0-9]*",text) else None
                shape={"status":"ocr_shape_only" if value is not None else "unknown","value":value}
            else:shape=_date_shape(text) if len(detections)==1 else {"status":"unknown","value":None,"reason":"multiple_date_detections" if detections else "no_date_detection"}
            proof={"schema_version":"source-quad-cell-replay/v1","source_sha256":sha,"page_number":number,
                "slot_index":index,"field_role":field,"render_layer":layer,"render_receipt_sha256":canonical_sha(rendered["receipt"]),
                **expected,"tsv_sha256":tsv_sha,"coverage":"auxiliary_only_not_full_source_coverage" if layer!="full_source" else "field_only_not_page_coverage"}
            result.append({"slot_index":index,**_secondary_slot_geometry(page,slot),"band_pdf_points":slot["band_pdf_points"],
                "page_raw_ocr_text":" ".join(slot["column_observation"][name]["raw_ocr_text"] for name in (("notification","amount") if field=="amount" else (field,))),"column_raw_ocr_text":text,
                "column_shape":shape,"column_words" if field=="type" else "column_detections":detections,"source_quad_cell":proof,
                **({"column_reading":amount_reading} if field=="amount" else {})})
            count+=1
        output.append({"page_number":number,"status":f"{'date' if field=='transaction_date' else field}_column_observed",
            "geometry_mode":DUAL_MODE,"slot_count":len(result),"slots":result})
    return {"schema_version":f"oge-278t-{'date' if field=='transaction_date' else field.replace('_','-')}-column-observation/v{'2' if field=='amount' else '1'}",
        "read_mode":MODE,"source_sha256":sha,"input_observation_canonical_sha256":canonical_sha(slots_observation),
        "input_page_canonical_sha256":{str(p["page_number"]):canonical_sha(pages[p["page_number"]]) for p in saved_pages},
        "input_column_geometry_sha256":{str(p["page_number"]):column_geometry_sha256(pages[p["page_number"]]) for p in saved_pages},
        "ocr_engine":ocr_engine,"ocr_resolution_dpi":resolution,"input_ocr_resolution_dpi":slots_observation["ocr_resolution_dpi"],
        "source_quad_input":{"field":field,"layer":layer,"resolution":resolution,"saved_pages":deepcopy(saved_pages),
            **({"detection_geometry_mode":detection_geometry_mode} if detection_geometry_mode else {}),
            **({'content_crop_mode':content_crop_mode} if content_crop_mode else {})},
        **({"detection_geometry_mode":detection_geometry_mode} if detection_geometry_mode else {}),
        "summary":{"observed_slots":count,"new_ocr_invocation_count":0},"pages":output}


def validate_source_quad_reading(reading, originals, source_pdf_path, *, render_cache=None):
    if source_pdf_path is None:raise ValueError("source quad read requires controlled original PDF path")
    matches=[o for o in originals if canonical_sha(o)==reading.get("input_observation_canonical_sha256")]
    if len(matches)!=1:raise ValueError("source quad read differs from primary source fingerprint")
    inputs=reading.get("source_quad_input")
    required={"field","layer","resolution","saved_pages"}
    if (not isinstance(inputs,dict) or not required<=set(inputs)<=required|{'detection_geometry_mode','content_crop_mode'} or
            ('detection_geometry_mode' in inputs and inputs['detection_geometry_mode']!=DETECTION_QUAD_MODE) or
            ('content_crop_mode' in inputs and inputs['content_crop_mode']!=NUMBER_CONTENT_MODE)):
        raise ValueError("source quad replay inputs are invalid")
    expected=observe_saved_source_quad_cells(source_pdf_path,slots_observation=matches[0],ocr_engine=reading.get("ocr_engine"),
        render_cache=render_cache,**inputs)
    if canonical_sha(expected)!=canonical_sha(reading):raise ValueError("source quad secondary differs from original crop/TSV replay")
