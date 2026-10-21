"""Read printed date structure from source pixels and saved numeric OCR.

No slash is inserted from a malformed string: two independent source ink
components must first satisfy the frozen diagonal-stroke geometry. Numeric
crops must contain their entire source components, including overlapping boxes.
"""
from collections import deque
import hashlib
from math import isfinite,sqrt
import re
from .senate import SenateEfdError

SCHEMA="senate-source-segmented-date/v1"


def segment_geometry(image):
    image=image.convert("L");width,height=image.size;raw=image.tobytes()
    dark={i for i,value in enumerate(raw) if value<128};components=[]
    while dark:
        seed=min(dark);dark.remove(seed);queue=deque([seed]);pixels=[seed]
        while queue:
            point=queue.popleft();x,y=point%width,point//width
            for yy in range(max(0,y-1),min(height,y+2)):
                for xx in range(max(0,x-1),min(width,x+2)):
                    neighbor=yy*width+xx
                    if neighbor in dark:
                        dark.remove(neighbor);queue.append(neighbor);pixels.append(neighbor)
        xs=[v%width for v in pixels];ys=[v//width for v in pixels]
        box=[min(xs),min(ys),max(xs)+1,max(ys)+1]
        if box[3]-box[1]<18 or len(pixels)<50:continue
        by_y={}
        for x,y in zip(xs,ys):by_y.setdefault(y,[]).append(x)
        centers={y:sum(values)/len(values) for y,values in by_y.items()}
        mean_y=sum(centers)/len(centers);mean_x=sum(centers.values())/len(centers)
        denominator=sum((y-mean_y)**2 for y in centers)
        slope=sum((y-mean_y)*(x-mean_x) for y,x in centers.items())/denominator
        offset=mean_x-slope*mean_y
        rmse=sqrt(sum((x-(slope*y+offset))**2 for y,x in centers.items())/len(centers))
        ratio=(box[2]-box[0])/(box[3]-box[1])
        slash=(ratio<=.5 and -.6<=slope<=-.15 and rmse<=1.25 and
               len(centers)/(box[3]-box[1])>=.8)
        ink=set(pixels)
        components.append({"box_px":box,"area":len(pixels),"slope":round(slope,6),
            "center_rmse":round(rmse,6),"is_slash":slash,
            "ink_mask_sha256":hashlib.sha256(bytes(1 if i in ink else 0
                for i in range(width*height))).hexdigest()})
    slashes=sorted([c for c in components if c["is_slash"]],key=lambda c:c["box_px"][0])
    result={"components":components,"status":"unknown","slashes":slashes}
    if len(slashes)!=2:return result
    centers=[(s["box_px"][0]+s["box_px"][2])/2 for s in slashes]
    groups=[[],[],[]]
    for component in components:
        if component["is_slash"]:continue
        box=component["box_px"];center=(box[0]+box[2])/2
        if any(box[0]<=edge<=box[2] for edge in centers):return result
        index=0 if center<centers[0] else 1 if center<centers[1] else 2
        groups[index].append(box)
    if any(not group for group in groups):return result
    boxes=[[min(b[0] for b in g),min(b[1] for b in g),max(b[2] for b in g),max(b[3] for b in g)] for g in groups]
    result.update(status="two_source_slashes",numeric_component_boxes_px=boxes)
    return result


def _numeric_crop(image,box):
    from PIL import ImageOps
    return ImageOps.expand(image.crop(tuple(box)).convert("RGB"),border=8,fill="white")


def saved_segmented_date(image,*,page_sha256,page_number,grid_slot,date_box_px,reads,engine):
    """Bind existing numeric reads to the source cell; never runs an OCR model."""
    cell=image.crop(tuple(date_box_px)).convert("RGB")
    return {"schema_version":SCHEMA,"page_sha256":page_sha256,"page_number":page_number,
        "grid_slot":grid_slot,"date_box_px":date_box_px,
        "date_grayscale_sha256":hashlib.sha256(cell.convert("L").tobytes()).hexdigest(),
        "geometry":segment_geometry(cell),"numeric_reads":[{**r,
            "input_pixels_sha256":hashlib.sha256(_numeric_crop(cell,r["box_px"]).tobytes()).hexdigest()}
            for r in reads],"engine":engine}


def interpret_segmented_date(image,record,*,page_sha256,page_number,grid_slot,date_box_px):
    if (not isinstance(record,dict) or record.get("schema_version")!=SCHEMA or
            any(record.get(k)!=v for k,v in [("page_sha256",page_sha256),("page_number",page_number),
                ("grid_slot",grid_slot),("date_box_px",date_box_px)])):
        raise SenateEfdError("Senate segmented date source slot differs")
    cell=image.crop(tuple(date_box_px)).convert("RGB")
    if record.get("date_grayscale_sha256")!=hashlib.sha256(cell.convert("L").tobytes()).hexdigest():
        raise SenateEfdError("Senate segmented date crop differs from source")
    geometry=segment_geometry(cell)
    if record.get("geometry")!=geometry:
        raise SenateEfdError("Senate segmented date geometry differs from source")
    if geometry["status"]!="two_source_slashes":return None
    reads=record.get("numeric_reads")
    if not isinstance(reads,list) or [r.get("role") for r in reads]!=["month","day","year"]:
        raise SenateEfdError("Senate segmented date numeric roles differ")
    centers=[(s["box_px"][0]+s["box_px"][2])/2 for s in geometry["slashes"]]
    intervals=[(0,centers[0]),(centers[0],centers[1]),(centers[1],cell.width)]
    for index,read in enumerate(reads):
        box=read.get("box_px");needed=geometry["numeric_component_boxes_px"][index]
        if (not isinstance(box,list) or len(box)!=4 or any(type(v)is not int for v in box) or
                not (intervals[index][0]<=box[0]<=needed[0]<needed[2]<=box[2]<=intervals[index][1] and
                     0<=box[1]<=needed[1]<needed[3]<=box[3]<=cell.height)):
            raise SenateEfdError("Senate numeric crop clips or crosses source components")
        if read.get("input_pixels_sha256")!=hashlib.sha256(_numeric_crop(cell,box).tobytes()).hexdigest():
            raise SenateEfdError("Senate numeric crop pixels differ from source")
        confidence=read.get("confidence");text=read.get("text")
        pattern=r"[0-9]{1,2}" if index<2 else r"(?:[0-9]{2}|[0-9]{4})\.?"
        if (not isinstance(text,str) or re.fullmatch(pattern,text) is None or
                type(confidence)not in (int,float) or not isfinite(confidence) or not .90<=confidence<=1):
            return None
    return {"rule_version":SCHEMA,"complete_date_text":"/".join(r["text"] for r in reads),
        "confidence":min(r["confidence"] for r in reads),"source_observation":record,
        "basis":"two_source_diagonal_slashes_and_independently_read_complete_numeric_components"}
