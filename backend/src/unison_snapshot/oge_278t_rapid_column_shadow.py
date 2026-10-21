"""Source-bound RapidOCR column reads for confirmed 2024 OGE 278-T grids.

Only raw detections and their physical slot assignment are produced here.
Field interpretation and source qualification belong to separate steps.
"""

from __future__ import annotations

import hashlib
from importlib.metadata import version
from pathlib import Path
from . import oge_278t_raster_slots as _geometry
from .oge_278t_field_shadow import (_validate_geometry_input, _dual_geometry, _known_page_geometry,
    _slot_owns_center, _secondary_slot_geometry)


_INPUT_SCHEMA = "oge-278t-raster-slot-observation/v2"
_MAX_PAGES = 8
_BAND_OVERHANG_LIMIT_POINTS = 3.0
_COLUMN_CROPS = {
    "transaction_date": (3, 4, 0.6, 1.5),
    "notification_amount": (4, 6, 0.6, 1.5),
}


def observe_rapid_column(pdf_path: Path, *, slots_observation: dict,
                         column: str, resolution: int = 300) -> dict:
    """Read one bounded column crop per confirmed page without granting facts."""
    if column not in _COLUMN_CROPS:
        raise ValueError("unsupported 278-T column crop")
    _validate_geometry_input(slots_observation)
    if type(resolution) is not int or not 200 <= resolution <= 400:
        raise ValueError("278-T column OCR resolution is outside bounds")
    pages = slots_observation.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= _MAX_PAGES:
        raise ValueError("278-T column observation needs 1-8 pages")
    numbers = [page["page_number"] for page in pages]
    if len(set(numbers)) != len(numbers):
        raise ValueError("duplicate page in 278-T slot observations")
    source = Path(pdf_path)
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    if source_sha256 != slots_observation.get("source_sha256"):
        raise ValueError("278-T column source SHA-256 differs")

    import numpy as np
    import pdfplumber
    from rapidocr_onnxruntime import RapidOCR

    engine = None
    results = []
    with pdfplumber.open(source) as document:
        if any(type(number) is not int or not 1 <= number <= len(document.pages)
               for number in numbers):
            raise ValueError("278-T page selection is outside PDF bounds")
        for page in pages:
            number = page["page_number"]
            if not _known_page_geometry(page):
                results.append({"page_number": number, "status": "geometry_unknown",
                                "slots": []})
                continue
            edges = page.get("column_edges_pdf_points")
            slots = page.get("slots")
            if not isinstance(edges, list) or len(edges) != 7 or \
                    not all(a < b for a, b in zip(edges, edges[1:])) or \
                    not isinstance(slots, list) or not slots or \
                    len(slots) != (page.get("known_physical_slot_count") if _dual_geometry(page) and page.get("partial_row_geometry") else page.get("physical_slot_count")):
                raise ValueError(f"page {number} has invalid confirmed grid geometry")
            if [slot["slot_index"] for slot in slots] != list(range(1, len(slots) + 1)):
                raise ValueError(f"page {number} has nonsequential slot indices")
            bands = [slot["band_pdf_points"] for slot in slots]
            if any(len(band) != 2 or band[0] >= band[1] for band in bands) or \
                    any(a[1] > b[0] for a, b in zip(bands, bands[1:])):
                raise ValueError(f"page {number} has overlapping slot bands")
            start, end, left_pad, right_pad = _COLUMN_CROPS[column]
            top, bottom = bands[0][0], bands[-1][1] + 2
            from .oge_278t_raster_slots import column_crop_bounds
            if _dual_geometry(page):
                left,top,right,bottom = _geometry.table_crop_bounds(page,slots,start,end,left_pad,right_pad)
            else:
                left, right = column_crop_bounds(page,start,end,top,bottom,left_pad,right_pad)
            if right <= left or bottom > document.pages[number - 1].height:
                raise ValueError(f"page {number} has invalid {column} crop")
            image = document.pages[number - 1].crop(
                (left, top, right, bottom)).to_image(
                    resolution=resolution, antialias=True).original.convert("RGB")
            if engine is None:
                engine = RapidOCR()
            detections, _elapsed = engine(np.asarray(image))
            by_slot: dict[int, list[dict]] = {slot["slot_index"]: [] for slot in slots}
            unassigned = 0
            unassigned_detections = []
            scale = 72.0 / resolution
            for box, raw, confidence in detections or []:
                ymin = top + min(point[1] for point in box) * scale
                ymax = top + max(point[1] for point in box) * scale
                center = (ymin + ymax) / 2
                source_box = [left+min(point[0] for point in box)*scale,ymin,
                              left+max(point[0] for point in box)*scale,ymax]
                owners = [slot for slot in slots if _slot_owns_center(page,slot,source_box,start,end)]
                if len(owners) != 1:
                    unassigned += 1
                    if _dual_geometry(page): unassigned_detections.append({"text":raw,"bbox_pdf_points":source_box,"reason":"outside_source_slot"})
                    continue
                if _dual_geometry(page) and not _geometry.box_in_slot(page,owners[0],source_box,start,end):
                    unassigned += 1
                    unassigned_detections.append({"text":raw,"bbox_pdf_points":source_box,"reason":"crosses_source_slot_or_field"})
                    continue
                band = owners[0]["band_pdf_points"]
                if not _dual_geometry(page) and max(band[0] - ymin, ymax - band[1], 0) > _BAND_OVERHANG_LIMIT_POINTS:
                    unassigned += 1
                    continue
                by_slot[owners[0]["slot_index"]].append({
                    "text": raw, "confidence": round(float(confidence), 3),
                    "bbox_pdf_points": [
                        round(left + min(point[0] for point in box) * scale, 2),
                        round(ymin, 2),
                        round(left + max(point[0] for point in box) * scale, 2),
                        round(ymax, 2)],
                })
            row_results = []
            for slot in slots:
                items = sorted(by_slot[slot["slot_index"]], key=lambda item:
                               (item["bbox_pdf_points"][1],
                                item["bbox_pdf_points"][0]))
                row_results.append({"slot_index": slot["slot_index"],
                                    **_secondary_slot_geometry(page,slot),
                                    "band_pdf_points": slot["band_pdf_points"],
                                    "raw_ocr_text": " ".join(item["text"] for item in items),
                                    "detections": items})
            results.append({"page_number": number, "status": "column_observed",
                            **({"geometry_mode":page["geometry_mode"],"crop_pdf_points":[left,top,right,bottom],
                                "unassigned_detections":unassigned_detections} if _dual_geometry(page) else {}),
                            "slot_count": len(row_results),
                            "detections_outside_slots": unassigned,
                            "slots": row_results})
    result = {"source_sha256": source_sha256,
            "ocr_engine": f"rapidocr-onnxruntime {version('rapidocr-onnxruntime')}",
            "ocr_resolution_dpi": resolution,
            "input_ocr_resolution_dpi": slots_observation.get("ocr_resolution_dpi"),
            "pages": results}
    if any(page.get("column_line_model") is not None or _dual_geometry(page) for page in pages):
        from .oge_278t_raster_slots import column_geometry_sha256
        result["input_column_geometry_sha256"] = {str(page["page_number"]): column_geometry_sha256(page)
                                                   for page in pages}
    return result
