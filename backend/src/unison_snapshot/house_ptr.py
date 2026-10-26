"""Conservative House PTR extraction, automatic qualification, and exception isolation."""
from __future__ import annotations

from datetime import datetime
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit

from .house import HouseIndexError

SCHEMA = "house-ptr-extraction/v1"
PARSER_VERSION = "house-ptr-2026-05"
LEGACY_PARSER_VERSION = "house-legacy-checkbox-2026-09-date-normalization-v1"
PARSER_RETRY_VERSION = "house-parser-suite-native-opacity-v1"
DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
AMOUNT_RANGE_RE = re.compile(r"^\$(\d[\d,]*)\s*-\s*\$(\d[\d,]*)$")
AMOUNT_EXACT_RE = re.compile(r"^\$(\d[\d,]*)(?:\.(\d{2}))?$")
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
DETERMINISTIC_IDENTITY_BASES = {
    "official_roster_exact_district_first_last_name",
    "official_roster_exact_district_unique_surname",
    "official_roster_same_state_unique_first_last_name_redistricted",
}
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
ZERO_TRANSACTION_STATEMENTS = {
    "nothingtoreport": "Nothing to report",
    "notransactiontoreport": "No transaction to report",
    "notransactionstoreport": "No transactions to report",
}


def _clean(value: str) -> str:
    return " ".join(value.replace("\x00", "").split())


def _explicit_zero_transaction_declaration(pages: list[dict]) -> dict | None:
    """Return evidence only for an explicit empty filing with no row-like content."""
    candidate: dict | None = None
    row_evidence = False
    for page_index, page in enumerate(pages):
        width, height = float(page.get("width", 0)), float(page.get("height", 0))
        words = page.get("words")
        if width <= 0 or height <= 0 or not isinstance(words, list):
            continue
        lines: list[list[dict]] = []
        for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
            if not lines or abs(float(word["top"]) - float(lines[-1][0]["top"])) > 3:
                lines.append([word])
            else:
                lines[-1].append(word)
        for line_words in lines:
            ordered = sorted(line_words, key=lambda item: float(item["x0"]))
            text = _clean(" ".join(str(word["text"]) for word in ordered))
            compact = re.sub(r"[^a-z]", "", text.lower())
            statement = next((display for key, display in ZERO_TRANSACTION_STATEMENTS.items()
                              if key in compact), None)
            top = min(float(word["top"]) for word in ordered)
            bottom = max(float(word["top"]) + float(word.get("size", 9)) for word in ordered)
            if statement and top >= 0.12 * height:
                candidate = {
                    "status": "explicit_no_transactions",
                    "basis": "official_document_statement",
                    "statement": statement,
                    "evidence": {
                        "page": page_index + 1,
                        "bbox_points": [round(min(float(word["x0"]) for word in ordered), 2),
                                        round(top, 2), round(width, 2), round(bottom, 2)],
                    },
                }
            dates = [word for word in ordered if DATE_RE.fullmatch(_clean(str(word["text"])))
                     or _legacy_date(_clean(str(word["text"]))) is not None]
            lower_form_line = top >= 0.45 * height
            has_amount = any("$" in str(word.get("text", "")) for word in ordered)
            has_asset_code = any(re.fullmatch(r"[\[{][A-Z0-9]{2}[\]}]",
                                              _clean(str(word.get("text", ""))))
                                 for word in ordered)
            # A statement printed near a populated or damaged table must never override the row.
            if lower_form_line and (len(dates) >= 2 or (dates and (has_amount or has_asset_code))
                                    or (has_amount and has_asset_code)):
                row_evidence = True
    return candidate if candidate is not None and not row_evidence else None


def _zero_transaction_extraction(metadata: dict, source_sha256: str, declaration: dict, *,
                                 copy_allowed: bool | None, ocr_engine: str | None,
                                 legacy_form: bool) -> dict:
    review_reasons = ["automatic_qualification_required", "source_use_clearance_required",
                      "explicit_no_transactions_declaration"]
    if legacy_form:
        review_reasons.append("legacy_checkbox_form")
    if copy_allowed is False:
        review_reasons.append("source_pdf_copy_permission_disabled")
    return {
        "schema_version": SCHEMA,
        "parser_version": LEGACY_PARSER_VERSION if legacy_form else PARSER_VERSION,
        "source": {key: metadata.get(key) for key in ("source_id", "source_url", "document_id",
            "filer_name", "state_district", "filing_year", "filed_date", "archive_path")},
        "source_sha256": source_sha256,
        "source_pdf_copy_allowed": copy_allowed,
        "extraction_method": ("tesseract_legacy_checkbox" if legacy_form else
                              "tesseract_ocr" if ocr_engine else "native_pdf_text"),
        "ocr_engine": ocr_engine,
        "document_disposition": declaration,
        "transactions": [],
        "review": {"status": "awaiting_automatic_qualification", "production_eligible": False,
                   "reasons": review_reasons},
    }


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
        # The consumer contract exposes integral bounds.  Preserve the source spelling in
        # amount_raw and use an enclosing interval so a disclosed cent value is never rounded
        # outside the published range.
        cents = matched.group(2)
        return exact, exact + (1 if cents and cents != "00" else 0), "exact"
    matched = AMOUNT_OVER_RE.fullmatch(value)
    if matched:
        # Keep the disclosed threshold; an explicit exclusive marker carries "over".
        return int(matched.group(1).replace(",", "")), None, "open_ended"
    return None


def _modern_date_anchors(words: list[dict], width: float) -> list[float]:
    """Locate native rows without requiring the optional notification value.

    A rightward notice date alone cannot anchor a row. Direction, an asset
    cell and an amount also locate rows with a damaged/missing event date,
    which must reach qualification rather than disappear.
    """
    left, split = 0.53 * width - 1.0, 0.62 * width
    candidates = [word for word in words
                  if (left <= float(word["x0"]) < split and
                      DATE_RE.fullmatch(_clean(str(word.get("text", "")))))
                  or (0.42 * width <= float(word["x0"]) < 0.53 * width and
                      re.fullmatch(r"[PSE](?:\s*\(partial\))?",
                                   _clean(str(word.get("text", ""))), re.I))]
    anchors = []
    for candidate in sorted(candidates, key=lambda item: float(item["top"])):
        top = float(candidate["top"])
        line = [word for word in words if abs(float(word["top"]) - top) <= 2.0]
        has_asset = any(0.165 * width <= float(word["x0"]) < 0.42 * width for word in line)
        has_type = any(0.42 * width <= float(word["x0"]) < 0.53 * width and
                       re.fullmatch(r"[PSE](?:\s*\(partial\))?",
                                    _clean(str(word.get("text", ""))), re.I) for word in line)
        has_notice = any(split <= float(word["x0"]) < 0.725 * width + 1 and
                         DATE_RE.fullmatch(_clean(str(word.get("text", "")))) for word in line)
        has_amount = any(0.725 * width <= float(word["x0"]) < 0.90 * width and
                         "$" in str(word.get("text", "")) for word in line)
        event_token = left <= float(candidate["x0"]) < split
        if has_asset and ((event_token and (has_type or has_notice)) or
                          (has_type and has_amount)):
            if not any(abs(top - existing) <= 2 for existing in anchors):
                anchors.append(top)
    return anchors


def _has_leading_row_detail(page: dict) -> bool:
    """Return whether a new page starts with details belonging to the prior final row."""
    width, height = float(page["width"]), float(page["height"])
    words = page["words"]
    date_tops = _modern_date_anchors(words, width)
    first_row_top = min(date_tops) if date_tops else height
    detail_words = [word for word in words
                    if 0.12 * height <= float(word["top"]) < min(first_row_top - 2, 0.35 * height)
                    and 0.165 * width <= float(word["x0"]) < 0.90 * width]
    lines: dict[float, list[dict]] = {}
    for word in detail_words:
        lines.setdefault(round(float(word["top"]), 1), []).append(word)
    for line in lines.values():
        text = _clean(" ".join(
            str(word["text"]) for word in sorted(line, key=lambda item: float(item["x0"]))))
        if re.match(r"^(?:F|Filing)\s+(?:S|Status):", text, re.I):
            return True
    return False


def _canonical_filing_status(value: str | None) -> str | None:
    if not value:
        return None
    matched = re.match(r"^(New|Amended)\b", value.strip(), re.I)
    return matched.group(1).title() if matched else value.strip()


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
    # Scanned House check-box forms are small enough that OCR can confuse a date
    # separator or a digit with a visually similar glyph. Normalize only the
    # complete date token; qualification still requires both dates and its
    # existing confidence threshold.
    value = _clean(value).strip("[]{}()|.,;:")
    value = value.translate(str.maketrans({"O": "0", "o": "0", "Q": "0",
                                           "I": "1", "l": "1", "|": "1"}))
    value = re.sub(r"(?<=\d)[.\-](?=\d)", "/", value)
    if re.fullmatch(r"\d{6}|\d{8}", value):
        value = f"{value[:2]}/{value[2:4]}/{value[4:]}"
    try:
        year = value.rsplit("/", 1)[-1]
        return datetime.strptime(value, "%m/%d/%Y" if len(year) == 4 else "%m/%d/%y").date().isoformat()
    except (ValueError, IndexError):
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
    # Some paper PTRs begin with the transaction table on page one. The fixed
    # status-box coordinates then fall inside trade cells, so an X or raster
    # ink there cannot establish whether the whole filing is an amendment.
    upper_text = re.sub(r"[^A-Z]", "", " ".join(
        _clean(str(word.get("text", ""))).upper() for word in words
        if float(word.get("top", height)) < 0.31 * height))
    if "FULLASSETNAME" in upper_text and "AMOUNTOFTRANSACTION" in upper_text:
        return None
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
        filing_year = int(metadata["filing_year"])
        wide_table = any(
            0.585 * width <= float(word["x0"]) < 0.665 * width
            and _legacy_date(_clean(str(word["text"]))) is not None
            and (parsed := _legacy_date(_clean(str(word["text"])))) is not None
            and datetime.fromisoformat(parsed).year in {filing_year, filing_year - 1}
            for word in words
        )
        full_table = "CAPITAL" in page_text and "PARTIAL" in page_text
        compact = not full_table and any(0.42 * width <= float(word["x0"]) < 0.47 * width
                      and _legacy_date(_clean(str(word["text"]))) is not None
                      for word in words)
        if wide_table:
            # Some hand-delivered scans devote roughly half the landscape page to the
            # asset name, moving both date columns and the checkbox amount grid right.
            # Select this layout only when an in-period transaction date occupies its
            # distinctive date column; printed headers or examples cannot activate it.
            owner_bounds, asset_bounds = (0.04, 0.09), (0.09, 0.51)
            type_bounds, type_values = (0.51, 0.585), ("purchase", "sale", "exchange")
            transaction_bounds, notification_bounds = (0.585, 0.665), (0.665, 0.75)
            amount_bounds = (0.75, 0.95)
        elif full_table:
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
                          and _legacy_date(_clean(str(word["text"]))) is not None
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
                **({"amount_low_exclusive": False} if amount[1] is None else {}),
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
                     copy_allowed: bool | None, ocr_engine: str | None = None,
                     page_text_methods: list[str] | None = None,
                     _row_member_sink: list | None = None) -> dict:
    if _row_member_sink is not None and (not isinstance(_row_member_sink, list) or _row_member_sink):
        raise HouseIndexError('House raw member collector must be an empty list')
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise HouseIndexError("House PTR source hash is invalid")
    document_id = str(metadata.get("document_id", ""))
    if (metadata.get("source_id") != "house_clerk" or metadata.get("filing_type") != "P" or
            not re.fullmatch(r"[0-9]{1,20}", document_id)):
        raise HouseIndexError("House PTR metadata is invalid")
    if not pages:
        raise HouseIndexError("House PTR contains no pages")
    from .house_native_visibility import KEY as VISIBILITY_KEY, MODE as VISIBILITY_MODE, BLOCKER, validate_visibility, unknown, affects_row, format_anchors_visible
    for number, page in enumerate(pages, 1):
        validate_visibility(page, source_sha256, number)
    visibility_unknown_pages = {number for number, page in enumerate(pages, 1) if unknown(page)}
    visibility_format_blocked = not format_anchors_visible(pages[0], document_id)
    if page_text_methods is None and any("text_reading_method" in page for page in pages):
        raise HouseIndexError("House mixed page observations require their reading methods")
    if page_text_methods is not None:
        from .house_image_body_routing import METHOD as IMAGE_BODY_METHOD, WORD_TAG, validate_image_body_page
        if (not isinstance(page_text_methods, list) or len(page_text_methods) != len(pages) or
                any(not isinstance(method, str) for method in page_text_methods) or
                not set(page_text_methods) <= {"native_pdf_text", "tesseract_ocr", IMAGE_BODY_METHOD} or
                not set(page_text_methods) & {"tesseract_ocr", IMAGE_BODY_METHOD} or
                page_text_methods[0] not in {"native_pdf_text", IMAGE_BODY_METHOD} or
                not isinstance(ocr_engine, str) or not ocr_engine):
            raise HouseIndexError("House mixed page reading methods are invalid")
        for page_number, (method, page) in enumerate(zip(page_text_methods, pages), 1):
            if page.get("text_reading_method", method) != method:
                raise HouseIndexError("House mixed page reading method binding differs")
            if method == IMAGE_BODY_METHOD:
                geometry = page.get('native_page_geometry', {})
                if geometry.get('source_sha256') != source_sha256 or geometry.get('page_number') != page_number:
                    raise HouseIndexError('House image body source or page binding differs')
                validate_image_body_page(page)
            elif 'image_body_observation' in page or any(WORD_TAG in word for word in page['words']):
                raise HouseIndexError("House image body observations require their reading method")
            for word in page["words"]:
                confidence = word.get("ocr_confidence")
                native_word = method == "native_pdf_text" or (method == IMAGE_BODY_METHOD and not word.get(WORD_TAG))
                ocr_word = method == "tesseract_ocr" or (method == IMAGE_BODY_METHOD and word.get(WORD_TAG))
                if ((native_word and confidence is not None) or
                        (ocr_word and (type(confidence) not in (int, float) or
                         not 0 <= confidence <= 100))):
                    raise HouseIndexError("House mixed page confidence differs from its reading method")
    if not any(page.get("words") for page in pages):
        raise HouseIndexError("House PTR requires OCR because it contains no extractable text")
    first_words = (pages[0]['image_body_observation']['native_words']
                   if page_text_methods is not None and page_text_methods[0] == IMAGE_BODY_METHOD
                   else pages[0].get('words', []))
    first_text = _clean(" ".join(str(word["text"]) for word in first_words))
    all_text = _clean(" ".join(str(word["text"]) for page in pages
                               for word in page.get("words", [])))
    compact_first_text = re.sub(r"[^A-Z]", "", first_text.upper())
    compact_all_text = re.sub(r"[^A-Z]", "", all_text.upper())
    large_heading = [_clean(str(word["text"])) for word in pages[0].get("words", [])
                     if float(word.get("size", 0)) >= 18]
    title_matches = ("PERIODICTRANSACTIONREPORT" in compact_first_text
                     or large_heading[:3] == ["P", "T", "R"])
    has_legacy_table = ("FULLASSETNAME" in compact_all_text
                        and "AMOUNTOFTRANSACTION" in compact_all_text
                        and any(_legacy_date(_clean(str(word["text"]))) is not None
                                for page in pages for word in page.get("words", [])))
    legacy_form = (f"#{document_id}" not in first_text and ocr_engine is not None
                   and ((title_matches and "HOUSE" in compact_first_text) or has_legacy_table))
    if page_text_methods is not None and (not title_matches or f"#{document_id}" not in first_text):
        raise HouseIndexError("House mixed pages require a native modern PTR header")
    if legacy_form:
        if visibility_unknown_pages:
            raise HouseIndexError('House legacy native visibility is unsupported; no zero claim can be made')
        declaration = _explicit_zero_transaction_declaration(pages)
        if declaration is not None:
            return _zero_transaction_extraction(
                metadata, source_sha256, declaration, copy_allowed=copy_allowed,
                ocr_engine=ocr_engine, legacy_form=True)
        return _parse_legacy_word_pages(metadata, source_sha256, pages,
                                        copy_allowed=copy_allowed, ocr_engine=ocr_engine)
    if not title_matches or f"#{document_id}" not in first_text:
        raise HouseIndexError("House PTR header does not match its archived identity")
    declaration = None if visibility_unknown_pages else _explicit_zero_transaction_declaration(pages)
    if page_text_methods is not None and declaration is not None:
        raise HouseIndexError("House mixed zero-transaction declaration lacks independent page coverage")
    if declaration is not None and not any(_unanchored_native_slots(page) for page in pages):
        return _zero_transaction_extraction(
            metadata, source_sha256, declaration, copy_allowed=copy_allowed,
            ocr_engine=ocr_engine, legacy_form=False)

    page_data: list[dict] = []
    anchors: list[dict] = []
    for page_index, page in enumerate(pages):
        width = float(page.get("width", 0))
        height = float(page.get("height", 0))
        words = page.get("words")
        if width <= 0 or height <= 0 or not isinstance(words, list):
            raise HouseIndexError("House PTR page geometry is invalid")
        page_data.append({"width": width, "height": height, "words": words,
            **({VISIBILITY_KEY: page[VISIBILITY_KEY]} if VISIBILITY_KEY in page else {}),
            **({"native_page_geometry": page["native_page_geometry"]}
               if "native_page_geometry" in page else {})})
        for top in _modern_date_anchors(words, width):
            anchors.append({"page_index": page_index, "top": top})

    extracted: list[dict] = []
    for position, anchor in enumerate(anchors):
        page_index, top = anchor["page_index"], anchor["top"]
        page_number = page_index + 1
        current = page_data[page_index]
        width, height, words = current["width"], current["height"], current["words"]
        following = anchors[position + 1] if position + 1 < len(anchors) else None
        last_page_index = following["page_index"] if following else page_index
        if (following is None and page_index + 1 < len(page_data)
                and (top >= 0.85 * height or _has_leading_row_detail(page_data[page_index + 1]))):
            # Some electronic PTRs split the final row after the amount dash. The next page repeats
            # the table header and continues the asset type and amount upper bound without a date.
            last_page_index = page_index + 1
        block: list[tuple[int, dict]] = []
        evidence_segments: list[dict] = []
        for segment_page_index in range(page_index, last_page_index + 1):
            segment = page_data[segment_page_index]
            start = top - 2 if segment_page_index == page_index else 0.14 * segment["height"]
            if segment_page_index > page_index and unknown(pages[segment_page_index]):
                from .house_native_coverage import _header_cells
                observed = pages[segment_page_index]
                cells = _header_cells({**observed, 'words': observed[VISIBILITY_KEY]['original_words']},
                                      observed['native_page_geometry'])
                if cells is not None:
                    start = max(start, cells[0][3])
            if (page_text_methods is not None and segment_page_index > page_index and
                    page_text_methods[segment_page_index] == IMAGE_BODY_METHOD):
                # The independently identified printed header is not a
                # continuation of the preceding native transaction.
                start = max(start, pages[segment_page_index]['image_body_observation']['region'][1])
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
        transaction_date = _line(words, x0=0.53 * width - 1.0, x1=0.62 * width - 1.0, top=top)
        notification_date = _line(words, x0=0.62 * width, x1=0.725 * width, top=top)
        raw_type = _line(words, x0=0.42 * width, x1=0.53 * width, top=top)
        reported_transaction_id = _line(words, x0=0.04 * width, x1=0.105 * width, top=top)
        raw_owner = _line(words, x0=0.105 * width, x1=0.165 * width, top=top)
        date_size = max((float(word.get("size", 0)) for word in words
                         if abs(float(word["top"]) - top) <= 2 and
                         0.53 * width - 1.0 <= float(word["x0"]) < 0.62 * width), default=0)
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
                      page_data[segment_page_index]["width"]
                      and float(word.get("size", date_size)) < date_size - 0.2]
        # The .90 edge belongs to the amount cell, not to full-width notes.
        # Keep literal trailing note words and include them in source evidence;
        # no financial column boundaries or identity inputs are changed.
        for segment in evidence_segments:
            right = max((float(w.get("x1", w["x0"])) for page_index, w in note_words
                         if page_index + 1 == segment["page"]), default=0.)
            segment["bbox_points"][2] = max(segment["bbox_points"][2], round(right, 2))
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
        if "filing_status" in details:
            details["filing_status"] = _canonical_filing_status(details["filing_status"])
        field_problems = []
        def event_iso(raw: str, field: str) -> str | None:
            try:
                return datetime.strptime(raw, "%m/%d/%Y").date().isoformat()
            except ValueError:
                if field == "transaction_date":
                    field_problems.append("transaction_date_invalid")
                return None
        transaction_iso = event_iso(transaction_date, "transaction_date")
        notification_iso = event_iso(notification_date, "notification_date")
        amount_value = _parse_amount(amount)
        asset_match = ASSET_RE.fullmatch(asset)
        type_code = raw_type[:1]
        if amount_value is None:
            field_problems.append("amount_cell_unresolved")
        if asset_match is None:
            field_problems.append("asset_cell_unresolved")
        if type_code not in TYPE_CODES:
            field_problems.append("transaction_type_unresolved")
        asset_name, ticker, asset_type_code = asset_match.groups() if asset_match else (asset, None, None)
        amount_low, amount_high, amount_kind = amount_value if amount_value else (None, None, None)
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
        if visibility_format_blocked or any(affects_row(pages[segment['page']-1], segment['bbox_points']) for segment in evidence_segments):
            field_problems = sorted(set(field_problems + [BLOCKER]))
        if visibility_format_blocked:
            field_problems = sorted(set(field_problems + ['native_format_anchor_visibility_unknown']))
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
            "transaction_type": TYPE_CODES.get(type_code),
            "transaction_date": transaction_iso,
            "notification_date": notification_iso,
            "field_problems": field_problems,
            "raw_cells": {"asset": asset, "owner": raw_owner, "type": raw_type,
                          "transaction_date": transaction_date, "notification_date": notification_date,
                          "amount": amount},
            "amount_low": amount_low,
            "amount_high": amount_high,
            "amount_kind": amount_kind,
            **({"amount_low_exclusive": True} if amount_kind == "open_ended" else {}),
            "amount_raw": amount,
            "ocr_confidence": (round(sum(ocr_confidences) / len(ocr_confidences), 2)
                               if ocr_confidences else None),
            **({"ocr_source_pages": sorted({index + 1 for index, _ in block
                 if page_text_methods[index] == "tesseract_ocr"} | {index + 1 for index, word in block
                 if page_text_methods[index] == IMAGE_BODY_METHOD and word.get(WORD_TAG)})}
               if page_text_methods is not None else {}),
            "subholding_of": details.get("subholding_of"),
            "location": details.get("location"),
            "description": details.get("description"),
            "evidence": {"page": page_number, "pages": [segment["page"] for segment in evidence_segments],
                         "bbox_points": evidence_segments[0]["bbox_points"], "segments": evidence_segments},
            "verification_status": "awaiting_automatic_qualification",
            "review_reasons": reasons,
        })
        if _row_member_sink is not None:
            used = {id(word) for _, word in [*asset_words, *amount_words, *note_words]}
            line_ranges = ((.04*width, .105*width), (.105*width, .165*width),
                           (.42*width, .53*width), (.53*width-1., .62*width-1.),
                           (.62*width, .725*width))
            used.update(id(word) for word in words if abs(float(word['top'])-top) <= 2.0
                        and any(left <= float(word['x0']) < right for left, right in line_ranges))
            blocks = []
            for pi in sorted({pi for pi, _ in block}):
                selected = {id(word) for index, word in block if index == pi}
                blocks.append({'page_number': pi+1,
                    'word_indices': [i for i, word in enumerate(page_data[pi]['words']) if id(word) in selected],
                    'field_word_indices': [i for i, word in enumerate(page_data[pi]['words'])
                                           if id(word) in selected and id(word) in used]})
            _row_member_sink.append({'raw_row_id': extracted[-1]['extraction_id'], 'blocks': blocks})
    ledger = _modern_report_ledger(source_sha256, page_data, extracted)
    if not extracted and not ledger["disposition_counts"].get("unknown") and not visibility_unknown_pages:
        raise HouseIndexError("House PTR contains no recognized transaction rows")
    if len({row["extraction_id"] for row in extracted}) != len(extracted):
        raise HouseIndexError("House PTR produced duplicate extraction IDs")
    review_reasons = ["automatic_qualification_required", "source_use_clearance_required"]
    if copy_allowed is False:
        review_reasons.append("source_pdf_copy_permission_disabled")
    saved_pages = ([{**{key:value for key,value in page.items() if key != "_image"},
                     "text_reading_method":page_text_methods[index]}
                    for index,page in enumerate(pages)] if page_text_methods is not None else pages)
    return {
        "source_report_ledger": ledger,
        "schema_version": SCHEMA,
        "parser_version": PARSER_VERSION,
        "source": {key: metadata.get(key) for key in ("source_id", "source_url", "document_id",
            "filer_name", "state_district", "filing_year", "filed_date", "archive_path")},
        "source_sha256": source_sha256,
        "source_pdf_copy_allowed": copy_allowed,
        "extraction_method": ("mixed_native_ocr" if page_text_methods is not None else
                              "tesseract_ocr" if ocr_engine else "native_pdf_text"),
        "ocr_engine": ocr_engine,
        **({'native_visibility_mode': VISIBILITY_MODE} if any(VISIBILITY_KEY in page for page in pages) else {}),
        **({"page_text_methods": list(page_text_methods)} if page_text_methods is not None else {}),
        **({"source_word_pages": deepcopy(saved_pages),
            "source_word_pages_sha256": hashlib.sha256(json.dumps(
                saved_pages, ensure_ascii=False,
                sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()}
           if ocr_engine is None or page_text_methods is not None else {}),
        "transactions": extracted,
        "review": {"status": "awaiting_automatic_qualification", "production_eligible": False,
                   "reasons": review_reasons},
    }


def _unanchored_native_slots(page: dict) -> list[dict]:
    """Retain strong financial-line observations, never infer missing facts.

    This is not a page census. A source asset-type cell, amount cell and
    financial-column text must be followed by a local official status detail.
    Ordinary notes, wrapped amounts and status mentions alone are insufficient.
    """
    words, width = page["words"], float(page["width"])
    anchors = _modern_date_anchors(words, width)
    lines: list[list[dict]] = []
    for item in sorted(words, key=lambda w: (float(w["top"]), float(w["x0"]))):
        if not lines or abs(float(item["top"]) - float(lines[-1][0]["top"])) > 2:
            lines.append([])
        lines[-1].append(item)
    statuses = []
    for line in lines:
        detail = _clean(" ".join(str(w["text"]) for w in sorted(line, key=lambda w: float(w["x0"]))
                         if .165 * width <= float(w["x0"]) < .42 * width))
        if re.fullmatch(r"(?:F|Filing)\s+(?:S|Status):\s*(?:New|Amended)", detail, re.I):
            statuses.append(float(line[0]["top"]))
    slots = []
    for line in lines:
        top = float(line[0]["top"])
        if any(abs(top - anchor) <= 2 for anchor in anchors):
            continue
        asset = [w for w in line if .165 * width <= float(w["x0"]) < .42 * width]
        amount = [w for w in line if .725 * width <= float(w["x0"]) < .90 * width]
        financial = [w for w in line if .42 * width <= float(w["x0"]) < .725 * width]
        nearby_status = [status for status in statuses if 2 < status - top <= 40]
        asset_cell = [w for w in words if nearby_status and top <= float(w["top"]) < min(nearby_status)
                      and .165 * width <= float(w["x0"]) < .42 * width]
        if not (asset and any(re.fullmatch(r"\[[A-Z]{2}[\]}]", _clean(str(w["text"]))) for w in asset_cell)
                and any("$" in str(w["text"]) for w in amount)
                and any(_clean(str(w["text"])) for w in financial)
                and nearby_status):
            continue
        digest = hashlib.sha256(json.dumps(line, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        slots.append({"local_key": f"unanchored-financial-line-{len(slots) + 1}",
            "bounds": [top, max(float(w.get("bottom", float(w["top"]) + float(w.get("size", 9))))
                                for w in line)], "disposition": "unknown",
            "observation_ref": f"native_financial_line_without_row_anchor:{digest}"})
    return slots


def _modern_report_ledger(source_sha: str, pages: list[dict], rows: list[dict], *,
                          include_unanchored: bool = True) -> dict:
    from .source_row_ledger import page_ledger, report_ledger
    page_slots = {number: [] for number in range(1, len(pages) + 1)}
    logical = []
    for index, row in enumerate(rows, start=1):
        refs = []
        for segment in row["evidence"]["segments"]:
            number = segment["page"]
            key = f"row-{index}-segment-{number}"
            bounds = segment["bbox_points"]
            page_slots[number].append({"local_key": key, "bounds": [bounds[1], bounds[3]],
                                       "disposition": "data_candidate",
                                       "observation_ref": row["extraction_id"]})
            refs.append({"page_number": number, "slot_key": key})
        logical.append({"local_key": f"row-{index}", "slot_refs": refs,
                        "observation_ref": row["extraction_id"]})
    if include_unanchored:
        for number, page in enumerate(pages, start=1):
            page_slots[number].extend(_unanchored_native_slots(page))
    observed = [page_ledger(source_id="house_clerk", document_sha256=source_sha,
                  page_number=number, adapter_version=PARSER_VERSION,
                  coverage="unknown", coverage_reason="native_row_anchors_not_complete_page_census",
                  coordinate_frame="source_page_pdf_points", slots=page_slots[number])
                for number in page_slots]
    ledger = report_ledger(source_id="house_clerk", document_sha256=source_sha,
                         adapter_version=PARSER_VERSION, page_count=len(pages),
                         pages=observed, logical_rows=logical)
    if include_unanchored:
        from .house_native_coverage import apply_native_coverage
        ledger = apply_native_coverage(source_sha, pages, rows, ledger)
        from .house_native_visibility import unknown, BLOCKER
        if any(unknown(page) for page in pages):
            for number, page in enumerate(pages, 1):
                if unknown(page):
                    ledger['pages'][number-1].update(coverage='unknown', coverage_reason=BLOCKER)
            ledger = report_ledger(source_id='house_clerk', document_sha256=source_sha,
                adapter_version=PARSER_VERSION, page_count=len(pages), pages=ledger['pages'],
                logical_rows=[{key:value for key,value in row.items() if key != 'qualification'}
                              for row in ledger['logical_rows']], unresolved_data_slots=ledger['unresolved_data_slots'])
    return ledger


def _native_account_context(extraction: dict) -> dict:
    """Reproduce native source observations before deriving display context.

    The word digest establishes cache consistency, not independent PDF truth.
    Older caches without saved words keep their existing financial facts.
    """
    mixed = extraction.get("extraction_method") == "mixed_native_ocr"
    if extraction.get("extraction_method") != "native_pdf_text" and not mixed:
        return {}
    titles = [r for r in extraction.get("transactions", []) if r.get("subholding_of")]
    pages = extraction.get("source_word_pages")
    if pages is None:
        if mixed:
            raise HouseIndexError("House mixed extraction requires saved page observations")
        return {"status": "source_pages_unavailable", "rows": []} if titles else {}
    try:
        actual = hashlib.sha256(json.dumps(pages, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        if actual != extraction.get("source_word_pages_sha256"):
            raise HouseIndexError("House native account source words differ from digest")
        source = extraction.get("source", {})
        reproduced = parse_word_pages({**source, "filing_type": "P"}, extraction["source_sha256"],
            pages, copy_allowed=extraction.get("source_pdf_copy_allowed"),
            **({"ocr_engine": extraction.get("ocr_engine"),
                "page_text_methods": extraction.get("page_text_methods")} if mixed else {}))
        # Exact legacy anchor-only ledgers remain valid; no financial/source
        # observation may differ. This accepts a recomputed old form, not a label.
        legacy_ledger = _modern_report_ledger(extraction["source_sha256"], pages,
            reproduced["transactions"], include_unanchored=False)
        if (not any("native_page_geometry" in page for page in pages)
                and extraction.get("source_report_ledger") == legacy_ledger):
            reproduced["source_report_ledger"] = legacy_ledger
        if any(extraction.get(key) != value for key, value in reproduced.items()):
            raise HouseIndexError("House native account observations differ from saved source words")
    except (KeyError, TypeError, ValueError) as exc:
        raise HouseIndexError("House native account source observations cannot be reproduced") from exc
    if not titles:
        return {}
    return {"status": "source_words_reproduced", "source_sha256": extraction["source_sha256"],
        "document_id": source["document_id"], "source_word_pages_sha256": actual,
        "rows": [{"id": r["extraction_id"], "asset_name_raw": r["asset_name"],
                  "subholding_of_raw": r["subholding_of"],
                  "asset_name_display": f"{r['asset_name']} — {r['subholding_of']}"} for r in titles]}


def qualify_automatic(extraction: dict, identity: dict, *,
                      evidence_root: Path | None = None,
                      verified_blank_paper_owner_ids: frozenset[str] = frozenset(),
                      _source_bound_revision_ids: frozenset[str] = frozenset()) -> dict:
    """Promote deterministic standard rows and isolate every ambiguous row with explicit reasons."""
    from .house_native_contrast import PARSER_VERSIONS as CONTRAST_VERSIONS, qualify_native_contrast
    if extraction.get('parser_version') in CONTRAST_VERSIONS or any(k.startswith('native_contrast_') for k in extraction):
        if verified_blank_paper_owner_ids:
            raise HouseIndexError('House native contrast cannot grant paper blank owners')
        return qualify_native_contrast(extraction, identity, evidence_root=evidence_root,
                                       revision_ids=_source_bound_revision_ids)
    if extraction.get("schema_version") != SCHEMA:
        raise HouseIndexError("House PTR extraction schema is invalid for automatic qualification")
    if evidence_root is not None and extraction.get('extraction_method') in {'native_pdf_text', 'mixed_native_ocr'}:
        from .house_native_contrast import plan_house_native_contrast
        relative = extraction.get('source', {}).get('archive_path')
        if not isinstance(relative, str):
            raise HouseIndexError('House native qualification needs archived source metadata')
        plan = plan_house_native_contrast(evidence_root, Path(evidence_root)/Path(relative).with_suffix('.json'))
        if plan['activated'] is not False:
            raise HouseIndexError('House native qualification cache differs from selected source version')
    from .house_native_visibility import KEY as VISIBILITY_KEY, MODE as VISIBILITY_MODE
    visibility_saved = any(VISIBILITY_KEY in page for page in (extraction.get('source_word_pages') or []))
    if visibility_saved or 'native_visibility_mode' in extraction:
        if (not visibility_saved or extraction.get('native_visibility_mode') != VISIBILITY_MODE or
                extraction.get('extraction_method') not in {'native_pdf_text', 'mixed_native_ocr'}):
            raise HouseIndexError('House native visibility observations require their saved native method')
    if extraction.get("extraction_method") != "mixed_native_ocr" and (
            "page_text_methods" in extraction or any("text_reading_method" in page
                for page in (extraction.get("source_word_pages") or []))):
        raise HouseIndexError("House mixed observations cannot change extraction method")
    if (not isinstance(verified_blank_paper_owner_ids, frozenset) or
            (verified_blank_paper_owner_ids and extraction.get("extraction_method") != "source_bound_paper_cells")):
        raise HouseIndexError("House verified blank owners require source-bound paper interpretation")
    source = extraction.get("source") or {}
    document_id = source.get("document_id")
    if identity.get("document_id") != document_id:
        raise HouseIndexError("House PTR identity does not match its extraction")
    identity_valid = (
        identity.get("status") in {"matched_automatically", "suggested_requires_review"}
        and identity.get("match_basis") in DETERMINISTIC_IDENTITY_BASES
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
    advisories: list[dict] = []
    revision_observations: list[dict] = []
    account_context = _native_account_context(extraction)
    display_names = {r["id"]: r["asset_name_display"] for r in account_context.get("rows", [])}
    for row in extraction.get("transactions", []):
        reasons: list[str] = list(row.get("field_problems", []))
        if not identity_valid:
            reasons.append("identity_not_deterministic")
        # The ID column identifies a transaction, not a filing or an
        # amendment target by itself. Filing Status establishes revision state.
        official_id = row.get("reported_transaction_id")
        if official_id is not None and (not isinstance(official_id, str) or
                not re.fullmatch(r"[0-9]{1,30}", official_id)):
            reasons.append("reported_transaction_id_invalid")
        if str(row.get("filing_status", "")).lower() != "new":
            if not (str(row.get("filing_status", "")).lower() == "amended" and
                    row.get("extraction_id") in _source_bound_revision_ids):
                reasons.append("revision_relationship_unresolved")
            target = row.get("reported_transaction_id")
            if str(row.get("filing_status", "")).lower() == "amended":
                revision_observations.append({"extraction_id": row.get("extraction_id"),
                    "reported_transaction_id": target,
                    "reference_status": "official_target_id_observed" if isinstance(target, str) and
                        re.fullmatch(r"[0-9]{1,30}", target) else "official_target_id_unavailable",
                    "source_sha256": extraction.get("source_sha256"), "evidence": row.get("evidence"),
                    "description": row.get("description")})
        if row.get("amount_high") is None:
            if row.get("amount_kind") != "open_ended":
                reasons.append("amount_upper_bound_missing")
            elif type(row.get("amount_low_exclusive")) is not bool:
                reasons.append("open_ended_amount_not_representable")
        needs_ocr_confidence = str(extraction.get("extraction_method", "")).startswith("tesseract_")
        if extraction.get("extraction_method") == "mixed_native_ocr":
            needs_ocr_confidence = bool(row["ocr_source_pages"])
        if needs_ocr_confidence and (
                not isinstance(row.get("ocr_confidence"), (int, float))
                or row["ocr_confidence"] < OCR_MINIMUM_ROW_CONFIDENCE):
            reasons.append("ocr_confidence_below_threshold")
        if not isinstance(row.get("asset_name"), str) or not row["asset_name"].strip():
            reasons.append("asset_name_invalid")
        blank_paper_owner = (row.get("owner") == "Undisclosed" and
                             row.get("extraction_id") in verified_blank_paper_owner_ids)
        if row.get("owner") not in set(OWNER_CODES.values()) and not blank_paper_owner:
            reasons.append("owner_not_normalized")
        if row.get("transaction_type") not in set(TYPE_CODES.values()):
            reasons.append("transaction_type_not_normalized")
        ticker = row.get("ticker")
        if ticker is not None and (not isinstance(ticker, str) or not re.fullmatch(
                r"[A-Z0-9][A-Z0-9.\-^/]{0,31}", ticker)):
            reasons.append("ticker_invalid")
        low, high = row.get("amount_low"), row.get("amount_high")
        if type(low) is not int or low < 0 or (high is not None and type(high) is not int) or \
                (type(high) is int and not 0 <= low <= high):
            reasons.append("amount_invalid")
        parsed_dates = {}
        try:
            parsed_dates["transaction_date"] = datetime.strptime(
                row["transaction_date"], "%Y-%m-%d").date()
        except (KeyError, TypeError, ValueError):
            reasons.append("transaction_date_invalid")
        if parsed_dates.get("transaction_date") and parsed_dates["transaction_date"] > filed_day:
            reasons.append("date_sequence_invalid")
        notice_issues = []
        try:
            parsed_dates["notification_date"] = datetime.strptime(
                row["notification_date"], "%Y-%m-%d").date()
        except (KeyError, TypeError, ValueError):
            notice_issues.append("notification_date_invalid")
        if parsed_dates.get("notification_date") and (
                parsed_dates["notification_date"] > filed_day or
                (parsed_dates.get("transaction_date") and
                 parsed_dates["notification_date"] < parsed_dates["transaction_date"])):
            notice_issues.append("notification_date_sequence_invalid")
        if notice_issues:
            advisories.append({"extraction_id": row.get("extraction_id"),
                               "issues": notice_issues,
                               "source_sha256": extraction.get("source_sha256")})
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
            "owner": row["owner"], "asset_name": display_names.get(row["extraction_id"], row["asset_name"]), "ticker": ticker,
            "ticker_mapping_basis": "filing_explicit" if ticker else None,
            "instrument_type": row.get("instrument_type"), "transaction_type": row["transaction_type"],
            **({"option_type": option_type, "strike_price": strike_price,
                "expiration_date": expiration_date} if row.get("instrument_type") == "Option" else {}),
            "transaction_date": row["transaction_date"], "filed_at": filed_at,
            "amount_low": low, "amount_high": high,
            **({"amount_low_exclusive": row["amount_low_exclusive"]} if high is None else {}),
            "position_effect": "unknown",
            "position_effect_basis": None, "source_id": "house_clerk", "source": "U.S. House Clerk",
            "source_url": source["source_url"], "verification_status": "official_matched",
        })
    disposition = extraction.get("document_disposition")
    explicit_zero = (isinstance(disposition, dict)
                     and disposition.get("status") == "explicit_no_transactions"
                     and extraction.get("transactions") == [])
    document_reasons = [] if identity_valid else ["identity_not_deterministic"]
    document_status = ("qualified_no_transactions" if explicit_zero and identity_valid else
                       "quarantined_document" if explicit_zero else
                       "qualified_rows" if transactions else "quarantined_rows")
    return {
        "schema_version": QUALIFICATION_SCHEMA,
        "source_sha256": extraction.get("source_sha256"),
        "parser_version": extraction.get("parser_version"),
        "document_id": document_id,
        "identity": identity,
        "document_disposition": disposition,
        **({"source_report_ledger": extraction["source_report_ledger"]}
           if "source_report_ledger" in extraction else {}),
        "transactions": transactions,
        "quarantined": quarantined,
        "advisories": advisories,
        **({"native_account_context": account_context} if account_context else {}),
        **({"revision_observations": revision_observations} if revision_observations else {}),
        "qualification": {
            "method": "deterministic_automatic_rules",
            "status": document_status,
            "qualified_count": len(transactions),
            "quarantined_count": len(quarantined),
            "advisory_count": len(advisories),
            "zero_transaction_document_count": 1 if explicit_zero and identity_valid else 0,
            "document_reasons": document_reasons if explicit_zero else [],
            "production_eligible": bool(transactions) or (explicit_zero and identity_valid),
        },
    }


def revision_reference_dispositions(qualification: dict, extraction: dict,
                                   current_transactions: list[dict]) -> list[dict]:
    """Keep official amendment targets distinct from absent predecessor inputs.

    A filing ID, a hashed local ID, or equal financial fields cannot stand in
    for the government's transaction identifier or a source-bound predecessor.
    """
    # Old source-reviewed formal paper qualifications can deliberately differ
    # from their legacy extractor's status reading. Never reopen those facts.
    if "revision_observations" not in qualification:
        return []
    amended = [row for row in extraction.get("transactions", [])
               if str(row.get("filing_status", "")).lower() == "amended"]
    if not amended:
        if qualification.get("revision_observations"):
            raise ValueError("House revision observations have no amended source rows")
        return []
    source = extraction.get("source") or {}
    if (qualification.get("source_sha256") != extraction.get("source_sha256") or
            qualification.get("document_id") != source.get("document_id")):
        raise ValueError("House amendment source binding differs from qualification")
    observed = [{"extraction_id": row.get("extraction_id"), "reported_transaction_id": row.get("reported_transaction_id"),
        "reference_status": "official_target_id_observed" if isinstance(row.get("reported_transaction_id"), str) and
            re.fullmatch(r"[0-9]{1,30}", row["reported_transaction_id"]) else "official_target_id_unavailable",
        "source_sha256": extraction.get("source_sha256"), "evidence": row.get("evidence"),
        "description": row.get("description")} for row in amended]
    if qualification.get("revision_observations", observed) != observed:
        raise ValueError("House amendment reference differs from its source row")
    person = qualification.get("identity", {}).get("person_id")
    existing = [row for row in current_transactions if row.get("source_id") == "house_clerk" and row.get("person_id") == person]
    return [{**row, "disposition": "official_target_id_unavailable" if row["reference_status"] != "official_target_id_observed"
                else "official_target_id_observed_predecessor_absent_from_candidate" if not existing
                else "official_target_id_observed_predecessor_binding_unavailable",
             "current_same_person_formal_row_count": len(existing),
             "action": "not_applied_no_source_bound_predecessor", "formal_transaction_id": None}
            for row in observed]


def _words_from_tesseract_tsv(value: str, *, points_per_pixel: float,
                              include_word_bounds: bool = False) -> list[dict]:
    words: list[dict] = []
    try:
        lines = value.splitlines()
        if not lines:
            return words
        columns = lines[0].split("\t")
        if not {"level", "text", "conf", "left", "top", "height"} <= set(columns):
            raise HouseIndexError("House PTR OCR returned invalid word geometry")
        if include_word_bounds and 'width' not in columns:
            raise HouseIndexError('House image body OCR requires complete word bounds')
        for line in lines[1:]:
            cells = line.split("\t", len(columns) - 1)
            if len(cells) != len(columns):
                raise HouseIndexError("House PTR OCR returned invalid word geometry")
            row = dict(zip(columns, cells))
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
                **({'x1': (float(row['left']) + float(row['width'])) * points_per_pixel,
                    'bottom': (float(row['top']) + float(row['height'])) * points_per_pixel}
                   if include_word_bounds else {}),
            })
    except (KeyError, TypeError, ValueError):
        raise HouseIndexError("House PTR OCR returned invalid word geometry") from None
    return words


def _ocr_pdf_pages(document, *, resolution: int = 200,
                   page_numbers: list[int] | None = None,
                   include_word_bounds: bool = False) -> tuple[list[dict], str]:
    selected = list(range(1, len(document.pages) + 1)) if page_numbers is None else page_numbers
    if (not isinstance(selected, list) or not selected or
            any(type(number) is not int or not 1 <= number <= len(document.pages) for number in selected) or
            len(set(selected)) != len(selected)):
        raise HouseIndexError("House PTR OCR page selection is invalid")
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
        for number in selected:
            page = document.pages[number - 1]
            image_path = Path(folder) / f"page-{number}.png"
            if page_numbers is not None and tuple(page.bbox[:2]) != (0, 0):
                raise HouseIndexError("House mixed OCR page origin is unsupported")
            image = page.to_image(resolution=resolution, antialias=True,
                **({"force_mediabox":True} if page_numbers is not None else {})).original.convert("L")
            image.save(image_path, format="PNG")
            completed = subprocess.run(
                [executable, str(image_path), "stdout", "--dpi", str(resolution), "-l", "eng", "tsv"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120, check=False)
            if completed.returncode:
                raise HouseIndexError(f"House PTR OCR failed on page {number}")
            pages.append({"width": page.width, "height": page.height,
                          "_image": image,
                          "words": _words_from_tesseract_tsv(
                              completed.stdout, points_per_pixel=72.0 / resolution,
                              include_word_bounds=include_word_bounds)})
    return pages, engine


def observe_native_word_pages(document, source_sha256: str) -> list[dict]:
    """Share the source inventory between runtime preparation and parsing."""
    from .house_native_coverage import observe_native_geometry
    from .house_native_visibility import observe_visibility
    return [observe_visibility(page, {"width":page.width, "height":page.height,
             "words":page.extract_words(x_tolerance=2,y_tolerance=2,extra_attrs=["size"]),
             "native_page_geometry":observe_native_geometry(page,source_sha256,number)}, source_sha256, number)
            for number,page in enumerate(document.pages,1)]


def planned_ocr_pages(pages: list[dict]) -> list[int]:
    """Select wordless content or an explicitly partitioned native table image."""
    from .house_image_body_routing import image_body_region
    if not any(page["words"] for page in pages):
        return list(range(1,len(pages)+1))  # preserve the existing image-only path
    return [number for number,page in enumerate(pages,1) if
            (not page["words"] and any(page["native_page_geometry"][kind]
                for kind in ("images","curves","rects","lines"))) or image_body_region(page) is not None]


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
    from .house_native_contrast import parse_selected_native_contrast, SourceInventoryUnavailable, bounded_legacy_unknown
    try:
        contrast = parse_selected_native_contrast(archive_root, metadata_path)
    except SourceInventoryUnavailable:
        return bounded_legacy_unknown(archive_root, metadata_path)
    if contrast is not None:
        return contrast
    try:
        with pdfplumber.open(pdf_path) as document:
            copy_allowed = getattr(document.doc, "is_extractable", None)
            pages = observe_native_word_pages(document,source_sha)
            ocr_engine = None
            page_text_methods = None
            if not any(page["words"] for page in pages):
                pages, ocr_engine = _ocr_pdf_pages(document)
            else:
                selected = planned_ocr_pages(pages)
                if selected:
                    if any(tuple(page.bbox[:2]) != (0, 0) for page in document.pages):
                        raise HouseIndexError("House mixed PDF page origin is unsupported")
                    first = _clean(" ".join(str(w["text"]) for w in pages[0]["words"]))
                    compact = re.sub(r"[^A-Z]", "", first.upper())
                    heading = [_clean(str(w["text"])) for w in pages[0]["words"] if float(w.get("size", 0)) >= 18]
                    if (f"#{metadata.get('document_id')}" not in first or not (
                            "PERIODICTRANSACTIONREPORT" in compact or heading[:3] == ["P", "T", "R"])):
                        raise HouseIndexError("House mixed pages require a native modern PTR header")
                    from .house_image_body_routing import image_body_region
                    image_body_selected = any(image_body_region(pages[number-1]) is not None for number in selected)
                    observed, ocr_engine = _ocr_pdf_pages(document, page_numbers=selected,
                        **({'include_word_bounds': True} if image_body_selected else {}))
                    if len(observed) != len(selected):
                        raise HouseIndexError("House mixed OCR page count differs from its selection")
                    page_text_methods = ["native_pdf_text"] * len(pages)
                    for number, observed_page in zip(selected, observed):
                        if any(observed_page.get(key) != pages[number-1][key] for key in ("width", "height")):
                            raise HouseIndexError("House mixed OCR page dimensions differ")
                        if pages[number-1]['words']:
                            from .house_image_body_routing import METHOD as IMAGE_BODY_METHOD, merge_image_body_page
                            pages[number-1] = merge_image_body_page(pages[number-1], observed_page)
                            page_text_methods[number-1] = IMAGE_BODY_METHOD
                        else:
                            pages[number-1] = observed_page
                            page_text_methods[number-1] = "tesseract_ocr"
    except HouseIndexError:
        raise
    except Exception as exc:
        raise HouseIndexError(f"House PTR extraction failed: {type(exc).__name__}") from None
    return parse_word_pages(metadata, source_sha, pages, copy_allowed=copy_allowed,
                            ocr_engine=ocr_engine,
                            **({"page_text_methods":page_text_methods} if page_text_methods is not None else {}))


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
    advisories = []
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
        try:
            transaction_day = datetime.strptime(row["transaction_date"], "%Y-%m-%d").date()
        except (KeyError, TypeError, ValueError):
            raise HouseIndexError("House PTR reviewed row has an invalid transaction_date") from None
        if transaction_day > filed_at.date():
            raise HouseIndexError("House PTR reviewed transaction date is after filing date")
        notice_issues = []
        try:
            notification_day = datetime.strptime(row["notification_date"], "%Y-%m-%d").date()
        except (KeyError, TypeError, ValueError):
            notification_day = None
            notice_issues.append("notification_date_invalid")
        if notification_day and not transaction_day <= notification_day <= filed_at.date():
            notice_issues.append("notification_date_sequence_invalid")
        if notice_issues:
            advisories.append({"extraction_id": extraction_id, "issues": notice_issues,
                               "source_sha256": extraction.get("source_sha256")})
        low, high = row.get("amount_low"), row.get("amount_high")
        if (type(low) is not int or low < 0 or
                (high is None and type(row.get("amount_low_exclusive")) is not bool) or
                (high is not None and (type(high) is not int or low > high))):
            raise HouseIndexError("House PTR reviewed row has an invalid amount range")
        transactions.append({
            "id": extraction_id, "filing_id": review["document_id"], "person_id": person_id,
            "owner": row["owner"], "asset_name": row["asset_name"], "ticker": ticker,
            "ticker_mapping_basis": "filing_explicit" if ticker else None,
            "instrument_type": row.get("instrument_type"), "transaction_type": row["transaction_type"],
            "transaction_date": row["transaction_date"], "filed_at": filed_at_text,
            "amount_low": low, "amount_high": high,
            **({"amount_low_exclusive": row["amount_low_exclusive"]} if high is None else {}),
            "position_effect": "unknown",
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
    return {"audit": audit, "transactions": transactions, "revisions": revisions,
            "advisories": advisories}

def resolve_automatic_revisions(inputs: list[tuple[dict, dict]], current_transactions: list[dict],
                                *, prior_transactions: list[dict] | None = None,
                                evidence_root: Path | None = None) -> dict:
    """Derive a bounded official-ID revision chain from replayable source rows.

    Saved qualifications stay unchanged. This is a current-source interpretation
    shared by candidate and status, never a free-standing approval receipt.
    """
    from copy import deepcopy
    if not any(q.get('revision_observations') for q, _ in inputs):
        return {'transactions': deepcopy(current_transactions), 'qualifications': {},
                'identities': {}, 'dispositions': {}, 'replacements': []}
    groups = {}
    observations = {}
    effective = {}
    identities = {}
    source_facts = {}
    non_native_groups = set()
    wanted_ids = {row.get('reported_transaction_id') for q, _ in inputs
                  for row in q.get('revision_observations', [])
                  if isinstance(row.get('reported_transaction_id'), str)}
    source_versions = {}
    for q, x in inputs:
        source_versions.setdefault(str(q.get('document_id')), set()).add(x.get('source_sha256'))
    for qualification, extraction in inputs:
        document = str(qualification.get('document_id'))
        if qualification.get('revision_observations'):
            observations[document] = revision_reference_dispositions(qualification, extraction, current_transactions)
        method = extraction.get('extraction_method')
        mixed = method == 'mixed_native_ocr'
        ocr_only = method == 'tesseract_ocr'
        if method not in {'native_pdf_text', 'mixed_native_ocr', 'tesseract_ocr'} or (
                not ocr_only and not extraction.get('source_word_pages')):
            continue
        if not any(row.get('reported_transaction_id') in wanted_ids for row in extraction.get('transactions', [])):
            continue
        if ocr_only and extraction.get('source_word_pages'):
            replay = parse_word_pages({**extraction['source'], 'filing_type': 'P'}, extraction['source_sha256'],
                extraction['source_word_pages'], copy_allowed=extraction.get('source_pdf_copy_allowed'),
                ocr_engine=extraction.get('ocr_engine'))
            if any(extraction.get(key) != value for key, value in replay.items()):
                raise HouseIndexError('House related OCR revision source differs from saved words')
        from .house_native_contrast import PARSER_VERSIONS as CONTRAST_VERSIONS
        source_roots = {'evidence_root': evidence_root} if extraction.get('parser_version') in CONTRAST_VERSIONS else {}
        expected = qualify_automatic(extraction, qualification.get('identity', {}), **source_roots)
        if expected != qualification:
            if qualification.get('revision_observations') or ocr_only:
                raise HouseIndexError('House amendment source qualification differs from replay')
            continue  # Legacy reviewed facts cannot become automatic predecessors.
        if ocr_only:
            non_native_groups.update((qualification['identity'].get('person_id'), row.get('reported_transaction_id'))
                for row in extraction['transactions'] if row.get('reported_transaction_id') in wanted_ids)
            continue  # Source evidence blocks uniqueness; it never becomes an automatic predecessor.
        effective[document] = expected
        identities[document] = qualification['identity']
        # The replay above verifies every row's saved provenance. An unrelated
        # OCR page does not make an entirely native row unreadable; conversely
        # no OCR-derived row becomes a predecessor merely from being mixed.
        native_rows = [row for row in extraction['transactions']
                       if not mixed or row.get('ocr_source_pages') == []]
        contrast = extraction.get('parser_version') in CONTRAST_VERSIONS
        if contrast:
            native_rows = [item['current_projected'] for item in extraction['native_contrast_dispositions']
                           if item['status'] == 'current_projected']
        candidates = qualify_automatic(extraction, qualification['identity'], **source_roots,
            _source_bound_revision_ids=frozenset(r['extraction_id'] for r in native_rows
                if str(r.get('filing_status', '')).lower() == 'amended'))
        qualified = {r['id']: r for r in candidates['transactions']}
        source_facts[document] = qualified
        for row in native_rows if contrast else extraction['transactions']:
            official = row.get('reported_transaction_id')
            status = str(row.get('filing_status', '')).lower()
            if not isinstance(official, str) or not re.fullmatch(r'[0-9]{1,30}', official) or status not in {'new', 'amended'}:
                continue
            key = (qualification['identity'].get('person_id'), official)
            if mixed and row.get('ocr_source_pages') != []:
                non_native_groups.add(key)
                continue
            groups.setdefault(key, []).append({'row': row, 'fact': qualified.get(row['extraction_id']),
                'status': status, 'extraction': extraction, 'qualification': qualification,
                'document_id': document, 'date': extraction['source']['filed_date']})
    facts = {r['id']: deepcopy(r) for r in current_transactions}
    prior = {r['id']: r for r in prior_transactions or []}
    def same_formal(left, right):
        return (isinstance(left, dict) and isinstance(right, dict) and
                all(left.get(key) == right.get(key) for key in set(left) | set(right)))
    involved = [entry for entries in groups.values() if any(r['status'] == 'amended' for r in entries)
                for entry in entries]
    involved_documents = {r['document_id'] for r in involved}
    blocked_documents = {str(row.get('filing_id')) for row in prior.values()
        if str(row.get('filing_id')) in involved_documents and
        not same_formal(source_facts.get(str(row.get('filing_id')), {}).get(row.get('id')), row)}
    # Check IDs directly too: a changed filing/person/URL must not evade the
    # full-field comparison by moving a colliding ID outside its source report.
    blocked_documents.update(entry['document_id'] for entry in involved
        if entry['row']['extraction_id'] in prior and
        not same_formal(prior[entry['row']['extraction_id']], entry['fact']))
    blocked_ids = {r['row']['extraction_id'] for r in involved if r['document_id'] in blocked_documents}
    if blocked_documents:
        facts = {key: row for key, row in facts.items()
                 if str(row.get('filing_id')) not in blocked_documents and key not in blocked_ids}
        facts.update({key: deepcopy(row) for key, row in prior.items()
                      if str(row.get('filing_id')) in blocked_documents or key in blocked_ids})
    mappings = []
    resolved_ids = {}
    resolved_sources = {}
    for (person, official), entries in groups.items():
        amended = [r for r in entries if r['status'] == 'amended']
        if not amended:
            continue
        originals = [r for r in entries if r['status'] == 'new']
        reason = None
        if (person, official) in non_native_groups:
            reason = 'revision_related_row_uses_ocr'
        elif any(len(source_versions[r['document_id']]) != 1 for r in entries):
            reason = 'revision_source_version_ambiguous'
        elif len({tuple(r['qualification']['identity'].get(field) for field in
                ('official_name', 'state', 'party', 'state_district')) for r in entries}) != 1:
            reason = 'official_transaction_id_source_identity_context_conflict'
        elif len(originals) != 1:
            reason = 'official_transaction_id_predecessor_not_unique' if originals else 'official_transaction_id_predecessor_missing'
        elif any(r['document_id'] in blocked_documents for r in entries):
            reason = 'existing_revision_fact_diff_requires_source_reconciliation'
        elif originals[0]['fact'] is None or facts.get(originals[0]['row']['extraction_id']) != originals[0]['fact']:
            reason = 'official_transaction_id_predecessor_fact_not_source_bound'
        elif len({r['date'] for r in entries}) != len(entries):
            reason = 'revision_filing_chronology_ambiguous'
        else:
            ordered = sorted(entries, key=lambda r: r['date'])
            if ordered[0] is not originals[0]:
                reason = 'revision_precedes_original_filing'
        if reason:
            entry_ids = {r['row']['extraction_id'] for r in entries}
            old_group = [row for row in prior.values() if row.get('id') in entry_ids]
            if old_group:
                for entry in entries:
                    facts.pop(entry['row']['extraction_id'], None)
                for row in old_group:
                    facts[row['id']] = deepcopy(row)
            for entry in amended:
                for observation in observations.get(entry['document_id'], []):
                    if observation['extraction_id'] == entry['row']['extraction_id']:
                        observation.update(disposition=reason)
            continue
        predecessor = originals[0]
        # A bad intermediate revision makes later target identity unresolved.
        for entry in sorted(amended, key=lambda r: r['date']):
            identifier = entry['row']['extraction_id']
            if entry['fact'] is None:
                break
            before = predecessor['row']['extraction_id']
            facts.pop(before, None)
            facts[identifier] = deepcopy(prior.get(identifier, entry['fact']))
            mapping = {'prior_transaction_id': before, 'transaction_id': identifier,
                'person_id': person, 'reported_transaction_id': official,
                'prior_document_id': predecessor['document_id'], 'document_id': entry['document_id'],
                'prior_source_sha256': predecessor['extraction']['source_sha256'],
                'source_sha256': entry['extraction']['source_sha256'],
                'prior_filed_date': predecessor['date'], 'filed_date': entry['date'],
                'basis': 'unique_official_transaction_id_same_person_replayed_full_formal_facts_and_strict_filing_chronology'}
            mappings.append(mapping)
            resolved_ids.setdefault(entry['document_id'], set()).add(identifier)
            resolved_sources[entry['document_id']] = entry
            for observation in observations.get(entry['document_id'], []):
                if observation['extraction_id'] == identifier:
                    observation.update(disposition='source_bound_revision_applied', action='replace_prior',
                        formal_transaction_id=identifier, replacement=mapping)
            predecessor = entry
    for document, identifiers in resolved_ids.items():
        entry = resolved_sources[document]
        effective[document] = qualify_automatic(entry['extraction'], entry['qualification']['identity'],
            _source_bound_revision_ids=frozenset(identifiers))
    return {'transactions': sorted(facts.values(), key=lambda r:r['id']), 'qualifications': effective,
            'identities': identities, 'dispositions': observations, 'replacements': mappings}
