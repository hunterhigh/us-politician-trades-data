"""Source-bound account roles and whitespace-only readings, never entity repair."""
import hashlib
import math
import re

from .senate import SenateEfdError

RULE = "senate-source-account-layout/v1"


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def interpret_account_spacing(image, record, *, page_sha256, page_number, grid_slot, words):
    """Re-read one existing token; every new space needs a real white corridor.

    Original OCR remains untouched. This interface accepts saved raw TSV, not
    corrected entity names, and cannot add/remove/change a non-space character.
    """
    from PIL import Image
    target = record.get("original_word")
    if (record.get("schema_version") != RULE or record.get("page_sha256") != page_sha256 or
            record.get("page_number") != page_number or record.get("grid_slot") != grid_slot or
            words.count(target) != 1):
        raise SenateEfdError("Senate account spacing source token differs")
    box = [math.floor(target["left"]), math.floor(target["top"]),
           math.ceil(target["left"] + target["width"]), math.ceil(target["top"] + target["height"])]
    if record.get("source_box_px") != box:
        raise SenateEfdError("Senate account spacing crop differs from original token")
    crop = image.convert("RGB").crop(tuple(box))
    padded = Image.new("RGB", (crop.width + 16, crop.height + 16), "white")
    padded.paste(crop, (8, 8))
    if record.get("input_pixels_sha256") != _sha(padded.tobytes()):
        raise SenateEfdError("Senate account spacing pixels differ")
    raw = record.get("raw_tsv")
    if not isinstance(raw, str) or record.get("raw_tsv_sha256") != _sha(raw.encode("utf8")):
        raise SenateEfdError("Senate account spacing raw TSV differs")
    tokens, lines = [], set()
    try:
        for row in raw.splitlines()[1:]:
            fields = row.split("\t", 11)
            if len(fields) != 12:
                raise ValueError()
            if fields[0] != "5":
                continue
            text, confidence = fields[11], float(fields[10])
            if not text.strip() or not math.isfinite(confidence) or not 90 <= confidence <= 100:
                raise ValueError()
            left, top, width, height = map(int, fields[6:10])
            source_box = [left + box[0] - 8, top + box[1] - 8,
                          left + width + box[0] - 8, top + height + box[1] - 8]
            if (width <= 0 or height <= 0 or source_box[0] < box[0] or source_box[1] < box[1] or
                    source_box[2] > box[2] or source_box[3] > box[3] or re.search(r"\s", text)):
                raise ValueError()
            lines.add(tuple(fields[1:5]))
            tokens.append({"text": text, "confidence": confidence, "box_px": source_box})
    except (ValueError, TypeError):
        return None
    if len(tokens) < 2 or len(lines) != 1:
        return None
    result = " ".join(token["text"] for token in tokens)
    if "".join(result.split()) != "".join(target["text"].split()):
        return None
    gray = image.convert("L")
    corridors = []
    for first, second in zip(tokens, tokens[1:]):
        start, stop = first["box_px"][2], second["box_px"][0]
        if start >= stop:
            return None
        longest = run = 0
        for x in range(start, stop):
            run = run + 1 if all(gray.getpixel((x, y)) == 255 for y in range(box[1], box[3])) else 0
            longest = max(longest, run)
        # Font-relative geometric support, with no thresholding or ink removal.
        if longest < .15 * (box[3] - box[1]):
            return None
        corridors.append(longest)
    return {"rule_version": RULE, "original_word": target, "text": result,
            "tokens": tokens, "white_corridor_widths_px": corridors,
            "source_box_px": box, "input_pixels_sha256": record["input_pixels_sha256"],
            "engine": record.get("engine"),
            "raw_tsv_sha256": record["raw_tsv_sha256"], "qualification": "not_assessed"}


def unpunctuated_heading(image, slot, next_slot, words, *, text, confidence):
    """Recognize this legacy form's unindented parent followed by an indented row.

    This is a positive layout role, not a general blank-row classifier. Empty
    OCR, residual marks and absent neighboring data do not satisfy it.
    """
    from .senate_slots_shadow import _row_form_lines, _mark_count
    if (slot.get("label") != "unknown" or slot.get("column_calibration") != "verified" or
            confidence is None or not math.isfinite(confidence) or confidence < 85 or
            not re.fullmatch(r"\((S|J|DC)\)\s*\S.+", text) or
            slot.get("type_marks") or slot.get("amount_marks") or slot.get("date_ocr_raw") or
            next_slot is None or next_slot.get("page_number") != slot["page_number"] or
            next_slot.get("grid_slot") != slot["grid_slot"] + 1 or next_slot.get("label") != "data_observed"):
        return None
    top, bottom = slot["row_bounds_px"]
    next_top, next_bottom = next_slot["row_bounds_px"]
    owner = [w for w in words if top + 6 <= w["top"] + w["height"] / 2 < bottom - 6 and
             re.match(r"^\((S|J|DC)\)", w["text"]) and 600 <= w["left"] < 700]
    child = [w for w in words if next_top + 6 <= w["top"] + w["height"] / 2 < next_bottom - 6 and
             re.match(r"^\((S|J|DC)\)", w["text"]) and 650 <= w["left"] < 850]
    if len(owner) != 1 or len(child) != 1 or child[0]["left"] - owner[0]["left"] < 60:
        return None
    if not math.isfinite(child[0]["confidence"]) or child[0]["confidence"] < 85:
        return None
    gray = image.convert("L")
    lines = _row_form_lines(gray.tobytes(), slot)
    if lines is None:
        return None
    date = slot["key_cells"]["date"]
    if date["box_px"] != [lines[3] + 10, top + 12, lines[4] - 10, bottom - 12]:
        return None
    date_crop = gray.crop(tuple(date["box_px"]))
    if (_sha(date_crop.tobytes()) != date["grayscale_sha256"] or date_crop.getextrema() != (255, 255)):
        return None
    for field in ("direction", "amount"):
        for read in slot["key_cells"][field]:
            crop = gray.crop(tuple(read["box_px"]))
            if (_sha(crop.tobytes()) != read["grayscale_sha256"] or
                    _mark_count(gray, *read["center_px"]) != 0 or read["dark_sample_count"] != 0):
                return None
    # Source-calibrated whole financial interiors, not only X sampling windows.
    # Gray official column shading remains intact; any dark trace rejects role.
    interior_boxes = []
    for bounds in (lines[:4], lines[4:]):
        for left, right in zip(bounds, bounds[1:]):
            box = [left + 8, top + 12, right - 8, bottom - 12]
            if gray.crop(tuple(box)).getextrema()[0] < 160:
                return None
            interior_boxes.append(box)
    if any(w["left"] + w["width"] / 2 >= 1385 and
           top + 6 <= w["top"] + w["height"] / 2 < bottom - 6 for w in words):
        return None
    return {"rule_version": RULE, "role": "heading", "original_label": "unknown",
            "text": text, "heading_owner_word": owner[0], "following_child_owner_word": child[0],
            "financial_interior_boxes_px": interior_boxes, "date_crop_sha256": date["grayscale_sha256"],
            "qualification": "not_assessed"}
