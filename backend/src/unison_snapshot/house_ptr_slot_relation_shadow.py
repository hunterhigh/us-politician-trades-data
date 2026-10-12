"""Read-only relation between old House PTR extraction evidence and physical slots.

Coordinates identify possible source fragments, not qualified transactions.
Unmatched physical data candidates remain investigation targets.
"""

from __future__ import annotations

import hashlib
import io
from collections import Counter, defaultdict


def relate_house_ptr_slots(page_heights: list[float], observation: dict,
                           extraction: dict) -> dict:
    """Join original-page evidence centers to physical bands conservatively."""
    if observation.get("schema_version") not in {
            "house-ptr-cell-observation-shadow/v1",
            "house-ptr-cell-observation-shadow/v2"}:
        raise ValueError("unsupported House physical observation")
    source_sha = observation.get("source_sha256")
    if not isinstance(source_sha, str) or extraction.get("source_sha256") != source_sha:
        raise ValueError("House extraction and physical source hashes differ")
    pages = observation.get("pages")
    old_rows = extraction.get("transactions")
    if not isinstance(pages, list) or len(pages) != len(page_heights):
        raise ValueError("House page census differs from PDF")
    if not isinstance(old_rows, list):
        raise ValueError("House extraction transactions are missing")
    bands: dict[tuple[int, int], dict] = {}
    for page_number, (page, height) in enumerate(zip(pages, page_heights), 1):
        if (page.get("page") != page_number or height <= 0 or
                page.get("coordinate_frame") != "original_page"):
            raise ValueError("House physical pages need original-page coordinates")
        for index, row in enumerate(page.get("rows", []), 1):
            bounds = row.get("band")
            if (row.get("physical_band_index") != index or
                    not isinstance(bounds, list) or len(bounds) != 2 or
                    not 0 <= bounds[0] < bounds[1] <= 1):
                raise ValueError("House physical band geometry is invalid")
            bands[(page_number, index)] = row

    links: dict[tuple[int, int], set[str]] = defaultdict(set)
    old_status = []
    seen_ids = set()
    for old in old_rows:
        old_id = old.get("extraction_id")
        if not isinstance(old_id, str) or not old_id or old_id in seen_ids:
            raise ValueError("House old extraction ID is missing or repeated")
        seen_ids.add(old_id)
        evidence = old.get("evidence") or {}
        segments = evidence.get("segments") or [evidence]
        if not isinstance(segments, list):
            raise ValueError("House evidence segments are invalid")
        linked = set()
        unresolved = []
        for segment_index, segment in enumerate(segments, 1):
            page_number = segment.get("page")
            box = segment.get("bbox_points")
            if (type(page_number) is not int or not 1 <= page_number <= len(pages)
                    or not isinstance(box, list) or len(box) != 4
                    or not all(isinstance(value, (int, float)) for value in box)
                    or not 0 <= box[1] < box[3] <= page_heights[page_number - 1]):
                unresolved.append({"segment": segment_index, "reason": "invalid_evidence_box"})
                continue
            center_y = (box[1] + box[3]) / (2 * page_heights[page_number - 1])
            hits = [key for key, row in bands.items()
                    if key[0] == page_number and row["band"][0] <= center_y <= row["band"][1]]
            if len(hits) != 1:
                unresolved.append({"segment": segment_index,
                                   "reason": "no_unique_physical_band",
                                   "matching_band_count": len(hits)})
                continue
            linked.add(hits[0])
        for key in linked:
            links[key].add(old_id)
        old_status.append({"extraction_id": old_id,
                           "physical_slots": [{"page": page, "band_index": index}
                                              for page, index in sorted(linked)],
                           "unresolved_segments": unresolved})

    slots = []
    for key, row in sorted(bands.items()):
        ids = sorted(links[key])
        slots.append({"page": key[0], "band_index": key[1],
                      "band": row["band"], "disposition": row["disposition"],
                      "old_extraction_ids": ids,
                      "relation_status": "old_extraction_linked" if ids
                      else "no_old_extraction_relation"})
    counts = Counter((slot["disposition"], slot["relation_status"]) for slot in slots)
    return {"schema_version": "house-ptr-slot-relation-shadow/v1",
            "source_sha256": source_sha, "qualification_status": "unverified_shadow_only",
            "old_extraction_count": len(old_rows), "physical_slot_count": len(slots),
            "relation_counts": [{"disposition": disposition, "relation_status": status,
                                 "count": count}
                                for (disposition, status), count in sorted(counts.items())],
            "old_extractions": old_status, "slots": slots}


def relate_house_ptr_pdf(pdf_bytes: bytes, observation: dict,
                         extraction: dict) -> dict:
    """Check archived bytes before relating original-page PDF coordinates."""
    if hashlib.sha256(pdf_bytes).hexdigest() != observation.get("source_sha256"):
        raise ValueError("House physical observation source hash mismatch")
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        heights = [page.height for page in pdf.pages]
    return relate_house_ptr_slots(heights, observation, extraction)
