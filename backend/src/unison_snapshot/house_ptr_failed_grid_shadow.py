"""Opt-in physical-row ledger for source-bound, archived failed House PTR scans.

The inspected table coordinates identify a fixed sample, not a layout rule for
production retry. Every observed row remains quarantined at report level.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

import pdfplumber

from .house_ptr_grid_shadow import _grid_rows, _require


def build_failed_grid_shadow(pdf_bytes: bytes, failure: dict, spec: dict) -> dict:
    _require(spec.get("schema_version") == "house-ptr-failed-grid-sample/v1",
             "House failed grid sample schema is invalid")
    source_sha = hashlib.sha256(pdf_bytes).hexdigest()
    document_id = spec.get("document_id")
    _require(isinstance(document_id, str) and document_id
             and spec.get("source_sha256") == source_sha
             and failure.get("source_sha256") == source_sha
             and failure.get("document_id") == document_id
             and failure.get("schema_version") == "house-ptr-parse-failure/v1",
             "House failed grid source or failure identity mismatch")
    layout_family = spec.get("layout_family")
    _require(layout_family in ("legacy_checkbox_10_amount_columns",
                               "legacy_checkbox_11_amount_columns"),
             "House failed grid requires an inspected layout family")
    regions = spec.get("page_regions")
    _require(isinstance(regions, list) and regions,
             "House failed grid requires inspected page regions")
    rows = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        _require(len(pdf.pages) == len(regions), "House failed grid page count mismatch")
        for page_number, (page, region) in enumerate(zip(pdf.pages, regions), 1):
            _require(isinstance(region, dict)
                     and all(type(region.get(key)) in (int, float)
                             for key in ("table_top", "table_bottom", "asset_x0", "asset_x1"))
                     and 0 < region["table_top"] < region["table_bottom"] < 1
                     and 0 < region["asset_x0"] < region["asset_x1"] < 1,
                     "House failed grid page region is invalid")
            image = page.to_image(resolution=150).original
            for row_number, (upper, lower, ink) in enumerate(
                    _grid_rows(image, region, min_line_coverage=.55), 1):
                rows.append({
                    "locator": f"house-ptr-grid:{source_sha[:16]}:p{page_number}:r{row_number}",
                    "page": page_number, "row": row_number,
                    "bbox_points": [round(.07 * page.width, 2), round(upper * page.height, 2),
                                    round(.90 * page.width, 2), round(lower * page.height, 2)],
                    "asset_ink_fraction": round(ink, 4),
                    "disposition": "quarantined_shadow_failed_report",
                    "reason": "archived_parse_failure_no_stored_row_disposition",
                    "candidate_id": None,
                })
    page_counts = [sum(row["page"] == page for row in rows)
                   for page in range(1, len(regions) + 1)]
    _require(all(page_counts) and len(rows) == sum(page_counts)
             and len({row["locator"] for row in rows}) == len(rows),
             "House failed grid physical rows do not conserve")
    return {
        "schema_version": "house-ptr-failed-grid-shadow/v1",
        "document_id": document_id,
        "source_sha256": source_sha,
        "archived_failure_parser_version": failure.get("parser_version"),
        "archived_failure_error": failure.get("error"),
        "layout_family": layout_family,
        "layout_classification_basis": "manual_inspection_of_fixed_pdf_table_headers",
        "render_resolution_dpi": 150,
        "grid_rule": "dark_pixels_lt_100_across_x_7_to_90_percent_gt_55_percent",
        "row_count_status": "observed_grid_lower_bound",
        "file_status": "failed_open",
        "counts": {"observed_page_minimum_rows": page_counts,
                   "observed_physical_row_lower_bound": len(rows),
                   "quarantined_shadow_rows": len(rows),
                   "qualified_rows": 0},
        "physical_rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--failure", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists")
    result = build_failed_grid_shadow(
        args.pdf.read_bytes(),
        json.loads(args.failure.read_text(encoding="utf-8")),
        json.loads(args.spec.read_text(encoding="utf-8")),
    )
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"document_id": result["document_id"], **result["counts"]}))


if __name__ == "__main__":
    main()
