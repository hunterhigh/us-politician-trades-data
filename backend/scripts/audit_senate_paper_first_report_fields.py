"""Read fixed row crops of one Senate paper PTR without projecting candidates."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
from io import BytesIO
import importlib.metadata
import json
from pathlib import Path

import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

from audit_senate_paper_pages import _git_bytes
from unison_snapshot.senate_paper_sample import DOCUMENT_ID, EVIDENCE_COMMIT


ROWS_SHA256 = "1653af8696a0a86b0e4f54277ab63caa6e5c9ddb638d463dfefa02dd2a438a70"
VISUAL_REVIEW_SHA256 = "c6c6e72dce711f5d67c115a3e198d90930d892669049ba796f96005f23bd9c9a"
MANIFEST_PATH = f"senate_efd/paper_pages/{DOCUMENT_ID}/manifest.json"


def _read_crop(ocr: RapidOCR, image: Image.Image, crop_record: dict) -> dict:
    box = crop_record["box_px"]
    crop = image.crop(tuple(box)).convert("RGB")
    if hashlib.sha256(crop.convert("L").tobytes()).hexdigest() != crop_record["grayscale_sha256"]:
        raise ValueError("fixed Senate field crop hash changed")
    result, _ = ocr(np.asarray(crop))
    observations = []
    for polygon, raw_text, confidence in result or []:
        observations.append({
            "text": raw_text,
            "confidence": round(float(confidence), 6),
            "box_in_crop_px": [[round(float(x), 2), round(float(y), 2)]
                               for x, y in polygon],
        })
    observations.sort(key=lambda item: (item["box_in_crop_px"][0][1],
                                        item["box_in_crop_px"][0][0]))
    return {"crop": crop_record, "observations": observations,
            "joined_text": " ".join(item["text"] for item in observations)}


def _date_status(value: dict) -> tuple[str, str | None]:
    if len(value["observations"]) != 1 or value["observations"][0]["confidence"] < 0.90:
        return "ambiguous_ocr", None
    text = value["observations"][0]["text"].strip()
    try:
        parsed = datetime.strptime(text, "%m/%d/%y").date()
    except ValueError:
        return "ambiguous_ocr", None
    if parsed.year != 2026:
        return "ambiguous_ocr", None
    return "legible_ocr_pending_visual", parsed.isoformat()


def audit(repo: Path, rows_path: Path, visual_review_path: Path) -> dict:
    raw_rows = rows_path.read_bytes()
    if hashlib.sha256(raw_rows).hexdigest() != ROWS_SHA256:
        raise ValueError("fixed Senate first-report row audit changed")
    source = json.loads(raw_rows)
    if source.get("evidence_commit") != EVIDENCE_COMMIT or source.get("document_id") != DOCUMENT_ID:
        raise ValueError("fixed Senate first-report source binding changed")
    raw_review = visual_review_path.read_bytes()
    if hashlib.sha256(raw_review.replace(b"\r\n", b"\n")).hexdigest() != VISUAL_REVIEW_SHA256:
        raise ValueError("fixed Senate visual review changed")
    visual_review = json.loads(raw_review)
    if (visual_review.get("document_id") != DOCUMENT_ID or
            visual_review.get("evidence_commit") != EVIDENCE_COMMIT or
            not isinstance(visual_review.get("rows"), list)):
        raise ValueError("fixed Senate visual review has invalid source binding")
    checked = {(item["page_number"], item["grid_slot"]): item
               for item in visual_review["rows"]}
    if len(checked) != 36 or len(visual_review["rows"]) != 36:
        raise ValueError("fixed Senate visual review does not cover 36 unique rows")
    manifest = json.loads(_git_bytes(repo, MANIFEST_PATH))
    if manifest["manifest_sha256"] != source["manifest_sha256"]:
        raise ValueError("fixed Senate paper page manifest changed")
    ocr = RapidOCR()
    pages: dict[int, Image.Image] = {}
    result_rows = []
    for row in source["rows"]:
        if row["disposition"] != "transaction_observed_quarantined":
            continue
        number = row["page_number"]
        archived = manifest["pages"][number - 1]
        if number not in pages:
            content = _git_bytes(repo, archived["archive_path"])
            if (hashlib.sha256(content).hexdigest() != row["page_sha256"] or
                    archived["sha256"] != row["page_sha256"]):
                raise ValueError("fixed Senate paper page hash changed")
            pages[number] = Image.open(BytesIO(content)).convert("L")
        image = pages[number]
        directions = [item for item in row["type_mark_cells"] if item["mark_signal"]]
        amounts = [item for item in row["amount_mark_cells"] if item["mark_signal"]]
        if len(directions) != 1 or len(amounts) != 1:
            raise ValueError("fixed Senate row has no unique direction and amount signals")
        asset = _read_crop(ocr, image, row["asset_crop"])
        date = _read_crop(ocr, image, row["date_crop"])
        date_status, normalized_date = _date_status(date)
        asset_status = ("legible_ocr_pending_visual" if len(asset["observations"]) == 1 and
                        asset["observations"][0]["confidence"] >= 0.90 else "ambiguous_ocr")
        review = checked.get((number, row["grid_slot"]))
        if review is None:
            raise ValueError("fixed Senate visual review omits a selected row")
        checks = {
            "asset": asset_status == "legible_ocr_pending_visual" and
                     review["asset_text"] == asset["joined_text"],
            "date": date_status == "legible_ocr_pending_visual" and
                    review["date_text"] == date["joined_text"],
            "direction": review["direction"] == directions[0]["label"],
            "amount": review["amount_band"] == amounts[0]["label"],
        }
        asset_status = "confirmed_fixed_image" if checks["asset"] else "ambiguous_visual_conflict"
        date_status = "confirmed_fixed_image" if checks["date"] else "ambiguous_visual_conflict"
        result_rows.append({
            "page_number": number,
            "grid_slot": row["grid_slot"],
            "page_sha256": row["page_sha256"],
            "source_url": row["source_url"],
            "row_bounds_px": row["row_bounds_px"],
            "row_crop": row["row_crop"],
            "direction": {"status": ("confirmed_fixed_image" if checks["direction"]
                                      else "ambiguous_visual_conflict"),
                          "observed_label": directions[0]["label"],
                          "selected_cell": directions[0],
                          "other_cells": [x for x in row["type_mark_cells"]
                                          if x["label"] != directions[0]["label"]]},
            "amount": {"status": ("confirmed_fixed_image" if checks["amount"]
                                   else "ambiguous_visual_conflict"),
                       "observed_band": amounts[0]["label"],
                       "selected_cell": amounts[0],
                       "other_cells": [x for x in row["amount_mark_cells"]
                                       if x["label"] != amounts[0]["label"]]},
            "asset": {"status": asset_status, "ocr": asset,
                      "previous_full_page_ocr": row["asset_ocr_raw"]},
            "date": {"status": date_status, "ocr": date,
                     "normalized_date_if_legible": normalized_date,
                     "previous_full_page_ocr": row["date_ocr_raw"]},
            "visual_review": {"expected": review, "field_matches": checks},
            "row_field_status": ("four_fields_confirmed_candidate_quarantined"
                                 if all(checks.values()) else "field_conflict_quarantined"),
            "projection_status": "quarantined_no_candidate",
            "candidate_transaction_id": None,
        })
    if len(result_rows) != 36 or len({(x["page_number"], x["grid_slot"])
                                      for x in result_rows}) != 36:
        raise ValueError("fixed Senate first-report transaction sample changed")
    return {
        "schema_version": "senate-paper-first-report-field-observations/v1",
        "document_id": DOCUMENT_ID,
        "evidence_commit": EVIDENCE_COMMIT,
        "row_audit_sha256": ROWS_SHA256,
        "visual_review_sha256": VISUAL_REVIEW_SHA256,
        "page_manifest_sha256": source["manifest_sha256"],
        "ocr_engine": {"name": "rapidocr_onnxruntime",
                       "version": importlib.metadata.version("rapidocr-onnxruntime")},
        "interpretation": "four row fields visually cross-checked; report identity and revision context remain unresolved and no candidate is projected",
        "rows": result_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--visual-review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.repo, args.rows, args.visual_review)
    args.output.write_text(json.dumps(result, sort_keys=True, ensure_ascii=False,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(result["rows"]),
                      "asset_confirmed": sum(x["asset"]["status"] == "confirmed_fixed_image"
                                              for x in result["rows"]),
                      "date_confirmed": sum(x["date"]["status"] == "confirmed_fixed_image"
                                             for x in result["rows"])}, sort_keys=True))


if __name__ == "__main__":
    main()
