"""Audit-only cell observations for already archived scanned House PTR pages.

This module never turns OCR text or checkbox ink into qualified facts. It
preserves the physical band disposition, cell crop hash, raw OCR tokens, and
unknown states for later source-bound interpretation.
"""

from __future__ import annotations

import hashlib
import io
import re
from statistics import mean

from .ocr_geometry import OcrGeometryError, ocr_pdf_pages


def _vertical_rules(image, upper: float, lower: float) -> list[float]:
    import numpy as np

    from .house_ptr_region_shadow import _clusters

    gray = np.asarray(image.convert("L"))
    height, width = gray.shape
    y0, y1 = int((upper + .008) * height), int((lower - .008) * height)
    if y1 <= y0:
        return []
    coverage = (gray[y0:y1, :] < 100).mean(axis=0)
    return [sum(group) / (len(group) * width)
            for group in _clusters(np.flatnonzero(coverage > .78))]


def _column_boxes(region: dict, image) -> dict:
    bands = region.get("bands") or []
    header = region.get("header_band")
    matches = [index for index, band in enumerate(bands)
               if header and abs(band["upper"] - header[0]) < .0005]
    if len(matches) != 1 or matches[0] == 0:
        return {"status": "unknown_header_topology"}
    major = bands[matches[0] - 1]["vertical_x"]
    family = region.get("column_family")
    if family == "dense_long" and len(major) == 4:
        action_left, action_right = major[:2]
        event_left, event_right = major[1:3]
        amount_left = major[3]
    elif family == "short_grid" and len(major) in (5, 6):
        action_left, action_right = major[1:3]
        event_left, event_right = major[2:4]
        amount_left = major[4]
    else:
        return {"status": "unknown_header_topology"}
    rules = _vertical_rules(image, *header)
    action = [x for x in rules if action_left - .004 <= x <= action_right + .004]
    amount = [x for x in rules if amount_left - .004 <= x <= .985]
    if (len(action) not in (4, 5) or len(amount) not in (11, 12)
            or abs(action[0] - action_left) > .006
            or abs(action[-1] - action_right) > .006
            or abs(amount[0] - amount_left) > .006):
        return {"status": "unknown_checkbox_columns", "major_rules": major,
                "observed_action_rules": action, "observed_amount_rules": amount}
    return {"status": "located_shadow", "event": [event_left, event_right],
            "action": action, "amount": amount,
            "action_labels": (["purchase", "sale", "partial_sale", "exchange"]
                              if len(action) == 5 else ["purchase", "sale", "exchange"])}


def _cell(image, words: list[dict], page, *, label: str,
          x0: float, x1: float, y0: float, y1: float) -> dict:
    import numpy as np

    width, height = image.size
    if not 0 <= x0 < x1 <= 1 or not 0 <= y0 < y1 <= 1:
        raise ValueError("invalid shadow cell geometry")
    crop = image.crop((int(x0 * width), int(y0 * height),
                       int(x1 * width), int(y1 * height)))
    out = io.BytesIO()
    crop.save(out, format="PNG")
    selected = [word for word in words
                if x0 <= (word["x0"] + word["x1"]) / (2 * page.width) <= x1
                and y0 <= (word["top"] + word["bottom"]) / (2 * page.height) <= y1]
    selected.sort(key=lambda word: (word["top"], word["x0"]))
    gray = np.asarray(crop.convert("L"))
    pad_y, pad_x = max(1, gray.shape[0] // 6), max(1, gray.shape[1] // 6)
    interior = gray[pad_y:-pad_y, pad_x:-pad_x]
    raw_text = " ".join(word["text"] for word in selected)
    # A date-shaped OCR string is only a reading-quality hint. The row can
    # still be a printed legend and this function never emits a date value.
    date_shape = r"\d{1,2}/\d{1,2}/\d{2,4}"
    reading = ("date_shape_match" if label == "event_date" and
               re.fullmatch(date_shape, raw_text)
               else "date_shape_with_noise" if label == "event_date" and
               re.search(date_shape, raw_text)
               else "date_no_ocr_text" if label == "event_date" and not raw_text
               else "date_ocr_unclear" if label == "event_date" else "mark_unknown")
    return {"label": label, "status": "raw_unverified", "reading": reading,
            "raw_text": raw_text,
            "raw_ocr_confidence": round(mean(word["ocr_confidence"] for word in selected), 2)
            if selected else None,
            "bbox_normalized": [x0, y0, x1, y1],
            "bbox_pixels": [int(x0 * width), int(y0 * height),
                            int(x1 * width), int(y1 * height)],
            "bbox_points": [round(x0 * page.width, 2), round(y0 * page.height, 2),
                            round(x1 * page.width, 2), round(y1 * page.height, 2)],
            "crop_sha256": hashlib.sha256(out.getvalue()).hexdigest(),
            "interior_dark_fraction": round(float((interior < 100).mean()), 4)
            if interior.size else None, "value": None}


def observe_house_ptr_pdf(pdf_bytes: bytes, *, source_sha256: str,
                          executable: str | None = None, dpi: int = 200,
                          max_pages: int = 12) -> dict:
    """Observe physical rows and key cells without eligibility or fact IDs."""
    if hashlib.sha256(pdf_bytes).hexdigest() != source_sha256:
        raise ValueError("House PTR shadow source hash mismatch")
    if type(dpi) is not int or dpi not in (150, 200, 300):
        raise ValueError("House PTR shadow DPI is unsupported")
    import pdfplumber

    from .house_ptr_region_shadow import locate_transaction_grid

    pages_out = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if not 1 <= len(pdf.pages) <= max_pages:
            raise ValueError("House PTR shadow page count is outside bounds")
        for number, page in enumerate(pdf.pages, 1):
            image = page.to_image(resolution=dpi).original
            region = locate_transaction_grid(image)
            record = {"page": number, "region_status": region["status"],
                      "region_reason": region.get("reason"),
                      "orientation_degrees": region.get("orientation_degrees"),
                      "coordinate_frame": region.get("coordinate_frame"),
                      "rows": [
                          {"physical_band_index": index,
                           "disposition": physical["disposition"],
                           "band": physical["band"], "cells": None}
                          for index, physical in enumerate(
                              region.get("physical_row_bands", []), 1)]}
            if not region.get("physical_row_bands"):
                record["cell_status"] = "unobserved_no_physical_grid"
                pages_out.append(record)
                continue
            if region.get("coordinate_frame") != "original_page":
                record["cell_status"] = "unobserved_rotated_frame"
                pages_out.append(record)
                continue
            columns = _column_boxes(region, image)
            record["column_status"] = columns["status"]
            if columns["status"] != "located_shadow":
                record["cell_status"] = "unobserved_unknown_columns"
                record["column_evidence"] = columns
                pages_out.append(record)
                continue
            try:
                ocr_pages, engine = ocr_pdf_pages(pdf, executable=executable,
                                                  resolution=dpi, page_numbers=[number],
                                                  max_pages=1)
                words = ocr_pages[0].extract_words()
                record["ocr_engine"] = engine
                record["cell_status"] = "raw_observed_unverified"
            except OcrGeometryError as error:
                words = []
                record["ocr_engine"] = None
                record["cell_status"] = "image_only_ocr_unavailable"
                record["ocr_error"] = str(error)
            for index, physical in enumerate(region["physical_row_bands"], 1):
                y0, y1 = physical["band"]
                cells = {"event_date": _cell(image, words, page, label="event_date",
                                              x0=columns["event"][0], x1=columns["event"][1],
                                              y0=y0, y1=y1),
                         "direction": [], "amount": []}
                for slot, (left, right) in enumerate(zip(columns["action"],
                                                         columns["action"][1:])):
                    cells["direction"].append(_cell(
                        image, words, page, label=columns["action_labels"][slot],
                        x0=left, x1=right, y0=y0, y1=y1))
                for slot, (left, right) in enumerate(zip(columns["amount"],
                                                         columns["amount"][1:])):
                    cells["amount"].append(_cell(
                        image, words, page, label=chr(ord("A") + slot),
                        x0=left, x1=right, y0=y0, y1=y1))
                record["rows"][index - 1]["cells"] = cells
            pages_out.append(record)
    return {"schema_version": "house-ptr-cell-observation-shadow/v1",
            "source_sha256": source_sha256, "render_dpi": dpi,
            "qualification_status": "unverified_shadow_only", "pages": pages_out}
