"""Read-only type-column OCR on independently located 2024 OGE 278-T grids.

This probes one ruled column as a page block. Exact OCR words are shapes only;
the original PDF and saved row geometry stay authoritative for positioning.
"""

from __future__ import annotations

from collections import Counter
import hashlib
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
    if slots_observation.get("schema_version") != _INPUT_SCHEMA:
        raise ValueError("expected saved 278-T raster slot observations v2")
    if type(resolution) is not int or not 150 <= resolution <= 400:
        raise ValueError("278-T type-column OCR resolution is outside bounds")
    pages = slots_observation.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= _MAX_PAGES:
        raise ValueError("278-T type-column observation needs 1-8 pages")
    numbers = [page["page_number"] for page in pages]
    if len(set(numbers)) != len(numbers):
        raise ValueError("duplicate page in 278-T slot observations")
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
            left, right = edges[2] + 0.6, edges[3] - 1.5
            top, bottom = bands[0][0], bands[-1][1] + 2
            if right <= left or bottom > document.pages[number - 1].height:
                raise ValueError(f"page {number} has invalid type-column crop")
            crop = document.pages[number - 1].crop((left, top, right, bottom))
            image = crop.to_image(resolution=resolution, antialias=True).original
            observed, engine = ocr_rendered_image(
                image, executable=executable, resolution=resolution,
                page_segmentation=6)
            by_slot: dict[int, list[dict]] = {slot["slot_index"]: [] for slot in slots}
            unassigned = 0
            for word in observed.extract_words():
                center = top + (word["top"] + word["bottom"]) / 2
                owners = [slot["slot_index"] for slot in slots
                          if slot["band_pdf_points"][0] <= center <
                          slot["band_pdf_points"][1]]
                if len(owners) != 1:
                    unassigned += 1
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
            results.append({"page_number": number, "status": "type_column_observed",
                            "slot_count": len(row_results),
                            "words_outside_slots": unassigned,
                            "slots": row_results})
    return {"schema_version": SCHEMA, "source_sha256": source_sha256,
            "ocr_engine": engine, "ocr_resolution_dpi": resolution,
            "input_ocr_resolution_dpi": slots_observation.get("ocr_resolution_dpi"),
            "summary": dict(sorted(counts.items())),
            "pages": results, "production_mutation_authorized": False}
