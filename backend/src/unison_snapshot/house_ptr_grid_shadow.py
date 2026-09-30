"""Opt-in, audit-only physical row census for scanned House PTR tables.

The caller supplies a source-bound table region for each page after inspecting
the official image. Grid and asset ink are measured again from PDF bytes; no
new row is eligible for production qualification through this module.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

import pdfplumber

from .house import HouseIndexError
from .house_ptr_coverage import assess_report_coverage


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise HouseIndexError(message)


def _grid_rows(image, region: dict) -> list[tuple[float, float, float]]:
    """Return (upper, lower, asset-ink density) in normalized page coordinates."""
    gray = image.convert("L")
    width, height = gray.size
    x0, x1 = int(.07 * width), int(.90 * width)
    covered = []
    for y in range(height):
        dark = sum(gray.crop((x0, y, x1, y + 1)).histogram()[:100])
        if dark / (x1 - x0) > .50:
            covered.append(y)
    clusters: list[list[int]] = []
    for y in covered:
        if not clusters or y > clusters[-1][-1] + 1:
            clusters.append([y])
        else:
            clusters[-1].append(y)
    top, bottom = region["table_top"], region["table_bottom"]
    bounds = [(sum(group) / len(group)) / height for group in clusters
              if top - .003 <= (sum(group) / len(group)) / height <= bottom + .003]
    _require(len(bounds) >= 2 and abs(bounds[0] - top) <= .003
             and abs(bounds[-1] - bottom) <= .003,
             "House PTR physical table grid does not match the inspected region")
    rows = []
    for upper, lower in zip(bounds, bounds[1:]):
        _require(lower - upper >= .015, "House PTR physical table has an ambiguous row band")
        crop = gray.crop((int(region["asset_x0"] * width), int((upper + .004) * height),
                          int(region["asset_x1"] * width), int((lower - .004) * height)))
        _require(crop.width > 0 and crop.height > 0, "House PTR physical asset region is invalid")
        ink = sum(crop.histogram()[:100]) / (crop.width * crop.height)
        _require(ink >= .04, "House PTR physical row has no independently visible asset ink")
        rows.append((upper, lower, ink))
    return rows


def build_grid_shadow(pdf_bytes: bytes, extraction: dict, qualification: dict,
                      spec: dict) -> dict:
    """Reconcile a fixed PDF grid with stored extraction, preserving candidates."""
    _require(spec.get("schema_version") == "house-ptr-grid-sample/v1",
             "House PTR grid sample schema is invalid")
    source_sha = hashlib.sha256(pdf_bytes).hexdigest()
    _require(spec.get("source_sha256") == source_sha
             and extraction.get("source_sha256") == source_sha
             and qualification.get("source_sha256") == source_sha,
             "House PTR grid source hash mismatch")
    document_id = spec.get("document_id")
    _require(isinstance(document_id, str) and document_id
             and (extraction.get("source") or {}).get("document_id") == document_id
             and qualification.get("document_id") == document_id,
             "House PTR grid document identity mismatch")
    regions = spec.get("page_regions")
    _require(isinstance(regions, list) and regions,
             "House PTR grid requires inspected page regions")
    for region in regions:
        _require(isinstance(region, dict) and all(type(region.get(key)) in (int, float)
                 for key in ("table_top", "table_bottom", "asset_x0", "asset_x1"))
                 and 0 < region["table_top"] < region["table_bottom"] < 1
                 and 0 < region["asset_x0"] < region["asset_x1"] < 1,
                 "House PTR grid page region is invalid")
    extracted = extraction.get("transactions")
    qualified = qualification.get("transactions")
    quarantined = qualification.get("quarantined")
    _require(isinstance(extracted, list) and isinstance(qualified, list)
             and isinstance(quarantined, list), "House PTR grid requires stored row dispositions")
    qualified_ids = {row.get("id") for row in qualified if isinstance(row, dict)}
    quarantined_ids = {row.get("extraction_id") for row in quarantined if isinstance(row, dict)}
    extracted_ids = {row.get("extraction_id") for row in extracted if isinstance(row, dict)}
    _require(len(extracted_ids) == len(extracted) and len(qualified_ids) == len(qualified)
             and len(quarantined_ids) == len(quarantined)
             and extracted_ids == qualified_ids | quarantined_ids
             and not qualified_ids & quarantined_ids,
             "House PTR grid stored row dispositions do not conserve extraction")
    physical_rows = []
    page_heights = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        _require(len(pdf.pages) == len(regions), "House PTR grid page count mismatch")
        for page_number, (page, region) in enumerate(zip(pdf.pages, regions), 1):
            page_heights.append(float(page.height))
            image = page.to_image(resolution=150).original
            for row_number, (upper, lower, ink) in enumerate(_grid_rows(image, region), 1):
                physical_rows.append({
                    "locator": f"house-ptr-grid:{source_sha[:16]}:p{page_number}:r{row_number}",
                    "page": page_number, "row": row_number,
                    "bbox_points": [round(.07 * page.width, 2), round(upper * page.height, 2),
                                    round(.90 * page.width, 2), round(lower * page.height, 2)],
                    "asset_ink_fraction": round(ink, 4),
                    "disposition": "quarantined_shadow_unrecognized",
                    "reason": "stored_extraction_missing_physical_row",
                    "stored_extraction_id": None,
                })
    mapped: set[str] = set()
    for raw in extracted:
        evidence = raw.get("evidence") or {}
        bbox = evidence.get("bbox_points")
        page_number = evidence.get("page")
        _require(type(page_number) is int and 1 <= page_number <= len(regions)
                 and isinstance(bbox, list) and len(bbox) == 4,
                 "House PTR stored row has no mappable page evidence")
        page_height = page_heights[page_number - 1]
        center = (float(bbox[1]) + float(bbox[3])) / (2 * page_height)
        options = [item for item in physical_rows if item["page"] == page_number
                   and item["bbox_points"][1] / page_height <= center
                   < item["bbox_points"][3] / page_height]
        _require(len(options) == 1 and options[0]["stored_extraction_id"] is None,
                 "House PTR stored row cannot map uniquely to a physical grid row")
        item = options[0]
        item["stored_extraction_id"] = raw["extraction_id"]
        item["disposition"] = ("qualified_existing" if raw["extraction_id"] in qualified_ids
                               else "quarantined_existing")
        item["reason"] = None
        mapped.add(raw["extraction_id"])
    _require(mapped == extracted_ids, "House PTR physical row mapping lost stored extraction")
    page_counts = [sum(item["page"] == page for item in physical_rows)
                   for page in range(1, len(regions) + 1)]
    coverage = assess_report_coverage(qualification, source_sha256=source_sha,
                                      observed_page_minimum_rows=page_counts)
    shadow_quarantined = sum(item["disposition"] == "quarantined_shadow_unrecognized"
                             for item in physical_rows)
    _require(len(physical_rows) == len(extracted) + shadow_quarantined
             and coverage["unhandled_row_lower_bound"] == shadow_quarantined,
             "House PTR physical row dispositions do not conserve")
    return {"schema_version": "house-ptr-grid-shadow/v1", "document_id": document_id,
            "source_sha256": source_sha, "render_resolution_dpi": 150,
            "grid_rule": "dark_pixels_lt_100_across_x_7_to_90_percent_gt_50_percent",
            "row_count_status": "observed_grid_lower_bound",
            "coverage": coverage,
            "counts": {"observed_physical_rows": len(physical_rows),
                       "stored_extraction_rows": len(extracted),
                       "existing_qualified_rows": len(qualified),
                       "existing_quarantined_rows": len(quarantined),
                       "shadow_quarantined_unrecognized_rows": shadow_quarantined},
            "physical_rows": physical_rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--extraction", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists")
    result = build_grid_shadow(args.pdf.read_bytes(),
                               json.loads(args.extraction.read_text(encoding="utf-8")),
                               json.loads(args.qualification.read_text(encoding="utf-8")),
                               json.loads(args.spec.read_text(encoding="utf-8")))
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"document_id": result["document_id"], **result["counts"]}))


if __name__ == "__main__":
    main()
