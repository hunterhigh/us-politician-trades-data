"""Offline, fail-closed extraction for archived Senate paper PTR pages.

The current Senate paper reports use the official 3400x4400 legacy PTR form.
Transactions are identified from three independent cells on that form: the
date, exactly one transaction-type mark, and exactly one amount-range mark.
OCR supplies text and mark locations; geometry supplies their meaning.  Rows
that do not satisfy every qualification rule remain in the extraction with an
explicit quarantine disposition.
"""
from __future__ import annotations

import csv
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
from typing import Iterable

from .senate import SenateEfdError
from .senate_reports import (
    PAPER_INSPECTION_SCHEMA, PAPER_PAGE_MANIFEST_SCHEMA, PARSER_VERSION,
)


PAPER_EXTRACTION_SCHEMA = "senate-efd-paper-ptr-extraction/v1"
PAPER_EXTRACTION_BATCH_SCHEMA = "senate-efd-paper-ptr-extraction-batch/v1"
PAPER_OCR_RULE_VERSION = "senate-paper-form-geometry-2026-10-v2"
_FORM_WIDTH = 3400.0
_FORM_HEIGHT = 4400.0
_DATE = re.compile(r"[0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}")
_OWNER = re.compile(r"^\s*\((S|J|DC)\)\s*(.+)$", re.IGNORECASE)
_TICKER = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,9})\)(?:[0-9]+)?\s*$")
_MARKS = {"X", "×"}
_AMOUNTS = (
    "$1,001 - $15,000",
    "$15,001 - $50,000",
    "$50,001 - $100,000",
    "$100,001 - $250,000",
    "$250,001 - $500,000",
    "$500,001 - $1,000,000",
    "Over $1,000,000",
    "$1,000,001 - $5,000,000",
    "$5,000,001 - $25,000,000",
    "$25,000,001 - $50,000,000",
    "Over $50,000,000",
)
_AMOUNT_CENTERS = (1980, 2077, 2168, 2255, 2342, 2431, 2528, 2617, 2704, 2792, 2880)
_TYPE_CENTERS = {1429: "purchase", 1518: "sale", 1612: "exchange"}


def _center(word: dict) -> tuple[float, float]:
    return (word["left"] + word["width"] / 2, word["top"] + word["height"] / 2)


def _normalized_words(page: dict) -> list[dict]:
    width, height = page.get("width"), page.get("height")
    words = page.get("words")
    if (not isinstance(width, int) or not isinstance(height, int) or
            width < 1000 or height < 1000 or not isinstance(words, list)):
        raise SenateEfdError("Senate paper OCR page dimensions are invalid")
    sx, sy = _FORM_WIDTH / width, _FORM_HEIGHT / height
    normalized = []
    for word in words:
        if (isinstance(word, dict) and isinstance(word.get("text"), str) and
                not word["text"].strip()):
            continue
        if (not isinstance(word, dict) or not isinstance(word.get("text"), str) or
                any(not isinstance(word.get(key), (int, float)) for key in
                    ("left", "top", "width", "height", "confidence"))):
            raise SenateEfdError("Senate paper OCR word is invalid")
        if word["width"] <= 0 or word["height"] <= 0:
            # Some OCR engines emit a zero-area separator token. It carries no
            # usable evidence and must not influence row discovery.
            continue
        normalized.append({
            "text": " ".join(word["text"].split()),
            "left": word["left"] * sx,
            "top": word["top"] * sy,
            "width": word["width"] * sx,
            "height": word["height"] * sy,
            "confidence": float(word["confidence"]),
        })
    return normalized


def _parse_date(raw: str) -> str | None:
    for pattern in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            value = datetime.strptime(raw, pattern).date()
            if 2000 <= value.year <= 2100:
                return value.isoformat()
        except ValueError:
            pass
    return None


def _nearest(value: float, centers: Iterable[float], *, maximum: float = 45) -> int | None:
    values = tuple(centers)
    distances = [abs(value - center) for center in values]
    index = min(range(len(values)), key=distances.__getitem__)
    return index if distances[index] <= maximum else None


def _cluster(values: list[float], *, radius: float = 45) -> list[float]:
    groups: list[list[float]] = []
    for value in sorted(values):
        if not groups or value - sum(groups[-1]) / len(groups[-1]) > radius:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [sum(group) / len(group) for group in groups]


def _join_cell(words: list[dict], y: float) -> tuple[str, float | None]:
    selected = [word for word in words
                if 600 <= _center(word)[0] < 1385 and abs(_center(word)[1] - y) <= 58]
    selected.sort(key=lambda word: (round(_center(word)[1] / 25), word["left"]))
    return (" ".join(word["text"] for word in selected).strip(),
            min((word["confidence"] for word in selected), default=None))


def _marks(words: list[dict], y: float, low: float, high: float) -> list[dict]:
    return [word for word in words
            if low <= _center(word)[0] < high and abs(_center(word)[1] - y) <= 45 and
            word["text"].strip().upper() in _MARKS]


def _paper_row(source_sha: str, page_number: int, position: int,
               words: list[dict], y: float, *, grid_slot: dict | None = None) -> dict:
    reasons: list[str] = []
    date_words = [word for word in words
                  if 1650 <= _center(word)[0] < 1940 and abs(_center(word)[1] - y) <= 48 and
                  _DATE.fullmatch(word["text"])]
    if grid_slot is not None:
        left, top, right, bottom = grid_slot["key_cells"]["date"]["box_px"]
        date_words = [word for word in words if
                      left <= _center(word)[0] < right and top <= _center(word)[1] < bottom]
    if len(date_words) != 1:
        reasons.append("paper_date_mark_ambiguous")
        transaction_date = None
        date_raw = None
    else:
        date_raw = date_words[0]["text"]
        transaction_date = _parse_date(date_raw)
        if transaction_date is None:
            reasons.append("paper_date_invalid")
        if date_words[0]["confidence"] < 80:
            reasons.append("paper_date_ocr_low_confidence")

    type_marks = _marks(words, y, 1380, 1665)
    type_indexes = [_nearest(_center(word)[0], _TYPE_CENTERS) for word in type_marks]
    type_indexes = [index for index in type_indexes if index is not None]
    if grid_slot is not None:
        type_indexes = [list(_TYPE_CENTERS.values()).index(value)
                        for value in grid_slot["type_marks"]]
    if len(type_indexes) != 1:
        reasons.append("paper_transaction_type_mark_ambiguous")
        transaction_type = None
    else:
        transaction_type = tuple(_TYPE_CENTERS.values())[type_indexes[0]]

    amount_marks = _marks(words, y, 1930, 2925)
    amount_indexes = [_nearest(_center(word)[0], _AMOUNT_CENTERS) for word in amount_marks]
    amount_indexes = [index for index in amount_indexes if index is not None]
    if grid_slot is not None:
        from .senate_slots_shadow import AMOUNT_CELLS
        labels = [label for label, _ in AMOUNT_CELLS]
        amount_indexes = [labels.index(value) for value in grid_slot["amount_marks"]]
    if len(amount_indexes) != 1:
        reasons.append("paper_amount_mark_ambiguous")
        amount_raw = None
    else:
        amount_raw = _AMOUNTS[amount_indexes[0]]
        # The legacy form prints an open-ended $1m column before the later
        # finite bands. Its position must not shift every following amount.
        # Open ranges retain their exact label. The candidate applies the
        # shared explicit threshold / null-upper-bound contract.

    asset_cell, asset_confidence = _join_cell(words, y)
    if grid_slot is not None:
        asset_cell, asset_confidence = _grid_asset_cell(words, grid_slot)
    owner_match = _OWNER.fullmatch(asset_cell)
    if owner_match is None:
        reasons.append("paper_owner_ocr_ambiguous")
        owner_raw, asset_name = None, asset_cell or None
    else:
        owner_raw = {"S": "Spouse", "J": "Joint", "DC": "Child"}[owner_match.group(1).upper()]
        asset_name = owner_match.group(2).strip()
    if not asset_name:
        reasons.append("paper_asset_name_missing")
    if asset_confidence is None or asset_confidence < 85:
        reasons.append("paper_asset_ocr_low_confidence")

    stock = bool(asset_name and re.search(r"\(Stock\)", asset_name, re.IGNORECASE))
    ticker_match = _TICKER.search(asset_name or "") if stock else None
    ticker = ticker_match.group(1) if ticker_match else None
    stable = "|".join((source_sha, str(page_number), str(position), date_raw or "",
                       owner_raw or "", asset_name or "", transaction_type or "",
                       amount_raw or ""))
    return {
        "extraction_id": "senate-ptr:" + hashlib.sha256(stable.encode()).hexdigest()[:24],
        "row_number": position,
        "page_number": page_number,
        "transaction_date": transaction_date,
        "owner_raw": owner_raw,
        "ticker_raw": ticker,
        "asset_name_raw": asset_name,
        "asset_type_raw": "Stock" if stock else "Other",
        "transaction_type_raw": transaction_type.title() if transaction_type else None,
        "transaction_type": transaction_type,
        "amount_raw": amount_raw,
        "comment_raw": None,
        "qualification_status": "eligible" if not reasons else "quarantined",
        "quarantine_reasons": sorted(set(reasons)),
        "ocr_min_confidence": asset_confidence,
    }


def _grid_asset_cell(words: list[dict], slot: dict) -> tuple[str, float | None]:
    top, bottom = slot["row_bounds_px"]
    selected = [word for word in words if 650 <= _center(word)[0] < 1385 and
                top + 6 <= _center(word)[1] < bottom - 6]
    selected.sort(key=lambda word: (round(_center(word)[1] / 25), word["left"]))
    return (" ".join(word["text"] for word in selected).strip(),
            min((word["confidence"] for word in selected), default=None))


def parse_paper_grid_observations(report: dict, manifest: dict, observation: dict,
                                  pages: list[dict], page_images: dict[int, bytes]) -> dict:
    """Interpret fixed source-bound slots for a previously failed report.

    The saved grid defines row positions; OCR no longer decides whether an
    unreadable date or missing X makes a physical row disappear. Existing
    extraction rows are deliberately not merged or re-keyed by this path.
    """
    from io import BytesIO
    from PIL import Image
    from .senate_paper_observation import _source_report_relations
    from .senate_slots_shadow import TYPE_CELLS, AMOUNT_CELLS, _mark_count

    document_id, source_sha = report.get("document_id"), manifest.get("entrypoint_source_sha256")
    if (report.get("source_id") != "senate_efd" or report.get("access_method") != "paper_ptr" or
            manifest.get("document_id") != document_id or
            manifest.get("schema_version") != PAPER_PAGE_MANIFEST_SCHEMA or
            manifest.get("evidence_complete") is not True or
            observation.get("document_id") != document_id or
            observation.get("entrypoint_source_sha256") != source_sha or
            observation.get("manifest_sha256") != manifest.get("manifest_sha256") or
            observation.get("coverage_status") != "fixed_slots_observed" or
            observation.get("legacy_extraction_failed") is not True or
            observation.get("legacy_extraction_count") != 0 or
            observation.get("source_report_ledger") != _source_report_relations(observation)):
        raise SenateEfdError("Senate grid facts require a bound previously failed source")
    listed = {page["page_number"]: page["sha256"] for page in manifest["pages"]}
    observed = {page["page_number"]: page["page_sha256"] for page in observation["pages"]}
    by_page = {page["page_number"]: _normalized_words(page) for page in pages}
    if (len(by_page) != len(pages) or set(page_images) != set(observed) or
            not set(observed) <= set(by_page) or len(observed) != len(observation["pages"]) or
            any(page.get("source_sha256") != observed.get(page["page_number"]) for page in pages)):
        raise SenateEfdError("Senate grid word pages or source images are missing or repeated")
    images = {}
    cache_sha = hashlib.sha256(json.dumps(pages, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode("utf8")).hexdigest()
    for number, expected_sha in observed.items():
        raw = page_images[number]
        if listed.get(number) != expected_sha or hashlib.sha256(raw).hexdigest() != expected_sha:
            raise SenateEfdError("Senate grid source image differs from archived page")
        image = Image.open(BytesIO(raw)).convert("L")
        if image.size != (3400, 4400):
            raise SenateEfdError("Senate grid source image has unsupported dimensions")
        images[number] = image
    rows, account, account_ref, scope_unknown, previous_page = [], None, None, False, None
    slots = sorted(observation["slots"], key=lambda slot: (slot["page_number"], slot["grid_slot"]))
    for slot in slots:
        number, label = slot["page_number"], slot["label"]
        if previous_page is not None and number > previous_page + 1:
            account, scope_unknown = None, account is not None
            account_ref = None
        previous_page = number
        if label == "unknown":
            # An unreadable transaction or a noisy blank is not evidence of a
            # new account. The legacy form also prints unindented account
            # headings without a colon; child transaction names are indented.
            text, confidence = _grid_asset_cell(by_page[number], slot)
            top, bottom = slot["row_bounds_px"]
            unindented = any(600 <= word["left"] < 750 and
                            top + 6 <= _center(word)[1] < bottom - 6 and
                            _OWNER.match(word["text"]) for word in by_page[number])
            explicit_heading = (slot.get("column_calibration") == "verified" and
                                unindented and not slot["type_marks"] and not slot["amount_marks"] and
                                not slot["date_ocr_raw"] and _OWNER.fullmatch(text))
            if explicit_heading:
                if confidence is not None and confidence >= 85:
                    account, scope_unknown = text, False
                    account_ref = {"page_number": number, "grid_slot": slot["grid_slot"],
                                   "page_sha256": observed[number], "row_crop_sha256": slot["row_crop_sha256"]}
                else:
                    account, scope_unknown = None, True
                    account_ref = None
            continue
        if label == "heading_candidate":
            text, confidence = _grid_asset_cell(by_page[number], slot)
            if text.endswith(":") and _OWNER.fullmatch(text) and confidence is not None and confidence >= 85:
                account, scope_unknown = text, False
                account_ref = {"page_number": number, "grid_slot": slot["grid_slot"],
                               "page_sha256": observed[number], "row_crop_sha256": slot["row_crop_sha256"]}
            else:
                account, scope_unknown = None, True
                account_ref = None
            continue
        if label != "data_observed":
            continue
        if slot.get("column_calibration") != "verified":
            raise SenateEfdError("Senate grid data row lacks calibrated columns")
        image = images[number]
        for field, cells in (("direction", TYPE_CELLS), ("amount", AMOUNT_CELLS)):
            reads = slot["key_cells"][field]
            if [read["label"] for read in reads] != [value for value, _ in cells]:
                raise SenateEfdError("Senate grid mark columns changed")
            signals = []
            for read in reads:
                box = read["box_px"]
                if hashlib.sha256(image.crop(tuple(box)).tobytes()).hexdigest() != read["grayscale_sha256"]:
                    raise SenateEfdError("Senate grid mark crop differs from source")
                count = _mark_count(image, *read["center_px"])
                if count != read["dark_sample_count"] or read["mark_signal"] != (count >= 25):
                    raise SenateEfdError("Senate grid mark signal differs from source")
                if count >= 25:
                    signals.append(read["label"])
            if signals != slot["type_marks" if field == "direction" else "amount_marks"]:
                raise SenateEfdError("Senate grid mark interpretation differs from source")
        date = slot["key_cells"]["date"]
        if hashlib.sha256(image.crop(tuple(date["box_px"])).tobytes()).hexdigest() != date["grayscale_sha256"]:
            raise SenateEfdError("Senate grid date crop differs from source")
        row = _paper_row(source_sha, number, len(rows) + 1, by_page[number],
                         sum(slot["row_bounds_px"]) / 2, grid_slot=slot)
        asset_raw, asset_confidence = _grid_asset_cell(by_page[number], slot)
        left, top, right, bottom = date["box_px"]
        date_words = [word for word in by_page[number] if
                      left <= _center(word)[0] < right and top <= _center(word)[1] < bottom]
        row["field_observations"] = {
            "cached_word_pages_sha256": cache_sha, "asset_raw": asset_raw,
            "asset_min_confidence": asset_confidence,
            "date_raw": " ".join(word["text"] for word in date_words),
            "date_min_confidence": min((word["confidence"] for word in date_words), default=None),
            "date_box_px": date["box_px"], "date_crop_sha256": date["grayscale_sha256"],
            "direction_labels": slot["type_marks"], "amount_labels": slot["amount_marks"],
        }
        reasons = row["quarantine_reasons"]
        prior_dates = {date.get("page_ocr_parsed"), date.get("cell_ocr_parsed")}
        prior_dates.discard(None)
        if date.get("conflict") or any(value != row["transaction_date"] for value in prior_dates):
            reasons.append("paper_date_observation_conflict")
        if scope_unknown:
            reasons.append("paper_account_scope_unresolved")
        if row["asset_type_raw"] == "Other":
            row["asset_type_raw"] = None
            row["asset_type_disclosure"] = "not_disclosed"
        if account:
            row["asset_name_raw"] = f"{row['asset_name_raw']} (Account: {account})"
        row["account_heading_raw"] = account
        row["account_heading_source_slot"] = account_ref
        row["source_slot"] = {"page_number": number, "grid_slot": slot["grid_slot"],
                              "page_sha256": observed[number], "row_crop_sha256": slot["row_crop_sha256"]}
        # Reuse the formal paper source-slot fingerprint already used by both
        # delivered fixed reports. OCR text is evidence, not row identity.
        fingerprint = "|".join((source_sha, str(number), observed[number],
                                str(slot["grid_slot"]), slot["row_crop_sha256"]))
        row["extraction_id"] = "senate-ptr:" + hashlib.sha256(fingerprint.encode("ascii")).hexdigest()[:24]
        row["row_number"] = slot["grid_slot"]
        row["quarantine_reasons"] = sorted(set(reasons))
        row["qualification_status"] = "quarantined" if reasons else "eligible"
        rows.append(row)
    try:
        filed = datetime.strptime(report["portal_listed_date"], "%Y-%m-%d").strftime("%m/%d/%Y")
    except (KeyError, TypeError, ValueError):
        raise SenateEfdError("Senate grid source catalog filing date is invalid") from None
    return {"schema_version": PAPER_EXTRACTION_SCHEMA, "parser_version": PARSER_VERSION,
            "paper_rule_version": PAPER_OCR_RULE_VERSION, "source_id": "senate_efd",
            "document_id": document_id, "source_sha256": source_sha, "source_url": report["document_url"],
            "filer_name": report["filer_name"], "portal_listed_date": report["portal_listed_date"],
            "report_label_date": report.get("report_label_date"),
            "report_amendment_number": report.get("report_amendment_number"),
            "report_title_date": report.get("report_label_date"), "catalog_title_date_matches": True,
            "filed_at_raw": f"Filed {filed}", "document_disposition": "transactions_parsed",
            "evidence_complete": True, "page_manifest_sha256": manifest["manifest_sha256"],
            "page_count": manifest["page_count"], "transactions": rows,
            "cached_word_pages_sha256": cache_sha,
            "source_report_ledger": observation["source_report_ledger"],
            "eligible_transaction_count": sum(row["qualification_status"] == "eligible" for row in rows),
            "quarantined_transaction_count": sum(row["qualification_status"] == "quarantined" for row in rows)}


def parse_paper_word_pages(report: dict, manifest: dict, pages: list[dict]) -> dict:
    """Parse already-OCRed official pages; useful for tests and offline replay."""

    document_id = report.get("document_id")
    source_sha = manifest.get("entrypoint_source_sha256")
    if (report.get("source_id") != "senate_efd" or report.get("access_method") != "paper_ptr" or
            manifest.get("schema_version") != PAPER_PAGE_MANIFEST_SCHEMA or
            manifest.get("source_id") != "senate_efd" or
            manifest.get("document_id") != document_id or
            manifest.get("parser_version") != PARSER_VERSION or
            not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha) or
            manifest.get("page_count") != len(pages) or len(pages) < 2):
        raise SenateEfdError("Senate paper report inputs are inconsistent")

    expected_numbers = list(range(1, len(pages) + 1))
    if [page.get("page_number") for page in pages] != expected_numbers:
        raise SenateEfdError("Senate paper OCR pages are not complete and ordered")
    transactions = []
    position = 0
    for page in pages[1:]:
        number = page["page_number"]
        words = _normalized_words(page)
        minimum_y = 2100 if number == 2 else 1200
        anchors = []
        for word in words:
            x, y = _center(word)
            if y < minimum_y or y > 4050:
                continue
            is_date = 1650 <= x < 1940 and bool(_DATE.fullmatch(word["text"]))
            is_type_mark = 1380 <= x < 1665 and word["text"].strip().upper() in _MARKS
            is_amount_mark = 1930 <= x < 2925 and word["text"].strip().upper() in _MARKS
            if is_date or is_type_mark or is_amount_mark:
                anchors.append(y)
        for y in _cluster(anchors):
            has_date = any(1650 <= _center(word)[0] < 1940 and
                           abs(_center(word)[1] - y) <= 48 and _DATE.fullmatch(word["text"])
                           for word in words)
            has_type = bool(_marks(words, y, 1380, 1665))
            has_amount = bool(_marks(words, y, 1930, 2925))
            if sum((has_date, has_type, has_amount)) < 2:
                continue
            position += 1
            transactions.append(_paper_row(source_sha, number, position, words, y))
    if not transactions:
        raise SenateEfdError("Senate paper PTR contains no bounded transaction rows")

    listed = report.get("portal_listed_date")
    try:
        filed = datetime.strptime(listed, "%Y-%m-%d").strftime("%m/%d/%Y")
    except (TypeError, ValueError):
        raise SenateEfdError("Senate paper PTR catalog filing date is invalid") from None
    return {
        "schema_version": PAPER_EXTRACTION_SCHEMA,
        "parser_version": PARSER_VERSION,
        "paper_rule_version": PAPER_OCR_RULE_VERSION,
        "source_id": "senate_efd",
        "document_id": document_id,
        "source_url": report["document_url"],
        "source_sha256": source_sha,
        "filer_name": report["filer_name"],
        "portal_listed_date": listed,
        "report_label_date": report.get("report_label_date"),
        "report_amendment_number": report.get("report_amendment_number"),
        "report_title_date": report.get("report_label_date"),
        "catalog_title_date_matches": True,
        "filed_at_raw": f"Filed {filed}",
        "document_disposition": "transactions_parsed",
        "evidence_complete": True,
        "page_manifest_sha256": manifest.get("manifest_sha256"),
        "page_count": len(pages),
        "transactions": transactions,
        "eligible_transaction_count": sum(
            row["qualification_status"] == "eligible" for row in transactions),
        "quarantined_transaction_count": sum(
            row["qualification_status"] == "quarantined" for row in transactions),
    }


def tesseract_words(image_path: Path, *, executable: str = "tesseract") -> list[dict]:
    """Return word boxes from a local archived image without network access."""

    try:
        completed = subprocess.run(
            [executable, str(image_path), "stdout", "-l", "eng", "tsv"],
            check=True, capture_output=True, text=True, encoding="utf-8", timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SenateEfdError(f"Senate paper OCR failed: {exc}") from None
    reader = csv.DictReader(io.StringIO(completed.stdout), delimiter="\t")
    words = []
    for row in reader:
        text = (row.get("text") or "").strip()
        if row.get("level") != "5" or not text:
            continue
        try:
            confidence = float(row["conf"])
            left, top = int(row["left"]), int(row["top"])
            width, height = int(row["width"]), int(row["height"])
        except (KeyError, TypeError, ValueError):
            raise SenateEfdError("Senate paper OCR TSV is malformed") from None
        if confidence < 0:
            continue
        words.append({"text": text, "confidence": confidence,
                      "left": left, "top": top, "width": width, "height": height})
    return words


def _load_page(evidence_root: Path, item: dict, page_number: int,
               *, executable: str) -> dict:
    metadata_paths = sorted((evidence_root / "senate_efd" / "paper_pages" /
                             item["document_id"] / str(page_number)).glob("*.metadata.json"))
    if len(metadata_paths) != 1:
        raise SenateEfdError("Senate paper page metadata is incomplete")
    metadata = json.loads(metadata_paths[0].read_text(encoding="utf-8"))
    image_path = evidence_root / metadata.get("archive_path", "")
    content = image_path.read_bytes()
    if (metadata.get("schema_version") != "senate-efd-paper-page-archive/v1" or
            metadata.get("document_id") != item["document_id"] or
            metadata.get("page_number") != page_number or
            metadata.get("page_count") != item["page_count"] or
            metadata.get("sha256") != hashlib.sha256(content).hexdigest() or
            image_path.suffix.lower() != ".gif"):
        raise SenateEfdError("Senate paper page evidence does not match its manifest")
    return {"page_number": page_number, "width": metadata["width"],
            "height": metadata["height"], "words": tesseract_words(
                image_path, executable=executable)}


def extract_archived_paper_reports(evidence_root: Path, review_root: Path,
                                   *, executable: str = "tesseract",
                                   grid_observations: dict[str, dict] | None = None,
                                   cached_word_pages: dict[str, list[dict]] | None = None,
                                   selected_document_ids: list[str] | None = None) -> dict:
    """Extract every current paper viewer from immutable archived page bytes."""

    status = json.loads((review_root / "status" / "senate_efd.json").read_text(encoding="utf-8"))
    catalog_sha = status.get("catalog_sha256")
    discovery = json.loads((review_root / "senate_efd" / "discoveries" /
                            f"{catalog_sha}.json").read_text(encoding="utf-8"))
    reports = {item.get("document_id"): item for item in discovery.get("reports", [])
               if isinstance(item, dict) and item.get("access_method") == "paper_ptr"}
    catalog_report_count = len(reports)
    if selected_document_ids is not None:
        if (not isinstance(selected_document_ids, list) or not selected_document_ids or
                len(selected_document_ids) != len(set(selected_document_ids)) or
                not set(selected_document_ids) <= set(reports)):
            raise SenateEfdError("Senate paper selected document IDs are outside the catalog or repeated")
        reports = {key: value for key, value in reports.items() if key in selected_document_ids}
    inspections = {}
    for path in sorted((review_root / "senate_efd" / "paper_inspections").glob(
            f"*/*/{PARSER_VERSION}.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        if selected_document_ids is not None and item.get("document_id") not in reports:
            continue
        if item.get("schema_version") != PAPER_INSPECTION_SCHEMA:
            raise SenateEfdError("Senate paper inspection schema changed")
        inspections[item.get("document_id")] = item
    if not reports or set(reports) != set(inspections):
        raise SenateEfdError("Senate paper catalog and inspection set differ")
    grids = grid_observations or {}
    cached_pages = cached_word_pages or {}
    if set(grids) != set(cached_pages) or not set(grids) <= set(reports):
        raise SenateEfdError("Senate saved grid inputs do not match selected paper reports")
    if grid_observations is not None and set(grids) != set(reports):
        raise SenateEfdError("Senate saved grid mode requires cached input for every selected report")

    extractions, failures = [], []
    for document_id in sorted(reports):
        try:
            manifest_path = (evidence_root / "senate_efd" / "paper_pages" /
                             document_id / "manifest.json")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            inspection = inspections[document_id]
            if manifest.get("entrypoint_source_sha256") != inspection.get("source_sha256"):
                raise SenateEfdError("Senate paper manifest and inspection evidence differ")
            if document_id in grids:
                observation = grids[document_id]
                archived = {page["page_number"]: page for page in manifest["pages"]}
                images = {}
                for observed in observation["pages"]:
                    number = observed["page_number"]
                    path = (evidence_root / archived[number]["archive_path"]).resolve()
                    if not path.is_relative_to(evidence_root.resolve()):
                        raise SenateEfdError("Senate saved grid image escaped evidence root")
                    images[number] = path.read_bytes()
                extractions.append(parse_paper_grid_observations(
                    reports[document_id], manifest, observation, cached_pages[document_id], images))
            else:
                pages = [_load_page(evidence_root, manifest, number, executable=executable)
                         for number in range(1, manifest.get("page_count", 0) + 1)]
                extractions.append(parse_paper_word_pages(reports[document_id], manifest, pages))
        except (OSError, json.JSONDecodeError, SenateEfdError) as exc:
            failures.append({
                "schema_version": "senate-efd-paper-ptr-failure/v1",
                "parser_version": PARSER_VERSION,
                "paper_rule_version": PAPER_OCR_RULE_VERSION,
                "source_id": "senate_efd",
                "document_id": document_id,
                "source_sha256": inspections[document_id].get("source_sha256"),
                "reason": str(exc),
            })
    result = {
        "schema_version": PAPER_EXTRACTION_BATCH_SCHEMA,
        "parser_version": PARSER_VERSION,
        "paper_rule_version": PAPER_OCR_RULE_VERSION,
        "source_id": "senate_efd",
        "report_count": len(reports),
        "extraction_count": len(extractions),
        "failure_count": len(failures),
        "transaction_count": sum(len(item["transactions"]) for item in extractions),
        "eligible_transaction_count": sum(item["eligible_transaction_count"] for item in extractions),
        "quarantined_transaction_count": sum(
            item["quarantined_transaction_count"] for item in extractions),
        "extractions": extractions,
        "failures": failures,
    }
    if selected_document_ids is not None:
        result.update(catalog_report_count=catalog_report_count,
                      selected_document_ids=sorted(selected_document_ids),
                      scope="selected_fixed_reports_not_full_catalog")
    return result
