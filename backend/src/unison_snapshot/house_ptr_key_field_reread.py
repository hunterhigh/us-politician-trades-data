"""Bounded second OCR pass for doubtful House paper asset and event cells.

The result is evidence for later source checks, never a corrected field or a
qualification decision. The first OCR reading remains unchanged on conflict.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
from pathlib import Path

from .house_ptr_key_fields_shadow import read_house_ptr_key_fields


def _second_ocr(crop, executable: Path) -> tuple[str, str]:
    encoded = io.BytesIO()
    crop.save(encoded, format="PNG")
    completed = subprocess.run(
        [str(executable), "stdin", "stdout", "--dpi", "300", "-l", "eng",
         "--psm", "7", "tsv"], input=encoded.getvalue(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False)
    if completed.returncode:
        raise ValueError("House paper second OCR pass failed")
    lines = completed.stdout.decode("utf-8", "replace").splitlines()
    if not lines or lines[0].split("\t") != [
            "level", "page_num", "block_num", "par_num", "line_num", "word_num",
            "left", "top", "width", "height", "conf", "text"]:
        raise ValueError("House paper second OCR TSV is invalid")
    words = []
    for line in lines[1:]:
        parts = line.split("\t", 11)
        if len(parts) != 12:
            raise ValueError("House paper second OCR TSV row is invalid")
        if parts[0] == "5" and parts[11].strip():
            words.append(parts[11].strip())
    return " ".join(words), hashlib.sha256(encoded.getvalue()).hexdigest()


def _conflict_probe(crop, executable: Path, field: str) -> dict:
    """Keep one alternate image reading for a disputed cell, never a correction."""
    from PIL import ImageOps

    if field == "event_date":
        image = ImageOps.expand(crop, border=20, fill="white")
        page_segmentation_mode = 7
    else:
        image = crop
        page_segmentation_mode = 11
    encoded = io.BytesIO()
    image.save(encoded, format="PNG")
    completed = subprocess.run(
        [str(executable), "stdin", "stdout", "--dpi", "300", "-l", "eng",
         "--psm", str(page_segmentation_mode)], input=encoded.getvalue(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False)
    if completed.returncode:
        raise ValueError("House paper conflict OCR probe failed")
    return {"raw": completed.stdout.decode("utf-8", "replace").strip(),
            "crop_sha256": hashlib.sha256(encoded.getvalue()).hexdigest(),
            "render_dpi": 300, "page_segmentation_mode": page_segmentation_mode,
            "image_border_pixels": 20 if field == "event_date" else 0,
            "status": "alternate_ocr_unverified"}


def _field_shape(value: str, name: str) -> str | None:
    if name == "asset":
        return re.sub(r"[^a-z0-9]", "", value.casefold()) or None
    dates = re.findall(r"(?<!\d)\d{1,2}/\d{1,2}/\d{4}(?!\d)", value)
    return dates[0] if len(dates) == 1 else None


def reread_doubtful_key_cells(pdf_bytes: bytes, observation: dict, *,
                             executable: Path, max_cells: int = 32) -> dict:
    """Reread only flagged asset/event crops, with a strict per-document cap."""
    source_sha = hashlib.sha256(pdf_bytes).hexdigest()
    if source_sha != observation.get("source_sha256"):
        raise ValueError("House paper second OCR source hash mismatch")
    if type(max_cells) is not int or not 1 <= max_cells <= 64:
        raise ValueError("House paper second OCR cap is invalid")
    reading = read_house_ptr_key_fields(observation)
    pages = {page["page"]: page for page in observation["pages"]}
    requests = []
    for row in reading["rows"]:
        for name in ("asset", "event_date"):
            if any(flag.startswith(name + "_") for flag in row["source_quality_flags"]):
                requests.append((row, name))
    if len(requests) > max_cells:
        raise ValueError("House paper second OCR selection exceeds cap")
    import pdfplumber

    rereads = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if len(pdf.pages) != len(pages):
            raise ValueError("House paper second OCR page count differs")
        rendered = {}
        for row, name in requests:
            number = row["page"]
            page = pages[number]
            if (page.get("coordinate_frame") != "original_page" or
                    page.get("orientation_degrees") not in (None, 0)):
                raise ValueError("House paper second OCR rotated page is unsupported")
            if number not in rendered:
                rendered[number] = pdf.pages[number - 1].to_image(
                    resolution=300).original.convert("L")
            image = rendered[number]
            cell = next(physical["cells"][name] for physical in page["rows"]
                        if physical["physical_band_index"] ==
                        row["physical_band_index"])
            box = cell.get("bbox_normalized")
            if (not isinstance(box, list) or len(box) != 4 or
                    not all(isinstance(value, (int, float)) for value in box) or
                    not 0 <= box[0] < box[2] <= 1 or
                    not 0 <= box[1] < box[3] <= 1):
                raise ValueError("House paper second OCR cell geometry is invalid")
            crop = image.crop((round(box[0] * image.width) + 3,
                               round(box[1] * image.height) + 3,
                               round(box[2] * image.width) - 3,
                               round(box[3] * image.height) - 3))
            if crop.width <= 0 or crop.height <= 0:
                raise ValueError("House paper second OCR crop is empty")
            second, crop_sha = _second_ocr(crop, executable)
            first = cell.get("raw_text", "")
            first_shape = _field_shape(first, name)
            second_shape = _field_shape(second, name)
            comparison = ("second_ocr_shape_agrees" if first_shape is not None and
                          first_shape == second_shape else "second_ocr_shape_differs")
            conflict_probe = None
            if comparison == "second_ocr_shape_differs":
                try:
                    conflict_probe = _conflict_probe(crop, executable, name)
                    conflict_probe["shape_comparison_to_first"] = (
                        "agrees" if first_shape is not None and
                        _field_shape(conflict_probe["raw"], name) == first_shape
                        else "differs_or_unknown")
                except (OSError, subprocess.TimeoutExpired, ValueError) as error:
                    conflict_probe = {"status": "alternate_ocr_unavailable",
                                      "error": f"{type(error).__name__}: {str(error)[:160]}"}
            rereads.append({"page": number,
                            "physical_band_index": row["physical_band_index"],
                            "field": name, "first_ocr_raw": first,
                            "first_crop_sha256": cell.get("crop_sha256"),
                            "second_ocr_raw": second,
                            "second_crop_sha256": crop_sha,
                            "second_crop_render_dpi": 300,
                            "comparison": comparison,
                            "conflict_probe": conflict_probe,
                            "literal_comparison": "agree" if first == second else "differ",
                            "source_quality_flags": [flag for flag in row[
                                "source_quality_flags"] if flag.startswith(name + "_")]})
    return {"schema_version": "house-paper-key-field-reread/v1",
            "source_sha256": source_sha, "qualification_status": "unverified_shadow_only",
            "selected_cell_count": len(rereads), "rereads": rereads}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--tesseract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-cells", type=int, default=32)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists")
    result = reread_doubtful_key_cells(
        args.pdf.read_bytes(), json.loads(args.observation.read_text(encoding="utf-8")),
        executable=args.tesseract, max_cells=args.max_cells)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
                           + "\n", encoding="utf-8")
    print(json.dumps({"selected_cell_count": result["selected_cell_count"],
                      "shape_conflict_count": sum(
                          row["comparison"] == "second_ocr_shape_differs"
                          for row in result["rereads"])}, sort_keys=True))


if __name__ == "__main__":
    main()
