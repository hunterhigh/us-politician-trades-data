"""Read-only field shapes from confirmed 2024 OGE 278-T raster cells.

This does not correct OCR, classify a cell as a transaction, or grant source
eligibility.  It consumes saved slot observations so field work needs no new
PDF scan and pages without confirmed geometry stay unknown.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
import re


SCHEMA = "oge-278t-field-shadow/v1"
COMPARISON_SCHEMA = "oge-278t-field-comparison/v1"
_INPUT_SCHEMA = "oge-278t-raster-slot-observation/v2"
_COLUMNS = ("printed_number", "description", "type", "transaction_date",
            "notification", "amount")
_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(20\d{2})")
_MONEY = re.compile(r"\$\s*([0-9][0-9,]*)")
_TYPE = {"purchase", "sale", "exchange"}
_HEADER = re.compile(r"\b(description|type|date|notification|amount)\b", re.I)


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


def observe_field_shapes(observations: list[dict]) -> dict:
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
    for item in observations:
        for page in item["pages"]:
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
            for slot in page["slots"]:
                columns = slot.get("column_observation")
                if not isinstance(columns, dict) or any(
                        name not in columns for name in _COLUMNS):
                    raise ValueError("confirmed column page lacks column observations")
                raw = {name: columns[name]["raw_ocr_text"] for name in _COLUMNS}
                printed_number = raw["printed_number"].strip()
                direction = raw["type"].strip().casefold()
                description = raw["description"].strip()
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
                slots.append({"slot_index": slot["slot_index"],
                              "raw_columns": raw, "field_shapes": field_shapes,
                              "issues": issues, "qualification": "not_assessed"})
            pages.append({"page_number": number, "status": "field_shapes_observed",
                          "slot_count": len(slots), "slots": slots})
    pages.sort(key=lambda page: page["page_number"])
    return {"schema_version": SCHEMA, "source_sha256": sources.pop(),
            "page_count": len(pages), "summary": dict(sorted(counts.items())),
            "pages": pages, "production_mutation_authorized": False}


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
