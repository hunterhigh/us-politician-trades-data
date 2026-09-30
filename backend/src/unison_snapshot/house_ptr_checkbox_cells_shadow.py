"""Offline cell geometry for already censused House checkbox scans.

This module locates source-image table columns. It deliberately does not read
field values or qualify transactions; every cell remains unverified.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

import pdfplumber

from .house_ptr_grid_shadow import _require


def _vertical_boundaries(image, top: float, bottom: float) -> list[float]:
    gray = image.convert("L")
    width, height = gray.size
    y0, y1 = int((top + .008) * height), int((bottom - .008) * height)
    _require(y1 - y0 >= 20, "House checkbox fingerprint has no usable row band")
    hits = []
    for x in range(width):
        dark = sum(gray.crop((x, y0, x + 1, y1)).histogram()[:100])
        if dark / (y1 - y0) > .90:
            hits.append(x)
    clusters: list[list[int]] = []
    for x in hits:
        if not clusters or x > clusters[-1][-1] + 1:
            clusters.append([x])
        else:
            clusters[-1].append(x)
    return [sum(group) / (len(group) * width) for group in clusters
            if .1 < sum(group) / (len(group) * width) < .96]


def _fingerprint(boundaries: list[float]) -> dict:
    _require(len(boundaries) >= 18, "House checkbox fingerprint lacks table columns")
    run = [boundaries[-1]]
    for value in reversed(boundaries[:-1]):
        if .027 <= run[0] - value <= .037:
            run.insert(0, value)
        else:
            break
    amount_columns = len(run) - 1
    _require(amount_columns in (10, 11),
             "House checkbox amount grid does not match a supported layout")
    start = len(boundaries) - len(run)
    prefix = boundaries[:start]
    action_columns = 3 if amount_columns == 10 else 4
    _require(len(prefix) == action_columns + 4,
             "House checkbox nonamount columns do not match amount grid")
    _require(.04 < run[0] - prefix[-1] < .075,
             "House checkbox notification column is ambiguous")
    _require(.04 < prefix[-1] - prefix[-2] < .075,
             "House checkbox transaction date column is ambiguous")
    _require(.02 < prefix[1] - prefix[0] < .06
             and .14 < prefix[2] - prefix[1] < .28,
             "House checkbox owner or asset column is ambiguous")
    family = f"legacy_checkbox_{amount_columns}_amount_columns"
    return {"layout_family": family, "amount_columns": amount_columns,
            "action_columns": action_columns,
            "owner": prefix[:2], "asset": prefix[1:3],
            "action": prefix[2:3 + action_columns],
            "transaction_date": prefix[-2:],
            "notification_date": [prefix[-1], run[0]],
            "amount": run}


def _cell(row: dict, label: str, left: float, right: float,
          width: float) -> dict:
    box = row["bbox_points"]
    return {"locator": f"{row['locator']}:{label}",
            "bbox_points": [round(left * width, 2), box[1],
                            round(right * width, 2), box[3]],
            "status": "unread_unverified", "value": None}


def build_checkbox_cells_shadow(pdf_bytes: bytes, grid: dict, spec: dict) -> dict:
    source_sha = hashlib.sha256(pdf_bytes).hexdigest()
    _require(grid.get("schema_version") == "house-ptr-failed-grid-shadow/v1"
             and spec.get("schema_version") == "house-ptr-failed-grid-sample/v1"
             and grid.get("source_sha256") == source_sha == spec.get("source_sha256")
             and grid.get("document_id") == spec.get("document_id")
             and grid.get("file_status") == "failed_open",
             "House checkbox cell source or failed-grid identity mismatch")
    regions = spec.get("page_regions")
    physical = grid.get("physical_rows")
    grid_counts = grid.get("counts") or {}
    _require(isinstance(regions, list) and isinstance(physical, list),
             "House checkbox cells require fixed page census")
    _require(grid_counts.get("observed_physical_row_lower_bound") == len(physical)
             and grid_counts.get("quarantined_shadow_rows") == len(physical)
             and grid_counts.get("qualified_rows") == 0,
             "House checkbox cells require conserved quarantined grid rows")
    pages = []
    recovered = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        _require(len(pdf.pages) == len(regions), "House checkbox cell page count mismatch")
        for page_number, (page, region) in enumerate(zip(pdf.pages, regions), 1):
            page_rows = [row for row in physical if row.get("page") == page_number]
            _require(page_rows and all(row.get("disposition") ==
                                       "quarantined_shadow_failed_report" for row in page_rows),
                     "House checkbox cell rows are not all quarantined")
            _require([row.get("row") for row in page_rows] == list(range(1, len(page_rows) + 1))
                     and all(row.get("locator") ==
                             f"house-ptr-grid:{source_sha[:16]}:p{page_number}:r{index}"
                             and isinstance(row.get("bbox_points"), list)
                             and len(row["bbox_points"]) == 4
                             and 0 <= row["bbox_points"][1] < row["bbox_points"][3] <= page.height
                             for index, row in enumerate(page_rows, 1)),
                     "House checkbox cell row geometry or locator is invalid")
            image = page.to_image(resolution=150).original
            columns = _fingerprint(_vertical_boundaries(
                image, region["table_top"], region["table_bottom"]))
            _require(columns["layout_family"] == spec.get("layout_family"),
                     "House checkbox detected layout differs from inspected sample")
            pages.append({"page": page_number, "detected_layout_family": columns["layout_family"],
                          "action_columns": columns["action_columns"],
                          "amount_columns": columns["amount_columns"]})
            for row in page_rows:
                cells = {name: _cell(row, name, bounds[0], bounds[1], page.width)
                         for name, bounds in (("owner", columns["owner"]),
                                              ("asset", columns["asset"]),
                                              ("transaction_date", columns["transaction_date"]),
                                              ("notification_date", columns["notification_date"]))}
                cells["action"] = [_cell(row, f"action:{index + 1}", left, right, page.width)
                                   for index, (left, right) in enumerate(zip(
                                       columns["action"], columns["action"][1:]))]
                cells["amount"] = [_cell(row, f"amount:{index + 1}", left, right, page.width)
                                   for index, (left, right) in enumerate(zip(
                                       columns["amount"], columns["amount"][1:]))]
                recovered.append({"physical_locator": row["locator"],
                                  "disposition": "quarantined_shadow_unverified_cells",
                                  "candidate_id": None, "cells": cells})
    _require(len(recovered) == len(physical)
             and len({row["physical_locator"] for row in recovered}) == len(physical),
             "House checkbox cell ledger does not conserve physical rows")
    return {"schema_version": "house-ptr-checkbox-cells-shadow/v1",
            "document_id": grid["document_id"], "source_sha256": source_sha,
            "file_status": "failed_open", "render_resolution_dpi": 150,
            "fingerprint_rule": "vertical_dark_pixel_coverage_gt_90_percent_and_amount_pitch_2.7_to_3.7_percent",
            "field_values_status": "unread_unverified",
            "counts": {"physical_rows": len(physical), "quarantined_rows": len(recovered),
                       "qualified_rows": 0},
            "pages": pages, "rows": recovered}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--grid", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists")
    result = build_checkbox_cells_shadow(
        args.pdf.read_bytes(), json.loads(args.grid.read_text(encoding="utf-8")),
        json.loads(args.spec.read_text(encoding="utf-8")))
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"document_id": result["document_id"], **result["counts"]}))


if __name__ == "__main__":
    main()
