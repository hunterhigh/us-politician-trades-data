"""Retain the OCR producer's line partition for asset description ordering.

Line labels are segmentation evidence, not verified text or financial facts.
Native PDF objects never acquire OCR lines through this contract.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from math import isfinite
from statistics import median


SCHEMA = "oge-ocr-page-line-source/v1"
MODE = "source_ocr_lines/v1"
_LABELS = ("block_num", "par_num", "line_num")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _line(word: dict) -> tuple:
    if any(type(word.get(key)) is not int or word[key] < 1 for key in _LABELS):
        raise ValueError("OGE OCR line source has invalid line labels")
    return tuple(word[key] for key in _LABELS)


def prepare_line_source(words: list[dict], *, source_sha256: str, page_number: int) -> dict | None:
    """Retain existing complete OCR labels; legacy/native inputs keep their path."""
    if not words or any("source_object_index" in word for word in words):
        return None
    # Some old checkpoints only label body words. Do not fabricate a complete
    # producer partition for them or turn this additive evidence into a gate.
    if not all(all(key in word for key in _LABELS) for word in words):
        return None
    for word in words:
        _line(word)
        if (not isinstance(word.get("text"), str) or not word["text"] or
                any(type(word.get(key)) not in (int, float) or not isfinite(word[key])
                    for key in ("x0", "top", "x1", "bottom", "ocr_confidence")) or
                word["x0"] >= word["x1"] or word["top"] >= word["bottom"]):
            raise ValueError("OGE OCR line source has invalid word geometry")
    return {"schema_version": SCHEMA, "source_sha256": source_sha256,
            "page_number": page_number, "words": deepcopy(words), "words_sha256": _digest(words)}


def _belongs(page: dict, slot: dict, word: dict) -> bool:
    x, y = (word["x0"] + word["x1"]) / 2, (word["top"] + word["bottom"]) / 2
    if page.get("geometry_mode") == "source_pdf_dual_axis/v1":
        from .oge_278t_raster_slots import point_in_slot
        return point_in_slot(page, slot, x, y)
    top, bottom = slot["band_pdf_points"]
    return top <= y < bottom


def validate_line_mode(observation: dict) -> None:
    mode = observation.get("description_line_mode")
    if mode not in (None, MODE):
        raise ValueError("unsupported OGE description OCR line mode")
    if mode is None and any("ocr_line_source" in page for page in observation.get("pages", [])):
        raise ValueError("OGE OCR line source requires its explicit description line mode")


def validate_line_source(page: dict, *, source_sha256: str) -> None:
    """Check exact saved-word references before any description uses line labels."""
    source = page.get("ocr_line_source")
    has_refs = any("ocr_line_word_index" in word for slot in page.get("slots", [])
                   for word in slot.get("words", []))
    if source is None:
        if has_refs:
            raise ValueError("OGE OCR line word references lack their page source")
        return
    if (not isinstance(source, dict) or source.get("schema_version") != SCHEMA or
            source.get("source_sha256") != source_sha256 or
            type(source.get("page_number")) is not int or source.get("page_number") != page["page_number"] or
            not isinstance(source.get("words"), list)):
        raise ValueError("OGE OCR line source binding differs")
    expected = prepare_line_source(source["words"], source_sha256=source_sha256,
                                   page_number=page["page_number"])
    if expected is None or expected != source:
        raise ValueError("OGE OCR line source words or digest differ")
    frame = page.get("render_frame")
    if frame is not None and frame.get("words_sha256") != source["words_sha256"]:
        raise ValueError("OGE OCR line source differs from its saved render words")
    words = source["words"]
    dual = page.get("geometry_mode") == "source_pdf_dual_axis/v1"
    for slot in page.get("slots", []):
        selected = sorted((i for i, word in enumerate(words) if _belongs(page, slot, word)),
                          key=lambda i: (words[i]["top"], words[i]["x0"]))
        local = slot.get("words", [])
        if (len(selected) != len(local) or
                any(type(word.get("ocr_line_word_index")) is not int or
                    word["ocr_line_word_index"] != index for index, word in zip(selected, local))):
            raise ValueError("OGE OCR line slot word references differ from the page source")
        for index, word in zip(selected, local):
            original = words[index]
            box = [float(original[key]) if dual else round(float(original[key]), 2)
                   for key in ("x0", "top", "x1", "bottom")]
            if (word.get("text") != original["text"] or word.get("bbox_pdf_points") != box or
                    word.get("confidence") != round(float(original["ocr_confidence"]), 2)):
                raise ValueError("OGE OCR line slot word differs from the original OCR record")


def description_line_reading(slot: dict, page: dict) -> dict | None:
    """Use witnessed OCR groups only inside one row and an unambiguous layout.

    Numeric line/block labels define membership, never page reading order.
    Each group must fit a text band in a supported coordinate frame, with
    separate lines and consistent order. Original word boxes never change.
    """
    source = page.get("ocr_line_source")
    if source is None:
        return None
    column = slot["column_observation"]["description"]
    indices = column.get("word_indices")
    if indices is None:
        return None
    words = slot["words"]
    if (not isinstance(indices, list) or
            any(type(i) is not int or not 0 <= i < len(words) for i in indices) or
            len(set(indices)) != len(indices)):
        raise ValueError("description word indices are invalid or duplicated")
    groups = {}
    for index in indices:
        original = source["words"][words[index]["ocr_line_word_index"]]
        groups.setdefault(_line(original), []).append(index)
    raw = column["raw_ocr_text"]
    basis = "source_ocr_line_partition_with_geometry_checks/v1"
    evidence = {"reading_coordinate_basis": basis, "ocr_line_source_sha256": source["words_sha256"]}

    def unknown(reason):
        return {"status": "unknown", "raw_ocr_text": raw, "ordered_ocr_text": None,
                "line_word_indices": None, "reason": "source_text_line_orientation_conflict",
                "ocr_line_rejection": reason, **evidence}

    selected_groups = set(groups)
    if any(_line(word) in selected_groups and not _belongs(page, slot, word) for word in source["words"]):
        return unknown("producer_line_crosses_physical_row")
    lines = [sorted(group, key=lambda i: words[i]["bbox_pdf_points"][0]) for group in groups.values()]
    if any(words[a]["bbox_pdf_points"][2] > words[b]["bbox_pdf_points"][0]
           for line in lines for a, b in zip(line, line[1:])):
        return unknown("producer_line_word_boxes_overlap")

    def center(index, relative=False):
        x0, top, x1, bottom = words[index]["bbox_pdf_points"]
        result = (top + bottom) / 2
        if relative and page.get("geometry_mode") == "source_pdf_dual_axis/v1":
            from .oge_278t_raster_slots import row_edges_at_x
            result -= row_edges_at_x(page, (x0 + x1) / 2)[slot["row_boundary_refs"][0]]
        return result

    # Reuse the existing reading tolerance symmetrically. A line ID cannot
    # merge geometrically separate text just because the engine labeled it so.
    def fits_band(group, relative):
        radii = {i: .6 * (words[i]["bbox_pdf_points"][3] - words[i]["bbox_pdf_points"][1])
                 for i in group}
        return max(center(i, relative) - radii[i] for i in group) <= min(
            center(i, relative) + radii[i] for i in group)

    if any(not any(fits_band(group, relative) for relative in (False, True)) for group in lines):
        return unknown("producer_line_has_no_supported_text_band")
    lines.sort(key=lambda group: median(center(i) for i in group))
    for relative in (False, True):
        if any(max(center(i, relative) for i in first) >= min(center(i, relative) for i in second)
               for first, second in zip(lines, lines[1:])):
            return unknown("producer_line_order_geometry_ambiguous")
    def edge(index, position, relative):
        offset = center(index) - center(index, relative)
        return words[index]["bbox_pdf_points"][position] - offset

    if not any(all(max(edge(i, 3, relative) for i in first) <= min(edge(i, 1, relative) for i in second)
                   for first, second in zip(lines, lines[1:])) for relative in (False, True)):
        return unknown("producer_line_order_geometry_ambiguous")
    refs = [{"line_reference": dict(zip(_LABELS, _line(source["words"][words[line[0]]["ocr_line_word_index"]]))),
             "source_page_word_indices": [words[i]["ocr_line_word_index"] for i in line]}
            for line in lines]
    return {"status": "geometry_ordered_unverified", "raw_ocr_text": raw,
            "ordered_ocr_text": " ".join(words[i]["text"] for line in lines for i in line),
            "line_word_indices": lines, "reason": None, "ocr_line_references": refs, **evidence}
