"""Read-only printed-row-number conservation for saved 2024 OGE grids.

The page-local majority offset is an OCR consistency observation. It never
fills missing row numbers or treats a physical slot as a qualified trade.
"""

from __future__ import annotations

from collections import Counter
from math import ceil
import re


SCHEMA = "oge-278t-printed-number-shadow/v1"
_INPUT_SCHEMA = "oge-278t-raster-slot-observation/v2"
_GRID = "oge_2024_six_column_grid"
_NUMBER = re.compile(r"[1-9][0-9]*")


def observe_printed_number_sequence(observations: list[dict]) -> dict:
    """Keep page-local row-number consensus, OCR errors and unknown gaps."""
    if not observations or any(item.get("schema_version") != _INPUT_SCHEMA
                               for item in observations):
        raise ValueError("expected saved 278-T raster slot observations v2")
    sources = {item.get("source_sha256") for item in observations}
    if len(sources) != 1 or None in sources:
        raise ValueError("printed-number observations must share one PDF SHA-256")
    source = sources.pop()
    page_inputs = [page for item in observations for page in item["pages"]]
    numbers = [page["page_number"] for page in page_inputs]
    if len(numbers) != len(set(numbers)):
        raise ValueError("duplicate page in printed-number observations")
    page_inputs.sort(key=lambda page: page["page_number"])
    pages = []
    counts: Counter[str] = Counter()
    for page in page_inputs:
        number = page["page_number"]
        physical_count = page.get("physical_slot_count")
        if page.get("column_geometry") != _GRID:
            pages.append({"page_number": number, "status": "geometry_unknown",
                          "physical_slot_count": physical_count,
                          "slots": []})
            counts["geometry_unknown_pages"] += 1
            continue
        slots = page.get("slots")
        if page.get("partial_row_geometry"):
            if (physical_count is not None or not isinstance(slots,list) or
                    page.get("known_physical_slot_count") != len(slots)):
                raise ValueError("partial printed-number input has an invalid known count")
            rows=[]
            for slot in slots:
                raw=slot["column_observation"]["printed_number"]["raw_ocr_text"].strip()
                rows.append({"slot_index":slot["slot_index"],"raw_ocr_text":raw,
                    "strict_ocr_number":int(raw) if _NUMBER.fullmatch(raw) else None,
                    "consensus_expected_number":None,"status":"partial_grid_no_sequence_inference",
                    "qualification":"not_assessed"})
            pages.append({"page_number":number,"status":"partial_grid_numbers_observed",
                "physical_slot_count":None,"known_physical_slot_count":len(slots),"slots":rows})
            counts["partial_grid_pages"]+=1
            counts["physical_slots"]+=len(slots)
            continue
        if not isinstance(slots, list) or len(slots) != physical_count or \
                [slot.get("slot_index") for slot in slots] != list(range(1, len(slots) + 1)):
            raise ValueError(f"page {number} has invalid physical slots")
        raw_numbers = []
        offsets: Counter[int] = Counter()
        for slot in slots:
            columns = slot.get("column_observation")
            if not isinstance(columns, dict) or "printed_number" not in columns:
                raise ValueError(f"page {number} lacks printed-number column")
            raw = columns["printed_number"]["raw_ocr_text"].strip()
            value = int(raw) if _NUMBER.fullmatch(raw) else None
            raw_numbers.append((raw, value))
            if value is not None:
                offsets[value - slot["slot_index"]] += 1
        ranking = offsets.most_common(2)
        minimum = max(3, ceil(len(slots) * 0.60))
        consensus = (ranking[0][0] if ranking and ranking[0][1] >= minimum and
                     (len(ranking) == 1 or ranking[0][1] > ranking[1][1]) else None)
        rows = []
        for slot, (raw, value) in zip(slots, raw_numbers):
            expected = consensus + slot["slot_index"] if consensus is not None else None
            if expected is None:
                status = "page_consensus_unknown"
            elif value is None:
                status = "unreadable_number"
            elif value == expected:
                status = "matches_page_consensus"
            else:
                status = "conflicts_page_consensus"
            counts[status] += 1
            rows.append({"slot_index": slot["slot_index"],
                         "raw_ocr_text": raw, "strict_ocr_number": value,
                         "consensus_expected_number": expected,
                         "status": status, "qualification": "not_assessed"})
        pages.append({"page_number": number,
                      "status": "page_consensus_observed" if consensus is not None
                                else "page_consensus_unknown",
                      "physical_slot_count": len(slots),
                      "consensus_support": ranking[0][1] if ranking else 0,
                      "strict_number_count": sum(offsets.values()),
                      "consensus_first_number": consensus + 1 if consensus is not None else None,
                      "consensus_last_number": consensus + len(slots) if consensus is not None else None,
                      "slots": rows})
        counts["confirmed_grid_pages"] += 1
        counts["physical_slots"] += len(slots)

    gaps = []
    anchored = [page for page in pages if page["status"] == "page_consensus_observed"]
    positions = {page["page_number"]: index for index, page in enumerate(pages)}
    for before, after in zip(anchored, anchored[1:]):
        between = pages[positions[before["page_number"]] + 1:
                        positions[after["page_number"]]]
        unlisted_pages = (after["page_number"] - before["page_number"] - 1) - len(between)
        gap = after["consensus_first_number"] - before["consensus_last_number"] - 1
        known_count = sum(item["physical_slot_count"] for item in between
                          if type(item["physical_slot_count"]) is int)
        unknown_count = sum(type(item["physical_slot_count"]) is not int
                            for item in between) + unlisted_pages
        if gap < 0:
            status = "number_overlap"
        elif unknown_count:
            status = "gap_unassessable"
        elif gap == known_count:
            status = "gap_matches_intervening_slots"
        else:
            status = "gap_disagrees_with_intervening_slots"
        counts[status] += 1
        gaps.append({"before_page": before["page_number"],
                     "after_page": after["page_number"],
                     "printed_number_gap": gap,
                     "intervening_known_physical_slots": known_count,
                     "intervening_unknown_or_unlisted_pages": unknown_count,
                     "status": status})
    return {"schema_version": SCHEMA, "source_sha256": source,
            "page_count": len(pages), "summary": dict(sorted(counts.items())),
            "pages": pages, "gaps": gaps,
            "production_mutation_authorized": False}
