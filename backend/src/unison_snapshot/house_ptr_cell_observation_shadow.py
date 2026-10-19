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
from types import SimpleNamespace

from .ocr_geometry import OcrGeometryError, ocr_pdf_pages, ocr_rendered_image


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
    major = region.get("major_columns") or bands[matches[0] - 1]["vertical_x"]
    family = region.get("column_family")
    if family == "dense_long" and len(major) == 4:
        action_left, action_right = major[:2]
        event_left, event_right = major[1:3]
        notice_left, notice_right = major[2:4]
        amount_left = notice_right
    elif family == "short_grid" and len(major) in (5, 6):
        action_left, action_right = major[1:3]
        event_left, event_right = major[2:4]
        notice_left = major[3]
        notice_right_candidates = major[4:]
    else:
        return {"status": "unknown_header_topology"}
    if family == "dense_long":
        notice_right_candidates = [notice_right]
    rules = _vertical_rules(image, *header)
    # The full-width ruled header retains the owner and asset boundaries even
    # when the preceding band omits the left rules from its detector window.
    pre_action = [x for x in rules if x <= action_left + .004]
    if (len(pre_action) != 3 or
            abs(pre_action[-1] - action_left) > .006):
        return {"status": "unknown_owner_asset_columns",
                "observed_pre_action_rules": pre_action}
    owner_left, owner_right, asset_right = pre_action
    asset_left = owner_right
    action = [x for x in rules if action_left - .004 <= x <= action_right + .004]
    amount_options = []
    for amount_left in notice_right_candidates:
        amount = [x for x in rules if amount_left - .004 <= x <= .985]
        if (len(amount) in (11, 12) and
                abs(amount[0] - amount_left) <= .006 and
                .04 <= amount_left - notice_left <= .12):
            amount_options.append((amount_left, amount))
    if (len(action) not in (4, 5) or len(amount_options) != 1
            or abs(action[0] - action_left) > .006
            or abs(action[-1] - action_right) > .006):
        return {"status": "unknown_checkbox_columns", "major_rules": major,
                "observed_action_rules": action, "observed_amount_rules": amount}
    notice_right, amount = amount_options[0]
    return {"status": "located_shadow", "owner": [owner_left, owner_right],
            "asset": [asset_left, asset_right],
            "event": [event_left, event_right],
            "notification": [notice_left, notice_right],
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
    lines: list[list[dict]] = []
    for word in selected:
        if lines and word["top"] - min(item["top"] for item in lines[-1]) <= 5:
            lines[-1].append(word)
        else:
            lines.append([word])
    gray = np.asarray(crop.convert("L"))
    pad_y, pad_x = max(1, gray.shape[0] // 6), max(1, gray.shape[1] // 6)
    interior = gray[pad_y:-pad_y, pad_x:-pad_x]
    raw_text = " ".join(word["text"] for line in lines
                        for word in sorted(line, key=lambda item: item["x0"]))
    # A date-shaped OCR string is only a reading-quality hint. The row can
    # still be a printed legend and this function never emits a date value.
    date_shape = r"\d{1,2}/\d{1,2}/\d{2,4}"
    date_field = label in {"event_date", "notification_date"}
    reading = ("date_shape_match" if date_field and
               re.fullmatch(date_shape, raw_text)
               else "date_shape_with_noise" if date_field and
               re.search(date_shape, raw_text)
               else "date_no_ocr_text" if date_field and not raw_text
               else "date_ocr_unclear" if date_field else "raw_unclassified"
               if label in {"owner", "asset"} else "mark_unknown")
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
                          max_pages: int = 12, page_numbers: list[int] | None = None) -> dict:
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
        if (page_numbers is not None and (not isinstance(page_numbers, list) or not page_numbers or
                any(type(number) is not int or not 1 <= number <= len(pdf.pages) for number in page_numbers) or
                len(set(page_numbers)) != len(page_numbers))):
            raise ValueError("House PTR shadow selected pages are invalid")
        selected = set(range(1, len(pdf.pages) + 1)) if page_numbers is None else set(page_numbers)
        for number, page in enumerate(pdf.pages, 1):
            if number not in selected:
                pages_out.append({"page": number, "region_status": "coverage_unknown",
                    "region_reason": "page_not_selected", "rows": [],
                    "cell_status": "unobserved_not_selected", "coordinate_frame": "original_page"})
                continue
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
                angle = region.get("orientation_degrees")
                if angle not in (90, 270):
                    record["cell_status"] = "unobserved_rotated_frame"
                    pages_out.append(record)
                    continue
                image = image.rotate(angle, expand=True)
            columns = _column_boxes(region, image)
            record["column_status"] = columns["status"]
            if columns["status"] != "located_shadow":
                record["cell_status"] = "unobserved_unknown_columns"
                record["column_evidence"] = columns
                pages_out.append(record)
                continue
            try:
                if region.get("coordinate_frame") == "original_page":
                    ocr_pages, engine = ocr_pdf_pages(
                        pdf, executable=executable, resolution=dpi,
                        page_numbers=[number], max_pages=1)
                    cell_page = page
                    words = ocr_pages[0].extract_words()
                else:
                    cell_page, engine = ocr_rendered_image(
                        image, executable=executable, resolution=dpi)
                    words = cell_page.extract_words()
                record["ocr_engine"] = engine
                record["cell_status"] = "raw_observed_unverified"
            except OcrGeometryError as error:
                words = []
                cell_page = (page if region.get("coordinate_frame") == "original_page"
                             else SimpleNamespace(width=image.width * 72.0 / dpi,
                                                  height=image.height * 72.0 / dpi))
                record["ocr_engine"] = None
                record["cell_status"] = "image_only_ocr_unavailable"
                record["ocr_error"] = str(error)
            if number == 1 and words and region.get("coordinate_frame") == "original_page":
                from .house_ptr import _legacy_filing_status
                statuses = {_legacy_filing_status({"_image": image}, words, page.width, page.height, compact)
                            for compact in (False, True)} - {None}
                record["filing_status_observation"] = next(iter(statuses)) if len(statuses) == 1 else None
            for index, physical in enumerate(region["physical_row_bands"], 1):
                y0, y1 = physical["band"]
                cells = {"owner": _cell(image, words, cell_page, label="owner",
                                     x0=columns["owner"][0], x1=columns["owner"][1],
                                     y0=y0, y1=y1),
                         "asset": _cell(image, words, cell_page, label="asset",
                                     x0=columns["asset"][0], x1=columns["asset"][1],
                                     y0=y0, y1=y1),
                         "event_date": _cell(image, words, cell_page, label="event_date",
                                              x0=columns["event"][0], x1=columns["event"][1],
                                              y0=y0, y1=y1),
                         "notification_date": _cell(
                             image, words, cell_page, label="notification_date",
                             x0=columns["notification"][0],
                             x1=columns["notification"][1], y0=y0, y1=y1),
                         "direction": [], "amount": []}
                for slot, (left, right) in enumerate(zip(columns["action"],
                                                         columns["action"][1:])):
                    cells["direction"].append(_cell(
                        image, words, cell_page, label=columns["action_labels"][slot],
                        x0=left, x1=right, y0=y0, y1=y1))
                for slot, (left, right) in enumerate(zip(columns["amount"],
                                                         columns["amount"][1:])):
                    cells["amount"].append(_cell(
                        image, words, cell_page, label=chr(ord("A") + slot),
                        x0=left, x1=right, y0=y0, y1=y1))
                record["rows"][index - 1]["cells"] = cells
            pages_out.append(record)
    return {"schema_version": "house-ptr-cell-observation-shadow/v3",
            "source_sha256": source_sha256, "render_dpi": dpi,
            "qualification_status": "unverified_shadow_only", "pages": pages_out}
