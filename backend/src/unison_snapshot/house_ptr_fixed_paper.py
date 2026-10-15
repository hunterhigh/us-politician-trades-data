"""Source-bound qualification and relationship closure of printed PTR 9116141."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import io
import json
from pathlib import Path

from .house_ptr import DETERMINISTIC_IDENTITY_BASES, OWNER_CODES
from .house_ptr_fixed_source_core import (
    canonical_json as _canonical,
    canonical_sha256 as _sha,
    review_slot as _slot,
    disclosure_transaction as _fixed_transaction,
    check_source_cells as _check_source_cells,
)
from .house_ptr_amount_header import evaluate_amount_header_pages
from .house_ptr_paper_qualification import (_pending_transaction_projection,
                                            qualify_paper_observation)
from .house_ptr_paper_temporal import validate_paper_source_metadata


DOCUMENT_ID = "9116141"
SOURCE_SHA256 = "716cdcc10bd57c400f10d8bb4133eb667931a9699fb1835ed3b7deca010a36a1"
PRECEDING_DOCUMENT_ID = "9115820"
PRECEDING_SOURCE_SHA256 = "e1254f953327174fe80a856137e386c3dea42aa58728d350105822bfabab6860"
REVIEW_SHA256 = "4cdb554eef9890a2f85e8378cf4a64e4bcb7feadfdbc52467f9e4e58c129f0b4"
RELATION_REVIEW_SHA256 = "3d24e8ac9c77a00dba66bc34e55f65fb04964ccf334c0950536ad32b6ce7a401"


def qualify_fixed_house_paper(pdf_bytes: bytes, metadata: dict, preceding: dict,
                              observation: dict, amount_header: dict, identity: dict,
                              current: dict, review: dict) -> dict:
    document_id, source_sha, filer, district = validate_paper_source_metadata(metadata)
    peer_id, peer_sha, peer_filer, peer_district = validate_paper_source_metadata(preceding)
    if (document_id != DOCUMENT_ID or source_sha != SOURCE_SHA256 or
            hashlib.sha256(pdf_bytes).hexdigest() != SOURCE_SHA256 or
            peer_id != PRECEDING_DOCUMENT_ID or peer_sha != PRECEDING_SOURCE_SHA256 or
            peer_filer != filer or peer_district != district or
            preceding["filed_date"] != "2026-05-04" or
            metadata["filed_date"] != "2026-06-10"):
        raise ValueError("fixed House paper official source or chronology changed")
    if (review.get("schema_version") != "house-paper-fixed-source-review/v1" or
            review.get("document_id") != DOCUMENT_ID or
            review.get("source_sha256") != SOURCE_SHA256 or
            review.get("reviewed_page_count") != 6 or
            _sha(review) != REVIEW_SHA256 or
            _sha(observation) != review.get("observation_sha256")):
        raise ValueError("fixed House paper visual review is stale")
    if (identity.get("document_id") != DOCUMENT_ID or
            identity.get("person_id") != "house:M001157" or
            identity.get("match_basis") not in DETERMINISTIC_IDENTITY_BASES or
            identity.get("status") != "matched_automatically"):
        raise ValueError("fixed House paper filer identity changed")
    checked_header = evaluate_amount_header_pages(SOURCE_SHA256, amount_header["pages"])
    if (checked_header != amount_header or
            checked_header["status"] != "printed_header_correlated" or
            checked_header["printed_a_variant"] != "A1000"):
        raise ValueError("fixed House paper amount legend changed")
    expected = qualify_paper_observation(observation, metadata, identity,
                                         amount_header=amount_header)
    if (_sha(expected["quarantined"]) != review.get("qualification_raw_sha256") or
            current.get("quarantined") != expected["quarantined"] or
            current.get("transactions") != [] or
            current.get("source_sha256") != SOURCE_SHA256 or
            current.get("identity") != identity or
            current.get("document_disposition") != expected["document_disposition"] or
            current.get("pending_transaction_projection") !=
            expected["pending_transaction_projection"]):
        raise ValueError("fixed House paper pending qualification changed")
    slots = _check_source_cells(pdf_bytes, observation, page_count=6)
    if (len(slots) != 139 or
            expected["document_disposition"]["physical_counts"] !=
            {"data_candidate": 134, "unknown": 4, "blank": 1} or
            len(expected["structural_observations"]) != 4 or
            review.get("physical_counts") !=
            {"data_candidate": 134, "section_heading": 4, "blank": 1}):
        raise ValueError("fixed House paper physical row census changed")
    corrections = {_slot(item): item for item in review["asset_corrections"]}
    date_corrections = {_slot(item): item for item in review["date_corrections"]}
    headings = {_slot(item): item for item in review["section_headings"]}
    if (len(corrections) != len(review["asset_corrections"]) or
            len(date_corrections) != 3 or len(headings) != 4):
        raise ValueError("fixed House paper review slots are duplicated")
    for key, item in corrections.items():
        if (slots[key]["disposition"] != "data_candidate" or
                slots[key]["cells"]["asset"]["crop_sha256"] !=
                item.get("asset_crop_sha256") or
                not isinstance(item.get("asset_name"), str) or
                not item["asset_name"].strip()):
            raise ValueError("fixed House paper asset correction is unbound")
    for key, item in date_corrections.items():
        if (slots[key]["disposition"] != "data_candidate" or
                slots[key]["cells"]["event_date"]["crop_sha256"] !=
                item.get("event_crop_sha256") or
                item.get("event_date") not in
                {"2026-05-01", "2026-05-07", "2026-05-22"}):
            raise ValueError("fixed House paper date correction is unbound")
    title_by_section = {}
    for key, item in headings.items():
        slot = slots[key]
        if (slot["disposition"] != "unknown" or
                slot["cells"]["asset"]["crop_sha256"] !=
                item.get("asset_crop_sha256") or
                not isinstance(item.get("title"), str) or not item["title"].strip()):
            raise ValueError("fixed House paper account title is unbound")
        title_by_section[f"paper-section:{SOURCE_SHA256}:{key[0]}:{key[1]}"] = item["title"]
    transactions = []
    pending = []
    seen_ids = set()
    for row in expected["quarantined"]:
        page, band = row["evidence"]["page"], row["evidence"]["physical_band_index"]
        slot = slots[page, band]
        raw = row["raw_fields"]
        account = row.get("account_section_observation") or {}
        title = title_by_section.get(account.get("section_key"))
        amount = row.get("amount_range_observation") or {}
        if (slot["disposition"] != "data_candidate" or title is None or
                raw.get("owner_code") != "SP" or
                OWNER_CODES["SP"] != "Spouse" or
                raw.get("notification_date") not in {None, "2026-06-05"} or
                raw.get("direction_mark") not in {"purchase", "sale"} or
                raw.get("direction_status") != "single_strong_mark_unverified" or
                raw.get("amount_status") != "single_strong_mark_unverified" or
                raw.get("amount_letter") not in "ABCDEF" or
                amount.get("low") != checked_header["ranges"][raw["amount_letter"]]["low"] or
                amount.get("high") != checked_header["ranges"][raw["amount_letter"]]["high"] or
                amount.get("kind") != "range" or
                not isinstance(raw.get("asset_raw"), str) or
                not raw["asset_raw"].strip() or
                (raw.get("event_date_status") not in
                 {"ocr_date_shape_unverified", "ocr_date_shape_with_edge_noise_unverified"}
                 and (page, band) not in date_corrections)):
            raise ValueError(f"fixed House paper field status is not reviewable: {page}:{band}")
        event_text = date_corrections.get((page, band), {}).get("event_date",
                                                                 raw.get("event_date"))
        event = date.fromisoformat(event_text)
        if not date(2026, 5, 1) <= event <= date(2026, 5, 29):
            raise ValueError("fixed House paper event date is outside the source month")
        asset = corrections.get((page, band), {}).get("asset_name", raw["asset_raw"])
        if event <= date(2026, 5, 4):
            unresolved = deepcopy(row)
            unresolved["reasons"] = ["prior_same_filer_report_relation_unresolved"]
            pending.append(unresolved)
            continue
        transaction = _fixed_transaction(page, band, event_text, asset, title,
                                         raw["direction_mark"], amount,
                                         metadata["source_url"],
                                         document_id=DOCUMENT_ID,
                                         source_sha256=SOURCE_SHA256,
                                         person_id="house:M001157", owner="Spouse",
                                         filed_at="2026-06-10T00:00:00Z")
        row_id = transaction["id"]
        if row_id in seen_ids:
            raise ValueError("fixed House paper transaction ID collision")
        seen_ids.add(row_id)
        transactions.append(transaction)
    if (len(transactions) != review.get("expected_eligible_count") or
            len(pending) != review.get("expected_relation_pending_count") or
            len(transactions) + len(pending) != 134):
        raise ValueError("fixed House paper qualified and pending rows do not conserve")
    result = deepcopy(current)
    result["transactions"] = transactions
    result["quarantined"] = pending
    result["document_disposition"]["status"] = "paper_source_bound_partial"
    result["source_review_sha256"] = _sha(review)
    result["qualification"] = {
        "method": "fixed_source_visual_review_v1", "status": "partially_qualified_rows",
        "qualified_count": len(transactions), "quarantined_count": len(pending),
        "advisory_count": len(result.get("advisories", [])),
        "zero_transaction_document_count": 0,
        "document_reasons": ["prior_same_filer_report_relation_unresolved"],
        "production_eligible": True,
    }
    result["pending_transaction_projection"] = _pending_transaction_projection(result, metadata)
    return result


def qualify_fixed_house_paper_paths(evidence_root: Path, review_root: Path,
                                    source_review: Path) -> dict:
    evidence_root, review_root = evidence_root.resolve(), review_root.resolve()
    suffix = f"2026/{DOCUMENT_ID}/{SOURCE_SHA256}"
    source_dir = evidence_root / "house_clerk" / "documents"
    review_dir = review_root / "house_clerk"
    peer_paths = list(source_dir.glob(f"2026/{PRECEDING_DOCUMENT_ID}/*.json"))
    if len(peer_paths) != 1:
        raise ValueError("fixed House paper preceding report is not uniquely archived")
    read = lambda path: json.loads(path.read_text(encoding="utf-8"))
    result = qualify_fixed_house_paper(
        (source_dir / f"{suffix}.pdf").read_bytes(),
        read(source_dir / f"{suffix}.json"), read(peer_paths[0]),
        read(review_dir / "paper_observations" / f"{suffix}.json"),
        read(review_dir / "paper_amount_headers" / f"{suffix}.json"),
        read(review_dir / "identities" / f"{suffix}.json"),
        read(review_dir / "qualifications" / f"{suffix}.json"),
        read(source_review),
    )
    path = review_dir / "qualifications" / f"{suffix}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_bytes(_canonical(result))
    temporary.replace(path)
    return {"document_id": DOCUMENT_ID,
            "qualified_count": len(result["transactions"]),
            "relation_pending_count": len(result["quarantined"])}


def complete_fixed_house_paper(target_pdf: bytes, preceding_pdf: bytes,
                               metadata: dict, preceding: dict,
                               observation: dict, current: dict,
                               source_review: dict, relation_review: dict) -> dict:
    """Finish the fixed report after source-bound prior-report comparison."""
    document_id, source_sha, filer, district = validate_paper_source_metadata(metadata)
    peer_id, peer_sha, peer_filer, peer_district = validate_paper_source_metadata(preceding)
    if (document_id != DOCUMENT_ID or source_sha != SOURCE_SHA256 or
            hashlib.sha256(target_pdf).hexdigest() != SOURCE_SHA256 or
            peer_id != PRECEDING_DOCUMENT_ID or peer_sha != PRECEDING_SOURCE_SHA256 or
            hashlib.sha256(preceding_pdf).hexdigest() != PRECEDING_SOURCE_SHA256 or
            peer_filer != filer or peer_district != district or
            preceding["filed_date"] != "2026-05-04" or
            metadata["filed_date"] != "2026-06-10"):
        raise ValueError("fixed House paper relationship sources changed")
    import pdfplumber
    with pdfplumber.open(io.BytesIO(preceding_pdf)) as pdf:
        if len(pdf.pages) != 4:
            raise ValueError("fixed House paper preceding page count changed")
    if (_sha(source_review) != REVIEW_SHA256 or
            _sha(observation) != source_review.get("observation_sha256") or
            _sha(relation_review) != RELATION_REVIEW_SHA256 or
            relation_review.get("schema_version") !=
            "house-paper-prior-report-relationship-review/v1" or
            relation_review.get("target_source_sha256") != SOURCE_SHA256 or
            relation_review.get("prior_source_sha256") != PRECEDING_SOURCE_SHA256 or
            relation_review.get("prior_filed_date") != "2026-05-04" or
            relation_review.get("prior_page_count") != 4 or
            relation_review.get("prior_physical_counts") !=
            {"data_candidate": 86, "account_heading_or_unknown": 8, "blank": 1} or
            relation_review.get("prior_data_event_month") != "2026-04" or
            relation_review.get("prior_latest_data_event_date") != "2026-04-30"):
        raise ValueError("fixed House paper relationship review changed")
    if (current.get("document_id") != DOCUMENT_ID or
            current.get("source_sha256") != SOURCE_SHA256 or
            current.get("source_review_sha256") != REVIEW_SHA256 or
            current.get("qualification", {}).get("method") !=
            "fixed_source_visual_review_v1" or
            len(current.get("transactions", [])) != 119 or
            len(current.get("quarantined", [])) != 15 or
            current.get("pending_transaction_projection", {}).get("row_count") != 15):
        raise ValueError("fixed House paper partial qualification changed")
    original_slots = {(p["page"], r["physical_band_index"]): r
                      for p in observation["pages"] for r in p["rows"]}
    asset_corrections = {_slot(x): x["asset_name"]
                         for x in source_review["asset_corrections"]}
    date_corrections = {_slot(x): x["event_date"]
                        for x in source_review["date_corrections"]}
    headings = {f"paper-section:{SOURCE_SHA256}:{x['page']}:{x['band']}": x["title"]
                for x in source_review["section_headings"]}
    relation_rows = {_slot(x): x for x in relation_review["target_pending_rows"]}
    if len(relation_rows) != 15 or len(relation_review["target_pending_rows"]) != 15:
        raise ValueError("fixed House paper relationship rows are duplicated")
    converted = []
    for row in current["quarantined"]:
        page, band = row["evidence"]["page"], row["evidence"]["physical_band_index"]
        key = page, band
        source = original_slots[key]
        reviewed = relation_rows.get(key)
        raw = row["raw_fields"]
        account = row["account_section_observation"]
        amount = row["amount_range_observation"]
        if (reviewed is None or row["reasons"] !=
                ["prior_same_filer_report_relation_unresolved"] or
                source["cells"]["asset"]["crop_sha256"] !=
                reviewed["asset_crop_sha256"] or
                source["cells"]["event_date"]["crop_sha256"] !=
                reviewed["event_crop_sha256"] or
                reviewed["asset_name"] != asset_corrections.get(key, raw["asset_raw"]) or
                reviewed["event_date"] != date_corrections.get(key, raw["event_date"]) or
                reviewed["account_title"] != headings.get(account["section_key"]) or
                reviewed["direction"] != raw["direction_mark"] or
                reviewed["amount_letter"] != raw["amount_letter"] or
                amount["letter"] != raw["amount_letter"] or
                amount["kind"] != "range" or amount["high"] is None or
                reviewed["event_date"] not in {"2026-05-01", "2026-05-04"}):
            raise ValueError(f"fixed House paper relationship row changed: {page}:{band}")
        converted.append(_fixed_transaction(
            page, band, reviewed["event_date"], reviewed["asset_name"],
            reviewed["account_title"], reviewed["direction"], amount,
            metadata["source_url"], document_id=DOCUMENT_ID,
            source_sha256=SOURCE_SHA256, person_id="house:M001157",
            owner="Spouse", filed_at="2026-06-10T00:00:00Z"))
    existing_ids = {row["id"] for row in current["transactions"]}
    if (len(existing_ids) != 119 or
            len({row["id"] for row in converted}) != 15 or
            existing_ids & {row["id"] for row in converted}):
        raise ValueError("fixed House paper relationship IDs are duplicated")
    result = deepcopy(current)
    result["transactions"] = [*current["transactions"], *converted]
    result["quarantined"] = []
    result.pop("pending_transaction_projection", None)
    result.pop("temporal_relation_observation", None)
    result["document_disposition"]["status"] = "paper_source_bound_complete"
    result["relationship_review_sha256"] = RELATION_REVIEW_SHA256
    result["qualification"] = {
        "method": "fixed_source_visual_review_complete_v1",
        "status": "qualified_rows", "qualified_count": 134,
        "quarantined_count": 0, "advisory_count": len(result.get("advisories", [])),
        "zero_transaction_document_count": 0, "document_reasons": [],
        "production_eligible": True,
    }
    return result


def complete_fixed_house_paper_paths(evidence_root: Path, review_root: Path,
                                     source_review_path: Path,
                                     relation_review_path: Path) -> dict:
    evidence_root, review_root = evidence_root.resolve(), review_root.resolve()
    suffix = f"2026/{DOCUMENT_ID}/{SOURCE_SHA256}"
    source_dir = evidence_root / "house_clerk" / "documents"
    review_dir = review_root / "house_clerk"
    peer_paths = list(source_dir.glob(f"2026/{PRECEDING_DOCUMENT_ID}/*.json"))
    if len(peer_paths) != 1:
        raise ValueError("fixed House paper preceding report is not uniquely archived")
    peer_pdf = peer_paths[0].with_suffix(".pdf")
    read = lambda path: json.loads(path.read_text(encoding="utf-8"))
    path = review_dir / "qualifications" / f"{suffix}.json"
    result = complete_fixed_house_paper(
        (source_dir / f"{suffix}.pdf").read_bytes(), peer_pdf.read_bytes(),
        read(source_dir / f"{suffix}.json"), read(peer_paths[0]),
        read(review_dir / "paper_observations" / f"{suffix}.json"),
        read(path), read(source_review_path), read(relation_review_path),
    )
    temporary = path.with_suffix(".json.tmp")
    temporary.write_bytes(_canonical(result))
    temporary.replace(path)
    return {"document_id": DOCUMENT_ID, "qualified_count": 134,
            "relationship_reviewed_count": 15, "quarantined_count": 0}
