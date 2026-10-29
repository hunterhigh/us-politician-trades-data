"""Reconcile a legacy paper default owner with its actual physical source row.

The original legacy parser must reproduce the entire old extraction and
qualification. A saved profile supplies replay inputs, never source authority.
"""
from copy import deepcopy
import hashlib
import math


PROFILE_KEY = "source_bound_paper_owner_correction"
PROFILE_VERSION = "house-paper-owner-correction/v1"
_PROFILE_FIELDS = {"schema_version", "source_sha256", "legacy_extraction",
                   "legacy_qualification", "id_mappings"}


def _saved_pages(observation, source_sha):
    from .house_asset_line_order import MODE, SCHEMA
    from .oge_ocr_line_order import prepare_line_source
    if observation.get("asset_line_mode") != MODE:
        raise ValueError("House owner correction requires complete saved page words")
    pages = observation.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("House owner correction has no saved pages")
    result = []
    for number, page in enumerate(pages, 1):
        proof = page.get("asset_line_source")
        if (page.get("page") != number or page.get("coordinate_frame") != "original_page"
                or not isinstance(proof, dict) or proof.get("schema_version") != SCHEMA):
            raise ValueError("House owner correction lacks a complete original page")
        size, source = proof.get("page_size_pdf_points"), proof.get("source")
        if (not isinstance(size, list) or len(size) != 2 or any(
                type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in size)
                or not isinstance(source, dict) or not isinstance(source.get("words"), list)
                or prepare_line_source(source["words"], source_sha256=source_sha,
                                       page_number=number) != source):
            raise ValueError("House owner correction saved page words differ from their binding")
        result.append({"width": size[0], "height": size[1], "words": deepcopy(source["words"])})
    return result


def _physical_bindings(legacy, observation):
    from .house_ptr_fixed_source_core import paper_disclosure_id
    pages = {page["page"]: page for page in observation["pages"]}
    result, physical_ids = {}, set()
    for row in legacy["transactions"]:
        row_id, evidence = row["extraction_id"], row["evidence"]
        number, box = evidence["page"], evidence["bbox_points"]
        page = pages[number]
        height = page["asset_line_source"]["page_size_pdf_points"][1]
        if (row_id in result or not isinstance(box, list) or len(box) != 4
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in box)
                or box[0] >= box[2] or box[1] >= box[3]):
            raise ValueError("House legacy physical geometry is invalid")
        matches = [physical for physical in page["rows"]
                   if physical.get("disposition") == "data_candidate"
                   and physical["band"][0] * height <= box[1]
                   and box[3] <= physical["band"][1] * height]
        if len(matches) != 1:
            raise ValueError("House legacy row lacks a unique physical source slot")
        band = matches[0]["physical_band_index"]
        physical_id = paper_disclosure_id(legacy["source_sha256"], number, band)
        if physical_id in physical_ids:
            raise ValueError("House legacy rows repeat a physical source slot")
        physical_ids.add(physical_id)
        result[row_id] = {"legacy_id": row_id, "physical_id": physical_id,
                          "page": number, "physical_band_index": band}
    return result


def _legacy_reference(extraction, metadata):
    from .codec import encode
    digest = hashlib.sha256(encode(extraction)).hexdigest()
    return {"path": f"house_clerk/paper_legacy_extraction_references/{digest}.json",
            "source_sha256": metadata["sha256"], "document_id": metadata["document_id"],
            "canonical_extraction_sha256": digest}


def prepare_paper_owner_correction(pdf_bytes, observation, metadata, identity, *,
        amount_header=None, field_reread=None, prior_extraction=None, prior_qualification=None):
    """Return complete independently rebuilt projections, or keep the prior.

    Unsupported or incomplete historical inputs return None. Callers retain
    their complete prior qualification; they must not replace it with a fresh
    physical-ID projection merely because a source observation exists.
    """
    try:
        return _prepare(pdf_bytes, observation, metadata, identity,
            amount_header=amount_header, field_reread=field_reread,
            prior_extraction=prior_extraction, prior_qualification=prior_qualification)
    except (ValueError, KeyError, TypeError, IndexError, OSError):
        return None


def _prepare(pdf_bytes, observation, metadata, identity, *, amount_header,
             field_reread, prior_extraction, prior_qualification):
    from .house_ptr import LEGACY_PARSER_VERSION, parse_word_pages, qualify_automatic
    from .house_ptr_paper_temporal import validate_paper_source_metadata
    from .house_ptr_paper_qualification import materialize_paper_observation
    document, source_sha, _, _ = validate_paper_source_metadata(metadata)
    if (hashlib.sha256(pdf_bytes).hexdigest() != source_sha
            or observation.get("source_sha256") != source_sha
            or not isinstance(prior_extraction, dict) or not isinstance(prior_qualification, dict)
            or not prior_qualification.get("transactions") or amount_header is None or field_reread is None):
        return None
    existing_profile = prior_qualification.get(PROFILE_KEY)
    if existing_profile is not None:
        if (not isinstance(existing_profile, dict) or set(existing_profile) != _PROFILE_FIELDS
                or existing_profile.get("schema_version") != PROFILE_VERSION
                or existing_profile.get("source_sha256") != source_sha):
            return None
        legacy = existing_profile["legacy_extraction"]
        historical_q = existing_profile["legacy_qualification"]
    else:
        legacy, historical_q = prior_extraction, prior_qualification
    if (not isinstance(legacy, dict) or not isinstance(historical_q, dict)
            or legacy.get("parser_version") != LEGACY_PARSER_VERSION
            or legacy.get("extraction_method") != "tesseract_legacy_checkbox"
            or legacy.get("source_sha256") != source_sha
            or historical_q.get("source_sha256") != source_sha
            or historical_q.get("document_id") != document
            or historical_q.get("identity") != identity
            or not isinstance(legacy.get("ocr_engine"), str) or not legacy["ocr_engine"].strip()):
        return None
    pages = _saved_pages(observation, source_sha)
    replayed = parse_word_pages(metadata, source_sha, pages,
        copy_allowed=legacy.get("source_pdf_copy_allowed"), ocr_engine=legacy["ocr_engine"])
    if replayed != legacy or qualify_automatic(replayed, identity) != historical_q:
        return None
    physical = materialize_paper_observation(pdf_bytes, observation, metadata, identity,
        amount_header=amount_header, field_reread=field_reread)
    all_bindings = _physical_bindings(legacy, observation)
    physical_facts = {row["id"]: row for row in physical["transactions"]}
    if len(physical_facts) != len(physical["transactions"]):
        return None
    blank_ids = {proof.get("extraction_id") for proof in physical.get("source_bound_owner_decisions", [])
                 if proof.get("rule") == "source_bound_blank_paper_owner/v2"
                 and proof.get("source_sha256") == source_sha and proof.get("owner") == "Undisclosed"}
    replacements, mappings, corrected = {}, [], False
    for old in historical_q["transactions"]:
        binding = all_bindings[old["id"]]
        current = physical_facts.get(binding["physical_id"])
        if current is None or set(old) != set(current):
            return None
        # The legacy parser's fixed fallback remains the actual old value.
        # Fresh paper None does not grant a classification correction.
        omitted = {"id", "owner", "instrument_type"}
        if (any(old[key] != current[key] for key in old.keys() - omitted)
                or not (old["instrument_type"] == current["instrument_type"] or
                        old["instrument_type"] == "Unspecified" and current["instrument_type"] is None)):
            return None
        final = deepcopy(old)
        if old["owner"] == "Self" and current["owner"] == "Undisclosed":
            if binding["physical_id"] not in blank_ids:
                return None
            final["owner"] = "Undisclosed"
            corrected = True
        elif old["owner"] != current["owner"]:
            return None
        replacements[binding["physical_id"]] = final
        mappings.append(binding)
    if not corrected:
        return None
    mappings.sort(key=lambda value: value["legacy_id"])
    final = deepcopy(physical)
    final["transactions"] = [deepcopy(replacements.get(row["id"], row)) for row in physical["transactions"]]
    if len({row["id"] for row in final["transactions"]}) != len(final["transactions"]):
        return None
    final[PROFILE_KEY] = {"schema_version": PROFILE_VERSION, "source_sha256": source_sha,
        "legacy_extraction": deepcopy(legacy), "legacy_qualification": deepcopy(historical_q),
        "id_mappings": deepcopy(mappings)}
    final["legacy_extraction_reference"] = _legacy_reference(legacy, metadata)
    if existing_profile is not None and (final != prior_qualification
            or final["materialized_extraction"] != prior_extraction):
        return None
    return {"legacy_extraction": deepcopy(legacy), "legacy_qualification": deepcopy(historical_q),
            "physical_qualification": physical, "id_mappings": mappings,
            "final_qualification": final}


def qualification_physical_ids(qualification):
    """Translate count IDs only; independent source replay still authorizes facts."""
    rows = qualification["transactions"]
    profile = qualification.get(PROFILE_KEY)
    if profile is None:
        return {row.get("id") for row in rows}
    if (not isinstance(profile, dict) or set(profile) != _PROFILE_FIELDS
            or profile.get("schema_version") != PROFILE_VERSION
            or profile.get("source_sha256") != qualification.get("source_sha256")
            or not isinstance(profile.get("id_mappings"), list)):
        raise ValueError("House paper owner count profile is invalid")
    mapping, targets = {}, set()
    for item in profile["id_mappings"]:
        if (not isinstance(item, dict) or set(item) != {"legacy_id", "physical_id", "page", "physical_band_index"}
                or not all(isinstance(item.get(key), str) for key in ("legacy_id", "physical_id"))
                or not all(type(item.get(key)) is int and item[key] > 0 for key in ("page", "physical_band_index"))
                or item["legacy_id"] in mapping or item["physical_id"] in targets):
            raise ValueError("House paper owner count mapping is invalid")
        mapping[item["legacy_id"]] = item["physical_id"]
        targets.add(item["physical_id"])
    actual = {row.get("id") for row in rows}
    if not mapping.keys() <= actual:
        raise ValueError("House paper owner count mapping has no actual fact")
    physical = {mapping.get(row.get("id"), row.get("id")) for row in rows}
    if len(physical) != len(rows):
        raise ValueError("House paper owner count mapping repeats a physical row")
    return physical
