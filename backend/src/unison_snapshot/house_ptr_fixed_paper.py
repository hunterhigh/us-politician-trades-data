"""Source-bound qualification of the fixed 9116141 printed House PTR.

This adapter promotes only verified disclosure slots dated after the preceding
same-filer PTR was filed. The other 15 slots stay in the review queue.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import re

from .house_ptr import DETERMINISTIC_IDENTITY_BASES, OWNER_CODES
from .house_ptr_amount_header import evaluate_amount_header_pages
from .house_ptr_paper_qualification import (_pending_transaction_projection,
                                            qualify_paper_observation)
from .house_ptr_paper_temporal import validate_paper_source_metadata


DOCUMENT_ID = "9116141"
SOURCE_SHA256 = "716cdcc10bd57c400f10d8bb4133eb667931a9699fb1835ed3b7deca010a36a1"
PRECEDING_DOCUMENT_ID = "9115820"
PRECEDING_SOURCE_SHA256 = "e1254f953327174fe80a856137e386c3dea42aa58728d350105822bfabab6860"
REVIEW_SHA256 = "4cdb554eef9890a2f85e8378cf4a64e4bcb7feadfdbc52467f9e4e58c129f0b4"


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _slot(item: dict) -> tuple[int, int]:
    page, band = item.get("page"), item.get("band")
    if type(page) is not int or type(band) is not int or page < 1 or band < 1:
        raise ValueError("fixed House paper review has an invalid slot")
    return page, band


def _check_source_cells(pdf_bytes: bytes, observation: dict) -> dict[tuple[int, int], dict]:
    import pdfplumber

    if observation.get("render_dpi") != 200 or len(observation.get("pages", [])) != 6:
        raise ValueError("fixed House paper page observation changed")
    slots = {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if len(pdf.pages) != 6:
            raise ValueError("fixed House paper source page count changed")
        for page_record, pdf_page in zip(observation["pages"], pdf.pages, strict=True):
            number = page_record.get("page")
            if (number != pdf_page.page_number or
                    page_record.get("coordinate_frame") != "original_page" or
                    page_record.get("cell_status") != "raw_observed_unverified"):
                raise ValueError("fixed House paper page geometry changed")
            for row in page_record["rows"]:
                band = row["physical_band_index"]
                key = number, band
                if key in slots:
                    raise ValueError("fixed House paper has duplicate physical slots")
                slots[key] = row
                cells = row.get("cells")
                if cells is None:
                    if row["disposition"] != "blank":
                        raise ValueError("fixed House paper nonblank slot lacks cells")
                    continue
                all_cells = [cells[name] for name in ("owner", "asset", "event_date",
                                                      "notification_date")]
                all_cells.extend(cells["direction"])
                all_cells.extend(cells["amount"])
                for cell in all_cells:
                    box = cell.get("bbox_pixels")
                    if (not isinstance(box, list) or len(box) != 4 or
                            not all(type(point) is int for point in box) or
                            not 0 <= box[0] < box[2] <= pdf_page.width * 200 / 72 + 4 or
                            not 0 <= box[1] < box[3] <= pdf_page.height * 200 / 72 + 4 or
                            not isinstance(cell.get("crop_sha256"), str) or
                            re.fullmatch(r"[0-9a-f]{64}", cell["crop_sha256"]) is None):
                        raise ValueError("fixed House paper cell geometry changed")
                    # The visual review pins the existing crop hash via the complete
                    # observation digest. Ghostscript renders differ across runners,
                    # so a new raster must not silently redefine that fixed crop.
    return slots


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
    slots = _check_source_cells(pdf_bytes, observation)
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
        fingerprint = f"{SOURCE_SHA256}:{page}:{band}"
        row_id = "house-ptr:" + hashlib.sha256(fingerprint.encode("ascii")).hexdigest()[:24]
        if row_id in seen_ids:
            raise ValueError("fixed House paper transaction ID collision")
        seen_ids.add(row_id)
        transactions.append({
            "id": row_id, "filing_id": DOCUMENT_ID, "person_id": "house:M001157",
            "owner": "Spouse", "asset_name": f"{asset} (Account: {title})",
            "ticker": None, "ticker_mapping_basis": None, "instrument_type": None,
            "option_type": None, "strike_price": None, "expiration_date": None,
            "transaction_type": raw["direction_mark"],
            "transaction_date": event_text, "filed_at": "2026-06-10T00:00:00Z",
            "amount_low": amount["low"], "amount_high": amount["high"],
            "position_effect": "unknown", "position_effect_basis": None,
            "source_id": "house_clerk", "source": "U.S. House Clerk",
            "source_url": metadata["source_url"], "verification_status": "official_matched",
        })
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
