"""Read-only, page-level 278-T table coverage probe.

This module does not parse transactions or assign source eligibility.  A page
with an unreadable raster remains unknown unless table geometry is visible.
"""

from __future__ import annotations

import hashlib
import csv
import io
import math
from pathlib import Path
import re
import shutil
from statistics import median
import subprocess


_HEADERS = ("description", "type", "date", "notification", "amount")
_END_MARKERS = ("endnotes", "summary of contents", "privacy act statement")


def _horizontal_rules(page) -> list[float]:
    """Return distinct long horizontal rules in PDF point coordinates."""
    tops: list[float] = []
    for line in page.lines:
        if abs(float(line["y0"]) - float(line["y1"])) > 1.5:
            continue
        if abs(float(line["x1"]) - float(line["x0"])) < float(page.width) * .32:
            continue
        top = float(line["top"])
        if not tops or all(abs(top - other) > 1.8 for other in tops):
            tops.append(top)
    return sorted(tops)


def _stitched_table_rules(page) -> list[float]:
    """Join six-column rule fragments at one y, without reading OCR values."""
    segments = []
    width = float(page.width)
    for line in page.lines:
        if abs(float(line["y0"]) - float(line["y1"])) > 1.5:
            continue
        left, right = sorted((float(line["x0"]), float(line["x1"])))
        if right - left >= width * .025:
            segments.append((float(line["top"]), left, right))
    segments.sort()
    groups: list[list[tuple[float, float, float]]] = []
    for segment in segments:
        if groups and segment[0] - groups[-1][0][0] <= 4:
            groups[-1].append(segment)
        else:
            groups.append([segment])
    tops = []
    for group in groups:
        intervals = sorted((left, right) for _, left, right in group)
        if not intervals or intervals[0][0] > width * .15 or \
                max(right for _, right in intervals) < width * .8:
            continue
        merged: list[list[float]] = []
        for left, right in intervals:
            if merged and left <= merged[-1][1] + 3:
                merged[-1][1] = max(merged[-1][1], right)
            else:
                merged.append([left, right])
        if sum(right - left for left, right in merged) >= width * .6:
            tops.append(median(top for top, _, _ in group))
    return tops


def _regular_rule_run(tops: list[float]) -> list[float]:
    """Find the longest plausible row-rule sequence, without claiming rows."""
    best: list[float] = []
    for start in range(len(tops)):
        run = [tops[start]]
        for value in tops[start + 1:]:
            gap = value - run[-1]
            if gap < 8:
                continue
            if gap > 45:
                break
            run.append(value)
        if len(run) > len(best):
            best = run
    if len(best) < 6:
        return []
    gaps = [right - left for left, right in zip(best, best[1:])]
    typical = median(gaps)
    if sum(abs(gap - typical) <= typical * .45 for gap in gaps) < len(gaps) * .6:
        return []
    return best


def _header_bounded_grid(tops: list[float], header_top: float,
                         page_height: float, end_top: float) -> list[list[float]]:
    """Separate a raster form's data grid from its title/header/footer rules.

    The resulting intervals are physical slots, not proven transaction rows.
    We require a consistent run of consecutive boundaries.  Missing or extra
    boundaries leave the page unknown at row level instead of guessing 26.
    """
    edges = _data_rule_edges(tops, header_top, page_height, end_top)
    if len(edges) < 3 or not 20 <= edges[0] - header_top <= 55:
        return []
    gaps = [right - left for left, right in zip(edges, edges[1:])]
    if any(not 8 <= gap <= 45 for gap in gaps):
        return []
    typical = median(gaps)
    if not typical * 1.2 <= edges[0] - header_top <= typical * 2.4:
        return []
    if any(abs(gap - typical) > typical * .45 for gap in gaps):
        return []
    return [[round(left, 1), round(right, 1)]
            for left, right in zip(edges, edges[1:])]


def _data_rule_edges(tops: list[float], header_top: float,
                     page_height: float, end_top: float) -> list[float]:
    edges = [top for top in tops if header_top + 18 <= top <
             min(end_top, page_height * .95)]
    distinct: list[float] = []
    for top in edges:
        if not distinct or top - distinct[-1] >= 5:
            distinct.append(top)
    return distinct


def _uncertain_grid_regions(tops: list[float], header_top: float,
                            page_height: float, end_top: float) -> list[list[float]]:
    edges = _data_rule_edges(tops, header_top, page_height, end_top)
    if len(edges) < 2:
        return [[round(header_top + 18, 1), round(min(end_top, page_height * .95), 1)]]
    gaps = [right - left for left, right in zip(edges, edges[1:])]
    plausible = [gap for gap in gaps if 8 <= gap <= 45]
    if not plausible:
        return [[round(edges[0], 1), round(edges[-1], 1)]]
    typical = median(plausible)
    regions = []
    if edges[0] - header_top > typical * 2.4:
        regions.append([round(header_top + 18, 1), round(edges[0], 1)])
    for left, right in zip(edges, edges[1:]):
        if right - left > typical * 1.45:
            regions.append([round(left, 1), round(right, 1)])
    return regions


def _uncertain_anchor_regions(anchors: list[float]) -> list[list[float]]:
    if len(anchors) < 3:
        return []
    gaps = [right - left for left, right in zip(anchors, anchors[1:])]
    typical = median(gaps)
    return [[round(left, 1), round(right, 1)]
            for left, right in zip(anchors, anchors[1:])
            if right - left > typical * 1.45]


def _native_unnumbered_prefix(page) -> int:
    """Count transaction-shaped cells before the first readable row number.

    The native extractor starts at the first numbered row. This shadow count
    exposes earlier filled cells without assigning a transaction or ID.
    """
    count = 0
    extract_tables = getattr(page, "extract_tables", None)
    if not callable(extract_tables):
        return 0
    for table in extract_tables() or []:
        rows = [row for row in table or [] if isinstance(row, list)]
        signature = " ".join(str(cell or "").casefold() for row in rows for cell in row)
        if not all(label in signature for label in _HEADERS):
            continue
        first_number = next((index for index, row in enumerate(rows)
                             if row and re.fullmatch(r"[1-9][0-9]*",
                                                     str(row[0] or "").strip())),
                            len(rows))
        for row in rows[:first_number]:
            if len(row) != 6:
                continue
            cells = [" ".join(str(value or "").split()) for value in row]
            joined = " ".join(cells).casefold()
            if "description" in joined and "type" in joined and "date" in joined:
                continue
            if (cells[1] and (re.search(r"\d{1,2}/\d{1,2}/20\d{2}", cells[3]) or
                              re.search(r"\$\s*\d", cells[5]))):
                count += 1
    return count


def inspect_page(page, page_number: int) -> dict:
    """Classify one page and retain provisional row locations for review."""
    text = page.extract_text() or ""
    words = page.extract_words() or []
    raster_coverage = max(
        (max(0., min(float(page.width), float(image["x1"])) -
                    max(0., float(image["x0"]))) *
         max(0., min(float(page.height), float(image["bottom"])) -
                    max(0., float(image["top"]))) /
         (float(page.width) * float(page.height)) for image in page.images),
        default=0.,
    )
    text_mode = ("raster_with_text" if raster_coverage >= .75 and len(text) >= 80
                 else "raster_without_text" if raster_coverage >= .75
                 else "native_text" if len(text) >= 80 else "sparse_or_unknown")
    header_words = [word for word in words
                    if re.sub(r"[^a-z]", "", str(word["text"]).casefold()) in _HEADERS]
    header_top = min((float(word["top"]) for word in header_words), default=None)
    present = {re.sub(r"[^a-z]", "", str(word["text"]).casefold())
               for word in header_words if header_top is not None and
               abs(float(word["top"]) - header_top) < 32}
    header_complete = set(_HEADERS) <= present
    header_partial = len(present) >= 3 and "description" in present
    lower = text.casefold()
    end_top = min((float(word["top"]) for word in words
                   if str(word["text"]).casefold().strip(" :") == "endnotes"),
                  default=float(page.height))
    rules = _horizontal_rules(page)
    stitched_rules = _stitched_table_rules(page) if text_mode.startswith("raster") else rules
    run = _regular_rule_run(rules)
    # Some scanned PDFs retain the column grid as vectors while adjacent
    # printed rows have irregular heights.  That still identifies a likely
    # table page, but it does not justify an inferred row count.
    dense_grid = (len(rules) >= 20 and
                  rules[-1] - rules[0] >= float(page.height) * .35)
    bands: list[list[float]] = []
    unresolved_bands: list[list[float]] = []
    unresolved_regions: list[list[float]] = []
    number_sequence_warnings: list[dict] = []
    row_basis = "none"
    row_coverage = "unknown"
    rule_source = "none"
    native_unnumbered_prefix = (
        _native_unnumbered_prefix(page) if not text_mode.startswith("raster") else 0)
    if header_partial and header_top is not None:
        numbered_anchors = [(int(word["text"]), round(float(word["top"]), 1))
                            for word in words
                            if re.fullmatch(r"\d{1,4}", str(word["text"])) and
                            float(word["x0"]) < float(page.width) * .2 and
                            header_top + 8 < float(word["top"]) < end_top]
        anchors = sorted({top for _, top in numbered_anchors})
        if text_mode.startswith("raster"):
            bands = _header_bounded_grid(stitched_rules, header_top, float(page.height),
                                         end_top)
            rule_source = "stitched_segments" if bands else "none"
            if not bands:
                bands = _header_bounded_grid(rules, header_top,
                                             float(page.height), end_top)
                if bands:
                    rule_source = "long_rules"
            if bands:
                row_basis = "header_bounded_grid_slots"
                unresolved_bands = [band for band in bands
                                    if not any(abs(anchor - band[0]) <= 5
                                               for anchor in anchors)]
                labels = []
                for band in bands:
                    values = [number for number, top in numbered_anchors
                              if abs(top - band[0]) <= 5]
                    labels.append(values[0] if len(values) == 1 else None)
                for index in range(1, len(labels)):
                    prior, current = labels[index - 1:index + 1]
                    if prior is not None and current is not None and current != prior + 1:
                        number_sequence_warnings.append({
                            "slot_index": index + 1,
                            "previous_label": prior,
                            "observed_label": current,
                        })
                row_coverage = ("physical_slots_with_unread_number" if unresolved_bands
                                else "physical_slots_located")
        if not bands and anchors:
            bands = [[top, top] for top in anchors]
            row_basis = ("unverified_printed_number_anchors" if
                         text_mode.startswith("raster") else
                         "printed_number_anchors")
            row_coverage = ("unknown_grid" if text_mode.startswith("raster")
                            else "anchor_only")
            if text_mode.startswith("raster"):
                unresolved_regions = _uncertain_grid_regions(
                    stitched_rules, header_top, float(page.height), end_top)
                for region in _uncertain_anchor_regions(anchors):
                    if region not in unresolved_regions:
                        unresolved_regions.append(region)
    # A raster page can retain partial vector rules from the original form.
    # The longest run can omit cells or include non-row lines (observed in a
    # held-out 278-T); it must not masquerade as physical row coverage.
    if not bands and run and not text_mode.startswith("raster"):
        bands = [[round(left, 1), round(right, 1)]
                 for left, right in zip(run, run[1:])]
        row_basis = "provisional_rule_intervals"
        row_coverage = "unverified_rule_intervals"
    if header_complete:
        coverage = "table_header_found"
    elif header_partial and bands:
        coverage = "table_geometry_candidate"
    elif (run or dense_grid) and (text_mode.startswith("raster") or
                                  "transaction" in lower):
        coverage = "table_geometry_candidate"
    elif text_mode.startswith("raster"):
        coverage = "coverage_unknown_raster"
    elif any(marker in lower for marker in _END_MARKERS):
        coverage = "non_table_section"
    else:
        coverage = "coverage_unknown"
    return {
        "page_number": page_number,
        "coverage": coverage,
        "text_mode": text_mode,
        "header_complete": header_complete,
        "raster_coverage": round(raster_coverage, 3),
        "long_rule_count": len(rules),
        "stitched_rule_count": len(stitched_rules),
        "row_basis": row_basis,
        "rule_source": rule_source,
        "row_coverage": row_coverage,
        "candidate_row_bands": bands,
        "unresolved_row_bands": unresolved_bands,
        "unresolved_row_regions": unresolved_regions,
        "number_sequence_warnings": number_sequence_warnings,
        "native_unnumbered_prefix_rows": native_unnumbered_prefix,
    }


def inspect_pdf(path: Path, *, page_numbers: list[int] | None = None,
                raster_ocr_records: list[dict] | None = None,
                raster_ocr_source_sha256: str | None = None) -> dict:
    """Inspect an immutable local PDF; caller decides whether any page is usable."""
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError("pdfplumber is required for the 278-T page shadow") from None
    source = Path(path)
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    with pdfplumber.open(source) as document:
        selected = page_numbers or list(range(1, len(document.pages) + 1))
        if any(not 1 <= number <= len(document.pages) for number in selected):
            raise ValueError("page number outside PDF")
        pages = [inspect_page(document.pages[number - 1], number)
                 for number in selected]
        result = {"source_sha256": source_sha256,
                  "page_count": len(document.pages), "pages": pages}
    if raster_ocr_records is not None:
        if raster_ocr_source_sha256 != source_sha256:
            raise ValueError("OCR extraction source differs from PDF")
        raster_numbers = [page["page_number"] for page in pages
                          if page["text_mode"].startswith("raster")]
        census = probe_raster_row_bands(
            source, expected_sha256=source_sha256,
            page_numbers=raster_numbers) if raster_numbers else {
                "source_sha256": source_sha256, "results": []}
        result["raster_ocr_coverage"] = reconcile_raster_ocr_rows(
            census, raster_ocr_records, expected_sha256=source_sha256,
            ocr_source_sha256=raster_ocr_source_sha256)
    return result


def _number_suggestion(attempts: list[dict]) -> str | None:
    """Keep conflicting or singly observed OCR values unresolved."""
    supported: dict[str, set[float]] = {}
    for attempt in attempts:
        value = str(attempt.get("text") or "").strip()
        if re.fullmatch(r"\d{1,4}", value) and float(attempt.get("confidence", 0)) >= 80:
            supported.setdefault(value, set()).add(float(attempt["x_left"]))
    if len(supported) != 1:
        return None
    value, independent_crops = next(iter(supported.items()))
    return value if len(independent_crops) >= 2 else None


def probe_number_cells(path: Path, *, expected_sha256: str,
                       requests: list[dict], executable: str) -> dict:
    """Targeted, non-promoting OCR of known raster table number cells.

    `requests` contains page_number and a physical band [top, bottom].  No
    expected printed number enters OCR or suggestion selection.
    """
    try:
        import pymupdf
        from PIL import Image, ImageOps
    except ImportError:
        raise RuntimeError("pymupdf and Pillow are required for cell OCR") from None
    engine = shutil.which(executable)
    if not engine:
        raise RuntimeError("Tesseract executable is unavailable")
    source = Path(path)
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    if source_sha256 != expected_sha256:
        raise ValueError("PDF bytes do not match the fixed source SHA-256")
    version = subprocess.run([engine, "--version"], capture_output=True,
                             check=False, timeout=15).stdout.decode(
                                 "utf-8", errors="replace").splitlines()[0]
    results = []
    with pymupdf.open(source) as document:
        for request in requests:
            number = int(request["page_number"])
            top, bottom = map(float, request["band"])
            if not 1 <= number <= len(document) or not 0 <= top < bottom <= \
                    float(document[number - 1].rect.height):
                raise ValueError("OCR cell is outside the PDF page")
            page = document[number - 1]
            original = [word[4] for word in page.get_text("words")
                        if float(word[0]) < float(page.rect.width) * .2 and
                        abs(float(word[1]) - top) <= 5]
            attempts = []
            for x_left in (81., 82., 83.):
                for y_offset in (-1., 0.):
                    rect = pymupdf.Rect(x_left, top + y_offset, 111., bottom - 1)
                    rendered = page.get_pixmap(matrix=pymupdf.Matrix(5, 5),
                                                clip=rect, alpha=False).tobytes("png")
                    image = ImageOps.expand(Image.open(io.BytesIO(rendered)).convert("L"),
                                            border=40, fill=255)
                    stream = io.BytesIO()
                    image.save(stream, format="PNG")
                    crop = stream.getvalue()
                    completed = subprocess.run(
                        [engine, "stdin", "stdout", "--psm", "7", "--oem", "1", "tsv"],
                        input=crop, capture_output=True, check=False, timeout=20)
                    if completed.returncode:
                        raise RuntimeError("Tesseract cell OCR failed")
                    rows = csv.DictReader(
                        io.StringIO(completed.stdout.decode("utf-8", errors="replace")),
                        delimiter="\t")
                    tokens = [(row["text"], float(row["conf"])) for row in rows
                              if row.get("level") == "5" and row.get("text", "").strip()]
                    attempts.append({"x_left": x_left, "y_offset": y_offset,
                                     "crop_sha256": hashlib.sha256(crop).hexdigest(),
                                     "text": " ".join(token for token, _ in tokens),
                                     "confidence": min((confidence for _, confidence in tokens),
                                                       default=0.)})
            results.append({"page_number": number,
                            "band": [top, bottom],
                            "text_layer_tokens": original,
                            "ocr_attempts": attempts,
                            "ocr_suggestion": _number_suggestion(attempts),
                            "status": "shadow_suggestion_only"})
    return {"source_sha256": source_sha256, "engine": version, "results": results}


def _raster_rule_centers(gray, *, points_per_pixel: float,
                         x0: float, x1: float, x_points_per_pixel: float | None = None) -> list[float]:
    """Detect dark horizontal rules in one narrow, text-light image strip."""
    import numpy as np

    x_scale = x_points_per_pixel if x_points_per_pixel is not None else points_per_pixel
    left, right = int(x0 / x_scale), int(x1 / x_scale)
    ratio = (gray[:, left:right] < 160).mean(axis=1)
    pixels = np.flatnonzero(ratio > .9)
    groups: list[list[int]] = []
    for pixel in pixels:
        y = int(pixel)
        if groups and y - groups[-1][-1] <= 3:
            groups[-1].append(y)
        else:
            groups.append([y])
    return [round(float(np.median(group)) * points_per_pixel, 1)
            for group in groups
            if 80 < float(np.median(group)) * points_per_pixel < 590]


def _raster_rule_consensus(strips: list[list[float]]) -> list[float]:
    """Require three independent x strips to agree on every boundary."""
    if len(strips) != 3 or len(strips[0]) < 6 or \
            any(len(values) != len(strips[0]) for values in strips[1:]):
        return []
    rows = list(zip(*strips, strict=True))
    if any(max(row) - min(row) > 2 for row in rows):
        return []
    centers = [round(median(row), 1) for row in rows]
    gaps = [right - left for left, right in zip(centers, centers[1:])]
    typical = median(gaps)
    if not 8 <= typical <= 25 or any(abs(gap - typical) > typical * .35
                                     for gap in gaps):
        return []
    return centers


def probe_raster_row_bands(path: Path, *, expected_sha256: str,
                           page_numbers: list[int]) -> dict:
    """Blind physical-grid census for selected raster 278-T pages.

    The algorithm receives no expected row count or printed number.  It uses
    three x strips of the archived page image and leaves disagreement unknown.
    """
    try:
        import numpy as np
        import pdfplumber
    except ImportError:
        raise RuntimeError("numpy and pdfplumber are required for raster census") from None
    source = Path(path)
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    if source_sha256 != expected_sha256:
        raise ValueError("PDF bytes do not match the fixed source SHA-256")
    results = []
    with pdfplumber.open(source) as document:
        for number in page_numbers:
            if not 1 <= number <= len(document.pages):
                raise ValueError("raster census page is outside the PDF")
            page = document.pages[number - 1]
            gray = np.asarray(page.to_image(
                resolution=216, force_mediabox=True).original.convert("L"))
            strips = [_raster_rule_centers(gray, points_per_pixel=page.height/gray.shape[0],
                                           x_points_per_pixel=page.width/gray.shape[1],
                                           x0=left, x1=right)
                      for left, right in ((100, 135), (110, 135), (125, 140))]
            centers = _raster_rule_consensus(strips)
            bands = [[left, right] for left, right in zip(centers, centers[1:])]
            results.append({"page_number": number,
                            "status": ("physical_slots_located" if bands else
                                       "coverage_unknown"),
                            "strip_rule_counts": [len(values) for values in strips],
                            "rule_positions": centers,
                            "physical_row_bands": bands})
    return {"source_sha256": source_sha256, "results": results}


def probe_oge_2024_alternate_bands(path: Path, *, expected_sha256: str,
                                   page_numbers: list[int]) -> dict:
    """Retry confirmed six-column pages when the generic left strip is occluded.

    The caller must independently confirm the seven 2024 form column rules.
    Three non-overlapping strips are required to agree; a single header rule
    may be removed only above the data grid.  No OCR value or expected row
    count is used to select or repair boundaries.
    """
    try:
        import numpy as np
        import pdfplumber
    except ImportError:
        raise RuntimeError("numpy and pdfplumber are required for raster census") from None
    source = Path(path)
    actual_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError("PDF bytes do not match the fixed source SHA-256")
    if len(page_numbers) > 8 or len(set(page_numbers)) != len(page_numbers):
        raise ValueError("2024 raster census needs at most 8 distinct pages")
    results = []
    with pdfplumber.open(source) as document:
        for number in page_numbers:
            if type(number) is not int or not 1 <= number <= len(document.pages):
                raise ValueError("raster census page is outside the PDF")
            page = document.pages[number - 1]
            gray = np.asarray(page.to_image(
                resolution=216, force_mediabox=True).original.convert("L"))
            strips = [_raster_rule_centers(gray, points_per_pixel=page.height/gray.shape[0],
                                           x_points_per_pixel=page.width/gray.shape[1],
                                           x0=left, x1=right)
                      for left, right in ((110, 135), (200, 220), (250, 270))]
            raw_counts = [len(values) for values in strips]
            centers = []
            header_excluded = False
            if len({len(values) for values in strips}) == 1 and len(strips[0]) >= 6:
                if (strips[0][0] < 100 and strips[0][1] > 100 and
                        strips[0][1] - strips[0][0] > 25 and
                        all(len(values) > 1 and abs(values[0] - strips[0][0]) <= 2
                            for values in strips)):
                    strips = [values[1:] for values in strips]
                    header_excluded = True
                if all(max(row) - min(row) <= 2 for row in zip(*strips, strict=True)):
                    candidate = [round(median(row), 1)
                                 for row in zip(*strips, strict=True)]
                    gaps = [right - left for left, right in
                            zip(candidate, candidate[1:])]
                    typical = median(gaps)
                    if (len(candidate) >= 6 and 8 <= typical <= 25 and
                            all(8 <= gap <= min(25, typical * 1.5)
                                for gap in gaps)):
                        centers = candidate
            bands = [[left, right] for left, right in zip(centers, centers[1:])]
            results.append({"page_number": number,
                            "status": ("physical_slots_located" if bands else
                                       "coverage_unknown"),
                            "strip_rule_counts": [len(values) for values in strips],
                            "raw_strip_rule_counts": raw_counts,
                            "header_rule_excluded": header_excluded,
                            "rule_positions": centers,
                            "physical_row_bands": bands,
                            "row_detection_basis": "oge_2024_alternate_strips"})
    return {"source_sha256": actual_sha256, "results": results}


def reconcile_raster_ocr_rows(census: dict, ocr_records: list[dict], *,
                              expected_sha256: str,
                              ocr_source_sha256: str) -> dict:
    """Account for every located raster slot independently of OCR row signals.

    An unmatched slot may be blank or may contain text that OCR missed in both
    date and amount columns. It stays unknown until source-bound cell evidence
    distinguishes those cases. OCR rows outside the grid also stay explicit.
    """
    if (not isinstance(census, dict) or
            census.get("source_sha256") != expected_sha256 or
            ocr_source_sha256 != expected_sha256 or
            not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or
            not isinstance(ocr_records, list)):
        raise ValueError("raster census and OCR source binding are invalid")
    pages = census.get("results")
    if not isinstance(pages, list):
        raise ValueError("raster census pages are invalid")
    by_page: dict[int, list[tuple[int, float]]] = {}
    for index, record in enumerate(ocr_records):
        if not isinstance(record, dict):
            raise ValueError("OCR row is invalid")
        page = record.get("page_number")
        top = record.get("geometry_top")
        if (type(page) is not int or page < 1 or
                type(top) not in (int, float) or not math.isfinite(top) or top < 0):
            raise ValueError("OCR row geometry is invalid")
        by_page.setdefault(page, []).append((index, float(top)))
    seen_pages: set[int] = set()
    slots: list[dict] = []
    unmatched_ocr: list[dict] = []
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError("raster census page is invalid")
        number = page.get("page_number")
        if type(number) is not int or number < 1 or number in seen_pages:
            raise ValueError("raster census page number is invalid")
        seen_pages.add(number)
        bands = page.get("physical_row_bands")
        status = page.get("status")
        if not isinstance(bands, list) or status not in {
                "physical_slots_located", "coverage_unknown"}:
            raise ValueError("raster census page coverage is invalid")
        if (status == "coverage_unknown" and bands or
                status == "physical_slots_located" and not bands):
            raise ValueError("raster page status and physical slots disagree")
        mapped: set[int] = set()
        previous_bottom = -1.0
        for position, band in enumerate(bands, start=1):
            if (not isinstance(band, list) or len(band) != 2 or
                    not all(isinstance(value, (int, float)) for value in band)):
                raise ValueError("raster slot band is invalid")
            top, bottom = map(float, band)
            if top < previous_bottom or top >= bottom:
                raise ValueError("raster slot bands overlap or are unordered")
            previous_bottom = bottom
            matches = [index for index, row_top in by_page.get(number, [])
                       if top <= row_top < bottom]
            mapped.update(matches)
            slots.append({"page_number": number, "slot_index": position,
                          "band": [top, bottom], "ocr_row_indices": matches,
                          "status": ("ocr_row_mapped" if len(matches) == 1 else
                                     "physical_slot_without_ocr_row" if not matches else
                                     "multiple_ocr_rows_in_slot"),
                          "content_status": "unverified"})
        unmatched_ocr.extend({"page_number": number, "ocr_row_index": index}
                             for index, _ in by_page.get(number, []) if index not in mapped)
    return {"source_sha256": expected_sha256, "physical_slot_count": len(slots),
            "slots": slots, "unmatched_ocr_rows": unmatched_ocr,
            "ocr_rows_outside_selected_pages": [
                {"page_number": page, "ocr_row_index": index}
                for page, rows in sorted(by_page.items()) if page not in seen_pages
                for index, _ in rows],
            "unknown_pages": sorted(page["page_number"] for page in pages
                                    if page["status"] == "coverage_unknown"),
            "production_mutation_authorized": False}
