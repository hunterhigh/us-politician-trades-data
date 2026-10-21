"""Bounded native EXHIBIT column evidence from an existing embedded image.

This reads no text and invokes no OCR. Header semantics are checked separately
by the native-word interpreter; seven tracks alone do not establish six roles.
"""
from copy import deepcopy
import hashlib
from pathlib import Path


SCHEMA = "oge-278t-embedded-exhibit-columns/v1"
PARAMETERS = {
    "threshold_dark_below": 128, "x_radius_pdf_points": .6,
    "header_offset_pdf_points": [3, 28], "body_offset_pdf_points": [36, -3],
    "bins_per_region": 5, "minimum_bin_dark_row_fraction": .1,
    "minimum_hit_bins_each_region": 4, "search_margin_pdf_points": 4,
    "maximum_candidate_cluster_width_pdf_points": 4,
}


def _groups(xs):
    result = []
    for x in xs:
        if not result or x > result[-1][-1] + 1:
            result.append([])
        result[-1].append(int(x))
    return result


def detect_column_tracks(pixels, box, transform):
    """Return all longitudinal support, not a count-only column assertion."""
    import numpy as np
    a = np.asarray(pixels)
    p = PARAMETERS
    height, width = a.shape
    x_origin, y_origin = transform["pixel_origin_pdf_points"]
    dx, dy = transform["pixel_step_pdf_points"]
    left, top, right, bottom = box
    rad = round(p["x_radius_pdf_points"] / dx)
    xlo = max(0, int((left-p["search_margin_pdf_points"]-x_origin)/dx))
    xhi = min(width-1, int((right+p["search_margin_pdf_points"]-x_origin)/dx))
    domains = {"header": tuple(top+v for v in p["header_offset_pdf_points"]),
               "body": (top+p["body_offset_pdf_points"][0],bottom+p["body_offset_pdf_points"][1])}
    regions, hits = {}, {}
    if xlo > xhi:
        return {"status": "unknown", "reason": "table_outside_embedded_image", "tracks": []}
    for role, (y0, y1) in domains.items():
        bins = np.linspace(round((y0-y_origin)/dy), round((y1-y_origin)/dy), p["bins_per_region"]+1).astype(int)
        if bins[0] < 0 or bins[-1] > height or np.any(np.diff(bins) <= 0):
            return {"status": "unknown", "reason": "header_or_body_support_outside_image", "tracks": []}
        counts = []
        for x in range(xlo, xhi+1):
            counts.append([int(np.count_nonzero(np.any(
                a[lo:hi, max(0,x-rad):min(width,x+rad+1)] < p["threshold_dark_below"], axis=1)))
                for lo, hi in zip(bins, bins[1:])])
        fractions = np.asarray(counts) / np.diff(bins)
        hits[role] = np.sum(fractions >= p["minimum_bin_dark_row_fraction"], axis=1) >= p["minimum_hit_bins_each_region"]
        regions[role] = {"bin_pixel_ranges": [[int(lo), int(hi)] for lo, hi in zip(bins,bins[1:])],
                         "dark_row_counts_by_scan_x": counts}
    clusters = _groups(np.where(hits["header"] & hits["body"])[0] + xlo)
    tracks = [{"candidate_interval_pdf_points": [x_origin+g[0]*dx, x_origin+g[-1]*dx],
               "candidate_pixel_interval": [g[0], g[-1]]} for g in clusters]
    known = len(tracks) == 7 and all((g[-1]-g[0])*dx <= p["maximum_candidate_cluster_width_pdf_points"] for g in clusters)
    return {"status": "tracks_observed" if known else "unknown",
            "reason": None if known else "missing_or_ambiguous_longitudinal_tracks",
            "scan_x_pixel_range": [xlo,xhi], "regions": regions, "tracks": tracks,
            "header_candidate_count": len(_groups(np.where(hits["header"])[0]+xlo)),
            "body_candidate_count": len(_groups(np.where(hits["body"])[0]+xlo))}


def observe_embedded_exhibit_columns(pdf_path: Path, *, source_sha256: str,
                                      page_number: int, table_bounds: list,
                                      native_page_size: tuple[float,float]) -> dict:
    """Decode the unique source image, retaining its actual PDF transform."""
    import pypdfium2 as pdfium
    import numpy as np
    source = Path(pdf_path)
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_sha256:
        raise ValueError("embedded EXHIBIT image PDF differs from source SHA")
    result = {"schema_version": SCHEMA, "source_sha256": source_sha256,
              "page_number": page_number, "parameters": deepcopy(PARAMETERS),
              "table_bounds": deepcopy(table_bounds), "status": "unknown", "blocks": [],
              "native_page_size_pdf_points":list(native_page_size)}
    with pdfium.PdfDocument(source) as document:
        if type(page_number) is not int or not 1 <= page_number <= len(document):
            raise ValueError("embedded EXHIBIT image page is outside source")
        page = document[page_number-1]
        objects = list(page.get_objects())
        images = [(i,obj) for i,obj in enumerate(objects) if isinstance(obj,pdfium.PdfImage)]
        result["image_object_count"] = len(images)
        if len(images) != 1:
            return {**result,"reason":"unique_embedded_image_not_available"}
        index,image = images[0]
        matrix = image.get_matrix()
        ctm = [getattr(matrix,k) for k in "abcdef"]
        page_box = list(page.get_bbox())
        result.update(image_object_index=index, image_index=0, image_ctm=ctm,
                      image_bounds_pdf=list(image.get_bounds()), page_bbox_pdf=page_box,
                      page_rotation=page.get_rotation(),page_crop_box_pdf=list(page.get_cropbox()),
                      page_media_box_pdf=list(page.get_mediabox()))
        # This finite capability is axis-aligned, unrotated and uncropped. It
        # explicitly refuses other transforms rather than using canvas scaling.
        if (page.get_rotation() != 0 or page_box[:2] != [0.,0.] or
                matrix.b != 0 or matrix.c != 0 or matrix.a <= 0 or matrix.d <= 0):
            return {**result,"reason":"embedded_image_transform_not_supported"}
        if (page_box != [0.,0.,*native_page_size] or
                result["page_crop_box_pdf"] != page_box or result["page_media_box_pdf"] != page_box):
            return {**result,"reason":"native_and_image_displayed_page_frames_differ"}
        try:
            bitmap = image.get_bitmap(render=False)
            try:
                pixels = np.asarray(bitmap.to_pil().convert("L")).copy()
            finally:
                if hasattr(bitmap,"close"):
                    bitmap.close()
        except Exception as exc:
            return {**result,"reason":"embedded_image_decode_failed","decode_failure_type":type(exc).__name__}
        height,width = pixels.shape
        transform = {"pixel_origin_pdf_points": [matrix.e,page_box[3]-matrix.f-matrix.d],
                     "pixel_step_pdf_points": [matrix.a/width,matrix.d/height],
                     "coordinate_frame":"pdfplumber_displayed_pdf_points",
                     "pixel_coordinate_basis":"top_left_pixel_index"}
        result.update(native_image_size=[width,height],decoded_pixels_sha256=hashlib.sha256(pixels.tobytes()).hexdigest(),
                      image_to_pdf=transform,decode_method="PDFium_PdfImage_get_bitmap_render_false")
        result["blocks"] = [{"table_index":i,"table_bounds":deepcopy(box),
                             **detect_column_tracks(pixels,box,transform)} for i,box in enumerate(table_bounds)]
        result["status"] = "observed"
    return result


def validated_structure_blocks(structure: dict, observation: dict) -> dict:
    """Check saved contract; the materializer independently reopens the PDF."""
    if (not isinstance(structure,dict) or structure.get("schema_version") != SCHEMA or
            structure.get("source_sha256") != observation["source_sha256"] or
            structure.get("page_number") != observation["page_number"] or
            structure.get("table_bounds") != observation["table_bounds"] or
            structure.get("parameters") != PARAMETERS):
        raise ValueError("native EXHIBIT column structure contract differs from observation")
    if structure.get("status") == "observed" and (
            structure.get("native_page_size_pdf_points") != [observation["page_width"],observation["page_height"]] or
            structure.get("page_bbox_pdf") != [0.,0.,observation["page_width"],observation["page_height"]] or
            structure.get("page_crop_box_pdf") != structure["page_bbox_pdf"] or
            structure.get("page_media_box_pdf") != structure["page_bbox_pdf"]):
        raise ValueError("native EXHIBIT image and source word coordinate frames differ")
    blocks = structure.get("blocks")
    if not isinstance(blocks,list) or (structure.get("status") == "observed" and len(blocks) != len(observation["table_bounds"])):
        raise ValueError("native EXHIBIT column structure block inventory differs")
    for i,block in enumerate(blocks):
        if block.get("table_index") != i or block.get("table_bounds") != observation["table_bounds"][i]:
            raise ValueError("native EXHIBIT structure table binding differs")
    return {b["table_index"]:b for b in blocks}
