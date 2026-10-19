"""Read-only field shapes from confirmed 2024 OGE 278-T raster cells.

This does not correct OCR, classify a cell as a qualified transaction, or grant source
eligibility.  It consumes saved slot observations so field work needs no new
PDF scan and pages without confirmed geometry stay unknown.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
from difflib import SequenceMatcher
import hashlib
import json
from math import isfinite
import re
from statistics import median
from .oge_278t_raster_slots import column_edges_at_y, column_crop_bounds, column_geometry_sha256


SCHEMA = "oge-278t-field-shadow/v1"
COMPARISON_SCHEMA = "oge-278t-field-comparison/v1"
_INPUT_SCHEMA = "oge-278t-raster-slot-observation/v2"
_COLUMNS = ("printed_number", "description", "type", "transaction_date",
            "notification", "amount")
_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(20\d{2})")
# A decimal-looking OCR artifact such as "$15.001" must not become "$15".
# A trailing punctuation mark without following digits remains harmless.
_MONEY = re.compile(r"\$\s*(\d{1,3}(?:,\d{3})+|\d+)(?!\d|[,.]\d)")
_TYPE = {"purchase", "sale", "exchange"}
_HEADER = re.compile(r"\b(description|type|date|notification|amount)\b", re.I)
_FIELD_COLUMNS = {"printed_number": (0,), "description": (1,), "type": (2,),
                  "transaction_date": (3,), "amount": (4, 5)}
_SECONDARY = {"oge-278t-type-column-observation/v1": ("type", "type_column_observed"),
              "oge-278t-description-column-observation/v1": ("description", "description_column_observed"),
              "oge-278t-date-column-observation/v1": ("transaction_date", "date_column_observed"),
              "oge-278t-amount-column-observation/v1": ("amount", "amount_column_observed"),
              "oge-278t-amount-column-observation/v2": ("amount", "amount_column_observed")}
_CONTENT_RULES = "oge-278t-content-rows/v3"


def _word_evidence(slot: dict, page: dict) -> dict:
    """Explain boundaries without discarding words or deciding field truth."""
    from .oge_278t_raster_slots import _observe_slot_columns

    words, edges = slot.get("words"), page.get("column_edges_pdf_points")
    if words is None or edges is None:
        return {"status": "unavailable", "reason": "word_or_column_geometry_unavailable",
                "crossing_words": [], "unassigned_word_indices": None}
    if (not isinstance(words, list) or not isinstance(edges, list) or len(edges) != 7 or
            any(type(v) not in (int, float) or not isfinite(v) for v in edges) or
            any(a >= b for a, b in zip(edges, edges[1:]))):
        raise ValueError("field word or column geometry is invalid")
    for word in words:
        box = word.get("bbox_pdf_points")
        if (not isinstance(word.get("text"), str) or not isinstance(box, list) or len(box) != 4 or
                any(type(v) not in (int, float) or not isfinite(v) for v in box) or
                box[0] >= box[2] or box[1] >= box[3]):
            raise ValueError("field word geometry is invalid")
    derived = _observe_slot_columns(words, edges, page=page)
    if slot.get("column_model_outside_word_indices", []) != derived.get("column_model_outside_word_indices", []):
        raise ValueError("saved word model-domain references differ from geometry")
    owner = {}
    for name, expected in derived["column_observation"].items():
        saved = slot["column_observation"][name]
        if saved.get("word_indices") != expected["word_indices"] or saved["raw_ocr_text"] != expected["raw_ocr_text"]:
            raise ValueError("saved column word references differ from source geometry")
        owner.update({index: name for index in expected["word_indices"]})
    crossings = []
    for index, word in enumerate(words):
        x0, top, x1, bottom = word["bbox_pdf_points"]
        local = column_edges_at_y(page, (top+bottom)/2)
        domain = (page.get("column_line_model") or {}).get("valid_y_span_pdf_points")
        if domain:
            top,bottom = max(top,domain[0]),min(bottom,domain[1])
        a,b = column_edges_at_y(page,top),column_edges_at_y(page,bottom)
        boundaries = [{"left_column": _COLUMNS[i - 1], "right_column": _COLUMNS[i],
                       "x_pdf_points": edge,
                       "shorter_overhang_pdf_points": round(max(0,min(edge - x0, x1 - edge)), 4)}
                      for i, edge in enumerate(local[1:-1], 1)
                      if x0 < max(a[i],b[i]) and x1 > min(a[i],b[i])]
        if boundaries:
            crossings.append({"word_index": index, "text": word["text"],
                              "bbox_pdf_points": list(word["bbox_pdf_points"]),
                              "assigned_column": owner.get(index), "boundaries": boundaries})
    return {"status": "source_words_linked", "reason": None, "crossing_words": crossings,
            "unassigned_word_indices": [i for i in range(len(words)) if i not in owner]}


def _field_readings(slot: dict, raw: dict, shapes: dict, word_evidence: dict, *,
                    original: dict, source_ref: str) -> dict:
    from .oge_278t_amount_categories import observe_template_amount

    result = {}
    for field, positions in _FIELD_COLUMNS.items():
        names = [_COLUMNS[i] for i in positions]
        indices = [slot["column_observation"][name].get("word_indices") for name in names]
        warnings = []
        for index, crossing in enumerate(word_evidence["crossing_words"]):
            for boundary in crossing["boundaries"]:
                affected = {boundary["left_column"], boundary["right_column"]}
                if affected & set(names):
                    warnings.append({"crossing_word_ref": index,
                                     "scope": "within_combined_columns" if affected <= set(names)
                                     else "field_boundary"})
        primary = {"method": "page_ocr", "raw_columns": names,
                   "engine": original.get("ocr_engine"),
                   "resolution_dpi": original.get("ocr_resolution_dpi"),
                   "source_ref": source_ref,
                   "raw_ocr_text": " ".join(raw[name] for name in names),
                   "shape": deepcopy(shapes[field]),
                   "word_indices": sorted({i for values in indices for i in values})
                   if all(isinstance(value, list) for value in indices) else None}
        if field == "amount":
            primary["template_category"] = observe_template_amount(primary["raw_ocr_text"])
        result[field] = {"observations": [primary], "geometry_warnings": warnings,
                         "geometry_evidence_status": word_evidence["status"],
                         "qualification": "not_assessed"}
    return result


def _known_value(shape: dict):
    if shape["status"] not in {"ocr_shape_only", "ocr_text_only"}:
        return None
    return shape.get("values", shape.get("value"))


def _description_text_difference(left: str, right: str) -> dict:
    """Describe literal differences; never use these labels to reconcile facts."""
    if left == right:
        kind = "exact_text"
    elif re.findall(r"\d+", left) != re.findall(r"\d+", right):
        kind = "numeric_tokens_changed"
    elif left.lower() == right.lower():
        kind = "case_variant"
    elif re.sub(r"\s+", "", left) == re.sub(r"\s+", "", right):
        kind = "whitespace_variant"
    elif left.strip(" .,:;|[]()") == right.strip(" .,:;|[]()"):
        kind = "edge_punctuation"
    else:
        kind = "text_change"
    # Output offsets into shape.value (physical word order), never raw_ocr_text.
    spans = ([{"operation": operation, "left_range": [i, j], "right_range": [a, b]}
              for operation, i, j, a, b in SequenceMatcher(None, left, right).get_opcodes()
              if operation != "equal"] if len(left) + len(right) <= 4096 else None)
    return {"difference_kind": kind, "changed_spans": spans,
            "detail_status": "available" if spans is not None else "omitted_for_long_text",
            "fact_resolution": "not_assessed"}


def _attach_secondary_reads(result: dict, originals: list[dict], readings: list[dict]) -> None:
    """Join saved reads by source/page/slot/band, never by equal row totals."""
    source_pages = {page["page_number"]: (item, page)
                    for item in originals for page in item["pages"]}
    rows = {(page["page_number"], slot["slot_index"]): slot
            for page in result["pages"] for slot in page["slots"]}
    seen = set()
    coverage = []
    input_refs = []
    for read_index, reading in enumerate(readings):
        kind = _SECONDARY.get(reading.get("schema_version"))
        if kind is None or reading.get("source_sha256") != result["source_sha256"]:
            raise ValueError("secondary field read has unsupported schema or different source")
        field, status = kind
        input_refs.append({"index": read_index, "schema_version": reading["schema_version"],
                           "canonical_content_sha256": hashlib.sha256(json.dumps(
                               reading, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False).encode()).hexdigest()})
        pages = reading.get("pages")
        if not isinstance(pages, list) or len({p["page_number"] for p in pages}) != len(pages):
            raise ValueError("secondary field read pages are missing or repeated")
        if field == "description":
            fingerprint = reading.get("input_observation_canonical_sha256")
            page_hashes = reading.get("input_page_canonical_sha256")
            if (not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint) or
                    not isinstance(page_hashes, dict) or set(page_hashes) != {str(p["page_number"]) for p in pages}):
                raise ValueError("description read lacks bound input provenance")
        observed_count = 0
        for page_index, page in enumerate(pages):
            number = page["page_number"]
            if number not in source_pages:
                raise ValueError("secondary field read references a page outside selected observations")
            original, original_page = source_pages[number]
            if (original_page.get("column_line_model") is not None or
                    "input_column_geometry_sha256" in reading) and (
                    reading.get("input_column_geometry_sha256", {}).get(str(number)) !=
                    column_geometry_sha256(original_page)):
                raise ValueError("secondary read differs from source column line model")
            if field == "description":
                expected_input = hashlib.sha256(json.dumps(
                    original_page, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    allow_nan=False).encode()).hexdigest()
                if reading["input_page_canonical_sha256"][str(number)] != expected_input:
                    raise ValueError("description read differs from bound primary input")
            if page["status"] == "geometry_unknown":
                if page.get("slots") != []:
                    raise ValueError("unknown secondary geometry contains field rows")
                coverage.append({"reading_index": read_index, "page_number": number,
                                 "field": field, "status": "geometry_unknown"})
                continue
            if (page["status"] != status or original_page.get("column_geometry") !=
                    "oge_2024_six_column_grid" or not isinstance(page.get("slots"), list)):
                raise ValueError("secondary field read has no matching confirmed grid")
            dpi = reading.get("ocr_resolution_dpi")
            if (reading.get("input_ocr_resolution_dpi") != original.get("ocr_resolution_dpi") or
                    not isinstance(reading.get("ocr_engine"), str) or not reading["ocr_engine"].strip() or
                    type(dpi) is not int or not (150 if field == "type" else 200) <= dpi <= 400):
                raise ValueError("secondary field read engine or input resolution differs")
            slots = {slot["slot_index"]: slot for slot in page["slots"]}
            base_slots = {slot["slot_index"]: slot for slot in original_page["slots"]}
            if (len(slots) != len(page["slots"]) or len(base_slots) != len(original_page["slots"]) or
                    set(slots) != set(base_slots) or page.get("slot_count") != len(slots)):
                raise ValueError("secondary field slots are missing, duplicated or unbound")
            unassigned = page.get("unassigned_detections", [])
            if field == "description":
                _validate_description_unassigned(page, original_page)
            for slot_index, slot in slots.items():
                base = base_slots[slot_index]
                if slot.get("band_pdf_points") != base.get("band_pdf_points"):
                    raise ValueError("secondary field slot band differs from source slot")
                identity = (field, number, slot_index, reading["ocr_engine"], reading["ocr_resolution_dpi"])
                if identity in seen:
                    raise ValueError("secondary field reading identity repeated")
                seen.add(identity)
                target = rows[(number, slot_index)]["field_readings"][field]
                if slot.get("page_raw_ocr_text") != target["observations"][0]["raw_ocr_text"]:
                    raise ValueError("secondary field read was compared against different original text")
                raw = slot["column_raw_ocr_text"]
                detections = slot.get("column_words" if field == "type" else "column_detections")
                if not isinstance(raw, str) or not isinstance(detections, list):
                    raise ValueError("secondary field read lacks raw detections")
                edges = original_page.get("column_edges_pdf_points")
                if not isinstance(edges, list) or len(edges) != 7:
                    raise ValueError("secondary field read lacks source column boundaries")
                left, right = min(_FIELD_COLUMNS[field]), max(_FIELD_COLUMNS[field]) + 1
                rounding = 72.0 / dpi + .02
                geometry_issues = []
                if field == "description":
                    overlaps = [i for i, item in enumerate(unassigned)
                                if item["bbox_pdf_points"][1] < base["band_pdf_points"][1] and
                                item["bbox_pdf_points"][3] > base["band_pdf_points"][0]]
                    if slot.get("unassigned_detection_indices") != overlaps:
                        raise ValueError("description unassigned references differ from geometry")
                    if overlaps:
                        geometry_issues.append("unassigned_description_detections_overlap_slot")
                for detection in detections:
                    box = detection.get("bbox_pdf_points")
                    if (not isinstance(detection.get("text"), str) or
                            not isinstance(box, list) or len(box) != 4 or
                            any(type(v) not in (int, float) or not isfinite(v) for v in box) or
                            box[0] >= box[2] or box[1] >= box[3] or
                            not base["band_pdf_points"][0] <= (box[1] + box[3]) / 2 <
                            base["band_pdf_points"][1]):
                        raise ValueError("secondary detection is outside its source field or slot")
                    local = column_edges_at_y(original_page, (box[1]+box[3])/2)
                    domain = (original_page.get("column_line_model") or {}).get("valid_y_span_pdf_points")
                    y0,y1 = box[1],box[3]
                    if domain and not domain[0] <= y0 < y1 <= domain[1]:
                        geometry_issues.append("detection_outside_column_model_domain")
                        y0,y1 = max(y0,domain[0]),min(y1,domain[1])
                    a,b = column_edges_at_y(original_page,y0),column_edges_at_y(original_page,y1)
                    if not local[left] <= (box[0]+box[2])/2 < local[right]:
                        if original_page.get("column_line_model") is None:
                            raise ValueError("secondary detection is outside its source field or slot")
                        geometry_issues.append("detection_outside_sloped_field")
                    if (box[0] < max(a[left],b[left]) + .6 - rounding or
                            box[2] > min(a[right],b[right]) - 1.5 + rounding or
                            max(base["band_pdf_points"][0] - box[1],
                                box[3] - base["band_pdf_points"][1], 0) > 3.0 + .02):
                        geometry_issues.append("detection_extends_beyond_crop_or_slot")
                if raw != " ".join(detection["text"] for detection in detections):
                    raise ValueError("secondary text differs from saved detections")
                if field == "amount":
                    shape = _amount_shape(raw)
                elif field == "description":
                    description_reading = _description_detection_reading(detections)
                    shape = _description_shape(description_reading)
                    if slot.get("column_reading") != description_reading:
                        raise ValueError("description physical reading differs from detections")
                elif field == "transaction_date":
                    shape = _date_shape(raw) if len(detections) == 1 else {
                        "status": "unknown", "value": None,
                        "reason": "multiple_date_detections" if detections else "no_date_detection"}
                else:
                    value = raw.strip().casefold()
                    value = value if len(detections) == 1 and value in _TYPE else None
                    shape = {"status": "ocr_shape_only" if value else "unknown", "value": value}
                if (slot.get("column_shape") != (shape["value"] if field == "type" else shape)):
                    raise ValueError("secondary field shape differs from its raw text")
                observation = {"method": "column_ocr", "raw_ocr_text": raw,
                    "shape": shape, "detections": deepcopy(detections),
                    "geometry_status": "ambiguous" if geometry_issues else "source_bound",
                    "geometry_issues": sorted(set(geometry_issues)),
                    "engine": reading["ocr_engine"], "resolution_dpi": reading["ocr_resolution_dpi"],
                    "source_ref": f"secondary_observations/{read_index}/pages/{page_index}/slots/"
                                  f"{page['slots'].index(slot)}"}
                if field == "amount":
                    from .oge_278t_amount_categories import observe_template_amount
                    observation["template_category"] = observe_template_amount(raw)
                elif field == "description":
                    observation["description_reading"] = description_reading
                    observation["page_unassigned_detection_indices"] = overlaps
                    observation["unassigned_detections"] = [deepcopy(unassigned[i]) for i in overlaps]
                target["observations"].append(observation)
                observed_count += 1
        # Producers omit the zero counter only when every selected page is unknown.
        default_count = 0 if pages and all(page["status"] == "geometry_unknown" for page in pages) else None
        if reading.get("summary", {}).get("observed_slots", default_count) != observed_count:
            raise ValueError("secondary field summary differs from linked slots")
    counts = Counter()
    description_pair_counts = Counter()
    description_ineligible_pairs = 0
    has_description_pairs = False
    for row in rows.values():
        for field, evidence in row["field_readings"].items():
            values = [_known_value(obs["shape"]) for obs in evidence["observations"]]
            known = [value for value, observation in zip(values, evidence["observations"], strict=True)
                     if value is not None and observation.get("geometry_status") != "ambiguous"]
            if len({repr(value) for value in known}) > 1:
                consistency = "conflicting_observations"
            elif len(known) > 1:
                consistency = "matching_observations"
            elif known:
                consistency = "single_recognized_observation"
            else:
                consistency = "no_recognized_observation"
            evidence["consistency"] = consistency
            evidence["observation_count"] = len(values)
            evidence["recognized_observation_count"] = sum(value is not None for value in values)
            evidence["comparable_observation_count"] = len(known)
            ambiguous = sum(obs.get("geometry_status") == "ambiguous" for obs in evidence["observations"])
            evidence["geometry_ambiguous_observation_count"] = ambiguous
            if ambiguous:
                counts[f"{field}_geometry_ambiguous_observations"] += ambiguous
            counts[f"{field}_{consistency}"] += 1
            if field == "description" and len(evidence["observations"]) > 1:
                has_description_pairs = True
                comparisons = []
                primary = evidence["observations"][0]
                for index, other in enumerate(evidence["observations"][1:], 1):
                    left, right = values[0], values[index]
                    recognized = isinstance(left, str) and isinstance(right, str)
                    eligible = recognized and all(obs.get("geometry_status") != "ambiguous"
                                                  for obs in (primary, other))
                    difference = (_description_text_difference(left, right) if recognized else {
                        "difference_kind": "unrecognized_observation", "changed_spans": None,
                        "detail_status": "unavailable", "fact_resolution": "not_assessed"})
                    comparisons.append({"left_observation_index": 0, "right_observation_index": index,
                        "left_source_ref": primary["source_ref"], "right_source_ref": other["source_ref"],
                        "text_basis": "shape.value",
                        "eligible_for_existing_conflict_rule": eligible, **difference})
                    if eligible:
                        description_pair_counts[difference["difference_kind"]] += 1
                    else:
                        description_ineligible_pairs += 1
                evidence["text_comparisons"] = comparisons
    result["secondary_field_summary"] = dict(sorted(counts.items()))
    result["secondary_geometry_unknown_pages"] = coverage
    result["secondary_input_refs"] = input_refs
    if has_description_pairs:
        result["description_text_comparison_summary"] = {
            "eligible_pair_counts": dict(sorted(description_pair_counts.items())),
            "ineligible_pair_count": description_ineligible_pairs,
            "fact_resolution": "not_assessed"}


def _validate_description_unassigned(page: dict, original_page: dict) -> None:
    detections = page.get("unassigned_detections")
    if not isinstance(detections, list) or page.get("words_outside_slots") != len(detections):
        raise ValueError("description unassigned detections are missing or miscounted")
    edges = original_page["column_edges_pdf_points"]
    if original_page.get("column_line_model") is not None:
        slots = original_page["slots"]
        left,right = column_crop_bounds(original_page,1,2,slots[0]["band_pdf_points"][0],
                                        slots[-1]["band_pdf_points"][1]+2)
    else:
        left,right = edges[1],edges[2]
    for item in detections:
        box = item.get("bbox_pdf_points")
        if (not isinstance(item.get("text"), str) or not isinstance(box, list) or len(box) != 4 or
                any(type(v) not in (int, float) or not isfinite(v) for v in box) or
                not box[0] < box[2] or not box[1] < box[3] or
                not left <= (box[0] + box[2]) / 2 < right):
            raise ValueError("description unassigned detection geometry is invalid")
        owners = [slot for slot in original_page["slots"] if
                  slot["band_pdf_points"][0] <= (box[1] + box[3]) / 2 < slot["band_pdf_points"][1]]
        if item.get("reason") == "center_rounding_ambiguous":
            center = item.get("unrounded_center_pdf_points")
            original_owners = [slot for slot in original_page["slots"] if
                               slot["band_pdf_points"][0] <= center < slot["band_pdf_points"][1]] if type(center) in (int, float) and isfinite(center) else owners
            if (type(center) not in (int, float) or not isfinite(center) or
                    abs(center - (box[1] + box[3]) / 2) > .0051 or owners == original_owners):
                raise ValueError("description center rounding ambiguity lacks its source basis")
            continue
        overhang = max(owners[0]["band_pdf_points"][0] - box[1],
                       box[3] - owners[0]["band_pdf_points"][1], 0) if len(owners) == 1 else None
        expected_reason = "vertical_overhang" if overhang is not None and overhang > 3.0 else "outside_slot_bands"
        if (len(owners) == 1 and overhang <= 3.0 or
                item.get("reason") != expected_reason):
            raise ValueError("description detection marked unassigned without its geometry basis")


def _date_shape(raw: str) -> dict:
    matches = list(_DATE.finditer(raw))
    if len(matches) != 1:
        return {"status": "unknown", "value": None,
                "reason": "missing_or_multiple_date_shapes"}
    match = matches[0]
    try:
        value = date(int(match[3]), int(match[1]), int(match[2])).isoformat()
    except ValueError:
        return {"status": "unknown", "value": None,
                "reason": "invalid_calendar_date"}
    residue = raw[:match.start()] + raw[match.end():]
    if re.sub(r"[\s\]\[)(|;:.,]", "", residue):
        return {"status": "unknown", "value": None,
                "reason": "date_with_unexplained_text"}
    return {"status": "ocr_shape_only", "value": value,
            "reason": "punctuation_residue" if residue.strip() else None}


def _amount_shape(raw: str) -> dict:
    values = [int(value.replace(",", "")) for value in _MONEY.findall(raw)]
    if len(values) != 2 or values[0] >= values[1]:
        return {"status": "unknown", "values": None,
                "reason": "not_exactly_two_ordered_money_shapes"}
    return {"status": "ocr_shape_only", "values": values,
            "reason": None}


def _description_reading(slot: dict) -> dict:
    """Order saved words by physical line and x, retaining the original read.

    Word tops vary with glyph height. Sorting by that top before x scrambles
    a single printed line. Geometry ordering changes no characters and grants
    no asset identity or transaction eligibility.
    """
    column = slot["column_observation"]["description"]
    raw = column["raw_ocr_text"]
    indices, words = column.get("word_indices"), slot.get("words")
    if indices is None or words is None:
        return {"status": "original_ocr_order_unverified", "raw_ocr_text": raw,
                "ordered_ocr_text": raw, "line_word_indices": None,
                "reason": "word_geometry_unavailable"}
    if (not isinstance(indices, list) or not isinstance(words, list) or
            any(type(index) is not int or not 0 <= index < len(words) for index in indices) or
            len(set(indices)) != len(indices)):
        raise ValueError("description word indices are invalid or duplicated")
    for index in indices:
        word = words[index]
        box = word.get("bbox_pdf_points")
        if (not isinstance(word.get("text"), str) or not isinstance(box, list) or len(box) != 4
                or any(type(value) not in (int, float) or not isfinite(value) for value in box)
                or box[0] >= box[2] or box[1] >= box[3]):
            raise ValueError("description word geometry is invalid")

    def center(index: int) -> float:
        box = words[index]["bbox_pdf_points"]
        return (box[1] + box[3]) / 2

    lines: list[list[int]] = []
    for index in sorted(indices, key=lambda i: (center(i), words[i]["bbox_pdf_points"][0], i)):
        matches = [line for line in lines if abs(center(index) - median(center(i) for i in line))
                   <= .6 * median(words[i]["bbox_pdf_points"][3] -
                                    words[i]["bbox_pdf_points"][1] for i in line)]
        if len(matches) > 1:
            return {"status": "unknown", "raw_ocr_text": raw,
                    "ordered_ocr_text": None, "line_word_indices": None,
                    "reason": "multiple_possible_text_lines"}
        if matches:
            matches[0].append(index)
        else:
            lines.append([index])
    lines.sort(key=lambda line: median(center(i) for i in line))
    lines = [sorted(line, key=lambda i: (words[i]["bbox_pdf_points"][0], i)) for line in lines]
    return {"status": "geometry_ordered_unverified", "raw_ocr_text": raw,
            "ordered_ocr_text": " ".join(words[index]["text"] for line in lines for index in line),
            "line_word_indices": lines, "reason": None}


def _description_detection_reading(detections: list[dict]) -> dict:
    """Reuse the physical reading order for every word; keep detector order raw."""
    return _description_reading({
        "words": detections, "column_observation": {"description": {
            "raw_ocr_text": " ".join(item["text"] for item in detections),
            "word_indices": list(range(len(detections)))}}})


def _description_shape(reading: dict) -> dict:
    description = (reading["ordered_ocr_text"] or "").strip()
    return {"status": "ocr_text_only" if description and not _HEADER.search(description) else "unknown",
            "value": description or None}


def observe_field_shapes(observations: list[dict], *,
                         secondary_observations: list[dict] | None = None) -> dict:
    """Summarize saved OCR observations; never turn shapes into facts."""
    if not observations or any(item.get("schema_version") != _INPUT_SCHEMA
                               for item in observations):
        raise ValueError("expected saved 278-T raster slot observations v2")
    sources = {item.get("source_sha256") for item in observations}
    if len(sources) != 1 or None in sources:
        raise ValueError("278-T field observations must share one PDF SHA-256")
    seen_pages: set[int] = set()
    pages = []
    counts: Counter[str] = Counter()
    evidence_counts: Counter[str] = Counter()
    for input_index, item in enumerate(observations):
        for page_index, page in enumerate(item["pages"]):
            number = page["page_number"]
            if number in seen_pages:
                raise ValueError("duplicate page in 278-T field observations")
            seen_pages.add(number)
            if page.get("column_geometry") != "oge_2024_six_column_grid":
                pages.append({"page_number": number, "status": "geometry_unknown",
                              "slot_count": page.get("physical_slot_count"),
                              "slots": []})
                counts["geometry_unknown_pages"] += 1
                continue
            slots = []
            for slot_index, slot in enumerate(page["slots"]):
                columns = slot.get("column_observation")
                if not isinstance(columns, dict) or any(
                        name not in columns for name in _COLUMNS):
                    raise ValueError("confirmed column page lacks column observations")
                raw = {name: columns[name]["raw_ocr_text"] for name in _COLUMNS}
                printed_number = raw["printed_number"].strip()
                direction = raw["type"].strip().casefold()
                description_reading = _description_reading(slot)
                # OCR may join a notification word to the first dollar value.
                # Searching both adjacent columns is an observation, not a
                # positional correction or a claim about notification status.
                money_raw = raw["notification"] + " " + raw["amount"]
                field_shapes = {
                    "printed_number": {"status": "ocr_shape_only" if
                                       re.fullmatch(r"[1-9][0-9]*", printed_number) else
                                       "unknown",
                                       "value": int(printed_number) if
                                       re.fullmatch(r"[1-9][0-9]*", printed_number) else None},
                    "description": _description_shape(description_reading),
                    "type": {"status": "ocr_shape_only" if direction in _TYPE else
                             "unknown", "value": direction if direction in _TYPE
                             else None},
                    "transaction_date": _date_shape(raw["transaction_date"]),
                    "amount": _amount_shape(money_raw),
                }
                issues = [name + "_unknown" for name, value in field_shapes.items()
                          if value["status"] == "unknown"]
                if slot.get("boundary_crossing_word_count"):
                    issues.append("boundary_crossing_words")
                if slot.get("unassigned_column_word_count"):
                    issues.append("unassigned_column_words")
                if _HEADER.search(slot.get("raw_ocr_text", "")):
                    issues.append("possible_header_bleed")
                counts.update(issues)
                counts["observed_slots"] += 1
                if not issues:
                    counts["shape_complete_slots"] += 1
                word_evidence = _word_evidence(slot, page)
                readings = _field_readings(
                    slot, raw, field_shapes, word_evidence, original=item,
                    source_ref=f"observations/{input_index}/pages/{page_index}/slots/{slot_index}")
                evidence_counts[word_evidence["status"] + "_slots"] += 1
                evidence_counts["crossing_word_count"] += len(word_evidence["crossing_words"])
                evidence_counts["slots_with_crossing_words"] += bool(word_evidence["crossing_words"])
                for field, reading in readings.items():
                    for scope in {warning["scope"] for warning in reading["geometry_warnings"]}:
                        evidence_counts[f"{field}_{scope}_slots"] += 1
                slots.append({"slot_index": slot["slot_index"],
                              "raw_columns": raw, "field_shapes": field_shapes,
                              "description_reading": description_reading,
                              "word_evidence": word_evidence, "field_readings": readings,
                              "issues": issues, "qualification": "not_assessed"})
            pages.append({"page_number": number, "status": "field_shapes_observed",
                          "slot_count": len(slots), "slots": slots})
    pages.sort(key=lambda page: page["page_number"])
    result = {"schema_version": SCHEMA, "source_sha256": sources.pop(),
            "field_evidence_schema_version": "oge-278t-linked-field-evidence/v1",
            "input_refs": [{"index": i, "canonical_content_sha256": hashlib.sha256(json.dumps(
                item, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                allow_nan=False).encode()).hexdigest()} for i, item in enumerate(observations)],
            "page_count": len(pages), "summary": dict(sorted(counts.items())),
            "field_evidence_summary": dict(sorted(evidence_counts.items())),
            "pages": pages, "production_mutation_authorized": False}
    if secondary_observations is not None:
        if not isinstance(secondary_observations, list):
            raise ValueError("secondary field observations must be an array")
        _attach_secondary_reads(result, observations, secondary_observations)
    return result


def _content_disposition(row: dict) -> tuple[str, str]:
    """Recognize content separately from shape completeness and eligibility."""
    raw = row["raw_columns"]
    cues = [field for field in ("type", "transaction_date", "amount")
            if any(_known_value(read["shape"]) is not None and
                   read.get("geometry_status") != "ambiguous"
                   for read in row["field_readings"][field]["observations"])]
    # A single '#' can be a bad printed number on a genuine data row.
    labels = {name for name in _COLUMNS[1:] if
              re.fullmatch(name.replace("transaction_date", "date"), raw[name].strip(), re.I)}
    description = raw["description"].strip()
    header = "description" in labels and len(labels) >= 3
    heading = bool(re.fullmatch(r"(?:investment|retirement) account\s*#[0-9]+",
                                description, re.I))
    note = bool(re.fullmatch(
        r"(?:(?:this (?:page|section) )?(?:is )?(?:intentionally left blank|left intentionally blank)|"
        r"endnotes|summary of contents|privacy act statement)[.!:]?", description, re.I))
    if (header or heading or note) and cues:
        return "unknown", "structural_and_data_evidence_conflict"
    if header:
        return "heading", "multiple_column_header_labels"
    if heading and not any(raw[name].strip() for name in ("type", "transaction_date", "notification", "amount")):
        return "heading", "explicit_account_section_heading"
    if note and not any(raw[name].strip() for name in ("type", "transaction_date", "notification", "amount")):
        return "note", "explicit_non_data_notice"
    description_observed = any(_known_value(read["shape"]) is not None and
                               read.get("geometry_status") != "ambiguous"
                               for read in row["field_readings"]["description"]["observations"])
    if description_observed and cues:
        return "data_candidate", "asset_text_with_transaction_field_evidence"
    if not any(value.strip() for value in raw.values()):
        return "unknown", "empty_ocr_is_not_blank_source_evidence"
    return "unknown", "possible_continuation_or_unreadable_fields"


def _content_slot_bands(page: dict) -> list[bool]:
    """Revalidate saved grid bands even when column positions are unknown."""
    slots = page.get("slots")
    count = page.get("physical_slot_count")
    if not isinstance(slots, list) or (count != len(slots) and not (count is None and not slots)):
        raise ValueError("physical slot count differs from saved grid")
    previous_bottom, rounding_ambiguous = -1., []
    for index, slot in enumerate(slots, 1):
        band = slot.get("band_pdf_points")
        if (type(slot.get("slot_index")) is not int or slot["slot_index"] != index or
                not isinstance(band, list) or len(band) != 2 or
                any(type(v) not in (int, float) or not isfinite(v) for v in band) or
                not 0 <= band[0] < band[1] or band[0] < previous_bottom):
            raise ValueError("saved slot order or PDF point band is invalid")
        previous_bottom = band[1]
        ambiguous = False
        for word in slot.get("words", []):
            box = word.get("bbox_pdf_points")
            if (not isinstance(box, list) or len(box) != 4 or
                    any(type(v) not in (int, float) or not isfinite(v) for v in box) or
                    not box[0] < box[2] or not box[1] < box[3]):
                raise ValueError("saved word geometry is invalid")
            center = (box[1] + box[3]) / 2
            if not band[0] <= center < band[1]:
                # v2 stores word coordinates rounded to 0.01 point after assigning
                # by the unrounded center. Keep that rounding ambiguity explicit.
                if not band[0] - .0051 <= center <= band[1] + .0051:
                    raise ValueError("saved word position differs from source slot band")
                ambiguous = True
        rounding_ambiguous.append(ambiguous)
    return rounding_ambiguous


def _complete_secondary_geometry(read: dict, *, field: str, slot: dict, page: dict) -> bool:
    """Require complete boxes inside the actual horizontal field crop.

    The original read join permits recorded pixel rounding / crop overhangs;
    that horizontal tolerance cannot replace primary column-boundary evidence.
    Vertical assignment retains the existing bound-read center/overhang rules;
    column OCR is cropped by x, not separately cropped to each physical row.
    """
    if (read.get("method") != "column_ocr" or read.get("geometry_status") != "source_bound"
            or _known_value(read["shape"]) is None or not read.get("detections")):
        return False
    positions = _FIELD_COLUMNS[field]
    start,end = min(positions),max(positions)+1
    top, bottom = slot["band_pdf_points"]
    for detection in read["detections"]:
        box = detection["bbox_pdf_points"]
        domain = (page.get("column_line_model") or {}).get("valid_y_span_pdf_points")
        if domain and not domain[0] <= box[1] < box[3] <= domain[1]:
            return False
        a,b = column_edges_at_y(page,box[1]),column_edges_at_y(page,box[3])
        if not (max(a[start],b[start])+.6 <= box[0] < box[2] <= min(a[end],b[end])-1.5
                and top <= (box[1]+box[3])/2 < bottom):
            return False
    return True


def _row_relation_evidence(row: dict, *, rounding_ambiguous: bool,
                            source_slot: dict, source_page: dict) -> dict:
    """Use all bound reads to establish a local relation, without choosing facts.

    One unambiguous recognized secondary read can supply missing primary shape
    evidence. Repeated reads do not outvote conflicting values or remove geometry
    blockers. Supporting references identify the actual field and read index.
    """
    reasons, field_evidence = [], {}
    if source_slot.get("column_model_outside_word_indices"):
        reasons.append("source_words_outside_column_model_domain")
    for field in ("description", "type", "transaction_date", "amount"):
        evidence = row["field_readings"][field]
        recognized = [(index, read, _known_value(read["shape"]))
                      for index, read in enumerate(evidence["observations"])
                      if read.get("geometry_status") != "ambiguous" and
                      _known_value(read["shape"]) is not None]
        values = {repr(value) for _, _, value in recognized}
        if len(values) > 1:
            status = "conflicting_observations"
            reasons.append(f"{field}_observation_conflict")
        elif values:
            status = "recognized_observations"
        else:
            status = "missing_observation"
            reasons.append(f"{field}_observation_missing")
        field_evidence[field] = {
            "status": status,
            "supporting_observations": [{"observation_index": index, "source_ref": read["source_ref"]}
                                        for index, read, _ in recognized],
            "primary_recognized": any(index == 0 for index, _, _ in recognized),
            "secondary_recognized": any(index > 0 for index, _, _ in recognized),
            "qualification": "not_assessed"}
    geometry = row["word_evidence"]
    if geometry["status"] != "source_words_linked":
        reasons.append("word_geometry_unavailable")
    if geometry["unassigned_word_indices"]:
        reasons.append("unassigned_column_words")
    crossing_refs, geometry_evidence, unresolved_geometry_fields = set(), {}, []
    for field, reading in row["field_readings"].items():
        warnings = [warning for warning in reading["geometry_warnings"]
                    if warning["scope"] == "field_boundary"]
        if not warnings:
            continue
        crossing_refs.update(warning["crossing_word_ref"] for warning in warnings)
        supports = [{"observation_index": index, "source_ref": read["source_ref"]}
                    for index, read in enumerate(reading["observations"]) if index > 0 and
                    _complete_secondary_geometry(read, field=field, slot=source_slot, page=source_page)]
        geometry_evidence[field] = {
            "status": "complete_secondary_geometry" if supports else "unresolved_primary_boundary",
            "supporting_observations": supports,
            "basis": "full_secondary_boxes_inside_horizontal_source_field_crop",
            "vertical_assignment_basis": "existing_bound_read_center_and_overhang_validation",
            "primary_crossing_word_refs": sorted({warning["crossing_word_ref"] for warning in warnings})}
        if not supports:
            unresolved_geometry_fields.append(field)
    if unresolved_geometry_fields:
        reasons.append("cross_field_word_geometry")
    if rounding_ambiguous:
        reasons.append("word_center_rounding_ambiguous")
    return {"schema_version": "oge-278t-relation-evidence/v1",
            "status": "supported_observation_relation" if not reasons else "unresolved",
            "blocking_reasons": sorted(reasons), "fields": field_evidence,
            "crossing_word_refs": sorted(crossing_refs), "field_geometry_evidence": geometry_evidence,
            "unresolved_geometry_fields": sorted(unresolved_geometry_fields),
            "qualification": "not_assessed"}


def observe_content_rows(observations: list[dict], *, document_page_count: int,
                         secondary_observations: list[dict] | None = None) -> dict:
    """Account for saved grids and single-row observations, without qualification.

    The whole-document page count must come from the bound source metadata,
    never from the selected OCR page count. Incomplete fragments remain explicit
    rather than being joined by printed number, asset name or array position.
    """
    from .source_row_ledger import page_ledger, report_ledger

    if type(document_page_count) is not int or document_page_count < 1:
        raise ValueError("bound document page count is required")
    fields = observe_field_shapes(observations, secondary_observations=secondary_observations)
    sha = fields["source_sha256"]
    originals = {}
    for item in observations:
        if item.get("coordinate_frame", "pdf_points") != "pdf_points":
            raise ValueError("OGE content rows require PDF point coordinates")
        for page in item["pages"]:
            number = page["page_number"]
            if type(number) is not int or not 1 <= number <= document_page_count:
                raise ValueError("observed page outside bound document")
            originals[number] = page
    pages, rows, unresolved, content, logical_evidence = [], [], [], [], []
    blocking_counts: Counter[str] = Counter()
    secondary_only_counts: Counter[str] = Counter()
    for page in fields["pages"]:
        number = page["page_number"]
        original = originals[number]
        rounding_ambiguous = _content_slot_bands(original)
        if page["status"] == "geometry_unknown":
            unknown_slots = [{"local_key": str(slot["slot_index"]),
                              "bounds": slot["band_pdf_points"], "disposition": "unknown"}
                             for slot in original["slots"]]
            content.extend({"page_number": number, "slot_key": slot["local_key"],
                            "disposition": "unknown", "reason": "column_geometry_unknown",
                            "field_observation_ref": None} for slot in unknown_slots)
            pages.append(page_ledger(
                source_id="oge", document_sha256=sha, page_number=number,
                adapter_version=_CONTENT_RULES, coverage="unknown", slots=unknown_slots,
                coverage_reason="column_geometry_unknown", coordinate_frame="pdf_points"))
            continue
        raw_slots = original["slots"]
        slots = []
        for index, (raw_slot, row) in enumerate(zip(raw_slots, page["slots"], strict=True), 1):
            band = raw_slot["band_pdf_points"]
            disposition, reason = _content_disposition(row)
            key = str(index)
            reference = {"page_number": number, "slot_key": key}
            observation_ref = row["field_readings"]["description"]["observations"][0]["source_ref"]
            slots.append({"local_key": key, "bounds": band,
                          "disposition": disposition, "observation_ref": observation_ref})
            content_row = {**reference, "disposition": disposition, "reason": reason,
                           "word_center_rounding_ambiguous": rounding_ambiguous[index - 1],
                           "field_observation_ref": observation_ref}
            content.append(content_row)
            if disposition != "data_candidate":
                continue
            relation = _row_relation_evidence(row, rounding_ambiguous=rounding_ambiguous[index - 1],
                                               source_slot=raw_slot, source_page=original)
            content_row["relation_evidence"] = relation
            if relation["status"] == "supported_observation_relation":
                local_key = f"page:{number}:slot:{key}"
                rows.append({"local_key": local_key, "slot_refs": [reference],
                             "observation_ref": observation_ref})
                logical_evidence.append({"local_key": local_key, "slot_refs": [reference],
                                         "field_evidence": relation["fields"],
                                         "field_geometry_evidence": relation["field_geometry_evidence"],
                                         "qualification": "not_assessed"})
                secondary_only_counts.update(field for field, evidence in relation["fields"].items()
                                             if evidence["secondary_recognized"] and not evidence["primary_recognized"])
            else:
                blocking_counts.update(relation["blocking_reasons"])
                unresolved.append({**reference, "reason": "single_disclosure_relation_not_established"})
        pages.append(page_ledger(
            source_id="oge", document_sha256=sha, page_number=number,
            adapter_version=_CONTENT_RULES, coverage="located", slots=slots,
            coordinate_frame="pdf_points"))
    ledger = report_ledger(
        source_id="oge", document_sha256=sha, adapter_version=_CONTENT_RULES,
        page_count=document_page_count, pages=pages, logical_rows=rows,
        unresolved_data_slots=unresolved)
    return {"field_observations": fields, "slot_content": content,
            "logical_row_field_evidence": logical_evidence,
            "relation_evidence_summary": {
                "unresolved_blocking_reason_counts": dict(sorted(blocking_counts.items())),
                "secondary_only_supported_field_counts": dict(sorted(secondary_only_counts.items()))},
            "source_report_ledger": ledger, "production_mutation_authorized": False}


def compare_field_observations(left: dict, right: dict) -> dict:
    """Compare two OCR passes only after their physical grid identities agree.

    Agreement is still an OCR observation, never a verified source fact.
    Pages present in only one pass remain explicit rather than disappearing.
    """
    if any(item.get("schema_version") != _INPUT_SCHEMA for item in (left, right)):
        raise ValueError("expected two saved 278-T raster slot observations v2")
    source = left.get("source_sha256")
    if not source or source != right.get("source_sha256"):
        raise ValueError("OCR passes must refer to the same PDF SHA-256")
    resolutions = [item.get("ocr_resolution_dpi") for item in (left, right)]
    if None in resolutions or resolutions[0] == resolutions[1]:
        raise ValueError("OCR passes must have distinct, recorded resolutions")

    def pages_by_number(item: dict) -> dict[int, dict]:
        pages = item.get("pages")
        if not isinstance(pages, list):
            raise ValueError("OCR pass has no page list")
        result = {page["page_number"]: page for page in pages}
        if len(result) != len(pages):
            raise ValueError("duplicate page in OCR pass")
        return result

    left_pages, right_pages = pages_by_number(left), pages_by_number(right)
    common = sorted(left_pages.keys() & right_pages.keys())
    left_shapes = {p["page_number"]: p for p in observe_field_shapes([left])["pages"]}
    right_shapes = {p["page_number"]: p for p in observe_field_shapes([right])["pages"]}
    fields = ("printed_number", "type", "transaction_date", "amount")
    counts: Counter[str] = Counter()
    pages = []
    for number in common:
        first, second = left_pages[number], right_pages[number]
        if first.get("column_geometry") != second.get("column_geometry") or \
                first.get("column_edges_pdf_points") != second.get("column_edges_pdf_points") or \
                first.get("column_line_model") != second.get("column_line_model") or \
                first.get("render_coordinate_frame") != second.get("render_coordinate_frame") or \
                first.get("physical_slot_count") != second.get("physical_slot_count"):
            raise ValueError(f"page {number} grid geometry differs between OCR passes")
        if first.get("column_geometry") != "oge_2024_six_column_grid":
            pages.append({"page_number": number, "status": "geometry_unknown", "slots": []})
            counts["geometry_unknown_pages"] += 1
            continue
        a_slots, b_slots = first["slots"], second["slots"]
        a_fields = left_shapes[number]["slots"]
        b_fields = right_shapes[number]["slots"]
        if len(a_slots) != len(b_slots) or len(a_slots) != len(a_fields) or \
                len(b_slots) != len(b_fields):
            raise ValueError(f"page {number} slot count differs between OCR passes")
        compared = []
        for a, b, a_field, b_field in zip(a_slots, b_slots, a_fields, b_fields):
            if a.get("slot_index") != b.get("slot_index") or \
                    a.get("band_pdf_points") != b.get("band_pdf_points"):
                raise ValueError(f"page {number} slot identity differs between OCR passes")
            field_results = {}
            for name in fields:
                av, bv = a_field["field_shapes"][name], b_field["field_shapes"][name]
                a_value = av.get("values") if name == "amount" else av.get("value")
                b_value = bv.get("values") if name == "amount" else bv.get("value")
                a_known = av["status"] == "ocr_shape_only"
                b_known = bv["status"] == "ocr_shape_only"
                if a_known and b_known:
                    status = "matching_shapes" if a_value == b_value else "conflicting_shapes"
                elif a_known or b_known:
                    status = "single_shape"
                else:
                    status = "no_shape"
                counts[f"{name}_{status}"] += 1
                field_results[name] = {
                    "status": status,
                    "values_by_resolution": {str(resolutions[0]): a_value,
                                             str(resolutions[1]): b_value},
                    "raw_by_resolution": {
                        str(resolutions[0]): a_field["raw_columns"][name] if name != "amount"
                        else [a_field["raw_columns"]["notification"],
                              a_field["raw_columns"]["amount"]],
                        str(resolutions[1]): b_field["raw_columns"][name] if name != "amount"
                        else [b_field["raw_columns"]["notification"],
                              b_field["raw_columns"]["amount"]],
                    },
                }
            compared.append({"slot_index": a["slot_index"], "band_pdf_points": a["band_pdf_points"],
                             "fields": field_results, "qualification": "not_assessed"})
            counts["compared_slots"] += 1
        pages.append({"page_number": number, "status": "fields_compared",
                      "slot_count": len(compared), "slots": compared})
    return {
        "schema_version": COMPARISON_SCHEMA, "source_sha256": source,
        "resolutions_dpi": resolutions, "compared_page_count": len(common),
        "left_only_pages": sorted(left_pages.keys() - right_pages.keys()),
        "right_only_pages": sorted(right_pages.keys() - left_pages.keys()),
        "summary": dict(sorted(counts.items())), "pages": pages,
        "production_mutation_authorized": False,
    }
