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


def _bounds(pixels,width):
    return [min(v%width for v in pixels),min(v//width for v in pixels),
            max(v%width for v in pixels)+1,max(v//width for v in pixels)+1]


def segment_geometry(image,mode="two_slashes"):
    if mode not in {"two_slashes","disconnected_strokes","prefix_slash"}:
        raise SenateEfdError("Senate date source geometry mode is unsupported")
    image=image.convert("L");width,height=image.size;raw=image.tobytes()
    dark={i for i,value in enumerate(raw) if value<128};groups=[]
    while dark:
        seed=min(dark);dark.remove(seed);queue=deque([seed]);pixels=[seed]
        while queue:
            point=queue.popleft();x,y=point%width,point//width
            for yy in range(max(0,y-1),min(height,y+2)):
                for xx in range(max(0,x-1),min(width,x+2)):
                    neighbor=yy*width+xx
                    if neighbor in dark:
                        dark.remove(neighbor);queue.append(neighbor);pixels.append(neighbor)
        if len(pixels)>=50:groups.append(pixels)
    if mode=="disconnected_strokes" and groups:
        # Union existing ink only. No bridge pixel is drawn. Two vertically
        # disjoint strokes need a shared horizontal footprint and short gap.
        primary=max((_bounds(g,width)[3]-_bounds(g,width)[1]) for g in groups)
        changed=True
        while changed:
            changed=False
            for i in range(len(groups)):
                if changed:break
                for j in range(i+1,len(groups)):
                    x,y,z,b=_bounds(groups[i],width);u,v,w,d=_bounds(groups[j],width)
                    overlap=max(0,min(z,w)-max(x,u))/min(z-x,w-u)
                    gap=max(y,v)-min(b,d)
                    if overlap>=.8 and 0<=gap<=.15*primary:
                        groups[i]+=groups.pop(j);changed=True;break
    components=[]
    for pixels in groups:
        box=_bounds(pixels,width)
        if box[3]-box[1]<18:continue
        by_y={}
        for point in pixels:by_y.setdefault(point//width,[]).append(point%width)
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
    count=1 if mode=="prefix_slash" else 2
    if len(slashes)!=count:return result
    centers=[(v["box_px"][0]+v["box_px"][2])/2 for v in slashes]
    numeric=[[] for _ in range(count+1)]
    for component in components:
        if component["is_slash"]:continue
        box=component["box_px"];center=(box[0]+box[2])/2
        if any(box[0]<=edge<=box[2] for edge in centers):return result
        numeric[sum(center>edge for edge in centers)].append(box)
    if any(not group for group in numeric):return result
    boxes=[[min(b[0] for b in g),min(b[1] for b in g),max(b[2] for b in g),max(b[3] for b in g)] for g in numeric]
    result.update(status="source_prefix_slash" if count==1 else "two_source_slashes",
                  numeric_component_boxes_px=boxes)
    return result


def _numeric_crop(image,box):
    from PIL import ImageOps
    return ImageOps.expand(image.crop(tuple(box)).convert("RGB"),border=8,fill="white")


def saved_segmented_date(image,*,page_sha256,page_number,grid_slot,date_box_px,reads,engine,mode="two_slashes"):
    """Bind existing numeric reads to the source cell; never runs an OCR model."""
    cell=image.crop(tuple(date_box_px)).convert("RGB")
    return {"schema_version":SCHEMA,"page_sha256":page_sha256,"page_number":page_number,
        "grid_slot":grid_slot,"date_box_px":date_box_px,
        "date_grayscale_sha256":hashlib.sha256(cell.convert("L").tobytes()).hexdigest(),
        "geometry":segment_geometry(cell,mode),
        **({"geometry_mode":mode} if mode!="two_slashes" else {}),"numeric_reads":[{**r,
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
    mode=record.get("geometry_mode","two_slashes")
    geometry=segment_geometry(cell,mode)
    if record.get("geometry")!=geometry:
        raise SenateEfdError("Senate segmented date geometry differs from source")
    if geometry["status"] not in {"two_source_slashes","source_prefix_slash"}:return None
    reads=record.get("numeric_reads")
    roles=["month","remainder"] if mode=="prefix_slash" else ["month","day","year"]
    if not isinstance(reads,list) or [r.get("role") for r in reads]!=roles:
        raise SenateEfdError("Senate segmented date numeric roles differ")
    centers=[(s["box_px"][0]+s["box_px"][2])/2 for s in geometry["slashes"]]
    edges=[0]+centers+[cell.width]
    intervals=list(zip(edges,edges[1:]))
    for index,read in enumerate(reads):
        box=read.get("box_px");needed=geometry["numeric_component_boxes_px"][index]
        if (not isinstance(box,list) or len(box)!=4 or any(type(v)is not int for v in box) or
                not (intervals[index][0]<=box[0]<=needed[0]<needed[2]<=box[2]<=intervals[index][1] and
                     0<=box[1]<=needed[1]<needed[3]<=box[3]<=cell.height)):
            raise SenateEfdError("Senate numeric crop clips or crosses source components")
        if read.get("input_pixels_sha256")!=hashlib.sha256(_numeric_crop(cell,box).tobytes()).hexdigest():
            raise SenateEfdError("Senate numeric crop pixels differ from source")
        confidence=read.get("confidence");text=read.get("text")
        if mode=="prefix_slash" and index==1:
            pattern=r"[0-9]{1,2}/(?:[0-9]{2}|[0-9]{4})\.?"
        else:
            pattern=r"[0-9]{1,2}" if index<2 else r"(?:[0-9]{2}|[0-9]{4})\.?"
        if (not isinstance(text,str) or re.fullmatch(pattern,text) is None or
                type(confidence)not in (int,float) or not isfinite(confidence) or not .90<=confidence<=1):
            return None
    return {"rule_version":SCHEMA,"complete_date_text":"/".join(r["text"] for r in reads),
        "confidence":min(r["confidence"] for r in reads),"source_observation":record,
        "basis":("source_first_slash_and_directly_read_explicit_slash_in_complete_remainder"
                 if mode=="prefix_slash" else "two_source_diagonal_slashes_and_independently_read_complete_numeric_components")}
