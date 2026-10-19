"""Read-only field shapes from confirmed 2024 OGE 278-T raster cells.

This does not correct OCR, classify a cell as a qualified transaction, or grant source
eligibility.  It consumes saved slot observations so field work needs no new
PDF scan and pages without confirmed geometry stay unknown.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
import hashlib
import json
from math import isfinite
import re
from statistics import median


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
              "oge-278t-date-column-observation/v1": ("transaction_date", "date_column_observed"),
              "oge-278t-amount-column-observation/v1": ("amount", "amount_column_observed"),
              "oge-278t-amount-column-observation/v2": ("amount", "amount_column_observed")}


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
    derived = _observe_slot_columns(words, edges)
    owner = {}
    for name, expected in derived["column_observation"].items():
        saved = slot["column_observation"][name]
        if saved.get("word_indices") != expected["word_indices"] or saved["raw_ocr_text"] != expected["raw_ocr_text"]:
            raise ValueError("saved column word references differ from source geometry")
        owner.update({index: name for index in expected["word_indices"]})
    crossings = []
    for index, word in enumerate(words):
        x0, _, x1, _ = word["bbox_pdf_points"]
        boundaries = [{"left_column": _COLUMNS[i - 1], "right_column": _COLUMNS[i],
                       "x_pdf_points": edge,
                       "shorter_overhang_pdf_points": round(min(edge - x0, x1 - edge), 4)}
                      for i, edge in enumerate(edges[1:-1], 1) if x0 < edge < x1]
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
        observed_count = 0
        for page_index, page in enumerate(pages):
            number = page["page_number"]
            if number not in source_pages:
                raise ValueError("secondary field read references a page outside selected observations")
            original, original_page = source_pages[number]
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
                for detection in detections:
                    box = detection.get("bbox_pdf_points")
                    if (not isinstance(detection.get("text"), str) or
                            not isinstance(box, list) or len(box) != 4 or
                            any(type(v) not in (int, float) or not isfinite(v) for v in box) or
                            box[0] >= box[2] or box[1] >= box[3] or
                            not base["band_pdf_points"][0] <= (box[1] + box[3]) / 2 <
                            base["band_pdf_points"][1] or
                            not edges[left] <= (box[0] + box[2]) / 2 < edges[right]):
                        raise ValueError("secondary detection is outside its source field or slot")
                    if (box[0] < edges[left] + .6 - rounding or
                            box[2] > edges[right] - 1.5 + rounding or
                            max(base["band_pdf_points"][0] - box[1],
                                box[3] - base["band_pdf_points"][1], 0) > 3.0 + .02):
                        geometry_issues.append("detection_extends_beyond_crop_or_slot")
                if raw != " ".join(detection["text"] for detection in detections):
                    raise ValueError("secondary text differs from saved detections")
                if field == "amount":
                    shape = _amount_shape(raw)
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
                target["observations"].append(observation)
                observed_count += 1
        if reading.get("summary", {}).get("observed_slots") != observed_count:
            raise ValueError("secondary field summary differs from linked slots")
    counts = Counter()
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
    result["secondary_field_summary"] = dict(sorted(counts.items()))
    result["secondary_geometry_unknown_pages"] = coverage
    result["secondary_input_refs"] = input_refs


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
                description = (description_reading["ordered_ocr_text"] or "").strip()
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
                    "description": {"status": "ocr_text_only" if description and
                                    not _HEADER.search(description) else "unknown",
                                    "value": description or None},
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
    raw, shapes = row["raw_columns"], row["field_shapes"]
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
    if _known_value(shapes["description"]) and cues:
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
    pages, rows, unresolved, content = [], [], [], []
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
                adapter_version="oge-278t-content-rows/v1", coverage="unknown", slots=unknown_slots,
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
            content.append({**reference, "disposition": disposition, "reason": reason,
                            "word_center_rounding_ambiguous": rounding_ambiguous[index - 1],
                            "field_observation_ref": observation_ref})
            if disposition != "data_candidate":
                continue
            # Single row evidence requires one observed description/type/date/
            # amount, source word geometry, no unrelated words or field crossing.
            # This local relationship is not an assertion that the values are true.
            complete = all(_known_value(row["field_shapes"][field]) is not None
                           for field in ("description", "type", "transaction_date", "amount"))
            geometry = row["word_evidence"]
            conflicts = any(reading.get("consistency") == "conflicting_observations"
                            for reading in row["field_readings"].values())
            crossed = any(w["scope"] == "field_boundary" for reading in row["field_readings"].values()
                          for w in reading["geometry_warnings"])
            if (complete and geometry["status"] == "source_words_linked" and
                    not geometry["unassigned_word_indices"] and not crossed and not conflicts and
                    not rounding_ambiguous[index - 1]):
                rows.append({"local_key": f"page:{number}:slot:{key}", "slot_refs": [reference],
                             "observation_ref": observation_ref})
            else:
                unresolved.append({**reference, "reason": "single_disclosure_relation_not_established"})
        pages.append(page_ledger(
            source_id="oge", document_sha256=sha, page_number=number,
            adapter_version="oge-278t-content-rows/v1", coverage="located", slots=slots,
            coordinate_frame="pdf_points"))
    ledger = report_ledger(
        source_id="oge", document_sha256=sha, adapter_version="oge-278t-content-rows/v1",
        page_count=document_page_count, pages=pages, logical_rows=rows,
        unresolved_data_slots=unresolved)
    return {"field_observations": fields, "slot_content": content,
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
