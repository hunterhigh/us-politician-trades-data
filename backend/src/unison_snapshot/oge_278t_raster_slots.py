"""Bounded, read-only OCR observations inside confirmed raster 278-T slots."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .ocr_geometry import ocr_pdf_pages
from .oge_278t_page_shadow import probe_raster_row_bands


SCHEMA = "oge-278t-raster-slot-observation/v1"
MAX_PAGES_PER_RUN = 8


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
    census = probe_raster_row_bands(
        source, expected_sha256=expected_sha256, page_numbers=page_numbers)
    known_pages = [page["page_number"] for page in census["results"]
                   if page["status"] == "physical_slots_located"]
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError("pdfplumber is required for 278-T OCR observation") from None
    with pdfplumber.open(source) as document:
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
                          "words": observations})
        pages.append({"page_number": number,
                      "coverage": "physical_slots_located",
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
            "pages": pages,
            "production_mutation_authorized": False}
