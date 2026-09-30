"""Close the physical grid-row census of one fixed Senate paper PTR report.

This source-bound, read-only audit records original page and cell coordinates,
pixel crop hashes, and mark evidence. It does not produce candidate trades.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path

from PIL import Image

from audit_senate_paper_pages import _asset_ink, _git_bytes
from unison_snapshot.senate_paper_sample import DOCUMENT_ID, EVIDENCE_COMMIT


INVENTORY_SHA256 = "238732d74afcdabd5e8991c3168e4e6ca185fbd02a67121ccc3b3e3c0ba4cd7d"
MANIFEST_PATH = f"senate_efd/paper_pages/{DOCUMENT_ID}/manifest.json"
TYPE_CELLS = (("purchase", 1429), ("sale", 1518), ("exchange", 1612))
AMOUNT_CELLS = (
    ("1001_15000", 1980), ("15001_50000", 2077),
    ("50001_100000", 2168), ("100001_250000", 2255),
    ("250001_500000", 2342), ("500001_1000000", 2431),
    ("over_1000000", 2528), ("1000001_5000000", 2617),
    ("5000001_25000000", 2704), ("25000001_50000000", 2792),
    ("over_50000000", 2880),
)
# Visually checked section headings on these fixed official GIFs. All other
# nonblank slots must demonstrate both marks or remain unresolved.
HEADING_SLOTS = {2: {1, 7}, 3: {1, 10}, 4: {1, 7, 15}, 5: {1, 5}}
MARK_MIN_DARK_SAMPLES = 25


def _raw_crop(image: Image.Image, box: tuple[int, int, int, int]) -> dict:
    return {"box_px": list(box),
            "grayscale_sha256": hashlib.sha256(image.crop(box).tobytes()).hexdigest()}


def _mark_cell(image: Image.Image, y: int, label: str, x: int) -> dict:
    # Stay inside each printed checkbox. The wider preview window reaches a
    # slanted vertical grid rule on the final page and creates false X marks.
    box = (x - 15, y - 15, x + 16, y + 16)
    crop = image.crop(box)
    points = crop.load()
    count = sum(points[xx, yy] < 160
                for xx in range(0, crop.width, 2)
                for yy in range(0, crop.height, 2))
    return {"label": label, "center_px": [x, y],
            **_raw_crop(image, box),
            "dark_sample_count": count,
            "mark_signal": count >= MARK_MIN_DARK_SAMPLES}


def _classify_row(page_number: int, slot: int, asset_ink: int,
                  type_marks: list[dict], amount_marks: list[dict],
                  had_inventory_signal: bool) -> str:
    types = [item for item in type_marks if item["mark_signal"]]
    amounts = [item for item in amount_marks if item["mark_signal"]]
    if slot in HEADING_SLOTS.get(page_number, set()):
        if asset_ink >= 150 and not types and not amounts:
            return "section_heading_excluded"
        return "unresolved_quarantined"
    if len(types) == 1 and len(amounts) == 1 and asset_ink >= 150:
        return "transaction_observed_quarantined"
    if not types and not amounts and asset_ink < 150:
        return "ocr_grid_noise_excluded" if had_inventory_signal else "blank_grid_slot_observed"
    return "unresolved_quarantined"


def audit(repo: Path, inventory_path: Path) -> dict:
    raw_inventory = inventory_path.read_bytes()
    if hashlib.sha256(raw_inventory).hexdigest() != INVENTORY_SHA256:
        raise ValueError("Senate paper inventory file differs from pinned audit")
    inventory = json.loads(raw_inventory)
    if inventory["evidence_commit"] != EVIDENCE_COMMIT:
        raise ValueError("Senate paper inventory evidence commit changed")
    reports = [report for report in inventory["reports"]
               if report["document_id"] == DOCUMENT_ID]
    if len(reports) != 1 or reports[0]["page_count"] != 5:
        raise ValueError("Fixed Senate paper report missing from inventory")
    report = reports[0]
    manifest = json.loads(_git_bytes(repo, MANIFEST_PATH))
    if manifest["manifest_sha256"] != report["manifest_sha256"]:
        raise ValueError("Fixed Senate paper manifest changed")
    rows = []
    for page in report["pages"]:
        number = page["page_number"]
        archived = manifest["pages"][number - 1]
        content = _git_bytes(repo, archived["archive_path"])
        if (archived["sha256"] != page["page_sha256"] or
                hashlib.sha256(content).hexdigest() != page["page_sha256"]):
            raise ValueError(f"Fixed Senate paper page {number} hash changed")
        if number == 1:
            if page["table_grid_detected"]:
                raise ValueError("Senate paper cover unexpectedly has a table")
            continue
        image = Image.open(BytesIO(content)).convert("L")
        grid = page["grid_line_centers"]
        skip = 2 if page["printed_examples_detected"] else 0
        if (not page["table_grid_detected"] or len(grid) - 1 - skip !=
                (14 if number == 2 else 17)):
            raise ValueError(f"Senate paper page {number} grid slot count changed")
        inventory_bands = {item["grid_slot"]: item for item in page["filled_grid_bands"]}
        for index, (top, bottom) in enumerate(zip(grid, grid[1:]), start=1):
            if index <= skip:
                continue
            slot = index - skip
            y = (top + bottom) // 2
            type_marks = [_mark_cell(image, y, label, x) for label, x in TYPE_CELLS]
            amount_marks = [_mark_cell(image, y, label, x) for label, x in AMOUNT_CELLS]
            ink = _asset_ink(image, top, bottom)
            band = inventory_bands.get(slot)
            disposition = _classify_row(
                number, slot, ink, type_marks, amount_marks, band is not None)
            rows.append({
                "page_number": number,
                "page_sha256": page["page_sha256"],
                "source_url": page["source_url"],
                "grid_slot": slot,
                "row_bounds_px": [top, bottom],
                "row_crop": _raw_crop(image, (525, top, 2920, bottom)),
                "asset_crop": _raw_crop(image, (590, top + 8, 1385, bottom - 8)),
                "date_crop": _raw_crop(image, (1650, top + 8, 1940, bottom - 8)),
                "asset_ink_sample_count": ink,
                "asset_ocr_raw": band["asset_ocr"] if band else "",
                "date_ocr_raw": band["date_ocr"] if band else "",
                "type_mark_cells": type_marks,
                "amount_mark_cells": amount_marks,
                "observed_type": next((item["label"] for item in type_marks
                                       if item["mark_signal"]), None),
                "observed_amount_band": next((item["label"] for item in amount_marks
                                              if item["mark_signal"]), None),
                "disposition": disposition,
            })
    counts = {key: sum(row["disposition"] == key for row in rows) for key in (
        "transaction_observed_quarantined", "section_heading_excluded",
        "blank_grid_slot_observed", "ocr_grid_noise_excluded",
        "unresolved_quarantined")}
    if len(rows) != 65 or counts != {
        "transaction_observed_quarantined": 36,
        "section_heading_excluded": 9,
        "blank_grid_slot_observed": 17,
        "ocr_grid_noise_excluded": 3,
        "unresolved_quarantined": 0,
    }:
        unresolved = [(row["page_number"], row["grid_slot"],
                       row["asset_ink_sample_count"])
                      for row in rows if row["disposition"] == "unresolved_quarantined"]
        raise ValueError(f"Fixed Senate paper row census changed: {counts}; {unresolved}")
    return {
        "schema_version": "senate-paper-fixed-report-row-audit/v1",
        "evidence_commit": EVIDENCE_COMMIT,
        "document_id": DOCUMENT_ID,
        "entrypoint_source_sha256": report["entrypoint_source_sha256"],
        "manifest_sha256": report["manifest_sha256"],
        "inventory_sha256": INVENTORY_SHA256,
        "page_count": 5,
        "table_page_count": 4,
        "example_band_count": 2,
        "physical_grid_slot_count": len(rows),
        "mark_rule": {"cell_half_width_px": 15, "cell_half_height_px": 15,
                      "sample_stride_px": 2, "darkness_below": 160,
                      "minimum_dark_samples": MARK_MIN_DARK_SAMPLES},
        "disposition_counts": counts,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.repo, args.inventory)
    args.output.write_text(json.dumps(result, sort_keys=True, ensure_ascii=False,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["disposition_counts"], sort_keys=True))


if __name__ == "__main__":
    main()
