"""Source-bound, read-only comparisons of House paper PTR row shapes.

An exact match suggests a possible repeat, while no match never establishes
that an unobserved page or an OCR-distorted row contains no repeat. This audit
does not decide filing amendments, deduplicate rows, or qualify transactions.
"""

from __future__ import annotations

from collections import defaultdict

from .house_ptr_key_fields_shadow import read_house_ptr_key_fields
from .house_ptr_paper_temporal import validate_paper_source_metadata


def _bound_rows(observation: dict, metadata: dict) -> tuple[list[dict], int]:
    document_id, source_sha, _, _ = validate_paper_source_metadata(metadata)
    if (observation.get("source_sha256") != source_sha or
            observation.get("qualification_status") != "unverified_shadow_only"):
        raise ValueError(f"House paper {document_id} observation source mismatch")
    rows = read_house_ptr_key_fields(observation)["rows"]
    unknown_pages = sum(page.get("cell_status") not in
                        (None, "raw_observed_unverified")
                        for page in observation["pages"])
    return rows, unknown_pages


def _signature(row: dict) -> tuple[str, str, str, str, str] | None:
    fields = row.get("fields")
    if not isinstance(fields, dict):
        return None
    values = (fields.get("owner_code"), fields.get("asset_raw"),
              fields.get("event_date"), fields.get("direction_mark"),
              fields.get("amount_letter"))
    if not all(isinstance(value, str) and value.strip() for value in values):
        return None
    return values


def _indexed(rows: list[dict]) -> dict[tuple[str, ...], list[dict]]:
    result: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for row in rows:
        signature = _signature(row)
        if signature is not None:
            result[signature].append({"page": row["page"],
                                      "physical_band_index":
                                      row["physical_band_index"]})
    return result


def observe_house_ptr_peer_signatures(target_observation: dict,
                                      target_metadata: dict,
                                      peers: list[tuple[dict, dict]]) -> dict:
    """Compare only complete literal OCR shapes, retaining coverage gaps."""
    target_id, target_sha, filer, district = validate_paper_source_metadata(
        target_metadata)
    target_rows, target_unknown_pages = _bound_rows(
        target_observation, target_metadata)
    target_index = _indexed(target_rows)
    target_count = sum(map(len, target_index.values()))
    seen = {target_id}
    peer_results = []
    for metadata, observation in peers:
        peer_id, peer_sha, peer_filer, peer_district = (
            validate_paper_source_metadata(metadata))
        if (peer_id in seen or peer_filer != filer or
                peer_district != district):
            raise ValueError("House paper peer is duplicate or another filer")
        seen.add(peer_id)
        peer_rows, unknown_pages = _bound_rows(observation, metadata)
        peer_index = _indexed(peer_rows)
        overlap = []
        for signature in sorted(target_index.keys() & peer_index.keys()):
            overlap.append({"signature_shape": list(signature),
                            "target_slots": target_index[signature],
                            "peer_slots": peer_index[signature]})
        peer_results.append({
            "document_id": peer_id, "source_sha256": peer_sha,
            "filed_date": metadata["filed_date"],
            "data_candidate_count": len(peer_rows),
            "complete_signature_shape_count": sum(map(len, peer_index.values())),
            "incomplete_or_unobserved_data_slot_count":
                len(peer_rows) - sum(map(len, peer_index.values())),
            "unobserved_page_count": unknown_pages,
            "exact_ocr_shape_overlap_count": len(overlap),
            "exact_ocr_shape_overlaps": overlap,
            "relationship_status": "unresolved_unverified_source_rows",
        })
    internal = [{"signature_shape": list(signature), "slots": slots}
                for signature, slots in sorted(target_index.items())
                if len(slots) > 1]
    return {
        "schema_version": "house-paper-peer-signature-shadow/v1",
        "status": "read_only_unverified_ocr_shapes",
        "document_id": target_id, "source_sha256": target_sha,
        "target_data_candidate_count": len(target_rows),
        "target_complete_signature_shape_count": target_count,
        "target_incomplete_or_unobserved_data_slot_count":
            len(target_rows) - target_count,
        "target_unobserved_page_count": target_unknown_pages,
        "target_internal_repeat_shapes": internal,
        "peer_count": len(peer_results),
        "peers": sorted(peer_results, key=lambda item: item["document_id"]),
        "qualification_authorized": False,
    }
