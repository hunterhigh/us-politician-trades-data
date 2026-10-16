"""Read-only amount-column OCR on confirmed 2024 OGE 278-T grids."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .oge_278t_amount_categories import observe_template_amount
from .oge_278t_field_shadow import _amount_shape
from .oge_278t_rapid_column_shadow import observe_rapid_column


SCHEMA = "oge-278t-amount-column-observation/v2"


def observe_amount_column(pdf_path: Path, *, slots_observation: dict,
                          resolution: int = 300) -> dict:
    """Compare raw amount shapes without selecting either OCR read as fact."""
    reading = observe_rapid_column(
        pdf_path, slots_observation=slots_observation,
        column="notification_amount", resolution=resolution)
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
            original = slot.get("column_observation")
            if not isinstance(original, dict) or any(
                    field not in original for field in ("notification", "amount")):
                raise ValueError(f"page {number} lacks original amount observation")
            raw = raw_slot["raw_ocr_text"]
            column_shape = _amount_shape(raw)
            column_template = observe_template_amount(raw)
            original_raw = (original["notification"]["raw_ocr_text"] + " " +
                            original["amount"]["raw_ocr_text"])
            page_shape = _amount_shape(original_raw)
            page_template = observe_template_amount(original_raw)
            first = column_shape["values"] if column_shape["status"] == "ocr_shape_only" else None
            second = page_shape["values"] if page_shape["status"] == "ocr_shape_only" else None
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
            counts["column_" + column_template["status"]] += 1
            counts["page_" + page_template["status"]] += 1
            counts["observed_slots"] += 1
            row_results.append({"slot_index": raw_slot["slot_index"],
                                "band_pdf_points": raw_slot["band_pdf_points"],
                                "column_raw_ocr_text": raw,
                                "column_detections": raw_slot["detections"],
                                "column_shape": column_shape,
                                "column_template_category": column_template,
                                "page_raw_ocr_text": original_raw,
                                "page_shape": page_shape,
                                "page_template_category": page_template,
                                "comparison": comparison,
                                "qualification": "not_assessed"})
        results.append({"page_number": number, "status": "amount_column_observed",
                        "slot_count": len(row_results),
                        "detections_outside_slots": page["detections_outside_slots"],
                        "slots": row_results})
    return {"schema_version": SCHEMA, "source_sha256": reading["source_sha256"],
            "ocr_engine": reading["ocr_engine"],
            "ocr_resolution_dpi": reading["ocr_resolution_dpi"],
            "input_ocr_resolution_dpi": reading["input_ocr_resolution_dpi"],
            "summary": dict(sorted(counts.items())),
            "pages": results, "production_mutation_authorized": False}
