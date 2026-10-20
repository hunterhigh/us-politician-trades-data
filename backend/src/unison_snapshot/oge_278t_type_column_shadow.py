"""Read-only type-column OCR on independently located 2024 OGE 278-T grids.

This probes one ruled column as a page block. Exact OCR words are shapes only;
the original PDF and saved row geometry stay authoritative for positioning.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

from .ocr_geometry import ocr_rendered_image


SCHEMA = "oge-278t-type-column-observation/v1"
_INPUT_SCHEMA = "oge-278t-raster-slot-observation/v2"
_TYPE = {"purchase", "sale", "exchange"}
_MAX_PAGES = 8


def observe_type_column(pdf_path: Path, *, slots_observation: dict,
                        executable: str | None = None,
                        resolution: int = 400) -> dict:
    """Probe the ruled Type column without promoting or correcting any row."""
    return _observe_word_column(pdf_path, slots_observation=slots_observation,
                                executable=executable, resolution=resolution, field="type")


def observe_description_column(pdf_path: Path, *, slots_observation: dict,
                               executable: str | None = None,
                               resolution: int = 300, page_numbers: list[int] | None = None) -> dict:
    """Read the original asset column with the same bounded word OCR backend."""
    return _observe_word_column(pdf_path, slots_observation=slots_observation,
                                executable=executable, resolution=resolution, field="description",
                                page_numbers=page_numbers)


def _observe_word_column(pdf_path: Path, *, slots_observation: dict,
                         executable: str | None, resolution: int, field: str,
                         page_numbers: list[int] | None = None) -> dict:
    from .oge_278t_field_shadow import (_description_detection_reading, _description_shape,
        _validate_geometry_input, _dual_geometry, _known_page_geometry, _slot_owns_center, _slot_overlap, _secondary_slot_geometry)
    from . import oge_278t_raster_slots as _geometry
    from .oge_278t_raster_slots import column_crop_bounds, column_geometry_sha256

    _validate_geometry_input(slots_observation)
    if type(resolution) is not int or not (150 if field == "type" else 200) <= resolution <= 400:
        raise ValueError("278-T type-column OCR resolution is outside bounds")
    pages = slots_observation.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= _MAX_PAGES:
        raise ValueError("278-T type-column observation needs 1-8 pages")
    numbers = [page["page_number"] for page in pages]
    if len(set(numbers)) != len(numbers):
        raise ValueError("duplicate page in 278-T slot observations")
    if page_numbers is not None:
        if (not isinstance(page_numbers, list) or not page_numbers or
                any(type(number) is not int for number in page_numbers) or
                len(set(page_numbers)) != len(page_numbers) or not set(page_numbers) <= set(numbers)):
            raise ValueError("description page selection differs from bound primary pages")
        pages = [page for page in pages if page["page_number"] in page_numbers]
        numbers = [page["page_number"] for page in pages]
    source = Path(pdf_path)
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    if source_sha256 != slots_observation.get("source_sha256"):
        raise ValueError("278-T type-column source SHA-256 differs")

    import pdfplumber
    results = []
    counts: Counter[str] = Counter()
    engine = None
    with pdfplumber.open(source) as document:
        if any(type(number) is not int or not 1 <= number <= len(document.pages)
               for number in numbers):
            raise ValueError("278-T page selection is outside PDF bounds")
        for page in pages:
            number = page["page_number"]
            if not _known_page_geometry(page):
                results.append({"page_number": number, "status": "geometry_unknown",
                                "slots": []})
                counts["geometry_unknown_pages"] += 1
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
            column_index = 2 if field == "type" else 1
            top, bottom = bands[0][0], bands[-1][1] + 2
            if _dual_geometry(page):
                left,top,right,bottom = _geometry.table_crop_bounds(page,slots,column_index,column_index+1)
            else:
                left, right = column_crop_bounds(page, column_index, column_index+1, top, bottom)
            if right <= left or bottom > document.pages[number - 1].height:
                raise ValueError(f"page {number} has invalid type-column crop")
            crop = document.pages[number - 1].crop((left, top, right, bottom))
            image = crop.to_image(resolution=resolution, antialias=True).original
            observed, engine = ocr_rendered_image(
                image, executable=executable, resolution=resolution,
                page_segmentation=6)
            by_slot: dict[int, list[dict]] = {slot["slot_index"]: [] for slot in slots}
            unassigned = 0
            unassigned_detections = []
            for word in observed.extract_words():
                if field == "description" or _dual_geometry(page):
                    box = [round(left + word["x0"], 2), round(top + word["top"], 2),
                           round(left + word["x1"], 2), round(top + word["bottom"], 2)]
                    detection = {"text": word["text"], "bbox_pdf_points": box,
                                 "confidence": round(word["ocr_confidence"], 2)}
                center = top + (word["top"] + word["bottom"]) / 2
                raw_box=([left+word["x0"],top+word["top"],left+word["x1"],top+word["bottom"]]
                         if _dual_geometry(page) else None)
                owners = ([slot["slot_index"] for slot in slots if
                           _slot_owns_center(page,slot,raw_box,column_index,column_index+1)] if _dual_geometry(page) else
                          [slot["slot_index"] for slot in slots
                           if slot["band_pdf_points"][0] <= center < slot["band_pdf_points"][1]])
                if field == "description" or _dual_geometry(page):
                    rounded_owners = ([slot["slot_index"] for slot in slots if
                                       _slot_owns_center(page,slot,box,column_index,column_index+1)] if _dual_geometry(page) else
                                      [slot["slot_index"] for slot in slots if
                                       slot["band_pdf_points"][0] <= (box[1]+box[3])/2 < slot["band_pdf_points"][1]])
                    if owners != rounded_owners:
                        unassigned += 1
                        unassigned_detections.append({**detection, "reason": "center_rounding_ambiguous",
                                                       "unrounded_center_pdf_points": [(raw_box[0]+raw_box[2])/2,center] if _dual_geometry(page) else center})
                        continue
                if len(owners) != 1:
                    unassigned += 1
                    if field == "description" or _dual_geometry(page):
                        unassigned_detections.append({**detection, "reason": "outside_source_slot" if _dual_geometry(page) else "outside_slot_bands"})
                    continue
                if _dual_geometry(page) and not _geometry.box_in_slot(page,slots[owners[0]-1],box,column_index,column_index+1):
                    unassigned += 1
                    unassigned_detections.append({**detection,"reason":"crosses_source_slot_or_field"})
                    continue
                if field == "description" and not _dual_geometry(page):
                    band = slots[owners[0] - 1]["band_pdf_points"]
                    if max(band[0] - box[1], box[3] - band[1], 0) > 3.0:
                        unassigned += 1
                        unassigned_detections.append({**detection, "reason": "vertical_overhang"})
                        continue
                by_slot[owners[0]].append({
                    "text": word["text"],
                    "bbox_pdf_points": [round(left + word["x0"], 2),
                                        round(top + word["top"], 2),
                                        round(left + word["x1"], 2),
                                        round(top + word["bottom"], 2)],
                    "confidence": round(word["ocr_confidence"], 2),
                })
            row_results = []
            for slot in slots:
                index = slot["slot_index"]
                if field == "description":
                    words = by_slot[index]
                    reading = _description_detection_reading(words)
                    shape = _description_shape(reading)
                    original = slot.get("column_observation", {}).get("description")
                    if not isinstance(original, dict):
                        raise ValueError(f"page {number} lacks original description observation")
                    overlaps = [i for i, item in enumerate(unassigned_detections)
                                if _slot_overlap(page,slot,item["bbox_pdf_points"],column_index,column_index+1)]
                    row_results.append({"slot_index": index, "band_pdf_points": slot["band_pdf_points"],
                        **_secondary_slot_geometry(page,slot),
                        "column_raw_ocr_text": " ".join(word["text"] for word in words),
                        "column_detections": words, "column_reading": reading, "column_shape": shape,
                        "page_raw_ocr_text": original["raw_ocr_text"],
                        "unassigned_detection_indices": overlaps, "qualification": "not_assessed"})
                    counts["observed_slots"] += 1
                    counts["description_observed" if shape["status"] == "ocr_text_only" else "description_unknown"] += 1
                    continue
                words = sorted(by_slot[index], key=lambda item:
                               (item["bbox_pdf_points"][1],
                                item["bbox_pdf_points"][0]))
                raw = " ".join(word["text"] for word in words)
                normalized = raw.strip().casefold()
                value = normalized if len(words) == 1 and normalized in _TYPE else None
                original = slot.get("column_observation")
                if not isinstance(original, dict) or "type" not in original:
                    raise ValueError(f"page {number} lacks original type observation")
                original_raw = original["type"]["raw_ocr_text"]
                original_shape = original_raw.strip().casefold()
                original_value = original_shape if original_shape in _TYPE else None
                if value and original_value:
                    comparison = ("matching_shapes" if value == original_value
                                  else "conflicting_shapes")
                elif value:
                    comparison = "column_only_shape"
                elif original_value:
                    comparison = "page_only_shape"
                else:
                    comparison = "neither_shape"
                counts[comparison] += 1
                counts["observed_slots"] += 1
                row_results.append({"slot_index": index,
                                    **_secondary_slot_geometry(page,slot),
                                    "band_pdf_points": slot["band_pdf_points"],
                                    "column_raw_ocr_text": raw,
                                    "column_words": words,
                                    "column_shape": value,
                                    "page_raw_ocr_text": original_raw,
                                    "page_shape": original_value,
                                    "comparison": comparison,
                                    "qualification": "not_assessed"})
            results.append({"page_number": number, "status": f"{field}_column_observed",
                            **({"geometry_mode":page["geometry_mode"],"crop_pdf_points":[left,top,right,bottom],
                                "unassigned_detections":unassigned_detections} if _dual_geometry(page) else {}),
                            "slot_count": len(row_results),
                            "words_outside_slots": unassigned,
                            "slots": row_results})
            if field == "description":
                results[-1]["unassigned_detections"] = unassigned_detections
    result = {"schema_version": SCHEMA if field == "type" else "oge-278t-description-column-observation/v1",
            "source_sha256": source_sha256,
            "ocr_engine": engine, "ocr_resolution_dpi": resolution,
            "input_ocr_resolution_dpi": slots_observation.get("ocr_resolution_dpi"),
            "summary": dict(sorted(counts.items())),
            "pages": results, "production_mutation_authorized": False}
    if field == "description":
        result["input_observation_canonical_sha256"] = hashlib.sha256(json.dumps(
            slots_observation, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode()).hexdigest()
        result["input_page_canonical_sha256"] = {str(page["page_number"]): hashlib.sha256(json.dumps(
            page, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            for page in pages}
    if any(page.get("column_line_model") is not None or _dual_geometry(page) for page in pages):
        result["input_column_geometry_sha256"] = {str(page["page_number"]): column_geometry_sha256(page)
                                                   for page in pages}
    return result


def observe_type_cells(pdf_path: Path, *, slots_observation: dict,
                       selected_slots: dict[int, list[int]], resolution: int = 300,
                       crop_directory: Path | None = None) -> dict:
    """Read at most nine explicit Type cells once, using existing RapidOCR.

    No grid removal, character replacement, geometry expansion or fallback OCR
    is performed. Source pixels are cropped strictly inside the witnessed field.
    The legacy column schema retains empty unselected slots; its new receipt is
    consumed only after the original observation and model are reproduced.
    """
    from math import ceil, floor, isfinite
    from importlib.metadata import version
    import numpy as np
    import pdfplumber
    from .oge_278t_raster_slots import column_edges_at_y, column_geometry_sha256
    from .oge_278t_field_shadow import (_validate_geometry_input, _dual_geometry, _known_page_geometry,
        _type_cell_pdf_bounds, _secondary_slot_geometry)
    from . import oge_278t_raster_slots as _geometry
    _validate_geometry_input(slots_observation)

    if (
            type(resolution) is not int or not 200 <= resolution <= 400 or
            not isinstance(selected_slots, dict) or not selected_slots or
            any(type(number) is not int or not isinstance(indices, list) or not indices or
                any(type(i) is not int or i < 1 for i in indices) or len(set(indices)) != len(indices)
                for number, indices in selected_slots.items()) or
            not 1 <= sum(map(len, selected_slots.values())) <= 9):
        raise ValueError("Type cell read requires one to nine explicit source slots")
    pages = slots_observation.get("pages")
    if (not isinstance(pages, list) or len({p["page_number"] for p in pages}) != len(pages) or
            not set(selected_slots) <= {p["page_number"] for p in pages}):
        raise ValueError("Type cell selection differs from saved source pages")
    source = Path(pdf_path)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    if sha != slots_observation.get("source_sha256"):
        raise ValueError("Type cell source SHA-256 differs")
    result_pages, engine, read_count = [], None, 0
    with pdfplumber.open(source) as document:
        for page in pages:
            number = page["page_number"]
            if number not in selected_slots:
                continue
            slots = page.get("slots", [])
            if (not _known_page_geometry(page) or
                    page.get("render_coordinate_frame") != "displayed_mediabox" or
                    not 1 <= number <= len(document.pages) or
                    len(slots) != (page.get("known_physical_slot_count") if _dual_geometry(page) and page.get("partial_row_geometry") else page.get("physical_slot_count")) or
                    [s.get("slot_index") for s in slots] != list(range(1, len(slots)+1)) or
                    not set(selected_slots[number]) <= set(range(1, len(slots)+1))):
                raise ValueError("Type cell read lacks confirmed source roles/slots")
            pdf_page = document.pages[number-1]
            if tuple(pdf_page.bbox) != (0, 0, pdf_page.width, pdf_page.height):
                raise ValueError("Type cell page uses unsupported coordinate frame")
            image = pdf_page.to_image(resolution=resolution, force_mediabox=True,
                                      antialias=True).original.convert("RGB")
            sx, sy = image.width/pdf_page.width, image.height/pdf_page.height
            output = []
            for slot in slots:
                index, band = slot["slot_index"], slot["band_pdf_points"]
                raw, words, receipt = "", [], None
                if index in selected_slots[number]:
                    if slot.get("column_model_outside_word_indices"):
                        raise ValueError("Type cell primary words exceed witnessed model domain")
                    left,top,right,bottom = _type_cell_pdf_bounds(page,slot)
                    pixels = [ceil(left*sx), ceil(top*sy), floor(right*sx), floor(bottom*sy)]
                    x0,y0,x1,y1 = pixels
                    if not (0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height):
                        raise ValueError("Type cell crop is outside source image")
                    crop = image.crop(tuple(pixels))
                    pixel_sha = hashlib.sha256(crop.tobytes()).hexdigest()
                    if crop_directory is not None:
                        directory = Path(crop_directory)
                        directory.mkdir(parents=True, exist_ok=True)
                        crop.save(directory/f"{sha}-p{number}-s{index}-{pixel_sha}.png")
                    receipt = {"schema_version": "oge-278t-type-cell-crop/v1", "source_sha256": sha,
                               **({"crop_geometry_mode":page["geometry_mode"],
                                   "source_quad_pdf_points":_geometry.slot_quad(page,slot,2,3)} if _dual_geometry(page) else {}),
                               "page_number": number, "slot_index": index, "field_role": "type",
                               "render_coordinate_frame": "displayed_mediabox",
                               "source_page_size_pdf_points": [pdf_page.width, pdf_page.height],
                               "source_render_pixel_size": list(image.size), "crop_pixel_bounds": pixels,
                               "crop_pdf_points": [x0/sx, y0/sy, x1/sx, y1/sy],
                               "pixel_to_pdf_scale": [1/sx, 1/sy],
                               "raw_crop_pixel_sha256": pixel_sha, "derived_crop_pixel_sha256": pixel_sha,
                               "processing": "unaltered_source_pixels_no_grid_removal",
                               "ocr_resolution_dpi": resolution}
                    if engine is None:
                        from rapidocr_onnxruntime import RapidOCR
                        engine = RapidOCR()
                    detections, _elapsed = engine(np.asarray(crop))
                    read_count += 1
                    for box, text, confidence in detections or []:
                        values = [v for point in box for v in point]
                        if (not isinstance(text, str) or
                                any(type(v) not in (int,float) or not isfinite(v) for v in values)):
                            raise ValueError("Type cell OCR returned invalid geometry")
                        words.append({"text": text, "confidence": round(float(confidence), 3),
                            "bbox_pdf_points": [round((x0+min(p[0] for p in box))/sx, 2),
                                                round((y0+min(p[1] for p in box))/sy, 2),
                                                round((x0+max(p[0] for p in box))/sx, 2),
                                                round((y0+max(p[1] for p in box))/sy, 2)]})
                    words.sort(key=lambda w: (w["bbox_pdf_points"][1], w["bbox_pdf_points"][0]))
                    raw = " ".join(w["text"] for w in words)
                normalized = raw.strip().casefold()
                output.append({"slot_index": index, "band_pdf_points": band,
                    **_secondary_slot_geometry(page,slot),
                    "column_raw_ocr_text": raw, "column_words": words,
                    "column_shape": normalized if len(words) == 1 and normalized in _TYPE else None,
                    "page_raw_ocr_text": slot["column_observation"]["type"]["raw_ocr_text"],
                    **({"cell_crop": receipt} if receipt else {}), "qualification": "not_assessed"})
            result_pages.append({"page_number": number, "status": "type_column_observed",
                                 **({"geometry_mode":page["geometry_mode"]} if _dual_geometry(page) else {}),
                                 "slot_count": len(output), "slots": output})
    return {"schema_version": SCHEMA, "source_sha256": sha,
        "read_mode": "bound_single_type_cells/v1",
        "selected_slots": {str(n): sorted(indices) for n,indices in selected_slots.items()},
        "ocr_engine": f"rapidocr-onnxruntime {version('rapidocr-onnxruntime')} single-type-cell",
        "ocr_resolution_dpi": resolution, "input_ocr_resolution_dpi": slots_observation.get("ocr_resolution_dpi"),
        "input_observation_canonical_sha256": hashlib.sha256(json.dumps(slots_observation,
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
        "input_page_canonical_sha256": {str(p["page_number"]): hashlib.sha256(json.dumps(p,
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            for p in pages if p["page_number"] in selected_slots},
        "input_column_geometry_sha256": {str(p["page_number"]): column_geometry_sha256(p)
            for p in pages if p["page_number"] in selected_slots},
        "summary": {"observed_slots": sum(p["slot_count"] for p in result_pages),
                    "selected_cell_count": read_count, "ocr_invocation_count": read_count},
        "pages": result_pages, "production_mutation_authorized": False}
