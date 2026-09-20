"""Conservative House PTR extraction, automatic qualification, and exception isolation."""
from __future__ import annotations

from datetime import datetime
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit

from .house import HouseIndexError

SCHEMA = "house-ptr-extraction/v1"
PARSER_VERSION = "house-ptr-2026-04"
LEGACY_PARSER_VERSION = "house-legacy-checkbox-2026-02"
PARSER_RETRY_VERSION = "house-parser-suite-2026-05"
DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
LEGACY_DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{2}")
AMOUNT_RANGE_RE = re.compile(r"^\$(\d[\d,]*)\s*-\s*\$(\d[\d,]*)$")
AMOUNT_EXACT_RE = re.compile(r"^\$(\d[\d,]*)(?:\.00)?$")
AMOUNT_OVER_RE = re.compile(r"^(?:Spouse/DC\s+)?Over\s+\$(\d[\d,]*)(?:\.00)?$", re.I)
ASSET_RE = re.compile(r"^(.*?)\s*(?:\(([A-Z][A-Z0-9.\-^/]{0,15})\))?\s*\[([A-Z0-9]{2})\]\s*$")
OWNER_CODES = {"": "Self", "SP": "Spouse", "DC": "Dependent Child", "JT": "Joint"}
TYPE_CODES = {"P": "purchase", "S": "sale", "E": "exchange"}
ASSET_TYPES = {
    "ST": "Stock", "OP": "Option", "CS": "Corporate Security", "CT": "Cryptocurrency",
    "RS": "Restricted Stock Unit", "AB": "Asset-Backed Security", "ET": "Exchange Traded Note",
    "MF": "Mutual Fund", "OT": "Other",
}
REVIEW_SCHEMA = "house-ptr-review/v1"
QUALIFICATION_SCHEMA = "house-ptr-qualification/v1"
OCR_MINIMUM_ROW_CONFIDENCE = 85.0
CORRECTION_FIELDS = {"owner", "asset_name", "ticker", "instrument_type", "transaction_type",
                     "option_type", "strike_price", "expiration_date", "transaction_date",
                     "notification_date", "amount_low", "amount_high"}
REVISION_ACTIONS = {"replace_prior", "standalone_correction"}
LEGACY_AMOUNT_BUCKETS = (
    (1001, 15000, "range"), (15001, 50000, "range"),
    (50001, 100000, "range"), (100001, 250000, "range"),
    (250001, 500000, "range"), (500001, 1000000, "range"),
    (1000001, 5000000, "range"), (5000001, 25000000, "range"),
    (25000001, 50000000, "range"), (50000001, None, "open_ended"),
)


def _clean(value: str) -> str:
    return " ".join(value.replace("\x00", "").split())


def _line(words: list[dict], *, x0: float, x1: float, top: float, tolerance: float = 2.0) -> str:
    selected = [word for word in words if x0 <= float(word["x0"]) < x1
                and abs(float(word["top"]) - top) <= tolerance]
    return _clean(" ".join(str(word["text"]) for word in sorted(selected, key=lambda word: float(word["x0"]))))


def _parse_amount(value: str) -> tuple[int, int | None, str] | None:
    matched = AMOUNT_RANGE_RE.fullmatch(value)
    if matched:
        return (int(matched.group(1).replace(",", "")),
                int(matched.group(2).replace(",", "")), "range")
    matched = AMOUNT_EXACT_RE.fullmatch(value)
    if matched:
        exact = int(matched.group(1).replace(",", ""))
        return exact, exact, "exact"
    matched = AMOUNT_OVER_RE.fullmatch(value)
    if matched:
        # Dollar values are integral in the frontend contract, so "over" starts at the next dollar.
        return int(matched.group(1).replace(",", "")) + 1, None, "open_ended"
    return None


def _parse_option_details(value: object) -> tuple[str | None, int | float | None, str | None]:
    """Read only explicitly disclosed option facts from the filing description."""
    if not isinstance(value, str):
        return None, None, None
    option_match = re.search(r"\b(call|put)\s+options?\b", value, re.I)
    strike_match = re.search(r"\bstrike\s+price(?:\s+of)?\s*\$([0-9][0-9,]*(?:\.[0-9]+)?)", value, re.I)
    expiry_match = re.search(
        r"\b(?:an\s+expiration\s+date\s+of|expiration\s+date\s+of|expires)\s+"
        r"(\d{1,2}/\d{1,2}/\d{2,4})\b", value, re.I)
    option_type = option_match.group(1).title() if option_match else None
    strike_price: int | float | None = None
    if strike_match:
        parsed = float(strike_match.group(1).replace(",", ""))
        strike_price = int(parsed) if parsed.is_integer() else parsed
    expiration_date = None
    if expiry_match:
        raw = expiry_match.group(1)
        try:
            expiration_date = datetime.strptime(raw, "%m/%d/%Y" if len(raw.rsplit("/", 1)[-1]) == 4
                                                else "%m/%d/%y").date().isoformat()
        except ValueError:
            pass
    return option_type, strike_price, expiration_date


def _is_mark(word: dict) -> bool:
    return bool(re.fullmatch(r"[Xx×/\\]{1,3}", _clean(str(word.get("text", "")))))


def _legacy_date(value: str) -> str | None:
    try:
        return datetime.strptime(value, "%m/%d/%y").date().isoformat()
    except ValueError:
        return None


def _raster_ink(page: dict, *, x0: float, x1: float, y0: float, y1: float) -> float:
    image = page.get("_image")
    if image is None:
        return 0.0
    left = max(int(x0 * image.width), 0)
    right = min(int(x1 * image.width), image.width)
    top = max(int(y0 * image.height), 0)
    bottom = min(int(y1 * image.height), image.height)
    if right <= left or bottom <= top:
        return 0.0
    pixels = image.crop((left, top, right, bottom)).getdata()
    return sum(value < 96 for value in pixels) / ((right - left) * (bottom - top))


def _legacy_filing_status(page: dict, words: list[dict], width: float, height: float,
                          compact: bool) -> str | None:
    marks = [float(word["x0"]) / width for word in words if _is_mark(word)
             and 0.31 * height <= float(word["top"]) <= 0.48 * height]
    centers = (0.49, 0.585) if compact else (0.46, 0.55)
    initial = any(abs(value - centers[0]) <= 0.035 for value in marks)
    amended = any(abs(value - centers[1]) <= 0.035 for value in marks)
    if not initial and not amended and page.get("_image") is not None:
        scores = [_raster_ink(page, x0=center - 0.009, x1=center + 0.009,
                              y0=0.39, y1=0.43) for center in centers]
        if max(scores) >= 0.035 and max(scores) >= min(scores) * 1.35:
            initial, amended = scores[0] > scores[1], scores[1] > scores[0]
    if initial == amended:
        return None
    return "New" if initial else "Amended"


def _legacy_mark_column(page: dict, words: list[dict], *, width: float, height: float, top: float,
                        x0: float, x1: float, columns: int) -> int | None:
    marks = [word for word in words if _is_mark(word)
             and x0 * width <= float(word["x0"]) < x1 * width
             and abs(float(word["top"]) - top) <= 7]
    positions = {min(int(((float(word["x0"]) / width) - x0) / ((x1 - x0) / columns)),
                     columns - 1) for word in marks}
    if len(positions) == 1:
        return next(iter(positions))
    if page.get("_image") is None:
        return None
    cell = (x1 - x0) / columns
    # The OCR top belongs to the date text near the row's upper edge. Sampling the inner
    # checkbox area removes printed cell borders and compares only handwritten ink.
    y0 = max((top + 5) / height, 0)
    y1 = min((top + 19) / height, 1)
    scores = [_raster_ink(page, x0=x0 + index * cell + cell * 0.35,
                          x1=x0 + (index + 1) * cell - cell * 0.35,
                          y0=y0, y1=y1) for index in range(columns)]
    ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
    if ranked[0][1] >= 0.025 and ranked[0][1] >= ranked[1][1] * 1.45:
        return ranked[0][0]
    return None


def _parse_legacy_word_pages(metadata: dict, source_sha256: str, pages: list[dict], *,
                             copy_allowed: bool | None, ocr_engine: str | None) -> dict:
    extracted: list[dict] = []
    first_page = pages[0]
    first_width, first_height = float(first_page.get("width", 0)), float(first_page.get("height", 0))
    first_words = first_page.get("words", [])
    statuses = {_legacy_filing_status(first_page, first_words, first_width, first_height, compact)
                for compact in (False, True)} - {None}
    document_filing_status = next(iter(statuses)) if len(statuses) == 1 else None
    for page_index, page in enumerate(pages):
        width, height = float(page.get("width", 0)), float(page.get("height", 0))
        words = page.get("words")
        if width <= 0 or height <= 0 or not isinstance(words, list):
            raise HouseIndexError("House legacy PTR page geometry is invalid")
        page_text = " ".join(_clean(str(word["text"])).upper() for word in words)
        full_table = "CAPITAL" in page_text and "PARTIAL" in page_text
        compact = not full_table and any(0.42 * width <= float(word["x0"]) < 0.47 * width
                      and LEGACY_DATE_RE.fullmatch(_clean(str(word["text"])))
                      for word in words)
        if full_table:
            owner_bounds, asset_bounds = (0.057, 0.093), (0.093, 0.261)
            type_bounds, type_values = (0.261, 0.35), ("purchase", "sale", "exchange")
            transaction_bounds, notification_bounds = (0.409, 0.46), (0.46, 0.515)
            amount_bounds = (0.515, 0.515 + (0.96 - 0.515) * 10 / 11)
        elif compact:
            owner_bounds, asset_bounds = (0.139, 0.165), (0.165, 0.335)
            type_bounds, type_values = (0.335, 0.441), ("purchase", "sale", "sale", "exchange")
            transaction_bounds, notification_bounds = (0.441, 0.494), (0.494, 0.553)
            amount_bounds = (0.553, 0.553 + (0.907 - 0.553) * 10 / 11)
        else:
            owner_bounds, asset_bounds = (0.09, 0.14), (0.14, 0.405)
            type_bounds, type_values = (0.405, 0.477), ("purchase", "sale", "exchange")
            transaction_bounds, notification_bounds = (0.477, 0.55), (0.55, 0.607)
            amount_bounds = (0.607, 0.95)
        filing_status = document_filing_status
        anchors = sorted({float(word["top"]) for word in words
                          if transaction_bounds[0] * width <= float(word["x0"]) < transaction_bounds[1] * width
                          and LEGACY_DATE_RE.fullmatch(_clean(str(word["text"])))
                          and float(word["top"]) >= 0.58 * height})
        for row_index, top in enumerate(anchors):
            transaction_raw = _line(words, x0=transaction_bounds[0] * width,
                                    x1=transaction_bounds[1] * width,
                                    top=top, tolerance=5)
            notification_raw = _line(words, x0=notification_bounds[0] * width,
                                     x1=notification_bounds[1] * width,
                                     top=top, tolerance=5)
            transaction_date = _legacy_date(transaction_raw)
            notification_date = _legacy_date(notification_raw)
            filing_year = int(metadata["filing_year"])
            if transaction_date is None or datetime.fromisoformat(transaction_date).year not in {
                    filing_year, filing_year - 1}:
                continue
            asset = _line(words, x0=asset_bounds[0] * width, x1=asset_bounds[1] * width,
                          top=top, tolerance=7)
            owner_raw = _line(words, x0=owner_bounds[0] * width, x1=owner_bounds[1] * width,
                              top=top, tolerance=7)
            owner_code = next((code for code in ("JT", "SP", "DC")
                               if re.search(rf"\b{code}\b", owner_raw, re.I)), "")
            type_column = _legacy_mark_column(page, words, width=width, height=height, top=top,
                                              x0=type_bounds[0], x1=type_bounds[1],
                                              columns=len(type_values))
            amount_column = _legacy_mark_column(page, words, width=width, height=height, top=top,
                                                x0=amount_bounds[0], x1=amount_bounds[1], columns=10)
            transaction_type = type_values[type_column] \
                if type_column is not None else None
            amount = LEGACY_AMOUNT_BUCKETS[amount_column] if amount_column is not None else (None, None, None)
            ticker_match = re.search(r"\(([A-Z][A-Z0-9.\-^/]{0,15})\)\s*$", asset)
            ticker = ticker_match.group(1) if ticker_match else None
            asset_name = asset[:ticker_match.start()].strip() if ticker_match else asset
            row_words = [word for word in words if abs(float(word["top"]) - top) <= 7]
            confidences = [float(word["ocr_confidence"]) for word in row_words
                           if word.get("ocr_confidence") is not None]
            stable = "|".join((source_sha256, str(page_index + 1), f"{top:.2f}", asset,
                               transaction_raw, notification_raw, str(type_column), str(amount_column)))
            extracted.append({
                "extraction_id": "house-ptr:" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24],
                "reported_transaction_id": None, "filing_status": filing_status,
                "owner_code": owner_code or None, "owner": OWNER_CODES.get(owner_code),
                "asset_name": asset_name, "ticker": ticker,
                "ticker_mapping_basis": "filing_explicit" if ticker else None,
                "asset_type_code": None, "instrument_type": "Unspecified",
                "transaction_type_raw": transaction_type[:1].upper() if transaction_type else None,
                "transaction_type": transaction_type, "transaction_date": transaction_date,
                "notification_date": notification_date, "amount_low": amount[0],
                "amount_high": amount[1], "amount_kind": amount[2], "amount_raw": None,
                "ocr_confidence": (round(sum(confidences) / len(confidences), 2) if confidences else None),
                "subholding_of": None, "location": None, "description": None,
                "evidence": {"page": page_index + 1, "pages": [page_index + 1],
                             "bbox_points": [round(0.09 * width, 2), round(top - 8, 2),
                                             round(0.95 * width, 2), round(top + 8, 2)],
                             "segments": [{"page": page_index + 1,
                                           "bbox_points": [round(0.09 * width, 2), round(top - 8, 2),
                                                           round(0.95 * width, 2), round(top + 8, 2)]}]},
                "verification_status": "awaiting_automatic_qualification",
                "review_reasons": ["automatic_qualification_required", "legacy_checkbox_form"],
            })
    if not extracted:
        raise HouseIndexError("House legacy PTR contains no recognized transaction rows")
    review_reasons = ["automatic_qualification_required", "source_use_clearance_required",
                      "legacy_checkbox_form"]
    if copy_allowed is False:
        review_reasons.append("source_pdf_copy_permission_disabled")
    return {
        "schema_version": SCHEMA, "parser_version": LEGACY_PARSER_VERSION,
        "source": {key: metadata.get(key) for key in ("source_id", "source_url", "document_id",
            "filer_name", "state_district", "filing_year", "filed_date", "archive_path")},
        "source_sha256": source_sha256, "source_pdf_copy_allowed": copy_allowed,
        "extraction_method": "tesseract_legacy_checkbox", "ocr_engine": ocr_engine,
        "transactions": extracted,
        "review": {"status": "awaiting_automatic_qualification", "production_eligible": False,
                   "reasons": review_reasons},
    }


def parse_word_pages(metadata: dict, source_sha256: str, pages: list[dict], *,
                     copy_allowed: bool | None, ocr_engine: str | None = None) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise HouseIndexError("House PTR source hash is invalid")
    document_id = str(metadata.get("document_id", ""))
    if (metadata.get("source_id") != "house_clerk" or metadata.get("filing_type") != "P" or
            not re.fullmatch(r"[0-9]{1,20}", document_id)):
        raise HouseIndexError("House PTR metadata is invalid")
    if not pages:
        raise HouseIndexError("House PTR contains no pages")
    if not any(page.get("words") for page in pages):
        raise HouseIndexError("House PTR requires OCR because it contains no extractable text")
    first_text = _clean(" ".join(str(word["text"]) for word in pages[0].get("words", [])))
    large_heading = [_clean(str(word["text"])) for word in pages[0].get("words", [])
                     if float(word.get("size", 0)) >= 18]
    title_matches = "Periodic Transaction Report" in first_text or large_heading[:3] == ["P", "T", "R"]
    uppercase_text = first_text.upper()
    has_legacy_table = ("AMOUNT" in uppercase_text and "TRANSACTION" in uppercase_text
                        and any(LEGACY_DATE_RE.fullmatch(_clean(str(word["text"])))
                                for page in pages for word in page.get("words", [])))
    legacy_form = (f"#{document_id}" not in first_text and ocr_engine is not None
                   and ((title_matches and "HOUSE" in uppercase_text) or has_legacy_table))
    if legacy_form:
        return _parse_legacy_word_pages(metadata, source_sha256, pages,
                                        copy_allowed=copy_allowed, ocr_engine=ocr_engine)
    if not title_matches or f"#{document_id}" not in first_text:
        raise HouseIndexError("House PTR header does not match its archived identity")

    page_data: list[dict] = []
    anchors: list[dict] = []
    for page_index, page in enumerate(pages):
        width = float(page.get("width", 0))
        height = float(page.get("height", 0))
        words = page.get("words")
        if width <= 0 or height <= 0 or not isinstance(words, list):
            raise HouseIndexError("House PTR page geometry is invalid")
        page_data.append({"width": width, "height": height, "words": words})
        for top in sorted({float(word["top"]) for word in words
                           if 0.53 * width <= float(word["x0"]) < 0.62 * width
                           and DATE_RE.fullmatch(_clean(str(word["text"])))}):
            anchors.append({"page_index": page_index, "top": top})

    extracted: list[dict] = []
    for position, anchor in enumerate(anchors):
        page_index, top = anchor["page_index"], anchor["top"]
        page_number = page_index + 1
        current = page_data[page_index]
        width, height, words = current["width"], current["height"], current["words"]
        following = anchors[position + 1] if position + 1 < len(anchors) else None
        last_page_index = following["page_index"] if following else page_index
        if following is None and page_index + 1 < len(page_data) and top >= 0.85 * height:
            # Some electronic PTRs split the final row after the amount dash. The next page repeats
            # the table header and continues the asset type and amount upper bound without a date.
            last_page_index = page_index + 1
        block: list[tuple[int, dict]] = []
        evidence_segments: list[dict] = []
        for segment_page_index in range(page_index, last_page_index + 1):
            segment = page_data[segment_page_index]
            start = top - 2 if segment_page_index == page_index else 0.14 * segment["height"]
            if following and segment_page_index == following["page_index"]:
                end = following["top"] - 2
            elif segment_page_index == page_index and following is None:
                end = min(segment["height"], top + 120)
                following_section = [float(word["top"]) for word in segment["words"]
                                     if top + 10 < float(word["top"]) < end
                                     and float(word.get("size", 0)) >= 11]
                if following_section:
                    end = min(end, min(following_section) - 2)
            else:
                end = segment["height"]
            footer_tops = [float(word["top"]) for word in segment["words"]
                           if "asset-type-codes.aspx" in _clean(str(word["text"]))]
            if footer_tops:
                footer_top = min(footer_tops)
                if start < footer_top < end:
                    end = footer_top - 2
            block.extend((segment_page_index, word) for word in segment["words"]
                         if start <= float(word["top"]) < end)
            evidence_segments.append({"page": segment_page_index + 1,
                                      "bbox_points": [round(0.04 * segment["width"], 2), round(start, 2),
                                                       round(0.90 * segment["width"], 2), round(end, 2)]})
        transaction_date = _line(words, x0=0.53 * width, x1=0.62 * width, top=top)
        notification_date = _line(words, x0=0.62 * width, x1=0.725 * width, top=top)
        raw_type = _line(words, x0=0.42 * width, x1=0.53 * width, top=top)
        reported_transaction_id = _line(words, x0=0.04 * width, x1=0.105 * width, top=top)
        raw_owner = _line(words, x0=0.105 * width, x1=0.165 * width, top=top)
        date_size = max((float(word.get("size", 0)) for word in words
                         if abs(float(word["top"]) - top) <= 2 and
                         0.53 * width <= float(word["x0"]) < 0.62 * width), default=0)
        asset_words = [(segment_page_index, word) for segment_page_index, word in block
                       if 0.165 * page_data[segment_page_index]["width"] <= float(word["x0"]) <
                       0.42 * page_data[segment_page_index]["width"]
                       and float(word.get("size", date_size)) >= date_size - 0.2]
        asset = _clean(" ".join(str(word["text"]) for segment_page_index, word in sorted(
            asset_words, key=lambda item: (item[0], round(float(item[1]["top"]), 1), float(item[1]["x0"])))))
        amount_words = [(segment_page_index, word) for segment_page_index, word in block
                        if 0.725 * page_data[segment_page_index]["width"] <= float(word["x0"]) <
                        0.90 * page_data[segment_page_index]["width"]
                        and float(word.get("size", date_size)) >= date_size - 0.2]
        amount = _clean(" ".join(str(word["text"]) for segment_page_index, word in sorted(
            amount_words, key=lambda item: (item[0], round(float(item[1]["top"]), 1), float(item[1]["x0"])))))
        note_words = [(segment_page_index, word) for segment_page_index, word in block
                      if 0.165 * page_data[segment_page_index]["width"] <= float(word["x0"]) <
                      0.90 * page_data[segment_page_index]["width"]
                      and float(word.get("size", date_size)) < date_size - 0.2]
        note_lines: dict[tuple[int, float], list[dict]] = {}
        for segment_page_index, word in note_words:
            note_lines.setdefault((segment_page_index, round(float(word["top"]), 1)), []).append(word)
        details: dict[str, str] = {}
        last_detail: str | None = None
        patterns = (("filing_status", re.compile(r"^(?:F|Filing)\s+(?:S|Status):\s*(.*)$", re.I)),
                    ("subholding_of", re.compile(r"^(?:S|Subholding)\s+(?:O|Of):\s*(.*)$", re.I)),
                    ("location", re.compile(r"^(?:L|Location):\s*(.*)$", re.I)),
                    ("description", re.compile(r"^(?:D|Description):\s*(.*)$", re.I)))
        for line_words in (note_lines[key] for key in sorted(note_lines)):
            line = _clean(" ".join(str(word["text"]) for word in sorted(line_words, key=lambda word: float(word["x0"]))))
            if re.match(r"^\*\s*For the complete list of asset type abbreviations", line, re.I):
                last_detail = None
                continue
            matched = False
            for name, pattern in patterns:
                match = pattern.match(line)
                if match:
                    details[name] = match.group(1).strip()
                    last_detail = name
                    matched = True
                    break
            if not matched and last_detail and line:
                details[last_detail] = (details[last_detail] + " " + line).strip()
        try:
            transaction_iso = datetime.strptime(transaction_date, "%m/%d/%Y").date().isoformat()
            notification_iso = datetime.strptime(notification_date, "%m/%d/%Y").date().isoformat()
        except ValueError:
            raise HouseIndexError("House PTR row has an invalid date") from None
        amount_value = _parse_amount(amount)
        asset_match = ASSET_RE.fullmatch(asset)
        type_code = raw_type[:1]
        if not amount_value or not asset_match or type_code not in TYPE_CODES:
            failures = ",".join(name for name, valid in (("amount", amount_value is not None),
                ("asset", asset_match is not None), ("transaction_type", type_code in TYPE_CODES)) if not valid)
            raise HouseIndexError(
                f"House PTR page {page_number} row {position + 1} has unsupported {failures} layout")
        asset_name, ticker, asset_type_code = asset_match.groups()
        amount_low, amount_high, amount_kind = amount_value
        option_type, strike_price, expiration_date = _parse_option_details(details.get("description")) \
            if ASSET_TYPES.get(asset_type_code) == "Option" else (None, None, None)
        ocr_confidences = [float(word["ocr_confidence"]) for _, word in block
                           if word.get("ocr_confidence") is not None]
        reasons = ["automatic_qualification_required"]
        if raw_owner not in OWNER_CODES:
            reasons.append("unknown_owner_code")
        if asset_type_code not in ASSET_TYPES:
            reasons.append("unknown_asset_type_code")
        if ticker is None:
            reasons.append("ticker_not_explicitly_reported")
        if reported_transaction_id and not re.fullmatch(r"[0-9]{1,30}", reported_transaction_id):
            reasons.append("reported_transaction_id_invalid")
        if not details.get("filing_status"):
            reasons.append("filing_status_missing")
        elif details["filing_status"].lower() != "new":
            reasons.append("non_new_filing_requires_revision_resolution")
        stable = "|".join((source_sha256, str(page_number), f"{top:.2f}", asset,
                           transaction_date, raw_type, raw_owner, amount,
                           reported_transaction_id, details.get("filing_status", "")))
        extracted.append({
            "extraction_id": "house-ptr:" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24],
            "reported_transaction_id": reported_transaction_id or None,
            "filing_status": details.get("filing_status"),
            "owner_code": raw_owner or None,
            "owner": OWNER_CODES.get(raw_owner),
            "asset_name": asset_name.strip(),
            "ticker": ticker,
            "ticker_mapping_basis": "filing_explicit" if ticker else None,
            "asset_type_code": asset_type_code,
            "instrument_type": ASSET_TYPES.get(asset_type_code),
            "option_type": option_type,
            "strike_price": strike_price,
            "expiration_date": expiration_date,
            "transaction_type_raw": raw_type,
            "transaction_type": TYPE_CODES[type_code],
            "transaction_date": transaction_iso,
            "notification_date": notification_iso,
            "amount_low": amount_low,
            "amount_high": amount_high,
            "amount_kind": amount_kind,
            "amount_raw": amount,
            "ocr_confidence": (round(sum(ocr_confidences) / len(ocr_confidences), 2)
                               if ocr_confidences else None),
            "subholding_of": details.get("subholding_of"),
            "location": details.get("location"),
            "description": details.get("description"),
            "evidence": {"page": page_number, "pages": [segment["page"] for segment in evidence_segments],
                         "bbox_points": evidence_segments[0]["bbox_points"], "segments": evidence_segments},
            "verification_status": "awaiting_automatic_qualification",
            "review_reasons": reasons,
        })
    if not extracted:
        raise HouseIndexError("House PTR contains no recognized transaction rows")
    if len({row["extraction_id"] for row in extracted}) != len(extracted):
        raise HouseIndexError("House PTR produced duplicate extraction IDs")
    review_reasons = ["automatic_qualification_required", "source_use_clearance_required"]
    if copy_allowed is False:
        review_reasons.append("source_pdf_copy_permission_disabled")
    return {
        "schema_version": SCHEMA,
        "parser_version": PARSER_VERSION,
        "source": {key: metadata.get(key) for key in ("source_id", "source_url", "document_id",
            "filer_name", "state_district", "filing_year", "filed_date", "archive_path")},
        "source_sha256": source_sha256,
        "source_pdf_copy_allowed": copy_allowed,
        "extraction_method": "tesseract_ocr" if ocr_engine else "native_pdf_text",
        "ocr_engine": ocr_engine,
        "transactions": extracted,
        "review": {"status": "awaiting_automatic_qualification", "production_eligible": False,
                   "reasons": review_reasons},
    }


def qualify_automatic(extraction: dict, identity: dict) -> dict:
    """Promote deterministic standard rows and isolate every ambiguous row with explicit reasons."""
    if extraction.get("schema_version") != SCHEMA:
        raise HouseIndexError("House PTR extraction schema is invalid for automatic qualification")
    source = extraction.get("source") or {}
    document_id = source.get("document_id")
    if identity.get("document_id") != document_id:
        raise HouseIndexError("House PTR identity does not match its extraction")
    identity_valid = (
        identity.get("status") in {"matched_automatically", "suggested_requires_review"}
        and identity.get("match_basis") == "official_roster_exact_district_first_last_name"
        and isinstance(identity.get("roster_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", identity["roster_sha256"])
    )
    person_id = identity.get("person_id")
    evidence_url = identity.get("evidence_url")
    parsed_identity_url = urlsplit(evidence_url) if isinstance(evidence_url, str) else None
    identity_valid = bool(identity_valid and isinstance(person_id, str)
        and re.fullmatch(r"house:[A-Z][0-9]{6}", person_id)
        and parsed_identity_url and parsed_identity_url.scheme == "https"
        and parsed_identity_url.hostname == "bioguide.congress.gov")
    filed_date = source.get("filed_date")
    try:
        filed_day = datetime.strptime(filed_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise HouseIndexError("House PTR extraction has an invalid official filing date") from None
    filed_at = f"{filed_date}T00:00:00Z"
    transactions: list[dict] = []
    quarantined: list[dict] = []
    for row in extraction.get("transactions", []):
        reasons: list[str] = []
        if not identity_valid:
            reasons.append("identity_not_deterministic")
        if str(row.get("filing_status", "")).lower() != "new" or row.get("reported_transaction_id"):
            reasons.append("revision_relationship_unresolved")
        if row.get("amount_kind") == "open_ended" or row.get("amount_high") is None:
            reasons.append("open_ended_amount_not_representable")
        if str(extraction.get("extraction_method", "")).startswith("tesseract_") and (
                not isinstance(row.get("ocr_confidence"), (int, float))
                or row["ocr_confidence"] < OCR_MINIMUM_ROW_CONFIDENCE):
            reasons.append("ocr_confidence_below_threshold")
        if not isinstance(row.get("asset_name"), str) or not row["asset_name"].strip():
            reasons.append("asset_name_invalid")
        if row.get("owner") not in set(OWNER_CODES.values()):
            reasons.append("owner_not_normalized")
        if row.get("transaction_type") not in set(TYPE_CODES.values()):
            reasons.append("transaction_type_not_normalized")
        ticker = row.get("ticker")
        if ticker is not None and (not isinstance(ticker, str) or not re.fullmatch(
                r"[A-Z0-9][A-Z0-9.\-^/]{0,31}", ticker)):
            reasons.append("ticker_invalid")
        low, high = row.get("amount_low"), row.get("amount_high")
        if type(low) is not int or (high is not None and type(high) is not int) or \
                (type(high) is int and not 0 <= low <= high):
            reasons.append("amount_invalid")
        parsed_dates = {}
        for date_field in ("transaction_date", "notification_date"):
            try:
                parsed_dates[date_field] = datetime.strptime(row[date_field], "%Y-%m-%d").date()
            except (KeyError, TypeError, ValueError):
                reasons.append(f"{date_field}_invalid")
        if len(parsed_dates) == 2 and not (
                parsed_dates["transaction_date"] <= parsed_dates["notification_date"] <= filed_day):
            reasons.append("date_sequence_invalid")
        option_type, strike_price, expiration_date = _parse_option_details(row.get("description"))
        if row.get("instrument_type") == "Option":
            option_type = row.get("option_type") or option_type
            strike_price = row.get("strike_price") if row.get("strike_price") is not None else strike_price
            expiration_date = row.get("expiration_date") or expiration_date
            if option_type not in {"Call", "Put"} or type(strike_price) not in {int, float} \
                    or strike_price <= 0 or expiration_date is None:
                reasons.append("option_details_incomplete")
            else:
                try:
                    expiry_day = datetime.strptime(expiration_date, "%Y-%m-%d").date()
                    if parsed_dates.get("transaction_date") and expiry_day < parsed_dates["transaction_date"]:
                        reasons.append("option_expiration_invalid")
                except (TypeError, ValueError):
                    reasons.append("option_expiration_invalid")
        if reasons:
            quarantined.append({
                "extraction_id": row.get("extraction_id"),
                "reasons": sorted(set(reasons)),
                "source_sha256": extraction.get("source_sha256"),
                "evidence": row.get("evidence"),
            })
            continue
        transactions.append({
            "id": row["extraction_id"], "filing_id": document_id, "person_id": person_id,
            "owner": row["owner"], "asset_name": row["asset_name"], "ticker": ticker,
            "ticker_mapping_basis": "filing_explicit" if ticker else None,
            "instrument_type": row.get("instrument_type"), "transaction_type": row["transaction_type"],
            **({"option_type": option_type, "strike_price": strike_price,
                "expiration_date": expiration_date} if row.get("instrument_type") == "Option" else {}),
            "transaction_date": row["transaction_date"], "filed_at": filed_at,
            "amount_low": low, "amount_high": high, "position_effect": "unknown",
            "position_effect_basis": None, "source_id": "house_clerk", "source": "U.S. House Clerk",
            "source_url": source["source_url"], "verification_status": "official_matched",
        })
    return {
        "schema_version": QUALIFICATION_SCHEMA,
        "source_sha256": extraction.get("source_sha256"),
        "parser_version": extraction.get("parser_version"),
        "document_id": document_id,
        "identity": identity,
        "transactions": transactions,
        "quarantined": quarantined,
        "qualification": {
            "method": "deterministic_automatic_rules",
            "qualified_count": len(transactions),
            "quarantined_count": len(quarantined),
            "production_eligible": bool(transactions),
        },
    }


def _words_from_tesseract_tsv(value: str, *, points_per_pixel: float) -> list[dict]:
    words: list[dict] = []
    try:
        rows = csv.DictReader(io.StringIO(value), delimiter="\t")
        for row in rows:
            text = _clean(row.get("text") or "")
            if row.get("level") != "5" or not text:
                continue
            confidence = float(row["conf"])
            if confidence < 0:
                continue
            words.append({
                "text": text,
                "x0": float(row["left"]) * points_per_pixel,
                "top": float(row["top"]) * points_per_pixel,
                "size": max(float(row["height"]) * points_per_pixel, 1.0),
                "ocr_confidence": confidence,
            })
    except (KeyError, TypeError, ValueError):
        raise HouseIndexError("House PTR OCR returned invalid word geometry") from None
    return words


def _ocr_pdf_pages(document, *, resolution: int = 200) -> tuple[list[dict], str]:
    executable = shutil.which("tesseract")
    if not executable:
        raise HouseIndexError("House PTR requires OCR but the OCR engine is unavailable")
    version = subprocess.run([executable, "--version"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, timeout=15, check=False)
    engine = (version.stdout.splitlines() or [""])[0].strip()
    if version.returncode or not engine.lower().startswith("tesseract "):
        raise HouseIndexError("House PTR OCR engine version is unavailable")
    pages: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="house-ptr-ocr-") as folder:
        for index, page in enumerate(document.pages):
            image_path = Path(folder) / f"page-{index + 1}.png"
            image = page.to_image(resolution=resolution, antialias=True).original.convert("L")
            image.save(image_path, format="PNG")
            completed = subprocess.run(
                [executable, str(image_path), "stdout", "--dpi", str(resolution), "-l", "eng", "tsv"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120, check=False)
            if completed.returncode:
                raise HouseIndexError(f"House PTR OCR failed on page {index + 1}")
            pages.append({"width": page.width, "height": page.height,
                          "_image": image,
                          "words": _words_from_tesseract_tsv(
                              completed.stdout, points_per_pixel=72.0 / resolution)})
    return pages, engine


def parse_archived_pdf(archive_root: Path, metadata_path: Path) -> dict:
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise HouseIndexError("House PTR metadata file is invalid") from None
    relative = metadata.get("archive_path")
    if not isinstance(relative, str) or "\\" in relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise HouseIndexError("House PTR archive path is invalid")
    root = archive_root.resolve()
    pdf_path = (root / relative).resolve()
    if root not in pdf_path.parents:
        raise HouseIndexError("House PTR archive path escapes its root")
    try:
        content = pdf_path.read_bytes()
    except OSError:
        raise HouseIndexError("Archived House PTR is unavailable") from None
    source_sha = hashlib.sha256(content).hexdigest()
    if source_sha != metadata.get("sha256"):
        raise HouseIndexError("Archived House PTR hash does not match its metadata")
    try:
        import pdfplumber
    except ImportError:
        raise HouseIndexError("PTR extraction requires the optional pdfplumber package") from None
    try:
        with pdfplumber.open(pdf_path) as document:
            copy_allowed = getattr(document.doc, "is_extractable", None)
            pages = [{"width": page.width, "height": page.height,
                      "words": page.extract_words(x_tolerance=2, y_tolerance=2, extra_attrs=["size"])}
                     for page in document.pages]
            ocr_engine = None
            if not any(page["words"] for page in pages):
                pages, ocr_engine = _ocr_pdf_pages(document)
    except HouseIndexError:
        raise
    except Exception as exc:
        raise HouseIndexError(f"House PTR extraction failed: {type(exc).__name__}") from None
    return parse_word_pages(metadata, source_sha, pages, copy_allowed=copy_allowed,
                            ocr_engine=ocr_engine)


def make_review_template(extraction: dict) -> dict:
    if extraction.get("schema_version") != SCHEMA or extraction.get("review", {}).get("production_eligible") is not False:
        raise HouseIndexError("House PTR extraction is not a review-queue document")
    return {
        "schema_version": REVIEW_SCHEMA,
        "source_sha256": extraction.get("source_sha256"),
        "parser_version": extraction.get("parser_version"),
        "document_id": extraction.get("source", {}).get("document_id"),
        "identity": {"person_id": None, "evidence_url": None},
        "filing": {"filed_at": None},
        "source_use_clearance": {"status": "pending", "reference": None},
        "review": {"decision": "pending", "reviewed_by": None, "reviewed_at": None, "note": None},
        "rows": [{"extraction_id": row["extraction_id"], "decision": "pending",
                  "corrections": {}, "note": None,
                  "revision": {
                      "action": "pending" if row.get("reported_transaction_id") or
                      str(row.get("filing_status", "")).lower() != "new" else "none",
                      "prior_record_id": None,
                  }}
                 for row in extraction.get("transactions", [])],
    }


def _review_time(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise HouseIndexError(f"House PTR review requires {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HouseIndexError(f"House PTR review has invalid {field}") from None
    if parsed.tzinfo is None:
        raise HouseIndexError(f"House PTR review requires a timezone for {field}")
    return parsed


def promote_review(extraction: dict, review: dict) -> dict:
    if review.get("schema_version") != REVIEW_SCHEMA or extraction.get("schema_version") != SCHEMA:
        raise HouseIndexError("House PTR review schema is invalid")
    for field, expected in (("source_sha256", extraction.get("source_sha256")),
                            ("parser_version", extraction.get("parser_version")),
                            ("document_id", extraction.get("source", {}).get("document_id"))):
        if review.get(field) != expected:
            raise HouseIndexError(f"House PTR review {field} does not match its extraction")
    identity = review.get("identity") or {}
    person_id, evidence_url = identity.get("person_id"), identity.get("evidence_url")
    if not isinstance(person_id, str) or not re.fullmatch(r"house:[A-Za-z0-9._-]{2,80}", person_id):
        raise HouseIndexError("House PTR review requires a stable House person ID")
    parsed_url = urlsplit(evidence_url) if isinstance(evidence_url, str) else None
    identity_hosts = {"bioguide.congress.gov", "clerk.house.gov", "www.house.gov", "house.gov", "congress.gov", "www.congress.gov"}
    if not parsed_url or parsed_url.scheme != "https" or parsed_url.hostname not in identity_hosts:
        raise HouseIndexError("House PTR review requires an official identity evidence URL")
    clearance = review.get("source_use_clearance") or {}
    if clearance.get("status") != "approved" or not isinstance(clearance.get("reference"), str) \
            or not clearance["reference"].strip():
        raise HouseIndexError("House PTR review requires recorded source-use clearance")
    decision = review.get("review") or {}
    if decision.get("decision") != "approved" or not isinstance(decision.get("reviewed_by"), str) \
            or not decision["reviewed_by"].strip():
        raise HouseIndexError("House PTR review is not approved by a named reviewer")
    reviewed_at = _review_time(decision.get("reviewed_at"), "reviewed_at")
    filed_at_text = (review.get("filing") or {}).get("filed_at")
    filed_at = _review_time(filed_at_text, "filed_at")
    if filed_at.date().isoformat() != extraction.get("source", {}).get("filed_date"):
        raise HouseIndexError("House PTR reviewed filed_at must preserve the official filing date")
    extracted = {row.get("extraction_id"): row for row in extraction.get("transactions", [])}
    review_rows = review.get("rows")
    if not isinstance(review_rows, list) or len(review_rows) != len(extracted):
        raise HouseIndexError("House PTR review must decide every extracted row")
    indexed_reviews = {row.get("extraction_id"): row for row in review_rows if isinstance(row, dict)}
    if set(indexed_reviews) != set(extracted) or len(indexed_reviews) != len(review_rows):
        raise HouseIndexError("House PTR review row identities do not match the extraction")
    transactions = []
    revisions = []
    for extraction_id, source_row in extracted.items():
        row_review = indexed_reviews[extraction_id]
        row_decision = row_review.get("decision")
        note = row_review.get("note")
        if row_decision == "rejected":
            if not isinstance(note, str) or not note.strip():
                raise HouseIndexError("Rejected House PTR rows require a review note")
            continue
        if row_decision != "accepted":
            raise HouseIndexError("Every House PTR row must be accepted or rejected")
        corrections = row_review.get("corrections") or {}
        if not isinstance(corrections, dict) or set(corrections) - CORRECTION_FIELDS:
            raise HouseIndexError("House PTR row contains unsupported corrections")
        if corrections and (not isinstance(note, str) or not note.strip()):
            raise HouseIndexError("Corrected House PTR rows require a review note")
        row = {**source_row, **corrections}
        filing_status = row.get("filing_status")
        if not isinstance(filing_status, str) or not filing_status.strip():
            raise HouseIndexError("Accepted House PTR rows require a filing status")
        revision = row_review.get("revision")
        if not isinstance(revision, dict):
            raise HouseIndexError("Accepted House PTR rows require a revision decision")
        revision_action = revision.get("action")
        prior_record_id = revision.get("prior_record_id")
        requires_revision = bool(row.get("reported_transaction_id")) or filing_status.lower() != "new"
        if requires_revision:
            if revision_action not in REVISION_ACTIONS:
                raise HouseIndexError("Amended House PTR rows require a resolved revision action")
            if revision_action == "replace_prior":
                if not isinstance(prior_record_id, str) or not re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9:._/-]{2,199}", prior_record_id):
                    raise HouseIndexError("House PTR replacement revisions require a prior record ID")
            elif prior_record_id is not None:
                raise HouseIndexError("Standalone House PTR corrections cannot name a prior record ID")
            if revision_action == "standalone_correction" and (not isinstance(note, str) or not note.strip()):
                raise HouseIndexError("Standalone House PTR corrections require a review note")
            revisions.append({
                "extraction_id": extraction_id,
                "reported_transaction_id": row.get("reported_transaction_id"),
                "filing_status": filing_status,
                "action": revision_action,
                "prior_record_id": prior_record_id,
            })
        elif revision_action != "none" or prior_record_id is not None:
            raise HouseIndexError("New House PTR rows require revision action none")
        if row.get("owner") not in set(OWNER_CODES.values()) or row.get("transaction_type") not in set(TYPE_CODES.values()):
            raise HouseIndexError("House PTR reviewed row has an invalid owner or transaction type")
        ticker = row.get("ticker")
        if ticker is not None and (not isinstance(ticker, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-^/]{0,31}", ticker)):
            raise HouseIndexError("House PTR reviewed row has an invalid ticker")
        for date_field in ("transaction_date", "notification_date"):
            try:
                datetime.strptime(row[date_field], "%Y-%m-%d")
            except (KeyError, TypeError, ValueError):
                raise HouseIndexError(f"House PTR reviewed row has an invalid {date_field}") from None
        low, high = row.get("amount_low"), row.get("amount_high")
        if type(low) is not int or type(high) is not int or not 0 <= low <= high:
            raise HouseIndexError("House PTR reviewed row has an invalid amount range")
        transactions.append({
            "id": extraction_id, "filing_id": review["document_id"], "person_id": person_id,
            "owner": row["owner"], "asset_name": row["asset_name"], "ticker": ticker,
            "ticker_mapping_basis": "filing_explicit" if ticker else None,
            "instrument_type": row.get("instrument_type"), "transaction_type": row["transaction_type"],
            "transaction_date": row["transaction_date"], "filed_at": filed_at_text,
            "amount_low": low, "amount_high": high, "position_effect": "unknown",
            "position_effect_basis": None, "source_id": "house_clerk", "source": "U.S. House Clerk",
            "source_url": extraction["source"]["source_url"], "verification_status": "official_matched",
        })
    if not transactions:
        raise HouseIndexError("House PTR review accepted no transaction rows")
    audit = {"schema_version": "house-ptr-reviewed/v1", "source_sha256": review["source_sha256"],
             "parser_version": review["parser_version"], "document_id": review["document_id"],
             "person_id": person_id, "identity_evidence_url": evidence_url,
             "source_use_clearance_reference": clearance["reference"],
             "reviewed_by": decision["reviewed_by"], "reviewed_at": reviewed_at.isoformat(),
             "accepted_count": len(transactions), "rejected_count": len(extracted) - len(transactions),
             "revision_count": len(revisions)}
    return {"audit": audit, "transactions": transactions, "revisions": revisions}
