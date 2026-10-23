"""Structure-only OCR recovery; transformed pixels never supply financial facts."""
from __future__ import annotations

import hashlib
from pathlib import Path


def _rule_runs(gray, minimum):
    import numpy as np
    dark = gray < 160
    result = np.zeros_like(dark)
    for source, target in ((dark, result), (dark.T, result.T)):
        for line, mask in zip(source, target):
            transitions = np.diff(np.r_[False, line, False].astype("int8"))
            starts, ends = np.flatnonzero(transitions == 1), np.flatnonzero(transitions == -1)
            for start, end in zip(starts, ends):
                if end - start >= minimum:
                    mask[start:end] = True
    return result


def recover_header_words(pdf_path: Path, *, source_sha256: str, page_number: int,
                         geometry: dict, missing_columns: list[int], resolution: int,
                         executable: str | None) -> tuple[list[dict], dict]:
    """Read missing header columns only, retaining source and transform hashes.

    Long dark runs are an OCR aid, not a proof that removed ink was a rule.
    These words must only be passed to the existing header-role checker.
    """
    import numpy as np
    import pdfplumber
    from PIL import Image
    from .ocr_geometry import ocr_rendered_image
    from .oge_278t_raster_slots import column_edges_at_y

    if (type(resolution) is not int or not 150 <= resolution <= 400 or
            not missing_columns or len(set(missing_columns)) != len(missing_columns) or
            any(type(c) is not int or not 1 <= c <= 5 for c in missing_columns) or
            geometry.get("basis") != "same_page_image_grid" or geometry.get("partial_row_geometry")):
        raise ValueError("Header recovery requires a bounded original image grid")
    source = Path(pdf_path)
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_sha256:
        raise ValueError("Header recovery PDF differs from archived source")
    with pdfplumber.open(source) as pdf:
        if type(page_number) is not int or not 1 <= page_number <= len(pdf.pages):
            raise ValueError("Header recovery page is outside source")
        page = pdf.pages[page_number - 1]
        image = page.to_image(resolution=resolution, force_mediabox=True).original.convert("L")
        sx, sy = image.width / page.width, image.height / page.height
    raw = np.asarray(image)
    minimum = max(1, round(24 * min(sx, sy)))
    rules = _rule_runs(raw, minimum)
    transformed = raw.copy()
    transformed[rules] = 255
    top, bottom = geometry["header_band_pdf_points"]
    shape = {**geometry, "column_edges_pdf_points": geometry["edges_pdf_points"]}
    edges = column_edges_at_y(shape, (top + bottom) / 2)
    words, crops = [], []
    receipt = {"source_sha256": source_sha256, "page_number": page_number,
        "resolution": resolution, "coordinate_frame": "displayed_mediabox",
        "source_pixel_size": list(image.size), "source_grayscale_sha256": hashlib.sha256(raw.tobytes()).hexdigest(),
        "rule_mask_sha256": hashlib.sha256(rules.tobytes()).hexdigest(), "minimum_run_pdf_points": 24,
        "use": "header_structure_only", "financial_fact_resolution": "not_assessed", "crops": crops}
    for column in sorted(missing_columns):
        bounds = [round((edges[column] + .75) * sx), round((top - 3) * sy),
                  round((edges[column + 1] - .75) * sx), round((bottom + 3) * sy)]
        left, upper, right, lower = bounds
        if not (0 <= left < right <= image.width and 0 <= upper < lower <= image.height):
            raise ValueError("Header recovery crop is outside original page")
        crop = Image.fromarray(transformed[upper:lower, left:right])
        observed, engine = ocr_rendered_image(crop, executable=executable,
                                              resolution=resolution, page_segmentation=6)
        # Use the actual pixel mapping; OCR local units are nominal DPI units.
        mapped = [{**w, "x0": left/sx + w["x0"]*resolution/72/sx,
            "x1": left/sx + w["x1"]*resolution/72/sx,
            "top": upper/sy + w["top"]*resolution/72/sy,
            "bottom": upper/sy + w["bottom"]*resolution/72/sy}
            for w in observed.extract_words()]
        words.extend(mapped)
        crops.append({"column": column, "pixel_bounds": bounds, "engine": engine,
            "transformed_pixels_sha256": hashlib.sha256(crop.tobytes()).hexdigest(), "words": mapped})
    return words, receipt
