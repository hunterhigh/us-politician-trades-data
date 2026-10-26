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


def inspect_page(page, page_number: int, *, source_sha256: str | None = None) -> dict:
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
    page_role = None
    if any(marker in lower for marker in _END_MARKERS):
        from .oge_278t_page_roles import observe_native_privacy_role
        page_role = observe_native_privacy_role(page,page_number,source_sha256=source_sha256)
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
    elif page_role is not None and page_role['status']=='non_table':
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
        **({'page_role_proof':page_role} if page_role is not None else {}),
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
        pages = [inspect_page(document.pages[number - 1], number,source_sha256=source_sha256)
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
        from .oge_278t_raster_inventory import audit_raster_source_inventory
        result["raster_source_inventory"] = audit_raster_source_inventory(
            source, expected_sha256=source_sha256, census=census,
            document_page_count=result["page_count"])
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
                         x0: float, x1: float, x_points_per_pixel: float | None = None,
                         y_span: tuple[float, float] = (80., 590.)) -> list[float]:
    """Detect dark horizontal rules in one narrow, text-light image strip."""
    import numpy as np

    if (len(y_span) != 2 or not all(math.isfinite(v) for v in y_span) or
            not 0 <= y_span[0] < y_span[1]):
        raise ValueError("raster rule search requires an explicit ordered vertical domain")
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
            if y_span[0] < float(np.median(group)) * points_per_pixel < y_span[1]]


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
                    not all(type(value) in (int, float) and math.isfinite(value) for value in band)):
                raise ValueError("raster slot band is invalid")
            top, bottom = map(float, band)
            if top < 0 or top < previous_bottom or top >= bottom:
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


_NATIVE_EXHIBIT_SCHEMA = "oge-278t-native-page-observation/v1"
_NATIVE_EXHIBIT_METHOD = "existing_embedded_pdf_text_and_table_borders_no_new_ocr"
_EXHIBIT_LABEL = re.compile(r"In\s+place\s+of\s*line\s+item\s*#\s*(?P<number>[1-9]\d*)"
    r"(?P<context>.*?)\s*,?\s*substitute\s+the\s+following\s*:", re.I)


def observe_native_exhibit_page(pdf_path: Path, *, source_sha256: str, page_number: int,
                                embedded_column_structure: bool = False) -> dict:
    """Read exactly one archived native-text page; never invoke OCR."""
    import pdfplumber
    source = Path(pdf_path)
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_sha256:
        raise ValueError("native exhibit PDF differs from source SHA")
    with pdfplumber.open(source) as document:
        if type(page_number) is not int or not 1 <= page_number <= len(document.pages):
            raise ValueError("native exhibit page is outside the original PDF")
        page = document.pages[page_number-1]
        text = page.extract_text() or ""
        tables = page.find_tables()
        result = {"schema_version": _NATIVE_EXHIBIT_SCHEMA, "source_sha256": source_sha256,
            "page_number":page_number, "document_page_count":len(document.pages),
            "coordinate_frame":"pdfplumber_displayed_pdf_points", "method":_NATIVE_EXHIBIT_METHOD,
            "page_width":page.width,"page_height":page.height,"page_text":text,
            "page_text_sha256":hashlib.sha256(text.encode()).hexdigest(),
            "words":[{key:word[key] for key in ("text","x0","top","x1","bottom")} for word in page.extract_words()],
            "table_bounds":[list(table.bbox) for table in tables],
            "tables":[table.extract() for table in tables]}
    if embedded_column_structure:
        from .oge_278t_exhibit_structure import observe_embedded_exhibit_columns
        result["embedded_column_structure"] = observe_embedded_exhibit_columns(source,
            source_sha256=source_sha256,page_number=page_number,table_bounds=result["table_bounds"],
            native_page_size=(result["page_width"],result["page_height"]))
    return result


def _native_word_lines(words: list[dict], indices: list[int]) -> list[list[int]]:
    lines = []
    for index in sorted(indices,key=lambda i:(words[i]["top"],words[i]["x0"])):
        if not lines or abs(words[index]["top"]-words[lines[-1][0]]["top"]) > 3:
            lines.append([index])
        else:
            lines[-1].append(index)
    return [sorted(line,key=lambda i:words[i]["x0"]) for line in lines]


def interpret_native_exhibit_page(observation: dict) -> dict:
    """Partition source words into replacement candidates, never eligible facts.

    Header starts are semantic reading partitions, not proof of vertical rules.
    Every raw body word remains in a field or an explicit unresolved collection.
    """
    import json
    if (not isinstance(observation,dict) or observation.get("schema_version") != _NATIVE_EXHIBIT_SCHEMA or
            observation.get("method") != _NATIVE_EXHIBIT_METHOD or
            observation.get("coordinate_frame") != "pdfplumber_displayed_pdf_points" or
            not re.fullmatch(r"[0-9a-f]{64}",observation.get("source_sha256") or "") or
            type(observation.get("page_number")) is not int or type(observation.get("document_page_count")) is not int or
            not 1 <= observation["page_number"] <= observation["document_page_count"] or
            not isinstance(observation.get("page_text"),str) or
            hashlib.sha256(observation["page_text"].encode()).hexdigest() != observation.get("page_text_sha256")):
        raise ValueError("native exhibit source/page/text contract is invalid")
    width,height = observation.get("page_width"),observation.get("page_height")
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in (width,height)):
        raise ValueError("native exhibit page dimensions are invalid")
    words = observation.get("words")
    bounds = observation.get("table_bounds")
    if not isinstance(words,list) or not isinstance(bounds,list) or len(bounds) != len(observation.get("tables",[])):
        raise ValueError("native exhibit word/table inventory is invalid")
    for word in words:
        if (not isinstance(word,dict) or not isinstance(word.get("text"),str) or
                any(type(word.get(k)) not in (int,float) or not math.isfinite(word[k]) for k in ("x0","top","x1","bottom")) or
                not 0<=word["x0"]<word["x1"]<=width or not 0<=word["top"]<word["bottom"]<=height):
            raise ValueError("native exhibit source word box is invalid")
    for box in bounds:
        if (not isinstance(box,list) or len(box)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in box) or
                not 0<=box[0]<box[2]<=width or not 0<=box[1]<box[3]<=height):
            raise ValueError("native exhibit source table box is invalid")
    ordered = sorted(enumerate(bounds),key=lambda item:item[1][1])
    if any(a[1][3]>b[1][1] for a,b in zip(ordered,ordered[1:])):
        raise ValueError("native exhibit source table boxes overlap")
    lines = _native_word_lines(words,list(range(len(words))))
    labels = []
    for line in lines:
        text = " ".join(words[i]["text"] for i in line)
        match = _EXHIBIT_LABEL.fullmatch(text)
        if match:
            labels.append({"word_indices":line,"text":text,"target_printed_number":int(match["number"]),
                "top":min(words[i]["top"] for i in line),"bottom":max(words[i]["bottom"] for i in line),
                "predecessor_urls":re.findall(r"https://[^\s,]+",match["context"])})
    text_targets = [int(match["number"]) for match in _EXHIBIT_LABEL.finditer(observation["page_text"])]
    if text_targets != [row["target_printed_number"] for row in labels]:
        raise ValueError("native exhibit text and source-word label ordering differ")
    digest = hashlib.sha256(json.dumps(observation,ensure_ascii=False,sort_keys=True,separators=(",", ":")).encode()).hexdigest()
    structure_blocks = None
    if "embedded_column_structure" in observation:
        from .oge_278t_exhibit_structure import validated_structure_blocks
        structure_blocks = validated_structure_blocks(observation["embedded_column_structure"],observation)
    blocks, previous_bottom, used_labels = [],0,set()
    names = ("printed_number","description","type","transaction_date","notification","amount")
    role_names = ("#","description","type","date","notification","amount")
    for table_index,box in ordered:
        nearby = [(i,label) for i,label in enumerate(labels) if previous_bottom<=label["top"] and label["bottom"]<=box[1]]
        previous_bottom=box[3]
        base={"table_index":table_index,"table_bounds":box,"page_number":observation["page_number"],
            "input_observation_canonical_sha256":digest,"source_sha256":observation["source_sha256"],
            "partition_basis":"same_box_native_header_role_candidate_not_verified_column_geometry"}
        if len(nearby)!=1:
            blocks.append({**base,"status":"unresolved","reasons":["replacement_label_to_box_ambiguous"]});continue
        label_index,label=nearby[0];used_labels.add(label_index)
        indices=[i for i,w in enumerate(words) if box[0]<=w["x0"] and w["x1"]<=box[2] and box[1]<=w["top"] and w["bottom"]<=box[3]]
        number_candidates=[i for i in indices if words[i]["text"]==str(label["target_printed_number"]) and
            words[i]["x0"]<box[0]+(box[2]-box[0])*.15]
        if len(number_candidates)!=1:
            blocks.append({**base,"status":"unresolved","label":label,"reasons":["replacement_body_number_ambiguous"],"all_word_indices":indices});continue
        body_top=words[number_candidates[0]]["top"]
        header={}
        for role in role_names:
            found=[i for i in indices if words[i]["bottom"]<=body_top and words[i]["text"].strip("'!:•").casefold()==role]
            if len(found)==1:header[role]=found[0]
        if len(header)!=6 or not all(words[header[a]]["x0"]<words[header[b]]["x0"] for a,b in zip(role_names,role_names[1:])):
            blocks.append({**base,"status":"unresolved","label":label,"reasons":["replacement_header_roles_unresolved"],"all_word_indices":indices});continue
        edges=[box[0],*[words[header[role]]["x0"] for role in role_names[1:]],box[2]]
        intervals = None
        if structure_blocks is not None:
            structure = structure_blocks.get(table_index,{})
            tracks = structure.get("tracks",[])
            if structure.get("status") != "tracks_observed" or len(tracks) != 7:
                blocks.append({**base,"status":"unresolved","label":label,
                    "reasons":["native_embedded_column_structure_unknown"],"all_word_indices":indices});continue
            intervals = [track["candidate_interval_pdf_points"] for track in tracks]
            if (any(len(v)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) for x in v) or v[0]>v[1] for v in intervals) or
                    any(a[1]>=b[0] for a,b in zip(intervals,intervals[1:]))):
                raise ValueError("native EXHIBIT column intervals are not uniquely ordered")
            # A missing real track plus an extra glyph/false track may still
            # total seven. Official role anchors must agree with each column
            # for every admissible boundary. Header punctuation may overhang
            # a rule; body/financial boxes below still require full inclusion.
            role_centers = {role:(words[index]["x0"]+words[index]["x1"])/2 for role,index in header.items()}
            if any(not intervals[j][1] <= role_centers[role] <= intervals[j+1][0]
                   for j,role in enumerate(role_names)):
                blocks.append({**base,"status":"unresolved","label":label,
                    "reasons":["native_column_tracks_header_roles_conflict"],"all_word_indices":indices});continue
            base.update(partition_basis="source_embedded_image_tracks_and_complete_native_header_roles",
                        column_boundary_intervals_pdf_points=intervals,
                        header_role_anchor_basis="original_word_box_center_for_each_admissible_column_boundary",
                        header_role_anchors_pdf_points=role_centers)
        fields={name:[] for name in names};unassigned=[];crossing=[]
        body=[i for i in indices if words[i]["top"]>=body_top-3]
        for i in body:
            word=words[i];center=(word["x0"]+word["x1"])/2
            if intervals is not None:
                matches=[j for j in range(6) if intervals[j][1]<=word["x0"] and word["x1"]<=intervals[j+1][0]]
                if len(matches)!=1:
                    unassigned.append(i);crossing.append(i);continue
                fields[names[matches[0]]].append(i);continue
            matches=[j for j in range(6) if edges[j]<=center<edges[j+1]]
            if len(matches)!=1:unassigned.append(i);continue
            j=matches[0];fields[names[j]].append(i)
            if word["x0"]<edges[j] or word["x1"]>edges[j+1]:crossing.append(i)
        raw={name:" ".join(words[i]["text"] for line in _native_word_lines(words,field) for i in line)
             for name,field in fields.items()}
        blocks.append({**base,"status":"replacement_row_candidate","label":label,"raw_columns":raw,
            "field_word_indices":fields,"all_word_indices":indices,"body_word_indices":body,
            "header_word_indices":header,"unassigned_body_word_indices":unassigned,"partition_crossing_word_indices":crossing,
            "limitations":(["intersecting_source_word_boxes_remain_unresolved"] if intervals is not None else
                           ["header_partitions_are_not_verified_column_rules"])})
    return {"schema_version":"oge-278t-native-exhibit-rows/v1","source_sha256":observation["source_sha256"],
        "page_number":observation["page_number"],"input_observation_canonical_sha256":digest,"blocks":blocks,
        "unused_label_indices":[i for i in range(len(labels)) if i not in used_labels]}
