"""Conservative same-page binary glyph evidence for conflicting paper OCR.

This does not recognize securities or edit text.  Agreed source words supply
letter exemplars; only uniquely segmented, isolated letters may corroborate
one existing reading.  Every decisive exemplar pixel must agree, without a
noise allowance.  Failed evidence leaves the existing OCR conflict intact.
"""
from __future__ import annotations
import hashlib
import io
import re
from collections import Counter

MODE = "source_binary_glyph_exemplars/v1"


def _normal(text):
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def _components(mask):
    import numpy as np
    from collections import deque
    labels=np.zeros(mask.shape,dtype="int32")
    stats=[]
    for y,x in zip(*np.nonzero(mask)):
        if labels[y,x]:continue
        label=len(stats)+1;labels[y,x]=label;queue=deque([(int(x),int(y))]);points=[]
        while queue:
            px,py=queue.popleft();points.append((px,py))
            for ny in range(max(0,py-1),min(mask.shape[0],py+2)):
                for nx in range(max(0,px-1),min(mask.shape[1],px+2)):
                    if mask[ny,nx] and not labels[ny,nx]:
                        labels[ny,nx]=label;queue.append((nx,ny))
        xs,ys=zip(*points);stats.append((min(xs),min(ys),max(xs)-min(xs)+1,max(ys)-min(ys)+1,len(points)))
    return labels,stats


def _words(mask, first, second):
    import numpy as np
    labels, stats = _components(mask)
    height, width = mask.shape
    inner = []
    for label, (x, y, w, h, area) in enumerate(stats, 1):
        if x == 0 or y == 0 or x+w == width or y+h == height:
            continue  # A touching component cannot become an exemplar.
        inner.append((int(x), int(y), int(w), int(h), label))
    if not inner:
        return []
    modal = Counter(v[3] for v in inner).most_common(1)[0][0]
    components = sorted(v for v in inner if v[3] >= max(2, modal/2))
    if (not components or min(v[1]+v[3] for v in components) -
            max(v[1] for v in components) < modal/2):
        return []  # No independently shared single text-line band.
    one, two = first.split(), second.split()
    if len(one) != len(two) or len(one) < 2 or len(components) <= len(one):
        return []
    gaps = [components[i+1][0]-components[i][0]-components[i][2]
            for i in range(len(components)-1)]
    ranked = sorted(range(len(gaps)), key=lambda i:gaps[i], reverse=True)
    cut = set(ranked[:len(one)-1])
    if min(gaps[i] for i in cut) <= max(gaps[i] for i in ranked[len(one)-1:]):
        return []
    groups, group = [], []
    for index, component in enumerate(components):
        group.append(component)
        if index in cut or index == len(components)-1:
            groups.append(group)
            group = []
    output = []
    for index, (raw, alternate, group) in enumerate(zip(one, two, groups)):
        x0 = min(v[0] for v in group)
        x1 = max(v[0]+v[2] for v in group)
        y0 = min(v[1] for v in group)
        y1 = max(v[1]+v[3] for v in group)
        # Small or touching components inside a word are not silently dropped.
        selected = {v[4] for v in group}
        intersecting = {int(v) for v in np.unique(labels[y0:y1,x0:x1]) if v}
        alignable = (len(group) == len(raw) == len(alternate) and
                     selected == intersecting and all(value.isascii() for value in (raw, alternate)))
        glyphs = []
        if alignable:
            for character, other, (x,y,w,h,label) in zip(raw, alternate, group):
                glyphs.append({"first":character,"second":other,
                    "bbox":[x,y,w,h],"mask":labels[y:y+h,x:x+w] == label})
        output.append({"word_index":index,"raw":raw,"alternate":alternate,
                       "glyphs":glyphs,"alignable":alignable})
    return output


def _canvas(mask, dx=0, dy=0, size=96):
    import numpy as np
    h,w=mask.shape
    if h > 72 or w > 72:
        raise ValueError("glyph exceeds bounded source comparison")
    result=np.zeros((size,size),bool)
    result[8+dy:8+dy+h,8+dx:8+dx+w]=mask
    return result


def _aligned(target, reference):
    import numpy as np
    u=_canvas(target)
    best=None
    ties=0
    for dy in range(-2,3):
        for dx in range(-2,3):
            v=_canvas(reference,dx,dy)
            union=int((u|v).sum())
            distance=int((u^v).sum())/union if union else 1.
            if best is None or distance < best[0]:
                best=(distance,v,[dx,dy]);ties=1
            elif distance == best[0]:
                ties+=1
    return (*best,ties)


def _decide(target, first, other, exemplars):
    import numpy as np
    winners=exemplars.get(first,[])
    losers=exemplars.get(other,[])
    if len(winners)<2 or not losers:
        return None
    if any(not 0 < value < 73 for mask in [target]+[v["mask"] for v in winners+losers]
           for value in mask.shape):
        return None
    # No interpolation or font-size normalization. Different source scales do
    # not justify a letter decision.
    if any(v["mask"].shape[0] != target.shape[0] for v in winners+losers):
        return None
    aligned_w=[_aligned(target,v["mask"]) for v in winners]
    aligned_l=[_aligned(target,v["mask"]) for v in losers]
    if any(v[3] != 1 for v in aligned_w+aligned_l):
        return None
    between=min(_aligned(a["mask"],b["mask"])[0] for a in winners for b in losers)
    if max(v[0] for v in aligned_w) >= min(v[0] for v in aligned_l) or max(v[0] for v in aligned_w) >= between:
        return None
    w_black=np.logical_and.reduce([v[1] for v in aligned_w])
    w_white=np.logical_and.reduce([~v[1] for v in aligned_w])
    l_black=np.logical_and.reduce([v[1] for v in aligned_l])
    l_white=np.logical_and.reduce([~v[1] for v in aligned_l])
    absent=w_white & l_black
    present=w_black & l_white
    u=_canvas(target)
    if not absent.any() or not present.any() or (u&absent).any() or ((~u)&present).any():
        return None
    return {"first_character":first,"rejected_character":other,
        "source_white_discriminator_pixels":int(absent.sum()),
        "source_black_discriminator_pixels":int(present.sum()),
        "discriminator_mismatch_count":0,
        "reference_locators":[v["locator"] for v in winners+losers],
        "reference_distances":[v[0] for v in aligned_w+aligned_l],
        "comparison":"translation_only_no_resampling"}


def corroborate_asset_readings(pdf_bytes, observation, field_reread, rendered_secondary):
    """Recompute optional evidence; never accept a caller's claimed glyph label."""
    import numpy as np
    import pdfplumber
    import pypdfium2 as pdfium
    from .house_ptr_paper_qualification import _asset_reading_is_bound
    if (field_reread is None or observation.get("render_dpi") != 200 or
            not pdf_bytes.startswith(b"%PDF-") or not any(
                v.get("field")=="asset" and _normal(str(v.get("first_ocr_raw",""))) !=
                _normal(str(v.get("second_ocr_raw",""))) for v in field_reread.get("rereads",[]))):
        return {}
    if hashlib.sha256(pdf_bytes).hexdigest() != observation.get("source_sha256"):
        raise ValueError("House source glyph PDF hash mismatch")
    readings={(v.get("page"),v.get("physical_band_index")):v
              for v in field_reread.get("rereads",[]) if v.get("field")=="asset"}
    results={}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf, pdfium.PdfDocument(pdf_bytes) as raster:
        for page_obs in observation["pages"]:
            page_number=page_obs["page"]
            selected=[]
            for row in page_obs.get("rows",[]):
                cell=(row.get("cells") or {}).get("asset")
                read=readings.get((page_number,row.get("physical_band_index")))
                if cell and read and _asset_reading_is_bound(pdf_bytes,read,cell,page_number,rendered_secondary):
                    selected.append((row,cell,read))
            if not selected:
                continue
            page=pdf.pages[page_number-1]
            # This bounded mechanism uses original binary scanned pixels, not
            # a higher-DPI interpolation presented as new source information.
            images=page.images
            if page.chars or len(images)!=1:
                continue
            im=images[0]
            if (im.get("bits")!=1 or im["x0"]!=0 or im["top"]!=0 or
                    abs(im["x1"]-page.width)>.001 or abs(im["bottom"]-page.height)>.001):
                continue
            objects=[v for v in raster[page_number-1].get_objects() if isinstance(v,pdfium.PdfImage)]
            if len(objects)!=1:
                continue
            native=objects[0].get_bitmap().to_pil().convert("L")
            render=page.to_image(resolution=200).original
            a=np.asarray(native)
            if native.size != render.size or not np.array_equal(a,np.asarray(render.convert("L"))) or not np.isin(a,[0,255]).all():
                continue
            words=[]
            for row,cell,read in selected:
                box=cell["bbox_pixels"]
                crop=render.crop(tuple(box))
                stream=io.BytesIO();crop.save(stream,format="PNG")
                if hashlib.sha256(stream.getvalue()).hexdigest()!=cell["crop_sha256"]:
                    raise ValueError("House source glyph crop differs from source")
                for word in _words(np.asarray(crop.convert("L"))<100,read["first_ocr_raw"],read["second_ocr_raw"]):
                    word.update({"band":row["physical_band_index"],"crop_sha256":cell["crop_sha256"]})
                    words.append(word)
            exemplars={}
            for word in words:
                if not word["alignable"] or word["raw"] != word["alternate"]:
                    continue
                for index,glyph in enumerate(word["glyphs"]):
                    if glyph["first"].isalpha():
                        exemplars.setdefault(glyph["first"],[]).append({"mask":glyph["mask"],
                            "locator":{"page":page_number,"band":word["band"],"word_index":word["word_index"],
                            "character_index":index,"bbox_in_cell":glyph["bbox"],"crop_sha256":word["crop_sha256"]}})
            for row,cell,read in selected:
                first,second=read["first_ocr_raw"],read["second_ocr_raw"]
                if _normal(first)==_normal(second):
                    continue
                differences=[];valid=True
                for word in [v for v in words if v["band"]==row["physical_band_index"]]:
                    if _normal(word["raw"])==_normal(word["alternate"]):
                        continue
                    if not word["alignable"]:
                        valid=False;break
                    for index,glyph in enumerate(word["glyphs"]):
                        if glyph["first"]==glyph["second"]:
                            continue
                        decision=_decide(glyph["mask"],glyph["first"],glyph["second"],exemplars)
                        if decision is None:
                            valid=False;break
                        differences.append({"word_index":word["word_index"],"character_index":index,
                            "bbox_in_cell":glyph["bbox"],**decision})
                    if not valid:break
                if valid and differences:
                    results[(page_number,row["physical_band_index"])]= {"rule":MODE,
                        "source_sha256":observation["source_sha256"],"page":page_number,
                        "physical_band_index":row["physical_band_index"],"native_image_size":list(native.size),
                        "native_image_index":0,"native_image_bits":1,
                        "native_image_bbox_points":[im["x0"],im["top"],im["x1"],im["bottom"]],
                        "original_ocr_readings_retained":True,
                        "native_pixels_sha256":hashlib.sha256(a.tobytes()).hexdigest(),
                        "first_raw":first,"second_raw":second,"source_crop_sha256":cell["crop_sha256"],
                        "second_crop_sha256":read["second_crop_sha256"],"decisions":differences,
                        "accepted_existing_reading":"first","other_reading_disposition":"source_glyph_conflict"}
    return results
