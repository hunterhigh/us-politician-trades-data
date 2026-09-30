"""Audit-only House PTR report coverage, separate from row qualification.

This module is intentionally not called by the candidate or publication path.
An independent page census can prove a lower bound on missing rows, but equal
counts alone cannot prove that every physical row was handled correctly.
"""

from __future__ import annotations

import re

from .house import HouseIndexError


def assess_report_coverage(qualification: dict, *, source_sha256: str,
                           observed_page_minimum_rows: list[int]) -> dict:
    """Describe known omissions while retaining every already qualified row ID."""
    if not isinstance(qualification, dict) or not isinstance(qualification.get("document_id"), str):
        raise HouseIndexError("House PTR coverage requires a qualification artifact")
    if (not re.fullmatch(r"[0-9a-f]{64}", source_sha256)
            or qualification.get("source_sha256") != source_sha256):
        raise HouseIndexError("House PTR coverage source hash does not match qualification")
    qualified = qualification.get("transactions")
    quarantined = qualification.get("quarantined")
    if not isinstance(qualified, list) or not isinstance(quarantined, list):
        raise HouseIndexError("House PTR coverage requires row dispositions")
    if (not isinstance(observed_page_minimum_rows, list) or not observed_page_minimum_rows
            or any(type(count) is not int or count < 0 for count in observed_page_minimum_rows)):
        raise HouseIndexError("House PTR coverage requires per-page nonnegative row lower bounds")
    candidate_ids = [row.get("id") for row in qualified if isinstance(row, dict)]
    if (len(candidate_ids) != len(qualified)
            or any(not isinstance(value, str) or not value for value in candidate_ids)
            or len(set(candidate_ids)) != len(candidate_ids)):
        raise HouseIndexError("House PTR coverage requires stable qualified row IDs")
    if any(not isinstance(row, dict) for row in quarantined):
        raise HouseIndexError("House PTR coverage requires valid quarantined rows")
    observed_minimum = sum(observed_page_minimum_rows)
    handled = len(qualified) + len(quarantined)
    missing_minimum = max(0, observed_minimum - handled)
    return {
        "schema_version": "house-ptr-coverage-audit/v1",
        "document_id": qualification["document_id"],
        "source_sha256": source_sha256,
        "coverage_status": "known_incomplete" if missing_minimum else "unverified",
        "basis": "independent_pdf_page_census_lower_bound",
        "observed_page_minimum_rows": list(observed_page_minimum_rows),
        "observed_minimum_row_count": observed_minimum,
        "handled_row_count": handled,
        "qualified_row_count": len(qualified),
        "quarantined_row_count": len(quarantined),
        "unhandled_row_lower_bound": missing_minimum,
        "retained_candidate_ids": sorted(candidate_ids),
    }
