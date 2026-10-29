"""Prepare only positioned printed amount columns selected by observed row marks.

This schedules evidence, never assigns amounts. Pixel and interpretation checks
remain the materializer's responsibility; saved materialization does no OCR.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re

from .house_ptr_amount_header import evaluate_amount_header_pages, observe_amount_header
from .house_ptr_key_fields import _amount_marks


def plan_amount_header_columns(observation: dict) -> dict:
    source = observation.get("source_sha256")
    pages = observation.get("pages")
    if (observation.get("schema_version") != "house-ptr-cell-observation-shadow/v3" or
            observation.get("qualification_status") != "unverified_shadow_only" or
            not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{64}", source) or
            not isinstance(pages, list) or not 1 <= len(pages) <= 12 or
            [page.get("page") for page in pages] != list(range(1, len(pages) + 1))):
        raise ValueError("House amount column plan source or page census is invalid")
    requests = {}
    for page in pages:
        if (page.get("coordinate_frame") != "original_page" or
                page.get("orientation_degrees") not in (None, 0)):
            continue
        seen = set()
        for row in page.get("rows", []):
            band = row.get("physical_band_index")
            if type(band) is not int or band < 1 or band in seen:
                raise ValueError("House amount column plan has invalid or duplicate slots")
            seen.add(band)
            if row.get("disposition") != "data_candidate" or not row.get("cells"):
                continue
            cells = row["cells"].get("amount", [])
            letter, status, special, _ = _amount_marks(cells)
            # Do not choose between two marks or treat uncertain K as absent.
            if special is True:
                letter = "K" if letter is None and status == "no_strong_mark" else None
            elif len(cells) == 11 and special is not False:
                letter = None
            if letter is None:
                continue
            key = (page["page"], letter)
            request = {"page": key[0], "label": key[1], "amount_column_count": len(cells)}
            if key in requests and requests[key] != request:
                raise ValueError("House amount column plan has conflicting column geometry")
            requests[key] = request
    return {"schema_version": "house-amount-column-preparation-plan/v1",
            "source_sha256": source, "page_count": len(pages),
            "requests": [requests[key] for key in sorted(requests)]}


def missing_amount_header_columns(plan: dict, header: dict) -> list[dict]:
    """Reuse compatible attempts, including failed OCR, without hiding conflicts."""
    if (header.get("source_sha256") != plan["source_sha256"] or
            header.get("page_count") != plan["page_count"]):
        raise ValueError("House amount header cache source or page count differs")
    checked = evaluate_amount_header_pages(plan["source_sha256"], header.get("pages"))
    if any(checked[key] != header.get(key) for key in ("status", "ranges")):
        raise ValueError("House amount header cache interpretation differs")
    if header.get("reading_mode") not in (None, "printed_columns/v1"):
        raise ValueError("House amount header cache reading mode is unsupported")
    records = header.get("column_readings", [])
    if not isinstance(records, list):
        raise ValueError("House amount header cache readings are invalid")
    if records and header.get("reading_mode") != "printed_columns/v1":
        raise ValueError("House amount header cache columns lack a supported reading mode")
    by_key = {}
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("House amount header cache reading is invalid")
        key = item.get("page"), item.get("label")
        if (type(key[0]) is not int or not 1 <= key[0] <= plan["page_count"] or
                not isinstance(key[1], str) or key[1] not in list("ABCDEFGHIJK") or key in by_key):
            raise ValueError("House amount header cache has invalid or duplicate columns")
        by_key[key] = item
    missing = []
    for request in plan["requests"]:
        record = by_key.get((request["page"], request["label"]))
        # Old selected-column caches do not carry a per-reading count. Their
        # original page census supplies it; the consumer rechecks source pixels.
        page = header["pages"][request["page"] - 1]
        count = record.get("amount_column_count", page.get("amount_column_count", 11)) if record else None
        if record is not None and count != request["amount_column_count"]:
            raise ValueError("House amount header cache column geometry differs")
        if (record is None or record.get("status") not in {"raw_column_observed", "column_ocr_failed"} or
                record.get("render_dpi") != 300 or record.get("image_rotation_degrees") != 270 or
                record.get("coordinate_frame") != "original_page" or
                not isinstance(record.get("raw"), str) or
                not re.fullmatch(r"[0-9a-f]{64}", str(record.get("crop_sha256", "")))):
            missing.append(request)
    return missing


def prepare_amount_headers(pdf_bytes: bytes, observation: dict, *, executable: str,
                           existing_header: dict, max_column_readings: int = 24,
                           observer=None) -> dict:
    """Supplement an unresolved whole legend with bounded, deduplicated columns.

    The original whole-header evidence remains intact, including contradictions.
    Each request is bound to its actual page; no majority vote moves a printed
    range from one physical column or page to another.
    """
    plan = plan_amount_header_columns(observation)
    if hashlib.sha256(pdf_bytes).hexdigest() != plan["source_sha256"]:
        raise ValueError("House amount preparation source hash mismatch")
    if type(max_column_readings) is not int or not 1 <= max_column_readings <= 24:
        raise ValueError("House amount preparation budget is invalid")
    missing = missing_amount_header_columns(plan, existing_header)
    if existing_header["status"] == "printed_header_correlated":
        return deepcopy(existing_header)
    if len(missing) > max_column_readings:
        raise ValueError("House amount column preparation exceeds budget; OCR not started")
    result = deepcopy(existing_header)
    records = {(item["page"], item["label"]): item for item in result.get("column_readings", [])}
    observe = observer or observe_amount_header
    for page in sorted({item["page"] for item in missing}):
        selected = [item for item in missing if item["page"] == page]
        for start in range(0, len(selected), 2):
            batch = selected[start:start + 2]
            fresh = observe(pdf_bytes, source_sha256=plan["source_sha256"], executable=executable,
                            dpi=300, page_numbers=[page], column_labels=[item["label"] for item in batch])
            if (fresh.get("source_sha256") != plan["source_sha256"] or
                    fresh.get("page_count") != plan["page_count"] or
                    fresh.get("reading_mode") != "printed_columns/v1"):
                raise ValueError("House prepared amount columns source differs")
            wanted = {item["label"]: item for item in batch}
            fresh_keys = set()
            for item in fresh.get("column_readings", []):
                key = item.get("page"), item.get("label")
                if key[0] != page or key[1] not in wanted or key in fresh_keys:
                    raise ValueError("House prepared amount column request differs")
                fresh_keys.add(key)
                count = fresh["pages"][page - 1].get("amount_column_count")
                if count != wanted[key[1]]["amount_column_count"]:
                    raise ValueError("House prepared amount column geometry differs")
                records[key] = dict(item, amount_column_count=count)
    if records:
        result["reading_mode"] = "printed_columns/v1"
        result["column_readings"] = [records[key] for key in sorted(records)]
    result["column_preparation"] = {**plan,
        "missing_column_count": len(missing_amount_header_columns(plan, result))}
    return result
