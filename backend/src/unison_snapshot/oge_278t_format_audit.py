"""Read-only page accounting for OGE and White House 278-T reports.

The page observer is independent of the transaction parser.  In particular,
an equal count of table slots and parsed rows does not prove row identity or
authorize a transaction.  Unknown page coverage remains explicit.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .oge_278t_page_shadow import inspect_pdf
from .oge_278t_raster_slots import census_with_2024_fallback
from .oge_reports import _extract_pdf


SCHEMA = "oge-278t-format-audit/v1"
_NUMBER = re.compile(r"[1-9][0-9]{0,4}")
_TABLE_COVERAGE = {"table_header_found", "table_geometry_candidate"}


def _candidate_source_rows(extraction: dict) -> tuple[list[dict], str]:
    source_rows = extraction.get("source_rows")
    if isinstance(source_rows, list):
        return ([row for row in source_rows if isinstance(row, dict) and
                 isinstance(row.get("cells"), list) and
                 row["cells"] and
                 _NUMBER.fullmatch(str(row["cells"][0]).strip())],
                "source_rows_numbered")
    return ([row for row in _parsed_rows(extraction)
             if isinstance(row, dict)], "parsed_transactions_and_quarantine")


def _parsed_rows(extraction: dict) -> list[dict]:
    transactions = extraction.get("transactions", [])
    quarantined = extraction.get("quarantined", [])
    if not isinstance(transactions, list) or not isinstance(quarantined, list):
        raise ValueError("278-T extraction rows are invalid")
    return [row for row in [*transactions, *quarantined]
            if isinstance(row, dict)]


def audit_278t_format(pdf_path: Path, *, extraction: dict | None = None,
                      page_numbers: list[int] | None = None,
                      expected_sha256: str | None = None,
                      raster_census: bool = False) -> dict:
    """Compare selected archived pages to an existing extraction or raw probe.

    The raw probe calls the current parser's table selector for diagnosis only.
    It does not qualify rows or write to the production candidate pipeline.
    """
    pdf_path = Path(pdf_path)
    source_sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    if expected_sha256 is not None and expected_sha256 != source_sha256:
        raise ValueError("278-T PDF differs from expected source SHA-256")
    if extraction is not None:
        if not isinstance(extraction, dict):
            raise ValueError("278-T extraction must be a JSON object")
        if extraction.get("source_sha256") != source_sha256:
            raise ValueError("278-T extraction differs from archived PDF")
        rows, row_basis = _candidate_source_rows(extraction)
        parser_rows = _parsed_rows(extraction)
        extraction_parser_version = extraction.get("parser_version")
    else:
        _, selected, _ = _extract_pdf(pdf_path, include_inventory=True)
        rows = [{"page_number": page, "cells": cells}
                for page, cells in selected if cells and
                _NUMBER.fullmatch(str(cells[0] or "").strip())]
        row_basis = "current_raw_table_selector_probe"
        parser_rows = []
        extraction_parser_version = None
    shadow = inspect_pdf(pdf_path, page_numbers=page_numbers)
    if len({page["page_number"] for page in shadow["pages"]}) != len(shadow["pages"]):
        raise ValueError("278-T selected pages contain duplicates")
    selected_pages = {page["page_number"] for page in shadow["pages"]}
    raster_pages = [page["page_number"] for page in shadow["pages"]
                    if page["text_mode"].startswith("raster")]
    census, column_edges = (census_with_2024_fallback(
        pdf_path, expected_sha256=source_sha256,
        page_numbers=raster_pages)
        if raster_census and raster_pages else ({"results": []}, {}))
    census_by_page = {page["page_number"]: page for page in census["results"]}
    if any(type(row.get("page_number")) is not int or
           not 1 <= row["page_number"] <= shadow["page_count"]
           for row in [*rows, *parser_rows]):
        raise ValueError("278-T extraction contains an invalid page number")
    pages = []
    for page in shadow["pages"]:
        number = page["page_number"]
        observed = [row for row in rows if row["page_number"] == number]
        parsed = [row for row in parser_rows if row["page_number"] == number]
        census_page = census_by_page.get(number)
        geometry = (len(page["candidate_row_bands"])
                    if page["row_basis"] == "header_bounded_grid_slots" else None)
        physical = (len(census_page["physical_row_bands"])
                    if census_page and census_page["status"] ==
                    "physical_slots_located" else None)
        issues = []
        if page["coverage"] in _TABLE_COVERAGE and not observed:
            issues.append("table_page_without_selected_rows")
        if geometry is not None and geometry != len(observed):
            issues.append("geometry_slot_and_selected_row_count_differ")
        if physical is not None and physical != len(observed):
            issues.append("physical_slot_and_selected_row_count_differ")
        if page["unresolved_row_bands"] or page["unresolved_row_regions"]:
            issues.append("unresolved_table_region")
        if page["number_sequence_warnings"]:
            issues.append("printed_number_sequence_warning")
        if census_page and census_page["status"] == "coverage_unknown":
            issues.append("raster_physical_coverage_unknown")
        if (page["coverage"].startswith("coverage_unknown") or
                page["row_coverage"] in {"unknown_grid", "anchor_only",
                                          "unverified_rule_intervals"}):
            issues.append("page_or_row_coverage_unverified")
        pages.append({**page, "selected_row_count": len(observed),
                      "parsed_row_count": len(parsed) if extraction is not None else None,
                      "geometry_slot_count": geometry,
                      "physical_slot_count": physical,
                      "raster_census_status": (census_page["status"]
                                               if census_page else None),
                      "raster_row_detection_basis": (
                          census_page.get("row_detection_basis", "three_strip_grid")
                          if census_page and census_page["status"] ==
                          "physical_slots_located" else None),
                      "raster_column_geometry": (
                          "oge_2024_six_column_grid" if column_edges.get(number)
                          else "unknown" if number in column_edges else None),
                      "raster_column_geometry_observation": (
                          census_page.get("column_geometry_observation") if census_page else None),
                      "count_only_match": (physical == len(observed)
                                           if physical is not None else None),
                      "geometry_count_match": (geometry == len(observed)
                                               if geometry is not None else None),
                      "issues": issues})
    return {"schema_version": SCHEMA,
            "source_sha256": source_sha256,
            "page_count": shadow["page_count"],
            "selected_page_numbers": sorted(selected_pages),
            "row_basis": row_basis,
            "raster_census_requested": raster_census,
            "extraction_parser_version": extraction_parser_version,
            "selected_row_count": sum(page["selected_row_count"] for page in pages),
            "unselected_page_row_count": sum(row["page_number"] not in selected_pages
                                             for row in rows),
            "confirmed_physical_slot_count": sum(
                page["physical_slot_count"] or 0 for page in pages),
            "pages_with_confirmed_physical_slots": sum(
                page["physical_slot_count"] is not None for page in pages),
            "pages_with_unknown_raster_coverage": [
                page["page_number"] for page in pages
                if page["raster_census_status"] == "coverage_unknown"],
            "pages_with_issues": sum(bool(page["issues"]) for page in pages),
            "pages": pages,
            "production_mutation_authorized": False}
