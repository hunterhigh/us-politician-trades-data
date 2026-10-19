"""Bounded, read-only OCR observations inside confirmed raster 278-T slots."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .ocr_geometry import ocr_pdf_pages
from .oge_278t_page_shadow import (probe_oge_2024_alternate_bands,
                                   probe_raster_row_bands)


SCHEMA = "oge-278t-raster-slot-observation/v2"
MAX_PAGES_PER_RUN = 8
_COLUMNS = ("printed_number", "description", "type", "transaction_date",
            "notification", "amount")


def _column_profile_matches(edges: list[float]) -> bool:
    if len(edges) != 7 or not 80 <= edges[0] <= 120 or not 675 <= edges[-1] <= 710:
        return False
    return all(abs((right - left) - target) <= limit
               for left, right, target, limit in zip(
                   edges[:-1], edges[1:], (45, 310, 51, 57, 47, 82),
                   (12, 18, 12, 12, 12, 18), strict=True))


def _rule_pixel_confirmation(gray, *, x: float, top: float, bottom: float,
                             scale_x: float, scale_y: float) -> dict:
    """Require one unbroken dark path plus majority thin-line support per third.

    A path may move one pixel horizontally per scanline, but cannot bridge a
    blank scanline. Text, horizontal rules or a dark block alone do not satisfy
    both requirements. No OCR or expected transaction count is involved.
    """
    import numpy as np

    left, right = int((x - 3) * scale_x), int((x + 3) * scale_x)
    first, last = int(top * scale_y), int(bottom * scale_y)
    if not (0 <= left < right <= gray.shape[1] and 0 <= first < last <= gray.shape[0]):
        return {"status": "unknown", "reason": "pixel_window_outside_page"}
    mask = gray[first:last, left:right] < 160
    reach = mask[0].copy()
    continuous_height = 1 if reach.any() else 0
    if continuous_height:
        for row in mask[1:]:
            reach = (reach | np.r_[False, reach[:-1]] | np.r_[reach[1:], False]) & row
            if not reach.any():
                break
            continuous_height += 1
    width = mask.sum(axis=1)
    runs = mask[:, 0].astype(int) + (~mask[:, :-1] & mask[:, 1:]).sum(axis=1)
    thin = (width >= 1) & (width <= max(1, int(2 * scale_x))) & (runs == 1)
    supports = [round(float(part.mean()), 6) for part in np.array_split(thin, 3)]
    confirmed = continuous_height == len(mask) and all(value > .5 for value in supports)
    return {"status": "confirmed" if confirmed else "unknown",
            "reason": None if confirmed else "no_continuous_unique_thin_rule",
            "x_pdf_points": x, "checked_span_pdf_points": [top, bottom],
            "continuous_path_scanlines": continuous_height, "checked_scanlines": len(mask),
            "thin_rule_support_by_third": supports}


def _oge_2024_column_geometry(page) -> dict:
    """Locate seven same-page rules; corroborate short vectors from page pixels.

    The original strong-vector path is unchanged. The restricted fallback
    never supplies a missing x from the template or another page: all seven
    candidates must already have vector evidence and match this form profile.
    """
    lines = sorted((float(line["x0"]), abs(float(line["y1"]) - float(line["y0"])),
                    float(line["top"]), float(line["bottom"]))
                   for line in page.lines
                   if abs(float(line["x1"]) - float(line["x0"])) <= 1 and
                   abs(float(line["y1"]) - float(line["y0"])) >= 15)
    groups = []
    for x, length, top, bottom in lines:
        if groups and x - groups[-1][-1][0] <= 2.5:
            groups[-1].append((x, length, top, bottom))
        else:
            groups.append([(x, length, top, bottom)])
    def edge(group):
        return round(sum(x * length for x, length, _, _ in group) /
                     sum(length for _, length, _, _ in group), 1)

    strong = [group for group in groups if sum(line[1] for line in group) >= 200]
    edges = [edge(group) for group in strong]
    if _column_profile_matches(edges):
        return {"status": "confirmed", "basis": "strong_vector_rules",
                "edges_pdf_points": edges, "weak_rule_observations": []}
    unknown = {"status": "unknown", "basis": None, "edges_pdf_points": None,
               "weak_rule_observations": []}
    candidates = [group for group in groups if sum(line[1] for line in group) >= 40]
    edges = [edge(group) for group in candidates]
    if len(strong) < 5 or not _column_profile_matches(edges):
        return {**unknown, "reason": "insufficient_same_page_vector_profile"}
    # pdfplumber lines and to_image share displayed page coordinates, including
    # /Rotate. A cropped origin would need an additional transform; leave it unknown.
    if tuple(page.bbox) != (0, 0, page.width, page.height):
        return {**unknown, "reason": "unsupported_raster_coordinate_frame"}
    top = max(min(line[2] for line in group) for group in strong) + 40
    bottom = min(max(line[3] for line in group) for group in strong) - 10
    if bottom - top < 120:
        return {**unknown, "reason": "insufficient_common_grid_span"}
    weak = [group for group in candidates if sum(line[1] for line in group) < 200]
    gaps = []
    for group in weak:
        cursor, uncovered = top, []
        for _, _, start, end in sorted(group, key=lambda line: line[2]):
            start, end = max(top, start), min(bottom, end)
            if start >= end:
                continue
            if start > cursor:
                uncovered.append([cursor, start])
            cursor = max(cursor, end)
        if bottom > cursor:
            uncovered.append([cursor, bottom])
        gap = max(uncovered, key=lambda span: span[1] - span[0], default=[top, top])
        if gap[1] - gap[0] < 40:
            return {**unknown, "reason": "no_independent_pixel_witness_span"}
        gaps.append(gap)
    import numpy as np

    gray = np.asarray(page.to_image(resolution=216, force_mediabox=True).original.convert("L"))
    scale_x, scale_y = gray.shape[1] / page.width, gray.shape[0] / page.height
    confirmations = [
        {**_rule_pixel_confirmation(gray, x=edge(group), top=top, bottom=bottom,
                                   scale_x=scale_x, scale_y=scale_y),
         "vector_length_pdf_points": round(sum(line[1] for line in group), 3),
         "vector_uncovered_span_pdf_points": gap}
        for group, gap in zip(weak, gaps, strict=True)]
    if not confirmations or any(item["status"] != "confirmed" for item in confirmations):
        return {**unknown, "reason": "weak_vector_not_confirmed_by_pixels",
                "weak_rule_observations": confirmations}
    return {"status": "confirmed", "basis": "same_page_vector_and_pixel_rules",
            "edges_pdf_points": edges, "weak_rule_observations": confirmations}


def _oge_2024_column_edges(page) -> list[float] | None:
    return _oge_2024_column_geometry(page)["edges_pdf_points"]


def _observe_slot_columns(words: list[dict], edges: list[float] | None) -> dict:
    """Assign saved words by same-page columns, retaining out-of-column words."""
    if edges is None:
        return {"column_observation": None, "boundary_crossing_word_count": None,
                "unassigned_column_word_count": None}
    if len(edges) != 7 or any(left >= right for left, right in zip(edges, edges[1:])):
        raise ValueError("six-column observation requires seven ordered boundaries")
    by_column: dict[str, list[int]] = {name: [] for name in _COLUMNS}
    crossings = assigned = 0
    for index, word in enumerate(words):
        x0, _, x1, _ = word["bbox_pdf_points"]
        for name, left, right in zip(_COLUMNS, edges[:-1], edges[1:], strict=True):
            if left <= (x0 + x1) / 2 < right:
                by_column[name].append(index)
                assigned += 1
                break
        crossings += any(x0 < edge < x1 for edge in edges[1:-1])
    return {"column_observation": {
                name: {"raw_ocr_text": " ".join(words[index]["text"] for index in indices),
                       "word_indices": indices} for name, indices in by_column.items()},
            "boundary_crossing_word_count": crossings,
            "unassigned_column_word_count": len(words) - assigned}


def census_with_2024_fallback(pdf_path: Path, *, expected_sha256: str,
                              page_numbers: list[int]) -> tuple[dict, dict[int, list[float] | None]]:
    """Count raster slots, retrying only independently confirmed 2024 grids."""
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError("pdfplumber is required for 278-T grid observation") from None
    source = Path(pdf_path)
    census = probe_raster_row_bands(
        source, expected_sha256=expected_sha256, page_numbers=page_numbers)
    with pdfplumber.open(source) as document:
        geometry = {number: _oge_2024_column_geometry(document.pages[number - 1])
                    for number in page_numbers}
        edges_by_page = {number: item["edges_pdf_points"] for number, item in geometry.items()}
    retry_pages = [page["page_number"] for page in census["results"]
                   if page["status"] != "physical_slots_located" and
                   edges_by_page[page["page_number"]] is not None]
    recovered = {}
    for start in range(0, len(retry_pages), MAX_PAGES_PER_RUN):
        fallback = probe_oge_2024_alternate_bands(
            source, expected_sha256=expected_sha256,
            page_numbers=retry_pages[start:start + MAX_PAGES_PER_RUN])
        recovered.update({page["page_number"]: page for page in fallback["results"]
                          if page["status"] == "physical_slots_located"})
    census["results"] = [recovered.get(page["page_number"], page)
                         for page in census["results"]]
    for page in census["results"]:
        page["column_geometry_observation"] = geometry[page["page_number"]]
    return census, edges_by_page


def observe_raster_slots(pdf_path: Path, *, expected_sha256: str,
                         page_numbers: list[int],
                         executable: str | None = None,
                         resolution: int = 200) -> dict:
    """Retain OCR words by independently located physical slot.

    OCR words are observations only.  A slot without recognized words may be
    blank or unreadable, and a slot with words is not yet a transaction.
    """
    if (not page_numbers or len(page_numbers) > MAX_PAGES_PER_RUN or
            any(type(number) is not int or number < 1 for number in page_numbers) or
            len(set(page_numbers)) != len(page_numbers)):
        raise ValueError("278-T raster observation needs 1-8 distinct pages")
    if type(resolution) is not int or not 150 <= resolution <= 400:
        raise ValueError("278-T raster observation DPI is outside bounds")
    source = Path(pdf_path)
    actual_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError("278-T raster observation source SHA-256 differs")
    census, edges_by_page = census_with_2024_fallback(
        source, expected_sha256=expected_sha256, page_numbers=page_numbers)
    import pdfplumber
    with pdfplumber.open(source) as document:
        known_pages = [page["page_number"] for page in census["results"]
                       if page["status"] == "physical_slots_located"]
        ocr_pages, engine = (ocr_pdf_pages(
            document, page_numbers=known_pages, max_pages=MAX_PAGES_PER_RUN,
            resolution=resolution, executable=executable)
            if known_pages else ([], None))
    ocr_by_page = dict(zip(known_pages, ocr_pages, strict=True))
    pages = []
    for page in census["results"]:
        number = page["page_number"]
        if page["status"] != "physical_slots_located":
            pages.append({"page_number": number,
                          "coverage": "physical_slots_unknown",
                          "physical_slot_count": None,
                          "column_geometry_observation": page["column_geometry_observation"],
                          "slots": [], "ocr_word_count": None})
            continue
        words = ocr_by_page[number].extract_words()
        column_edges = edges_by_page[number]
        slots = []
        assigned: set[int] = set()
        for index, band in enumerate(page["physical_row_bands"], start=1):
            top, bottom = band
            selected = [
                (position, word) for position, word in enumerate(words)
                if top <= (float(word["top"]) + float(word["bottom"])) / 2 < bottom]
            assigned.update(position for position, _ in selected)
            ordered = sorted((word for _, word in selected),
                             key=lambda word: (float(word["top"]), float(word["x0"])))
            observations = [{"text": str(word["text"]),
                             "bbox_pdf_points": [round(float(word[key]), 2)
                                                 for key in ("x0", "top", "x1", "bottom")],
                             "confidence": round(float(word["ocr_confidence"]), 2)}
                            for word in ordered]
            slots.append({"slot_index": index,
                          "band_pdf_points": band,
                          "ocr_status": ("words_observed" if observations else
                                         "no_words_observed_unknown_content"),
                          "raw_ocr_text": " ".join(item["text"] for item in observations),
                          **_observe_slot_columns(observations, column_edges),
                          "words": observations})
        pages.append({"page_number": number,
                      "coverage": "physical_slots_located",
                      "row_detection_basis": page.get(
                          "row_detection_basis", "three_strip_grid"),
                      "column_geometry": ("oge_2024_six_column_grid" if column_edges
                                          else "unknown"),
                      "column_edges_pdf_points": column_edges,
                      "column_geometry_observation": page["column_geometry_observation"],
                      "physical_slot_count": len(slots),
                      "slots": slots,
                      "ocr_word_count": len(words),
                      "words_outside_slots": len(words) - len(assigned)})
    return {"schema_version": SCHEMA,
            "source_sha256": actual_sha256,
            "selected_page_numbers": page_numbers,
            "ocr_engine": engine,
            "ocr_resolution_dpi": resolution,
            "confirmed_physical_slot_count": sum(
                page["physical_slot_count"] or 0 for page in pages),
            "slots_with_ocr_words": sum(
                slot["ocr_status"] == "words_observed"
                for page in pages for slot in page["slots"]),
            "pages_with_column_geometry": sum(
                page.get("column_geometry") == "oge_2024_six_column_grid"
                for page in pages),
            "slots_with_column_observation": sum(
                slot["column_observation"] is not None
                for page in pages for slot in page["slots"]),
            "pages": pages,
            "production_mutation_authorized": False}
