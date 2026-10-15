"""Source-bound qualification of the 9115820 printed House PTR."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import io
import json
from pathlib import Path

from .house_ptr import DETERMINISTIC_IDENTITY_BASES
from .house_ptr_amount_header import evaluate_amount_header_pages
from .house_ptr_fixed_paper import (
    _canonical, _check_source_cells, _fixed_transaction, _sha, _slot,
)
from .house_ptr_paper_qualification import qualify_paper_observation
from .house_ptr_paper_temporal import validate_paper_source_metadata


DOCUMENT_ID = "9115820"
SOURCE_SHA256 = "e1254f953327174fe80a856137e386c3dea42aa58728d350105822bfabab6860"
PRIOR_DOCUMENT_ID = "9115728"
PRIOR_SOURCE_SHA256 = "38fd4e814542a1010e7df774145956510e37f0b6c2085bbf84c4a0077ef5d767"
REVIEW_SHA256 = "26965538b444125c49ce33ea2b51fd51842af23252a7f40b23b53c9c49203107"


def qualify_9115820(pdf_bytes: bytes, prior_pdf_bytes: bytes, metadata: dict,
                    prior_metadata: dict, observation: dict, amount_header: dict,
                    identity: dict, current: dict, review: dict) -> dict:
    document_id, source_sha, filer, district = validate_paper_source_metadata(metadata)
    prior_id, prior_sha, prior_filer, prior_district = validate_paper_source_metadata(
        prior_metadata)
    if (document_id != DOCUMENT_ID or source_sha != SOURCE_SHA256 or
            hashlib.sha256(pdf_bytes).hexdigest() != SOURCE_SHA256 or
            prior_id != PRIOR_DOCUMENT_ID or prior_sha != PRIOR_SOURCE_SHA256 or
            hashlib.sha256(prior_pdf_bytes).hexdigest() != PRIOR_SOURCE_SHA256 or
            filer != prior_filer or district != prior_district or
            metadata.get("filed_date") != "2026-05-04" or
            prior_metadata.get("filed_date") != "2026-04-08"):
        raise ValueError("House 9115820 official source or prior relation changed")
    import pdfplumber
    with pdfplumber.open(io.BytesIO(prior_pdf_bytes)) as pdf:
        if len(pdf.pages) != 5:
            raise ValueError("House 9115820 prior page count changed")
    if (review.get("schema_version") != "house-paper-fixed-source-review/v2" or
            review.get("document_id") != DOCUMENT_ID or
            review.get("source_sha256") != SOURCE_SHA256 or
            review.get("prior_document_id") != PRIOR_DOCUMENT_ID or
            review.get("prior_source_sha256") != PRIOR_SOURCE_SHA256 or
            review.get("prior_filed_date") != "2026-04-08" or
            review.get("prior_page_count") != 5 or
            review.get("prior_page_data_counts") != [25, 26, 25, 21, 1] or
            review.get("prior_rotated_pages") != [2, 5] or
            review.get("prior_physical_counts") !=
            {"data_candidate": 98, "account_heading_or_unknown": 8, "blank": 1} or
            review.get("prior_data_event_month") != "2026-03" or
            review.get("prior_latest_data_event_date") != "2026-03-31" or
            review.get("reviewed_page_count") != 4 or
            review.get("target_page_data_counts") != [25, 26, 26, 9] or
            review.get("target_data_event_month") != "2026-04" or
            review.get("physical_counts") !=
            {"data_candidate": 86, "section_heading": 8, "blank": 1} or
            review.get("expected_eligible_count") != 86 or
            _sha(review) != REVIEW_SHA256 or
            _sha(observation) != review.get("observation_sha256")):
        raise ValueError("House 9115820 source review is stale")
    if (identity.get("document_id") != DOCUMENT_ID or
            identity.get("person_id") != "house:M001157" or
            identity.get("match_basis") not in DETERMINISTIC_IDENTITY_BASES or
            identity.get("status") != "matched_automatically"):
        raise ValueError("House 9115820 filer identity changed")
    checked_header = evaluate_amount_header_pages(SOURCE_SHA256, amount_header["pages"])
    if (checked_header != amount_header or
            checked_header.get("status") != "printed_header_correlated" or
            checked_header.get("printed_a_variant") != "A1000"):
        raise ValueError("House 9115820 amount legend changed")
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
        raise ValueError("House 9115820 pending qualification changed")
    slots = _check_source_cells(pdf_bytes, observation, page_count=4)
    if (len(slots) != 95 or
            [sum(row["disposition"] == "data_candidate" for row in page["rows"])
             for page in observation["pages"]] != review["target_page_data_counts"] or
            expected["document_disposition"]["physical_counts"] !=
            {"data_candidate": 86, "unknown": 8, "blank": 1} or
            len(expected["structural_observations"]) != 8 or
            len(expected["quarantined"]) != 86):
        raise ValueError("House 9115820 physical row census changed")
    headings = {_slot(item): item for item in review["section_headings"]}
    corrections = {_slot(item): item for item in review["asset_corrections"]}
    owners = {_slot(item): item for item in review["owner_corrections"]}
    if (len(headings) != 8 or len(headings) != len(review["section_headings"]) or
            len(corrections) != 5 or len(corrections) != len(review["asset_corrections"]) or
            len(owners) != 1 or len(review["owner_corrections"]) != 1):
        raise ValueError("House 9115820 reviewed slots are duplicated")
    titles = {}
    for key, item in headings.items():
        slot = slots.get(key)
        if (slot is None or slot["disposition"] != "unknown" or
                slot["cells"]["asset"]["crop_sha256"] !=
                item.get("asset_crop_sha256") or
                not isinstance(item.get("title"), str) or not item["title"].strip()):
            raise ValueError("House 9115820 account title is unbound")
        titles[f"paper-section:{SOURCE_SHA256}:{key[0]}:{key[1]}"] = item["title"]
    for key, item in corrections.items():
        slot = slots.get(key)
        if (slot is None or slot["disposition"] != "data_candidate" or
                slot["cells"]["asset"]["crop_sha256"] !=
                item.get("asset_crop_sha256") or
                not isinstance(item.get("asset_name"), str) or
                not item["asset_name"].strip()):
            raise ValueError("House 9115820 asset correction is unbound")
    owner_item = owners.get((4, 16))
    if (owner_item is None or owner_item.get("owner") != "Self" or
            (owner_item.get("account_heading_page"),
             owner_item.get("account_heading_band")) != (4, 15) or
            slots[4, 16]["cells"]["owner"]["crop_sha256"] !=
            owner_item.get("owner_crop_sha256") or
            titles.get(f"paper-section:{SOURCE_SHA256}:4:15") != "MICHAEL MCCAUL"):
        raise ValueError("House 9115820 filer-owned row is unbound")
    transactions = []
    for row in expected["quarantined"]:
        page, band = row["evidence"]["page"], row["evidence"]["physical_band_index"]
        key = page, band
        raw = row["raw_fields"]
        account = row.get("account_section_observation") or {}
        title = titles.get(account.get("section_key"))
        amount = row.get("amount_range_observation") or {}
        owner = ("Self" if key == (4, 16) else "Spouse")
        if (slots[key]["disposition"] != "data_candidate" or title is None or
                raw.get("owner_code") != (None if owner == "Self" else "SP") or
                raw.get("direction_mark") not in {"purchase", "sale"} or
                raw.get("direction_status") != "single_strong_mark_unverified" or
                raw.get("amount_status") != "single_strong_mark_unverified" or
                raw.get("amount_letter") not in "ABCDEF" or
                amount.get("low") != checked_header["ranges"][raw["amount_letter"]]["low"] or
                amount.get("high") != checked_header["ranges"][raw["amount_letter"]]["high"] or
                amount.get("kind") != "range" or
                not isinstance(raw.get("asset_raw"), str) or
                not raw["asset_raw"].strip() or
                raw.get("event_date_status") not in
                {"ocr_date_shape_unverified", "ocr_date_shape_with_edge_noise_unverified"}):
            raise ValueError(f"House 9115820 source field is not reviewable: {page}:{band}")
        event_text = raw["event_date"]
        event = date.fromisoformat(event_text)
        if not date(2026, 4, 1) <= event <= date(2026, 4, 30):
            raise ValueError("House 9115820 transaction date is outside April")
        asset = corrections.get(key, {}).get("asset_name", raw["asset_raw"])
        transactions.append(_fixed_transaction(
            page, band, event_text, asset, title, raw["direction_mark"], amount,
            metadata["source_url"], document_id=DOCUMENT_ID,
            source_sha256=SOURCE_SHA256, owner=owner,
            filed_at="2026-05-04T00:00:00Z"))
    if (len(transactions) != 86 or
            len({item["id"] for item in transactions}) != 86):
        raise ValueError("House 9115820 transaction rows or IDs do not conserve")
    result = deepcopy(current)
    result["transactions"] = transactions
    result["quarantined"] = []
    result.pop("pending_transaction_projection", None)
    result.pop("temporal_relation_observation", None)
    result["document_disposition"]["status"] = "paper_source_bound_complete"
    result["source_review_sha256"] = REVIEW_SHA256
    result["qualification"] = {
        "method": "fixed_source_visual_review_complete_v2",
        "status": "qualified_rows", "qualified_count": 86,
        "quarantined_count": 0,
        "advisory_count": len(result.get("advisories", [])),
        "zero_transaction_document_count": 0,
        "document_reasons": [], "production_eligible": True,
    }
    return result


def qualify_9115820_paths(evidence_root: Path, review_root: Path,
                         source_review: Path) -> dict:
    evidence_root, review_root = evidence_root.resolve(), review_root.resolve()
    source_dir = evidence_root / "house_clerk" / "documents"
    review_dir = review_root / "house_clerk"
    suffix = f"2026/{DOCUMENT_ID}/{SOURCE_SHA256}"
    prior_paths = list(source_dir.glob(f"2026/{PRIOR_DOCUMENT_ID}/*.json"))
    if len(prior_paths) != 1:
        raise ValueError("House 9115820 prior official document is not unique")
    read = lambda path: json.loads(path.read_text(encoding="utf-8"))
    result = qualify_9115820(
        (source_dir / f"{suffix}.pdf").read_bytes(),
        prior_paths[0].with_suffix(".pdf").read_bytes(),
        read(source_dir / f"{suffix}.json"), read(prior_paths[0]),
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
    return {"document_id": DOCUMENT_ID, "qualified_count": 86,
            "quarantined_count": 0}
