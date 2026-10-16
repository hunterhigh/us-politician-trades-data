"""Read-only second-engine date observations on confirmed 2024 OGE 278-T grids.

The OCR engine reads only the printed date column of an archived PDF.  Exact
calendar shapes and agreement with earlier OCR remain observations, not facts.
"""

from __future__ import annotations

from collections import Counter
import hashlib
from importlib.metadata import version
from pathlib import Path

from .oge_278t_field_shadow import _date_shape


SCHEMA = "oge-278t-date-column-observation/v1"
_INPUT_SCHEMA = "oge-278t-raster-slot-observation/v2"
_MAX_PAGES = 8
_BAND_OVERHANG_LIMIT_POINTS = 3.0


def observe_date_column(pdf_path: Path, *, slots_observation: dict,
                        resolution: int = 300) -> dict:
    """Retain date OCR, its geometry, and any conflict with the first engine."""
    if slots_observation.get("schema_version") != _INPUT_SCHEMA:
        raise ValueError("expected saved 278-T raster slot observations v2")
    if type(resolution) is not int or not 200 <= resolution <= 400:
        raise ValueError("278-T date-column OCR resolution is outside bounds")
    pages = slots_observation.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= _MAX_PAGES:
        raise ValueError("278-T date-column observation needs 1-8 pages")
    numbers = [page["page_number"] for page in pages]
    if len(set(numbers)) != len(numbers):
        raise ValueError("duplicate page in 278-T slot observations")
    source = Path(pdf_path)
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    if source_sha256 != slots_observation.get("source_sha256"):
        raise ValueError("278-T date-column source SHA-256 differs")

    import numpy as np
    import pdfplumber
    from rapidocr_onnxruntime import RapidOCR

    engine = None
    results = []
    counts: Counter[str] = Counter()
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
            left, right = edges[3] + 0.6, edges[4] - 1.5
            top, bottom = bands[0][0], bands[-1][1] + 2
            if right <= left or bottom > document.pages[number - 1].height:
                raise ValueError(f"page {number} has invalid date-column crop")
            image = document.pages[number - 1].crop(
                (left, top, right, bottom)).to_image(
                    resolution=resolution, antialias=True).original.convert("RGB")
            if engine is None:
                engine = RapidOCR()
            detections, _elapsed = engine(np.asarray(image))
            by_slot: dict[int, list[dict]] = {slot["slot_index"]: [] for slot in slots}
            unassigned = 0
            scale = 72.0 / resolution
            for box, raw, confidence in detections or []:
                ymin = top + min(point[1] for point in box) * scale
                ymax = top + max(point[1] for point in box) * scale
                center = (ymin + ymax) / 2
                owners = [slot for slot in slots if
                          slot["band_pdf_points"][0] <= center <
                          slot["band_pdf_points"][1]]
                if len(owners) != 1:
                    unassigned += 1
                    continue
                band = owners[0]["band_pdf_points"]
                if max(band[0] - ymin, ymax - band[1], 0) > _BAND_OVERHANG_LIMIT_POINTS:
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
                index = slot["slot_index"]
                items = sorted(by_slot[index], key=lambda item:
                               (item["bbox_pdf_points"][1],
                                item["bbox_pdf_points"][0]))
                raw = " ".join(item["text"] for item in items)
                column_shape = _date_shape(raw) if len(items) == 1 else {
                    "status": "unknown", "value": None,
                    "reason": "multiple_date_detections" if items else "no_date_detection"}
                original = slot.get("column_observation")
                if not isinstance(original, dict) or "transaction_date" not in original:
                    raise ValueError(f"page {number} lacks original date observation")
                original_raw = original["transaction_date"]["raw_ocr_text"]
                page_shape = _date_shape(original_raw)
                first = column_shape["value"] if column_shape["status"] == "ocr_shape_only" else None
                second = page_shape["value"] if page_shape["status"] == "ocr_shape_only" else None
                if first and second:
                    comparison = ("matching_shapes" if first == second
                                  else "conflicting_shapes")
                elif first:
                    comparison = "column_only_shape"
                elif second:
                    comparison = "page_only_shape"
                else:
                    comparison = "neither_shape"
                counts[comparison] += 1
                counts["observed_slots"] += 1
                row_results.append({"slot_index": index,
                                    "band_pdf_points": slot["band_pdf_points"],
                                    "column_raw_ocr_text": raw,
                                    "column_detections": items,
                                    "column_shape": column_shape,
                                    "page_raw_ocr_text": original_raw,
                                    "page_shape": page_shape,
                                    "comparison": comparison,
                                    "qualification": "not_assessed"})
            results.append({"page_number": number, "status": "date_column_observed",
                            "slot_count": len(row_results),
                            "detections_outside_slots": unassigned,
                            "slots": row_results})
    return {"schema_version": SCHEMA, "source_sha256": source_sha256,
            "ocr_engine": f"rapidocr-onnxruntime {version('rapidocr-onnxruntime')}",
            "ocr_resolution_dpi": resolution,
            "input_ocr_resolution_dpi": slots_observation.get("ocr_resolution_dpi"),
            "summary": dict(sorted(counts.items())),
            "pages": results, "production_mutation_authorized": False}
