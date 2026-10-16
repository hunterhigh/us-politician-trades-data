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


def _oge_2024_column_edges(page) -> list[float] | None:
    """Locate all seven ruled boundaries of the 2024 six-column form.

    A missing line makes the page unknown; adjacent page geometry is never
    copied into it.  OCR text has no role in locating these boundaries.
    """
    lines = sorted((float(line["x0"]), abs(float(line["y1"]) - float(line["y0"])))
                   for line in page.lines
                   if abs(float(line["x1"]) - float(line["x0"])) <= 1 and
                   abs(float(line["y1"]) - float(line["y0"])) >= 15)
    groups: list[list[tuple[float, float]]] = []
    for x, length in lines:
        if groups and x - groups[-1][-1][0] <= 2.5:
            groups[-1].append((x, length))
        else:
            groups.append([(x, length)])
    edges = [round(sum(x * length for x, length in group) /
                   sum(length for _, length in group), 1)
             for group in groups if sum(length for _, length in group) >= 200]
    if len(edges) != 7 or not 80 <= edges[0] <= 120 or \
            not 675 <= edges[-1] <= 710:
        return None
    gaps = [right - left for left, right in zip(edges, edges[1:])]
    expected = (45, 310, 51, 57, 47, 82)
    tolerance = (12, 18, 12, 12, 12, 18)
    if any(abs(gap - target) > limit
           for gap, target, limit in zip(gaps, expected, tolerance, strict=True)):
        return None
    return edges


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
        edges_by_page = {number: _oge_2024_column_edges(document.pages[number - 1])
                         for number in page_numbers}
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
            column_observation = None
            boundary_crossing = None
            unassigned_column_words = None
            if column_edges is not None:
                by_column: dict[str, list[int]] = {name: [] for name in _COLUMNS}
                boundary_crossing = 0
                assigned_columns = 0
                for word_index, word in enumerate(observations):
                    x0, _, x1, _ = word["bbox_pdf_points"]
                    midpoint = (x0 + x1) / 2
                    for name, left, right in zip(
                            _COLUMNS, column_edges, column_edges[1:], strict=True):
                        if left <= midpoint < right:
                            by_column[name].append(word_index)
                            assigned_columns += 1
                            break
                    boundary_crossing += any(x0 < edge < x1
                                             for edge in column_edges[1:-1])
                column_observation = {
                    name: {"raw_ocr_text": " ".join(
                        observations[index]["text"] for index in indices),
                           "word_indices": indices}
                    for name, indices in by_column.items()}
                unassigned_column_words = len(observations) - assigned_columns
            slots.append({"slot_index": index,
                          "band_pdf_points": band,
                          "ocr_status": ("words_observed" if observations else
                                         "no_words_observed_unknown_content"),
                          "raw_ocr_text": " ".join(item["text"] for item in observations),
                          "column_observation": column_observation,
                          "boundary_crossing_word_count": boundary_crossing,
                          "unassigned_column_word_count": unassigned_column_words,
                          "words": observations})
        pages.append({"page_number": number,
                      "coverage": "physical_slots_located",
                      "row_detection_basis": page.get(
                          "row_detection_basis", "three_strip_grid"),
                      "column_geometry": ("oge_2024_six_column_grid" if column_edges
                                          else "unknown"),
                      "column_edges_pdf_points": column_edges,
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
