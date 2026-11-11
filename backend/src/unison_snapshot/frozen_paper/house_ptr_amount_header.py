"""Source-bound readings of printed House PTR amount-column headers.

OCR is used only to recognize the form's printed amount legend. Row marks and
transaction facts remain unverified and cannot be promoted by this module.
"""

from __future__ import annotations

import hashlib
import io
import re
import subprocess
import tempfile
from pathlib import Path


LABELS = {
    "B": "$15,001-$50,000", "C": "$50,001-$100,000",
    "D": "$100,001-$250,000", "E": "$250,001-$500,000",
    "F": "$500,001-$1,000,000", "G": "$1,000,001-$5,000,000",
    "H": "$5,000,001-$25,000,000", "I": "$25,000,001-$50,000,000",
    "J": "Over$50,000,000", "K": "Spouse/DCAmountover$1,000,000",
}
RANGES = {
    "B": (15001, 50000), "C": (50001, 100000),
    "D": (100001, 250000), "E": (250001, 500000),
    "F": (500001, 1000000), "G": (1000001, 5000000),
    "H": (5000001, 25000000), "I": (25000001, 50000000),
    "J": (50000001, None), "K": (1000001, None),
}


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("§", "$")


def evaluate_amount_header_pages(source_sha256: str, pages: list[dict]) -> dict:
    """Require corroborating printed legends across every located page."""
    if not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise ValueError("House paper source hash is invalid")
    if not isinstance(pages, list) or not pages:
        raise ValueError("House paper header pages are missing")
    expected_pages = list(range(1, len(pages) + 1))
    if [page.get("page") for page in pages] != expected_pages:
        raise ValueError("House paper header page census is invalid")
    observed = [page for page in pages if page.get("status") == "header_ocr_observed"]
    required = max(1, len(pages) // 2 + 1)
    column_counts = {page.get("amount_column_count", 11) for page in observed}
    columns = next(iter(column_counts)) if len(column_counts) == 1 else None
    labels = [label for label in LABELS if label != "K" or columns == 11]
    counts = {label: 0 for label in ["A1000", "A1001", *LABELS]}
    for page in observed:
        raw = page.get("raw_header_text")
        if not isinstance(raw, str):
            raise ValueError("House paper header OCR text is invalid")
        value = _normalized(raw)
        counts["A1000"] += "$1,000-$15,000" in value
        counts["A1001"] += "$1,001-$15,000" in value
        for label, printed in LABELS.items():
            counts[label] += printed in value
    first = ("A1000" if counts["A1000"] >= required and counts["A1001"] < required
             else "A1001" if counts["A1001"] >= required and counts["A1000"] < required
             else None)
    verified = (len(observed) == len(pages) and first is not None and columns in {10, 11}
                and all(counts[label] >= required for label in labels))
    ranges = None
    if verified:
        ranges = {"A": {"low": 1000 if first == "A1000" else 1001, "high": 15000,
                        "kind": "range"}}
        ranges.update({label: {"low": low, "high": high,
                               "kind": "range" if high is not None else
                               "spouse_dc_special_open_ended" if label == "K" else "open_ended"}
                       for label, (low, high) in RANGES.items() if label in labels})
    return {"schema_version": "house-ptr-amount-header-observation/v1",
            "source_sha256": source_sha256, "page_count": len(pages),
            "status": "printed_header_correlated" if verified else "header_unresolved",
            "required_matching_pages": required, "label_match_counts": counts,
            "printed_a_variant": first if verified else None,
            "ranges": ranges, "pages": pages,
            **({"amount_column_count": 10} if columns == 10 else {})}


def observe_amount_header(pdf_bytes: bytes, *, source_sha256: str,
                          executable: str, dpi: int = 300,
                          max_pages: int = 12, page_numbers: list[int] | None = None,
                          column_labels: list[str] | None = None) -> dict:
    """Read each archived page's amount legend in its original page frame."""
    if hashlib.sha256(pdf_bytes).hexdigest() != source_sha256:
        raise ValueError("House paper amount header source hash mismatch")
    if type(dpi) is not int or dpi not in (200, 300):
        raise ValueError("House paper amount header DPI is unsupported")
    import pdfplumber

    from .house_ptr_cell_observation import _column_boxes
    from .house_ptr_table_region import locate_transaction_grid

    if column_labels is not None and (not isinstance(column_labels, list) or not column_labels or
            len(column_labels) > 2 or any(not isinstance(label, str) or len(label) != 1 or
                label not in "ABCDEFGHIJK" for label in column_labels) or
            len(set(column_labels)) != len(column_labels) or
            not isinstance(page_numbers, list) or not 1 <= len(page_numbers) <= 2 or dpi != 300):
        raise ValueError("House selected column reading exceeds bounds")
    pages = []
    column_readings = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if not 1 <= len(pdf.pages) <= max_pages:
            raise ValueError("House paper amount header page count is outside bounds")
        if page_numbers is not None and (not isinstance(page_numbers, list) or not page_numbers or
                any(type(number) is not int or not 1 <= number <= len(pdf.pages) for number in page_numbers) or
                len(set(page_numbers)) != len(page_numbers)):
            raise ValueError("House paper selected header pages are invalid")
        selected = set(page_numbers) if page_numbers is not None else set(range(1, len(pdf.pages) + 1))
        for number, page in enumerate(pdf.pages, 1):
            if number not in selected:
                pages.append({"page": number, "status": "header_not_selected"})
                continue
            image = page.to_image(resolution=dpi).original.convert("L")
            region = locate_transaction_grid(image)
            if (region.get("status") != "located_shadow" or
                    region.get("coordinate_frame") != "original_page"):
                pages.append({"page": number, "status": "header_geometry_unresolved"})
                continue
            columns = _column_boxes(region, image)
            if columns.get("status") != "located_shadow" or len(columns["amount"]) not in (11, 12):
                pages.append({"page": number, "status": "header_columns_unresolved"})
                continue
            if column_labels is not None:
                for label in column_labels:
                    index = ord(label) - ord("A")
                    if index + 1 >= len(columns["amount"]):
                        continue
                    left, right = columns["amount"][index:index + 2]
                    upper, lower = region["header_band"]
                    box = [int(left * image.width) + 2, int((upper + .005) * image.height),
                           int(right * image.width) - 2, int((lower - .005) * image.height)]
                    crop = image.crop(tuple(box)).rotate(270, expand=True)
                    encoded = io.BytesIO()
                    crop.save(encoded, format="PNG")
                    completed = subprocess.run([executable, "stdin", "stdout", "--dpi", "300",
                        "--psm", "6", "-l", "eng"], input=encoded.getvalue(),
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False)
                    reading = {"page": number, "label": label, "bbox_pixels": box,
                        "amount_column_count": len(columns["amount"]) - 1,
                        "render_dpi": 300, "image_rotation_degrees": 270,
                        "coordinate_frame": "original_page", "crop_sha256": hashlib.sha256(encoded.getvalue()).hexdigest(),
                        "raw": completed.stdout.decode("utf-8", "replace").strip() if not completed.returncode else "",
                        "status": "raw_column_observed" if not completed.returncode else "column_ocr_failed"}
                    column_readings.append(reading)
                pages.append({"page": number, "status": "selected_columns_observed",
                              "amount_column_count": len(columns["amount"]) - 1})
                continue
            x0, x1 = columns["amount"][0], columns["amount"][-1]
            y0, y1 = region["header_band"]
            width, height = image.size
            # Preserve whitespace around the ruled table: Tesseract returns no
            # text when the crop ends directly on the outer vertical rules.
            crop = image.crop((int(max(0, x0 - .015) * width),
                               int((y0 + .005) * height),
                               int(min(1, x1 + .03) * width),
                               int((y1 - .005) * height))).rotate(270, expand=True)
            output = io.BytesIO()
            crop.save(output, format="PNG")
            with tempfile.TemporaryDirectory(prefix="house-amount-header-") as folder:
                image_path = Path(folder) / "header.png"
                image_path.write_bytes(output.getvalue())
                completed = subprocess.run(
                    [executable, str(image_path), "stdout", "--psm", "11", "-l", "eng"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=120, check=False)
            if completed.returncode:
                pages.append({"page": number, "status": "header_ocr_failed",
                              "crop_sha256": hashlib.sha256(output.getvalue()).hexdigest()})
                continue
            pages.append({"page": number, "status": "header_ocr_observed",
                          **({"amount_column_count": 10} if len(columns["amount"]) == 11 else {}),
                          "crop_sha256": hashlib.sha256(output.getvalue()).hexdigest(),
                          "raw_header_text": completed.stdout.decode("utf-8", errors="replace")})
    result = evaluate_amount_header_pages(source_sha256, pages)
    if column_labels is not None:
        result["reading_mode"] = "printed_columns/v1"
        result["column_readings"] = column_readings
    return result


def interpret_amount_column(reading: dict) -> dict | None:
    """Interpret only actual currency numerals in one positioned official column.

    The selected column, not a digit substitution, supplies the expected range.
    Rotated header-letter OCR noise is preserved in raw but is not a numeral.
    """
    if reading.get("status") != "raw_column_observed" or not isinstance(reading.get("raw"), str):
        return None
    label, raw = reading.get("label"), reading["raw"]
    currency = re.compile(r"\$\s*([0-9][0-9,. ]*)")
    tokens = currency.findall(raw)
    if re.search(r"\d", currency.sub("", raw)):
        return None
    values = []
    for token in tokens:
        token = token.strip()
        if not re.fullmatch(r"[0-9]+|[0-9]{1,3}(?:[, .][0-9]{3})+", token):
            return None
        values.append(int(re.sub(r"[, .]", "", token)))
    if label == "A":
        valid = len(values) == 2 and values in ([1000, 15000], [1001, 15000])
    else:
        bounds = RANGES.get(label)
        valid = bounds is not None and bounds[1] is not None and values == list(bounds)
    if valid:
        return {"letter": label, "low": values[0], "high": values[1], "kind": "range"}
    threshold = 50000000 if label == "J" else 1000000 if label == "K" else None
    role_bound = label != "K" or bool(re.search(r"spouse\s*/\s*dc", raw, re.I))
    over = re.search(r"\bOver\b", raw, re.I) or (label == "K" and re.search(r"Amount\s*over", raw, re.I))
    if threshold is not None and values == [threshold] and over and role_bound:
        return {"letter": label, "low": threshold, "high": None, "low_exclusive": True,
                "kind": "open_ended" if label == "J" else "spouse_dc_special_open_ended"}
    return None
