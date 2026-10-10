"""Read-only classification of already-censused Senate paper PTR grid slots.

The labels are observations, not transaction qualifications. In particular,
``data_observed`` means only that a fixed-form slot has one type mark, one
amount mark and asset ink. It does not establish its date, asset, filer,
duplicate relationship, or a publishable trade.
"""
from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime
import hashlib
from io import BytesIO
import io
import json
import os
from pathlib import Path
import re
from statistics import median
import subprocess
from typing import Callable

from PIL import Image


SCHEMA = "senate-paper-grid-slot-shadow/v1"
EVIDENCE_COMMIT = "80f086267677b8fde34314f24024bdc505d97cb0"
TYPE_CELLS = (("purchase", 1429), ("sale", 1518), ("exchange", 1612))
AMOUNT_CELLS = (
    ("1001_15000", 1980), ("15001_50000", 2077),
    ("50001_100000", 2168), ("100001_250000", 2255),
    ("250001_500000", 2342), ("500001_1000000", 2431),
    ("over_1000000", 2528), ("1000001_5000000", 2617),
    ("5000001_25000000", 2704), ("25000001_50000000", 2792),
    ("over_50000000", 2880),
)
TYPE_BOUNDS = (1385, 1464, 1562, 1659)
AMOUNT_BOUNDS = (1933, 2026, 2124, 2202, 2299, 2378, 2474,
                 2573, 2654, 2744, 2824, 2925)
FORM_BOUNDS = TYPE_BOUNDS + AMOUNT_BOUNDS
_NOTE = re.compile(r"^(?:notes?|comments?|explanations?)\s*:", re.I)
_DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")


def _crop_sha(image: Image.Image, box: list[int]) -> str:
    return hashlib.sha256(image.crop(tuple(box)).tobytes()).hexdigest()


def _mark_count(image: Image.Image, x: int, y: int) -> int:
    # The narrow window was calibrated against the 65-slot fixed report. A
    # wider window touches the final page's slanted vertical table rule.
    crop = image.crop((x - 15, y - 15, x + 16, y + 16))
    points = crop.load()
    return sum(points[xx, yy] < 160
               for yy in range(0, crop.height, 2)
               for xx in range(0, crop.width, 2))


def _mark_observations(image: Image.Image, cells: tuple, centers: list[int],
                       y: int) -> list[dict]:
    return [{"label": label, "center_px": [x, y],
             "box_px": [x - 15, y - 15, x + 16, y + 16],
             "grayscale_sha256": _crop_sha(
                 image, [x - 15, y - 15, x + 16, y + 16]),
             "dark_sample_count": count,
             "mark_signal": count >= 25}
            for (label, _), x in zip(cells, centers, strict=True)
            for count in [_mark_count(image, x, y)]]


def _parse_date(raw: str) -> str | None:
    if not _DATE.fullmatch(raw.strip()):
        return None
    for pattern in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            value = datetime.strptime(raw.strip(), pattern).date()
            if 2000 <= value.year <= 2100:
                return value.isoformat()
        except ValueError:
            continue
    return None


def _date_ocr_variant(crop: Image.Image, *, executable: str,
                      scale: int, digits_only: bool, name: str) -> dict:
    """Run one bounded OCR variant after dropping inherited transparency."""
    opaque = Image.new("L", crop.size, 255)
    opaque.paste(crop)
    enlarged = opaque.resize((opaque.width * scale, opaque.height * scale))
    buffer = BytesIO()
    enlarged.save(buffer, format="PNG")
    command = [executable, "stdin", "stdout", "-l", "eng", "--psm", "7"]
    if digits_only:
        command += ["-c", "tessedit_char_whitelist=0123456789/"]
    command.append("tsv")
    completed = subprocess.run(
        command, input=buffer.getvalue(), capture_output=True, check=True,
        timeout=30)
    words = []
    for row in csv.DictReader(io.StringIO(completed.stdout.decode("utf-8")),
                              delimiter="\t"):
        if row.get("level") != "5" or not (row.get("text") or "").strip():
            continue
        words.append((row["text"].strip(), float(row["conf"])))
    raw = " ".join(word for word, _ in words)
    return {"raw": raw, "parsed": _parse_date(raw),
            "min_confidence": min((confidence for _, confidence in words),
                                  default=None),
            "engine": name}


def tesseract_date_cell(crop: Image.Image, *, executable: str) -> dict:
    """One bounded date-cell OCR pass, retained for comparative experiments."""
    return _date_ocr_variant(crop, executable=executable, scale=2,
                             digits_only=True, name="tesseract-psm7-cell-x2-digits")


def combine_date_reads(reads: dict[str, dict]) -> dict:
    """Fail closed on strict-date disagreement or a sole valid OCR reading."""
    parsed = [item["parsed"] for item in reads.values() if item["parsed"]]
    distinct = set(parsed)
    if len(distinct) > 1:
        status, date = "conflict", None
    elif len(parsed) >= 2:
        status, date = "agreement", parsed[0]
    elif len(parsed) == 1:
        status, date = "insufficient", None
    else:
        status, date = "unreadable", None
    return {"status": status, "date": date, "raw": None,
            "valid_read_count": len(parsed), "variants": reads,
            "engine": "three-pass-strict-date-shadow"}


def tesseract_date_consensus(crop: Image.Image, *, executable: str) -> dict:
    """Three bounded passes; any valid conflict leaves the date unresolved."""
    return combine_date_reads({
        "x1_plain_psm7": _date_ocr_variant(
            crop, executable=executable, scale=1, digits_only=False,
            name="tesseract-psm7-cell-x1-plain"),
        "x2_digits_psm7": tesseract_date_cell(crop, executable=executable),
        "x3_digits_psm7": _date_ocr_variant(
            crop, executable=executable, scale=3, digits_only=True,
            name="tesseract-psm7-cell-x3-digits"),
    })


def _row_form_lines(image_bytes: bytes, slot: dict) -> tuple[int, ...] | None:
    """Find continuous vertical rules inside one row, excluding X-sized marks."""
    top, bottom = slot["row_bounds_px"]
    ys = range(top + 12, bottom - 12, 2)
    if len(ys) < 20:
        return None
    minimum = int(len(ys) * .6)
    groups: list[list[tuple[int, int]]] = []
    for x in range(1300, 2950):
        dark = sum(image_bytes[y * 3400 + x] < 160 for y in ys)
        if dark >= minimum:
            if not groups or x > groups[-1][-1][0] + 1:
                groups.append([])
            groups[-1].append((x, dark))
    peaks = [max(group, key=lambda point: point[1])[0] for group in groups]
    selected = []
    for expected in FORM_BOUNDS:
        nearby = sorted((abs(x - expected), x) for x in peaks
                        if abs(x - expected) <= 60)
        if not nearby:
            return None
        selected.append(nearby[0][1])
    if len(set(selected)) != len(FORM_BOUNDS):
        return None
    type_lines, amount_lines = selected[:4], selected[4:]
    if not all(55 <= b - a <= 125 for lines in (type_lines, amount_lines)
               for a, b in zip(lines, lines[1:])):
        return None
    return tuple(selected)


def _calibrate_page(image: Image.Image, slots: list[dict]) -> tuple[dict, dict[int, tuple]]:
    """Validate page-level grid topology, then give each slot local centers.

    Local line positions handle skew across a page. A missing line means the
    affected slot stays unknown; the page median does not fill missing proof.
    """
    raw = image.tobytes()
    lines_by_slot = {slot["grid_slot"]: _row_form_lines(raw, slot)
                     for slot in slots}
    good = [lines for lines in lines_by_slot.values() if lines is not None]
    if len(good) < 3:
        return ({"status": "unknown", "calibrated_slot_count": 0,
                 "reference_bounds_px": None}, {})
    reference = tuple(int(median(row[index] for row in good))
                      for index in range(len(FORM_BOUNDS)))
    centers = {}
    for slot, lines in lines_by_slot.items():
        if lines is None or any(abs(a - b) > 35
                                for a, b in zip(lines, reference)):
            continue
        type_centers = [(a + b) // 2 for a, b in zip(lines[:4], lines[1:4])]
        amount_centers = [(a + b) // 2 for a, b in zip(lines[4:], lines[5:])]
        centers[slot] = (type_centers, amount_centers, (lines[3], lines[4]))
    return ({"status": "verified" if len(centers) == len(slots) else "partial",
             "calibrated_slot_count": len(centers),
             "reference_bounds_px": list(reference)}, centers)


def classify_slot(slot: dict, image: Image.Image, *,
                  calibrated_centers: tuple[list[int], list[int], tuple[int, int]] | None = None,
                  calibration_required: bool = False,
                  date_reader: Callable[[Image.Image], dict] | None = None) -> dict:
    """Return one conservative label and raw mark evidence for a fixed slot."""
    if image.mode != "L" or image.size != (3400, 4400):
        raise ValueError("Senate paper page must be 3400x4400 grayscale")
    top, bottom = slot["row_bounds_px"]
    if not (0 <= top < bottom <= image.height):
        raise ValueError("invalid Senate paper grid bounds")
    for key in ("row_crop", "asset_crop", "date_crop"):
        crop = slot[key]
        if _crop_sha(image, crop["box_px"]) != crop["grayscale_sha256"]:
            raise ValueError(f"Senate paper {key} digest mismatch")
    y = (top + bottom) // 2
    if calibration_required and calibrated_centers is None:
        type_cells: list[dict] = []
        amount_cells: list[dict] = []
    else:
        type_centers, amount_centers = (calibrated_centers[:2]
                                        if calibrated_centers is not None else
                                        ([x for _, x in TYPE_CELLS],
                                         [x for _, x in AMOUNT_CELLS]))
        type_cells = _mark_observations(image, TYPE_CELLS, type_centers, y)
        amount_cells = _mark_observations(image, AMOUNT_CELLS, amount_centers, y)
    type_counts = {item["label"]: item["dark_sample_count"] for item in type_cells}
    amount_counts = {item["label"]: item["dark_sample_count"] for item in amount_cells}
    types = [label for label, count in type_counts.items() if count >= 25]
    amounts = [label for label, count in amount_counts.items() if count >= 25]
    asset = slot.get("asset_ocr_raw", "").strip()
    date = slot.get("date_ocr_raw", "").strip()
    ink = slot["asset_ink_sample_count"]
    if calibrated_centers is not None:
        date_left, date_right = calibrated_centers[2]
        date_box = [date_left + 10, top + 12, date_right - 10, bottom - 12]
        date_geometry = "calibrated"
    else:
        date_box = slot["date_crop"]["box_px"]
        date_geometry = "unverified" if calibration_required else "fixed_inventory"
    if calibration_required and calibrated_centers is None:
        label = "unknown"
    elif len(types) == len(amounts) == 1 and ink >= 150:
        label = "data_observed"
    elif not types and not amounts and not date and _NOTE.match(asset):
        label = "note_candidate"
    elif not types and not amounts and not date and asset.endswith(":") and ink >= 150:
        label = "heading_candidate"
    elif not types and not amounts and not asset and not date and ink < 150:
        label = "blank_appearance"
    else:
        label = "unknown"
    date_cell_ocr = (date_reader(image.crop(tuple(date_box)))
                     if date_reader is not None and date_geometry == "calibrated"
                     and label == "data_observed" else None)
    cell_date = (date_cell_ocr.get("date") if "date" in date_cell_ocr else
                 _parse_date(date_cell_ocr["raw"])) if date_cell_ocr else None
    return {
        "page_number": slot["page_number"],
        "grid_slot": slot["grid_slot"],
        "row_bounds_px": [top, bottom],
        "row_crop_sha256": slot["row_crop"]["grayscale_sha256"],
        "label": label,
        "type_marks": types,
        "amount_marks": amounts,
        "type_dark_samples": type_counts,
        "amount_dark_samples": amount_counts,
        "key_cells": {
            "direction": type_cells,
            "amount": amount_cells,
            "date": {"box_px": date_box,
                     "grayscale_sha256": _crop_sha(image, date_box),
                     "geometry": date_geometry,
                     "page_ocr_raw": date,
                     "page_ocr_parsed": _parse_date(date),
                     "cell_ocr": date_cell_ocr,
                     "cell_ocr_parsed": cell_date,
                     "conflict": bool(cell_date and _parse_date(date) and
                                      cell_date != _parse_date(date))},
        },
        "column_calibration": ("verified" if calibrated_centers is not None else
                               "unknown" if calibration_required else "fixed"),
        "asset_ocr_raw": asset,
        "date_ocr_raw": date,
        "asset_ink_sample_count": ink,
        "candidate_transaction_id": None,
    }


def classify_ledger(ledger: dict, page_bytes: Callable[[str, int], bytes], *,
                    calibrate_columns: bool = False,
                    date_reader: Callable[[Image.Image], dict] | None = None) -> dict:
    """Classify one existing ledger, validating original page and crop hashes."""
    if ledger.get("evidence_commit") != EVIDENCE_COMMIT:
        raise ValueError("Senate paper evidence commit changed")
    reports = ledger.get("reports")
    if reports is None:
        reports = [ledger]
    result = []
    for report in reports:
        by_page: dict[int, list[dict]] = {}
        for slot in report.get("slots", report.get("rows", [])):
            by_page.setdefault(slot["page_number"], []).append(slot)
        page_results = []
        observed = []
        for number, slots in sorted(by_page.items()):
            raw = page_bytes(report["document_id"], number)
            actual_sha = hashlib.sha256(raw).hexdigest()
            if any(slot["page_sha256"] != actual_sha for slot in slots):
                raise ValueError("Senate paper page SHA mismatch")
            image = Image.open(BytesIO(raw)).convert("L")
            calibration, centers = _calibrate_page(image, slots) if calibrate_columns else (
                {"status": "fixed", "calibrated_slot_count": 0,
                 "reference_bounds_px": None}, {})
            classified = [classify_slot(
                slot, image, calibrated_centers=centers.get(slot["grid_slot"]),
                calibration_required=calibrate_columns,
                date_reader=date_reader) for slot in slots]
            if len({row["grid_slot"] for row in classified}) != len(classified):
                raise ValueError("duplicate Senate grid slot")
            observed.extend(classified)
            page_results.append({"page_number": number,
                                 "page_sha256": actual_sha,
                                 "physical_grid_slot_count": len(classified),
                                 "column_calibration": calibration,
                                 "label_counts": dict(sorted(Counter(
                                     row["label"] for row in classified).items()))})
        if len(observed) != report["physical_grid_slot_count"]:
            raise ValueError("Senate paper physical slot conservation failed")
        result.append({"document_id": report["document_id"],
                       "entrypoint_source_sha256": report["entrypoint_source_sha256"],
                       "physical_grid_slot_count": len(observed),
                       "label_counts": dict(sorted(Counter(
                           row["label"] for row in observed).items())),
                       "pages": page_results, "slots": observed})
    return {"schema_version": SCHEMA, "evidence_commit": EVIDENCE_COMMIT,
            "interpretation": "classification only; no qualified or published trades",
            "reports": result}


def fixed_archive_pages(repo: Path, inventory: dict) -> Callable[[str, int], bytes]:
    """Read only pages named by the fixed inventory and evidence manifests."""
    if inventory.get("evidence_commit") != EVIDENCE_COMMIT:
        raise ValueError("Senate paper inventory evidence commit changed")
    paths: dict[tuple[str, int], tuple[str, str]] = {}
    env = dict(os.environ, GIT_NO_LAZY_FETCH="1")

    def git_bytes(path: str) -> bytes:
        proc = subprocess.run(["git", "-C", str(repo), "show",
                               f"{EVIDENCE_COMMIT}:{path}"],
                              capture_output=True, env=env, check=False)
        if proc.returncode:
            raise ValueError(f"fixed Senate paper archive unavailable: {path}")
        return proc.stdout

    for report in inventory["reports"]:
        document_id = report["document_id"]
        manifest = json.loads(git_bytes(
            f"senate_efd/paper_pages/{document_id}/manifest.json"))
        if manifest["manifest_sha256"] != report["manifest_sha256"]:
            raise ValueError("Senate paper manifest mismatch")
        for listed, archived in zip(report["pages"], manifest["pages"], strict=True):
            if listed["page_sha256"] != archived["sha256"]:
                raise ValueError("Senate paper inventory and manifest disagree")
            paths[(document_id, listed["page_number"])] = (
                archived["archive_path"], archived["sha256"])

    def read(document_id: str, page_number: int) -> bytes:
        path, expected = paths[(document_id, page_number)]
        raw = git_bytes(path)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("Senate paper archived image SHA mismatch")
        return raw

    return read
