"""Plan and prepare bounded secondary House paper asset/event evidence.

The result is evidence for later source checks, never a corrected field or a
qualification decision. The first OCR reading remains unchanged on conflict.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import subprocess
from pathlib import Path

from .house_ptr_key_fields import read_house_ptr_key_fields

REREAD_RULE_VERSION = "house-paper-key-field-conflict-probe-v2"
MATERIALIZATION_SELECTION = "materialization"
DOUBTFUL_SELECTION = "doubtful"


def plan_key_field_rereads(observation: dict, *, selection_mode: str = MATERIALIZATION_SELECTION,
                          max_cells: int = 32) -> dict:
    """Select consumer requirements before rendering or starting any recognizer.

    Every readable data slot needs an asset corroboration regardless of OCR
    confidence. Existing flagged event diagnostics remain in the same budget.
    Unlocated slots remain unknown; they cannot supply a crop request.
    """
    if selection_mode not in (MATERIALIZATION_SELECTION, DOUBTFUL_SELECTION):
        raise ValueError("House paper second OCR selection mode is invalid")
    if type(max_cells) is not int or not 1 <= max_cells <= 64:
        raise ValueError("House paper second OCR cap is invalid")
    source = observation.get("source_sha256")
    if not isinstance(source, str) or re.fullmatch(r"[0-9a-f]{64}", source) is None:
        raise ValueError("House paper second OCR source binding is invalid")
    reading = read_house_ptr_key_fields(observation)
    pages = {}
    for page in observation["pages"]:
        number = page["page"]
        if type(number) is not int or number < 1 or number in pages:
            raise ValueError("House paper second OCR page is invalid or duplicated")
        pages[number] = page
    requests = []
    seen = set()
    for row in reading["rows"]:
        key = row["page"], row["physical_band_index"]
        if type(key[1]) is not int or key[1] < 1 or key in seen:
            raise ValueError("House paper second OCR slot is invalid or duplicated")
        seen.add(key)
        if row.get("fields") is None:
            continue
        page = pages[row["page"]]
        physical = next(p for p in page["rows"] if p["physical_band_index"] == key[1])
        for name in ("asset", "event_date"):
            flags = [f for f in row.get("source_quality_flags", []) if f.startswith(name + "_")]
            required = name == "asset" and selection_mode == MATERIALIZATION_SELECTION
            if not required and not flags:
                continue
            if (page.get("coordinate_frame") != "original_page" or
                    page.get("orientation_degrees") not in (None, 0)):
                raise ValueError("House paper second OCR rotated page is unsupported")
            cell = physical["cells"][name]
            box = cell.get("bbox_normalized")
            if (not isinstance(box, list) or len(box) != 4 or
                    not all(type(v) in (int, float) and math.isfinite(v) for v in box) or
                    not 0 <= box[0] < box[2] <= 1 or not 0 <= box[1] < box[3] <= 1):
                raise ValueError("House paper second OCR cell geometry is invalid")
            crop_sha = cell.get("crop_sha256")
            if not isinstance(cell.get("raw_text", ""), str):
                raise ValueError("House paper second OCR first text is invalid")
            if not isinstance(crop_sha, str) or re.fullmatch(r"[0-9a-f]{64}", crop_sha) is None:
                raise ValueError("House paper second OCR first crop binding is invalid")
            requests.append({"page": key[0], "physical_band_index": key[1], "field": name,
                "first_ocr_raw": cell.get("raw_text", ""), "first_crop_sha256": crop_sha,
                "bbox_normalized": list(box), "source_quality_flags": flags,
                "selection_reasons": (["asset_corroboration_required"] if required else []) + flags})
    requests.sort(key=lambda r: (r["page"], r["physical_band_index"], r["field"]))
    if len(requests) > max_cells:
        raise ValueError("House paper second OCR selection exceeds cap")
    return {"schema_version": "house-paper-key-field-reread-plan/v1", "source_sha256": source,
            "selection_mode": selection_mode, "required_cell_count": len(requests), "requests": requests}


def missing_key_field_rereads(plan: dict, cached: dict | None) -> list[dict]:
    """Check preparation coverage, leaving pixel/fact checks to the consumer.

    Complete legacy caches are reusable without a new receipt. Their source,
    first reading and crop must still bind; materialization rechecks pixels.
    """
    if cached is None:
        return list(plan["requests"])
    if (not isinstance(cached, dict) or
            cached.get("schema_version") != "house-paper-key-field-reread/v1" or
            cached.get("rule_version") != REREAD_RULE_VERSION or
            cached.get("source_sha256") != plan["source_sha256"] or
            cached.get("qualification_status") != "unverified_shadow_only" or
            not isinstance(cached.get("rereads"), list) or
            type(cached.get("selected_cell_count")) is not int or
            cached["selected_cell_count"] != len(cached["rereads"])):
        raise ValueError("House paper second reading cache source, version or count mismatch")
    by_key = {}
    for item in cached["rereads"]:
        if not isinstance(item, dict):
            raise ValueError("House paper second reading cache has an invalid record")
        key = item.get("page"), item.get("physical_band_index"), item.get("field")
        if (type(key[0]) is not int or type(key[1]) is not int or min(key[:2]) < 1 or
                not isinstance(key[2], str) or key in by_key):
            raise ValueError("House paper second reading cache has invalid or duplicate keys")
        by_key[key] = item
    missing = []
    for request in plan["requests"]:
        item = by_key.get((request["page"], request["physical_band_index"], request["field"]), {})
        if (item.get("first_ocr_raw") != request["first_ocr_raw"] or
                item.get("first_crop_sha256") != request["first_crop_sha256"] or
                item.get("second_crop_render_dpi") != 300 or
                not isinstance(item.get("second_ocr_raw"), str) or
                not re.fullmatch(r"[0-9a-f]{64}", str(item.get("second_crop_sha256", ""))) or
                ("source_request" in item and (not isinstance(item["source_request"], dict) or
                    any(item["source_request"].get(k) != request[k] for k in (
                        "page", "physical_band_index", "field", "first_ocr_raw",
                        "first_crop_sha256", "bbox_normalized"))))):
            missing.append(request)
    return missing


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
                             executable: Path, max_cells: int = 32,
                             selection_mode: str = DOUBTFUL_SELECTION,
                             existing_reread: dict | None = None) -> dict:
    """Execute a bounded plan; retain existing evidence and read only missing cells."""
    from copy import deepcopy
    source_sha = hashlib.sha256(pdf_bytes).hexdigest()
    if source_sha != observation.get("source_sha256"):
        raise ValueError("House paper second OCR source hash mismatch")
    plan = plan_key_field_rereads(observation, selection_mode=selection_mode, max_cells=max_cells)
    requests = missing_key_field_rereads(plan, existing_reread)
    if existing_reread is not None and not requests:
        return deepcopy(existing_reread)
    if not requests:
        return {"schema_version": "house-paper-key-field-reread/v1", "rule_version": REREAD_RULE_VERSION,
                "source_sha256": source_sha, "qualification_status": "unverified_shadow_only",
                "selected_cell_count": 0, "rereads": [], "selection_plan": plan}
    previous = deepcopy(existing_reread["rereads"]) if existing_reread is not None else []
    replaced = {(r["page"], r["physical_band_index"], r["field"]) for r in requests}
    rereads = [r for r in previous if (r["page"], r["physical_band_index"], r["field"]) not in replaced]
    pages = {page["page"]: page for page in observation["pages"]}
    import pdfplumber

    # Render and validate every selected crop before the first OCR invocation.
    crops = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if len(pdf.pages) != len(pages):
            raise ValueError("House paper second OCR page count differs")
        if set(pages) != set(range(1, len(pdf.pages) + 1)):
            raise ValueError("House paper second OCR page numbers differ")
        rendered = {}
        for request in requests:
            number = request["page"]
            if number not in rendered:
                rendered[number] = pdf.pages[number - 1].to_image(
                    resolution=300).original.convert("L")
            image = rendered[number]
            box = request["bbox_normalized"]
            crop = image.crop((round(box[0] * image.width) + 3,
                               round(box[1] * image.height) + 3,
                               round(box[2] * image.width) - 3,
                               round(box[3] * image.height) - 3))
            if crop.width <= 0 or crop.height <= 0:
                raise ValueError("House paper second OCR crop is empty")
            crops.append((request, crop))
    for request, crop in crops:
        name = request["field"]
        second, crop_sha = _second_ocr(crop, executable)
        first = request["first_ocr_raw"]
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
        rereads.append({"page": request["page"],
                        "physical_band_index": request["physical_band_index"],
                        "field": name, "first_ocr_raw": first,
                        "first_crop_sha256": request["first_crop_sha256"],
                        "second_ocr_raw": second,
                        "second_crop_sha256": crop_sha,
                        "second_crop_render_dpi": 300,
                        "comparison": comparison,
                        "conflict_probe": conflict_probe,
                        "literal_comparison": "agree" if first == second else "differ",
                        "source_quality_flags": request["source_quality_flags"],
                        "source_request": request})
    rereads.sort(key=lambda r: (r["page"], r["physical_band_index"], r["field"]))
    result = {**(deepcopy(existing_reread) if existing_reread is not None else {}),
            "schema_version": "house-paper-key-field-reread/v1",
            "rule_version": REREAD_RULE_VERSION,
            "source_sha256": source_sha, "qualification_status": "unverified_shadow_only",
            "selected_cell_count": len(rereads), "rereads": rereads, "selection_plan": plan}
    if missing_key_field_rereads(plan, result):
        raise ValueError("House paper secondary preparation is incomplete")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--tesseract", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-cells", type=int, default=32)
    parser.add_argument("--selection-mode", choices=[MATERIALIZATION_SELECTION, DOUBTFUL_SELECTION],
                        default=MATERIALIZATION_SELECTION)
    parser.add_argument("--plan-only", action="store_true", help="Export required crops without rendering or OCR")
    parser.add_argument("--existing-rereads", type=Path, help="Reuse bound readings and prepare only missing cells")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists")
    pdf_bytes = args.pdf.read_bytes()
    observation = json.loads(args.observation.read_text(encoding="utf-8"))
    if hashlib.sha256(pdf_bytes).hexdigest() != observation.get("source_sha256"):
        parser.error("House paper second OCR source hash mismatch")
    if args.plan_only:
        result = plan_key_field_rereads(observation, selection_mode=args.selection_mode, max_cells=args.max_cells)
        if args.existing_rereads:
            missing = missing_key_field_rereads(result,
                json.loads(args.existing_rereads.read_text(encoding="utf-8")))
            result["cache_coverage"] = {"missing_cell_count":len(missing), "missing_requests":missing}
    else:
        if args.tesseract is None:
            parser.error("--tesseract is required to prepare readings")
        result = reread_doubtful_key_cells(pdf_bytes, observation,
            executable=args.tesseract, max_cells=args.max_cells, selection_mode=args.selection_mode,
            existing_reread=json.loads(args.existing_rereads.read_text(encoding="utf-8")) if args.existing_rereads else None)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
                           + "\n", encoding="utf-8")
    print(json.dumps({"selected_cell_count": result.get("selected_cell_count", result.get("required_cell_count")),
                      "shape_conflict_count": sum(
                          row.get("comparison") == "second_ocr_shape_differs"
                          for row in result.get("rereads", []))}, sort_keys=True))


if __name__ == "__main__":
    main()
