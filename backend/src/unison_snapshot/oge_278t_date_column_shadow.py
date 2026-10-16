"""Read-only second-engine date observations on confirmed 2024 OGE grids."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .oge_278t_field_shadow import _date_shape
from .oge_278t_rapid_column_shadow import observe_rapid_column


SCHEMA = "oge-278t-date-column-observation/v1"


def observe_date_column(pdf_path: Path, *, slots_observation: dict,
                        resolution: int = 300) -> dict:
    """Retain date OCR, its geometry, and any conflict with the first engine."""
    reading = observe_rapid_column(
        pdf_path, slots_observation=slots_observation,
        column="transaction_date", resolution=resolution)
    originals = {page["page_number"]: page for page in slots_observation["pages"]}
    results = []
    counts: Counter[str] = Counter()
    for page in reading["pages"]:
        number = page["page_number"]
        if page["status"] == "geometry_unknown":
            results.append(page)
            counts["geometry_unknown_pages"] += 1
            continue
        row_results = []
        for raw_slot, slot in zip(page["slots"], originals[number]["slots"]):
            items = raw_slot["detections"]
            raw = raw_slot["raw_ocr_text"]
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
            row_results.append({"slot_index": raw_slot["slot_index"],
                                "band_pdf_points": raw_slot["band_pdf_points"],
                                "column_raw_ocr_text": raw,
                                "column_detections": items,
                                "column_shape": column_shape,
                                "page_raw_ocr_text": original_raw,
                                "page_shape": page_shape,
                                "comparison": comparison,
                                "qualification": "not_assessed"})
        results.append({"page_number": number, "status": "date_column_observed",
                        "slot_count": len(row_results),
                        "detections_outside_slots": page["detections_outside_slots"],
                        "slots": row_results})
    return {"schema_version": SCHEMA, "source_sha256": reading["source_sha256"],
            "ocr_engine": reading["ocr_engine"],
            "ocr_resolution_dpi": reading["ocr_resolution_dpi"],
            "input_ocr_resolution_dpi": reading["input_ocr_resolution_dpi"],
            "summary": dict(sorted(counts.items())),
            "pages": results, "production_mutation_authorized": False}
