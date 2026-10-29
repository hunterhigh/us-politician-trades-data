"""Opt-in Date observations from replayed PDF glyph interpretations."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import Path

SOURCE_GLYPH_MODE = "source_pdf_glyph_dates/v1"


def attach_source_glyph_dates(result: dict, originals: list[dict], *, source_pdf_path) -> None:
    """Append evidence, never overwrite primary raw/shape or erase warnings.

    Visibility is independently rechecked against the original page objects.
    Its baseline assignment is NOT used to relax the full Date quad condition.
    The normal relation consumer still compares every recognized date value.
    """
    from .oge_278t_fields import _date_shape, _known_value
    from .oge_278t_raster_slots import box_in_slot, slot_quad, column_geometry_sha256
    from .pdf_source_glyphs import observe_source_glyph_interpretations
    from .pdf_text_baselines import source_text_baseline_bindings

    if source_pdf_path is None or not Path(source_pdf_path).is_file():
        raise ValueError("source glyph mode requires a controlled original PDF path")
    source = Path(source_pdf_path)
    sha = result["source_sha256"]
    if hashlib.sha256(source.read_bytes()).hexdigest() != sha:
        raise ValueError("source glyph PDF differs from field observation source")
    source_pages = {p["page_number"]: p for item in originals for p in item["pages"]}
    inputs, counts, rejections = [], Counter(), []
    for page in result["pages"]:
        if page["status"] != "field_shapes_observed": continue
        original = source_pages[page["page_number"]]
        if original.get("geometry_mode") != "source_pdf_dual_axis/v1":
            raise ValueError("source glyph dates require confirmed dual-axis Date geometry")
        observed = observe_source_glyph_interpretations(source, expected_sha256=sha,
                                                       page_number=page["page_number"])
        if observed.get("source_sha256") != sha or observed.get("page_number") != page["page_number"]:
            raise ValueError("replayed glyph source differs from observed page")
        input_index = len(inputs); inputs.append(observed)
        candidates = []
        for index, record in enumerate(observed["observations"]):
            counts["source_glyph_records"] += 1
            shape = _date_shape(record.get("interpreted_text") or "")
            if record.get("status") != "interpreted" or shape.get("value") is None:
                counts["unsupported_or_fragment_records"] += 1; continue
            if record.get("allowed_field_roles") != ["transaction_date"]:
                raise ValueError("source glyph record is not authorized for Date role")
            matches = []
            for slot in original["slots"]:
                indices = slot["column_observation"]["transaction_date"].get("word_indices", [])
                for word_index in indices:
                    word = slot["words"][word_index]
                    if word["text"] == record["raw_text"] and word["bbox_pdf_points"] == record["bbox_displayed_mediabox"]:
                        matches.append((slot, word_index, word))
            if len(matches) != 1:
                rejections.append({"page_number": page["page_number"], "observation_index": index,
                                   "reason": "source_Date_word_not_uniquely_exact_raw_and_box_bound"})
                continue
            slot, word_index, word = matches[0]
            if not box_in_slot(original, slot, word["bbox_pdf_points"], 3, 4):
                rejections.append({"page_number": page["page_number"], "observation_index": index,
                                   "reason": "source_Date_word_not_fully_inside_actual_field_quad"})
                continue
            candidates.append((index, record, shape, slot, word_index, word))
        query_words = [{"text": record["raw_text"], **dict(zip(("x0", "top", "x1", "bottom"),
                       record["bbox_displayed_mediabox"]))} for _, record, _, _, _, _ in candidates]
        visibility = (source_text_baseline_bindings(source, expected_sha256=sha,
                         page_number=page["page_number"], page=original, words=query_words)
                      if candidates else {"bindings": {}})
        for query_index, (index, record, shape, source_slot, word_index, word) in enumerate(candidates):
            binding = visibility["bindings"].get(str(query_index))
            if (not binding or binding.get("source_object_index") != record["source_object_index"] or
                    binding.get("slot_index") != source_slot["slot_index"] or binding.get("column_index") != 3 or
                    binding.get("source_object_manifest_sha256") != observed["source_text_manifest_sha256"] or
                    binding.get("matrix_pdf_bottom_left") != record["matrix_pdf_bottom_left"]):
                rejections.append({"page_number": page["page_number"], "observation_index": index,
                                   "reason": "source_glyph_visibility_not_proved"})
                continue
            target = next(s for s in page["slots"] if s["slot_index"] == source_slot["slot_index"])
            evidence = target["field_readings"]["transaction_date"]
            source_ref = f"source_glyph_inputs/{input_index}/observations/{index}"
            evidence["observations"].append({
                "method": "source_glyph_interpretation", "raw_ocr_text": record["raw_text"],
                "interpreted_text": record["interpreted_text"],
                "shape": {**shape, "status": "source_glyph_shape_only"},
                "geometry_status": "source_bound", "geometry_issues": [],
                "source_ref": source_ref, "engine": "source_CID_embedded_glyph_geometry/v1",
                "confidence_basis": "not_applicable_not_ocr", "word_indices": [word_index],
                "source_glyph_binding": {"schema_version": "oge-278t-source-glyph-Date-binding/v1",
                    "source_sha256": sha, "page_number": page["page_number"],
                    "slot_index": source_slot["slot_index"], "source_object_index": record["source_object_index"],
                    "raw_text": record["raw_text"], "bbox_pdf_points": deepcopy(record["bbox_displayed_mediabox"]),
                    "field_quad_pdf_points": slot_quad(original, source_slot, 3, 4),
                    "column_geometry_sha256": column_geometry_sha256(original),
                    "visibility_evidence": deepcopy(binding)},
                "glyph_interpretation": deepcopy(record)})
            reads = evidence["observations"]
            recognized = [_known_value(r["shape"]) for r in reads
                          if r.get("geometry_status") != "ambiguous" and _known_value(r["shape"]) is not None]
            evidence.update(consistency="conflicting_observations" if len({repr(v) for v in recognized}) > 1 else
                            "matching_observations" if len(recognized) > 1 else "single_recognized_observation",
                            observation_count=len(reads), recognized_observation_count=sum(_known_value(r["shape"]) is not None for r in reads),
                            comparable_observation_count=len(recognized),
                            geometry_ambiguous_observation_count=sum(r.get("geometry_status") == "ambiguous" for r in reads))
            counts["appended_complete_Date_observations"] += 1
    counts["rejected_complete_Date_observations"] = len(rejections)
    result.update(source_glyph_mode=SOURCE_GLYPH_MODE, source_glyph_inputs=inputs,
                  source_glyph_field_summary=dict(sorted(counts.items())), source_glyph_rejections=rejections,
                  field_evidence_schema_version="oge-278t-linked-field-evidence/v2-source-glyph")
