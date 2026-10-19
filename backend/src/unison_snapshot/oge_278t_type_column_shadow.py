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
    from .oge_278t_field_shadow import _description_detection_reading, _description_shape
    from .oge_278t_raster_slots import column_crop_bounds, column_geometry_sha256

    if slots_observation.get("schema_version") != _INPUT_SCHEMA:
        raise ValueError("expected saved 278-T raster slot observations v2")
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
            if page.get("column_geometry") != "oge_2024_six_column_grid":
                results.append({"page_number": number, "status": "geometry_unknown",
                                "slots": []})
                counts["geometry_unknown_pages"] += 1
                continue
            edges = page.get("column_edges_pdf_points")
            slots = page.get("slots")
            if not isinstance(edges, list) or len(edges) != 7 or \
                    not all(a < b for a, b in zip(edges, edges[1:])) or \
                    not isinstance(slots, list) or not slots or \
                    len(slots) != page.get("physical_slot_count"):
                raise ValueError(f"page {number} has invalid confirmed grid geometry")
            if [slot["slot_index"] for slot in slots] != list(range(1, len(slots) + 1)):
                raise ValueError(f"page {number} has nonsequential slot indices")
            bands = [slot["band_pdf_points"] for slot in slots]
            if any(len(band) != 2 or band[0] >= band[1] for band in bands) or \
                    any(a[1] > b[0] for a, b in zip(bands, bands[1:])):
                raise ValueError(f"page {number} has overlapping slot bands")
            column_index = 2 if field == "type" else 1
            top, bottom = bands[0][0], bands[-1][1] + 2
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
                if field == "description":
                    box = [round(left + word["x0"], 2), round(top + word["top"], 2),
                           round(left + word["x1"], 2), round(top + word["bottom"], 2)]
                    detection = {"text": word["text"], "bbox_pdf_points": box,
                                 "confidence": round(word["ocr_confidence"], 2)}
                center = top + (word["top"] + word["bottom"]) / 2
                owners = [slot["slot_index"] for slot in slots
                          if slot["band_pdf_points"][0] <= center <
                          slot["band_pdf_points"][1]]
                if field == "description":
                    rounded_owners = [slot["slot_index"] for slot in slots if
                                      slot["band_pdf_points"][0] <= (box[1] + box[3]) / 2 <
                                      slot["band_pdf_points"][1]]
                    if owners != rounded_owners:
                        unassigned += 1
                        unassigned_detections.append({**detection, "reason": "center_rounding_ambiguous",
                                                       "unrounded_center_pdf_points": center})
                        continue
                if len(owners) != 1:
                    unassigned += 1
                    if field == "description":
                        unassigned_detections.append({**detection, "reason": "outside_slot_bands"})
                    continue
                if field == "description":
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
                                if item["bbox_pdf_points"][1] < slot["band_pdf_points"][1] and
                                item["bbox_pdf_points"][3] > slot["band_pdf_points"][0]]
                    row_results.append({"slot_index": index, "band_pdf_points": slot["band_pdf_points"],
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
                                    "band_pdf_points": slot["band_pdf_points"],
                                    "column_raw_ocr_text": raw,
                                    "column_words": words,
                                    "column_shape": value,
                                    "page_raw_ocr_text": original_raw,
                                    "page_shape": original_value,
                                    "comparison": comparison,
                                    "qualification": "not_assessed"})
            results.append({"page_number": number, "status": f"{field}_column_observed",
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
    if any(page.get("column_line_model") is not None for page in pages):
        result["input_column_geometry_sha256"] = {str(page["page_number"]): column_geometry_sha256(page)
                                                   for page in pages}
    return result
