"""Read fixed House PTR cell crops and preserve conflicting evidence for audit.

This opt-in reader never emits transaction facts or candidates. Image
observations are supplied separately and are not treated as qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
from pathlib import Path

from .house_ptr_grid_shadow import _require


def _ocr_words(image, executable: Path, *, dpi: int, psm: int | None = None) -> list[dict]:
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    command = [str(executable), "stdin", "stdout", "--dpi", str(dpi), "-l", "eng"]
    if psm is not None:
        command += ["--psm", str(psm)]
    command.append("tsv")
    completed = subprocess.run(
        command, input=stream.getvalue(), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=60, check=False)
    _require(completed.returncode == 0, "House PTR cell OCR failed")
    lines = completed.stdout.decode("utf-8", "replace").splitlines()
    _require(lines and lines[0].split("\t") ==
             ["level", "page_num", "block_num", "par_num", "line_num", "word_num",
              "left", "top", "width", "height", "conf", "text"],
             "House PTR cell OCR TSV header is invalid")
    words = []
    for line in lines[1:]:
        parts = line.split("\t", 11)
        _require(len(parts) == 12, "House PTR cell OCR TSV row is invalid")
        if parts[0] == "5" and parts[11].strip():
            words.append({"text": parts[11].strip(), "confidence": round(float(parts[10]), 2),
                          "left": int(parts[6]), "top": int(parts[7]),
                          "width": int(parts[8]), "height": int(parts[9])})
    return words


def _crop(image, bbox: list[float], *, dpi: int, inset_x: int, inset_y: int):
    x0, y0, x1, y1 = [round(value * dpi / 72) for value in bbox]
    _require(x1 - x0 > 2 * inset_x and y1 - y0 > 2 * inset_y,
             "House PTR cell crop is too small")
    return image.crop((x0 + inset_x, y0 + inset_y, x1 - inset_x, y1 - inset_y))


def _checkbox_ink(image, bbox: list[float], *, dpi: int) -> float:
    x0, y0, x1, y1 = [round(value * dpi / 72) for value in bbox]
    width, height = x1 - x0, y1 - y0
    crop = image.crop((x0 + int(.27 * width), y0 + int(.22 * height),
                       x1 - int(.27 * width), y1 - int(.22 * height)))
    _require(crop.width > 0 and crop.height > 0, "House PTR checkbox crop is invalid")
    return round(sum(crop.histogram()[:100]) / (crop.width * crop.height), 4)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _comparison(ocr_text: str, observed: dict | None) -> str:
    if not observed or observed.get("status") != "clear":
        return "image_observation_unresolved"
    expected = observed.get("text")
    _require(isinstance(expected, str) and expected,
             "House PTR clear image observation requires text")
    return ("ocr_agrees_with_image_observation" if _normalize(ocr_text) == _normalize(expected)
            else "ocr_conflicts_with_image_observation")


def _page_words_in_cell(words: list[dict], bbox: list[float]) -> list[dict]:
    x0, y0, x1, y1 = bbox
    return [{"text": word["text"], "confidence": word["confidence"]}
            for word in words
            if x0 <= (word["left"] + word["width"] / 2) * 72 / 200 < x1
            and y0 <= (word["top"] + word["height"] / 2) * 72 / 200 < y1]


def build_cell_reread_shadow(pdf_bytes: bytes, cells: dict, observations: dict,
                             executable: Path) -> dict:
    source_sha = hashlib.sha256(pdf_bytes).hexdigest()
    _require(cells.get("schema_version") == "house-ptr-checkbox-cells-shadow/v1"
             and observations.get("schema_version") == "house-ptr-cell-observations/v1"
             and cells.get("source_sha256") == source_sha == observations.get("source_sha256")
             and cells.get("document_id") == observations.get("document_id")
             and cells.get("file_status") == "failed_open",
             "House PTR cell reread source identity mismatch")
    selected = observations.get("rows")
    _require(isinstance(selected, list) and 0 < len(selected) <= 4,
             "House PTR cell reread requires a bounded row selection")
    by_locator = {row["physical_locator"]: row for row in cells["rows"]}
    _require(len(by_locator) == len(cells["rows"])
             and len({row.get("physical_locator") for row in selected}) == len(selected),
             "House PTR cell reread row selection is ambiguous")
    version = subprocess.run([str(executable), "--version"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=15, check=False)
    engine = (version.stdout.decode("utf-8", "replace").splitlines() or [""])[0]
    _require(version.returncode == 0 and engine.startswith("tesseract "),
             "House PTR cell OCR engine is unavailable")
    results = []
    page_ocr_cache: dict[int, list[dict]] = {}
    # The visual reread is opt-in; normal validation works without OCR extras.
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for item in selected:
            locator = item.get("physical_locator")
            row = by_locator.get(locator)
            _require(row is not None and row.get("disposition") ==
                     "quarantined_shadow_unverified_cells", "House PTR cell reread row is invalid")
            match = re.search(r":p(\d+):r\d+$", locator)
            _require(match is not None and 1 <= int(match.group(1)) <= len(pdf.pages),
                     "House PTR cell reread page is invalid")
            page_number = int(match.group(1))
            page = pdf.pages[page_number - 1]
            if page_number not in page_ocr_cache:
                page_ocr_cache[page_number] = _ocr_words(
                    page.to_image(resolution=200, antialias=True).original.convert("L"),
                    executable, dpi=200)
            image = page.to_image(resolution=300).original.convert("L")
            fields = {}
            observed_fields = item.get("image_observation") or {}
            for name in ("asset", "transaction_date", "notification_date"):
                cell = row["cells"][name]
                crop = _crop(image, cell["bbox_points"], dpi=300, inset_x=7, inset_y=4)
                words = _ocr_words(crop, executable, dpi=300, psm=7)
                raw = " ".join(word["text"] for word in words)
                original_words = _page_words_in_cell(page_ocr_cache[page_number],
                                                     cell["bbox_points"])
                original_raw = " ".join(word["text"] for word in original_words)
                fields[name] = {"cell_locator": cell["locator"], "crop_ocr_words": words,
                                "crop_ocr_raw": raw,
                                "original_page_ocr_words": original_words,
                                "original_page_ocr_raw": original_raw,
                                "ocr_pass_agreement": "agree" if _normalize(original_raw) ==
                                _normalize(raw) else "conflict",
                                "image_observation": observed_fields.get(name),
                                "comparison": _comparison(raw, observed_fields.get(name))}
            for name in ("action", "amount"):
                boxes = row["cells"][name]
                observation = observed_fields.get(name)
                _require(isinstance(observation, dict)
                         and observation.get("status") in ("clear", "ambiguous")
                         and (observation.get("status") != "clear"
                              or type(observation.get("selected_index")) is int
                              and 1 <= observation["selected_index"] <= len(boxes)),
                         "House PTR checkbox image observation is invalid")
                densities = [_checkbox_ink(image, cell["bbox_points"], dpi=300)
                             for cell in boxes]
                fields[name] = {"cell_locators": [cell["locator"] for cell in boxes],
                                "inner_dark_fractions": densities,
                                "image_observation": observation,
                                "comparison": "checkbox_value_unverified"}
            results.append({"physical_locator": locator,
                            "disposition": "quarantined_shadow_field_evidence_incomplete",
                            "candidate_id": None, "fields": fields,
                            "blocking_reasons": sorted({
                                "report_failed_open", "action_checkbox_not_machine_verified",
                                "amount_checkbox_not_machine_verified",
                                *(f"{name}_{fields[name]['comparison']}"
                                  for name in ("asset", "transaction_date", "notification_date")
                                  if fields[name]["comparison"] !=
                                  "ocr_agrees_with_image_observation"),
                                *(f"{name}_original_crop_ocr_conflict"
                                  for name in ("asset", "transaction_date", "notification_date")
                                  if fields[name]["ocr_pass_agreement"] == "conflict")})})
    return {"schema_version": "house-ptr-cell-reread-shadow/v1",
            "document_id": cells["document_id"], "source_sha256": source_sha,
            "ocr_engine": engine, "crop_render_dpi": 300,
            "file_status": "failed_open", "qualified_rows": 0,
            "selected_rows": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--cells", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--tesseract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists")
    result = build_cell_reread_shadow(
        args.pdf.read_bytes(), json.loads(args.cells.read_text(encoding="utf-8")),
        json.loads(args.observations.read_text(encoding="utf-8")), args.tesseract)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"document_id": result["document_id"],
                      "selected_rows": len(result["selected_rows"]), "qualified_rows": 0}))


if __name__ == "__main__":
    main()
