"""Relate House paper transaction slots to preceding printed section headings.

The relation is structural source context only. A heading is never an asset
transaction, and its title is not a substitute for row ownership or identity.
"""

from __future__ import annotations

import re


def _heading_candidate(row: dict) -> bool:
    if row.get("disposition") != "unknown":
        return False
    cells = row.get("cells")
    if not isinstance(cells, dict):
        return False
    asset = cells.get("asset") or {}
    title = asset.get("raw_text")
    if not isinstance(title, str) or len(title.strip()) < 5:
        return False
    for name in ("event_date", "notification_date"):
        raw = (cells.get(name) or {}).get("raw_text")
        if not isinstance(raw, str) or re.search(r"\d", raw):
            return False
    for name in ("direction", "amount"):
        marks = cells.get(name)
        if not isinstance(marks, list) or not marks:
            return False
        if any(not isinstance(mark.get("interior_dark_fraction"), (int, float))
               or mark["interior_dark_fraction"] > .025 for mark in marks):
            return False
    return True


def relate_house_ptr_sections(observation: dict) -> dict:
    """Account for every physical band and carry section context across pages."""
    if observation.get("schema_version") != "house-ptr-cell-observation-shadow/v3":
        raise ValueError("House paper section context requires owner-aware cells")
    if observation.get("qualification_status") != "unverified_shadow_only":
        raise ValueError("House paper section context requires unverified observations")
    source_sha = observation.get("source_sha256")
    if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha):
        raise ValueError("House paper section source hash is invalid")
    physical = []
    seen = set()
    for expected_page, page in enumerate(observation["pages"], 1):
        if page.get("page") != expected_page:
            raise ValueError("House paper section page order is invalid")
        for expected_band, row in enumerate(page["rows"], 1):
            if row.get("physical_band_index") != expected_band:
                raise ValueError("House paper section band order is invalid")
            key = (expected_page, expected_band)
            if key in seen:
                raise ValueError("House paper section slot is duplicated")
            seen.add(key)
            physical.append((key, row))
    sections = []
    data_rows = []
    unknown_rows = []
    legend_rows = []
    active = None
    previous_page = None
    unread_pages = {page["page"] for page in observation["pages"]
                    if page.get("region_status") == "coverage_unknown" or
                       page.get("cell_status") == "unobserved_not_selected"}
    for index, (key, row) in enumerate(physical):
        if previous_page is not None and any(
                number in unread_pages for number in range(previous_page + 1, key[0] + 1)):
            active = None
        previous_page = key[0]
        disposition = row.get("disposition")
        next_is_data = (index + 1 < len(physical)
                        and physical[index + 1][1].get("disposition") == "data_candidate")
        if _heading_candidate(row) and next_is_data:
            title_cell = row["cells"]["asset"]
            active = {"section_key": f"paper-section:{source_sha}:{key[0]}:{key[1]}",
                      "page": key[0], "physical_band_index": key[1],
                      "section_title_raw": " ".join(title_cell["raw_text"].split()),
                      "title_crop_sha256": title_cell.get("crop_sha256"),
                      "status": "section_heading_candidate_unverified"}
            sections.append(active)
        elif disposition == "data_candidate":
            data_rows.append({"page": key[0], "physical_band_index": key[1],
                              "section_key": active["section_key"] if active else None,
                              "section_title_raw": active["section_title_raw"] if active else None,
                              "status": "section_context_observed_unverified" if active
                              else "section_context_unresolved"})
        elif disposition == "unknown":
            unknown_rows.append({"page": key[0], "physical_band_index": key[1],
                                 "reason": "not_a_strict_section_heading"})
            active = None
        elif disposition == "blank":
            active = None
        elif disposition == "title_legend":
            legend_rows.append({"page": key[0], "physical_band_index": key[1],
                                "reason": "printed_table_legend_not_transaction"})
        else:
            raise ValueError("House paper section disposition is invalid")
    result = {"schema_version": "house-ptr-section-context/v1",
              "source_sha256": source_sha,
              "qualification_status": "unverified_shadow_only",
              "physical_slot_count": len(physical),
              "section_heading_count": len(sections),
              "data_candidate_count": len(data_rows),
              "unclassified_unknown_count": len(unknown_rows),
              "sections": sections, "data_rows": data_rows,
              "unclassified_unknown_rows": unknown_rows}
    if legend_rows:
        result["printed_legend_count"] = len(legend_rows)
        result["printed_legend_rows"] = legend_rows
    return result
