"""Qualify one more fixed Senate paper PTR from archived cells and corrections."""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
from io import BytesIO
import json
from pathlib import Path

from qualify_senate_paper_first_report import AMOUNTS
from unison_snapshot.senate_paper import PAPER_EXTRACTION_SCHEMA, PAPER_OCR_RULE_VERSION
from unison_snapshot.senate_reports import PARSER_VERSION


DOCUMENT_ID = "3a4c5095-028a-4614-a692-836719da4e63"
SOURCE_SHA256 = "332bf8d6b8398c55443935a091e3b3a40617ffd6148a377c6a20c4b6902d83a3"
CORRECTIONS_SHA256 = "a4a2b56e8fec7b80aa6a6ea3fe436c48c9f4cec1e38a640ef5b06fabff4d37e4"
SOURCE_URL = f"https://efdsearch.senate.gov/search/view/paper/{DOCUMENT_ID}/"
KNOWN_ASSETS = {"(S) MH Built to Last LLC", "(S) MH Four Winds LLC",
                "(S) Decibel Partners I LP", "(S) Days Between LLC"}


def _slot_key(value: dict) -> tuple[int, int]:
    return value["page_number"], value["grid_slot"]


def qualify(batch: dict, pending: dict, corrections: dict, ledger: dict,
            manifest: dict, evidence_root: Path) -> dict:
    if (batch.get("parser_version") != PARSER_VERSION or
            corrections.get("schema_version") != "senate-paper-fixed-visual-corrections/v1" or
            corrections.get("document_id") != DOCUMENT_ID or
            corrections.get("entrypoint_source_sha256") != SOURCE_SHA256 or
            corrections.get("received_date") != "2026-07-17" or
            corrections.get("filer_name") != "Richard Blumenthal" or
            corrections.get("form_amendment_checkbox") != "no_mark_detected" or
            corrections.get("expected_data_row_count") != 46 or
            ledger.get("document_id") != DOCUMENT_ID or
            ledger.get("entrypoint_source_sha256") != SOURCE_SHA256 or
            manifest.get("document_id") != DOCUMENT_ID or
            manifest.get("entrypoint_source_sha256") != SOURCE_SHA256 or
            manifest.get("manifest_sha256") != corrections.get("manifest_sha256") or
            manifest.get("evidence_complete") is not True or
            pending.get("schema_version") != "senate-paper-pending-slot-projection/v3"):
        raise ValueError("second Senate paper report source binding changed")
    failures = [item for item in batch["failures"] if item["document_id"] == DOCUMENT_ID]
    if (len(failures) != 1 or failures[0].get("source_sha256") != SOURCE_SHA256 or
            any(item["document_id"] == DOCUMENT_ID for item in batch["extractions"])):
        raise ValueError("second Senate paper report has no unique failed disposition")
    from PIL import Image

    page_bytes, pages = {}, {}
    for item in manifest["pages"]:
        number = item["page_number"]
        path = (evidence_root / item["archive_path"]).resolve()
        if (number in page_bytes or not path.is_relative_to(evidence_root.resolve()) or
                not path.is_file()):
            raise ValueError("second Senate paper page archive is incomplete")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("second Senate paper page bytes changed")
        page_bytes[number] = raw
        pages[number] = Image.open(BytesIO(raw)).convert("L")
    if len(page_bytes) != manifest["page_count"] or sorted(page_bytes) != list(
            range(1, manifest["page_count"] + 1)):
        raise ValueError("second Senate paper page sequence changed")
    amendment = pages[2].crop((1089, 829, 1105, 844))
    if sum(pixel < 160 for pixel in amendment.getdata()) > 3:
        raise ValueError("second Senate paper amendment checkbox is marked")
    ledger_slots = {_slot_key(item): item for item in ledger["slots"]}
    if len(ledger_slots) != len(ledger["slots"]):
        raise ValueError("second Senate paper ledger slots repeat")
    headings = {_slot_key(item): item for item in corrections["account_headings"]}
    overrides = {_slot_key(item): item for item in corrections["asset_corrections"]}
    if (len(headings) != 5 or len(overrides) != 6 or
            len(headings) != len(corrections["account_headings"]) or
            len(overrides) != len(corrections["asset_corrections"])):
        raise ValueError("second Senate paper visual corrections are incomplete")
    for key, item in {**headings, **overrides}.items():
        source = ledger_slots.get(key)
        if (source is None or item.get("review_status") != "confirmed_fixed_image" or
                item.get("page_sha256") != source["page_sha256"] or
                item.get("asset_crop_sha256") != source["asset_crop"]["grayscale_sha256"]):
            raise ValueError("second Senate paper visual correction differs from source")
    continuation = {item["page_number"]: (item["previous_heading_page"],
                                          item["previous_heading_grid_slot"])
                    for item in corrections["page_continuations"]}
    if continuation != {3: (2, 7), 4: (3, 8), 5: (4, 17)}:
        raise ValueError("second Senate paper page continuation changed")
    active_by_slot = {}
    active = None
    previous_page = None
    for key in sorted(ledger_slots):
        if key[0] != previous_page:
            active = continuation.get(key[0])
            previous_page = key[0]
        if key in headings:
            active = key
        active_by_slot[key] = active
    pending_rows = {_slot_key(item["source_slot"]): item
                    for item in pending["pending_rows"]
                    if item["source_slot"]["document_id"] == DOCUMENT_ID}
    if len(pending_rows) != 46:
        raise ValueError("second Senate paper data slots are incomplete or repeated")
    rows = []
    seen_ids = set()
    for key, item in sorted(pending_rows.items()):
        source = ledger_slots.get(key)
        slot = item["source_slot"]
        if (source is None or source["page_sha256"] != slot["page_sha256"] or
                source["row_crop"]["grayscale_sha256"] != slot["row_crop_sha256"] or
                slot["entrypoint_source_sha256"] != SOURCE_SHA256 or
                item.get("legacy_extraction_failed") is not True or
                item.get("legacy_extraction_report_row_count") != 0 or
                item.get("date_cell_observation_status") != "agreement" or
                item.get("date_cell_conflict") is not False or
                len(item.get("direction_mark_observations", [])) != 1 or
                len(item.get("amount_mark_observations", [])) != 1):
            raise ValueError("second Senate paper field observation is not qualified")
        image = pages[key[0]]
        if hashlib.sha256(page_bytes[key[0]]).hexdigest() != slot["page_sha256"]:
            raise ValueError("second Senate paper row page changed")
        for crop_name in ("row_crop", "asset_crop", "date_crop"):
            crop = source[crop_name]
            actual = hashlib.sha256(image.crop(tuple(crop["box_px"])).tobytes()).hexdigest()
            if actual != crop["grayscale_sha256"]:
                raise ValueError("second Senate paper row crop changed")
        heading_key = active_by_slot.get(key)
        heading = headings.get(heading_key)
        if heading is None:
            raise ValueError("second Senate paper data row has no account heading")
        raw_asset = overrides[key]["printed_text"] if key in overrides else item["asset_ocr_raw"]
        if raw_asset not in KNOWN_ASSETS:
            raise ValueError("second Senate paper asset is not source reviewed")
        event = item["date_cell_ocr_parsed"]
        if not isinstance(event, str) or not (date(2026, 6, 8) <= date.fromisoformat(event)
                                               <= date(2026, 6, 24)):
            raise ValueError("second Senate paper event date is outside cover range")
        direction = item["direction_mark_observations"][0]
        amount_band = item["amount_mark_observations"][0]
        amount = next((raw for bounds, raw in AMOUNTS.items()
                       if bounds == AMOUNTS_TO_BOUNDS.get(amount_band)), None)
        if direction not in {"purchase", "sale"} or amount is None:
            raise ValueError("second Senate paper marked direction or amount changed")
        fingerprint = "|".join((SOURCE_SHA256, str(key[0]), slot["page_sha256"],
                                str(key[1]), slot["row_crop_sha256"]))
        row_id = "senate-ptr:" + hashlib.sha256(fingerprint.encode("ascii")).hexdigest()[:24]
        if row_id in seen_ids:
            raise ValueError("second Senate paper transaction ID collision")
        seen_ids.add(row_id)
        rows.append({"extraction_id": row_id, "row_number": key[1],
                     "page_number": key[0], "transaction_date": event,
                     "owner_raw": "Spouse", "ticker_raw": None,
                     "asset_name_raw": (raw_asset[4:] +
                                        f" (Account: {heading['printed_text']})"),
                     "asset_type_raw": None, "transaction_type_raw": direction.title(),
                     "transaction_type": direction, "amount_raw": amount,
                     "comment_raw": None, "qualification_status": "eligible",
                     "quarantine_reasons": [],
                     "qualification_basis": "fixed_source_visual_review_v1",
                     "source_slot": slot})
    extraction = {"schema_version": PAPER_EXTRACTION_SCHEMA,
                  "parser_version": PARSER_VERSION,
                  "paper_rule_version": PAPER_OCR_RULE_VERSION,
                  "source_id": "senate_efd", "document_id": DOCUMENT_ID,
                  "source_url": SOURCE_URL, "source_sha256": SOURCE_SHA256,
                  "filer_name": "RICHARD BLUMENTHAL",
                  "portal_listed_date": "2026-07-17",
                  "report_label_date": "2026-07-17",
                  "report_amendment_number": None,
                  "report_title_date": "2026-07-17",
                  "catalog_title_date_matches": True,
                  "filed_at_raw": "Filed 07/17/2026",
                  "document_disposition": "transactions_parsed",
                  "evidence_complete": True,
                  "page_manifest_sha256": manifest["manifest_sha256"],
                  "page_count": manifest["page_count"],
                  "transactions": rows, "eligible_transaction_count": 46,
                  "quarantined_transaction_count": 0,
                  "qualification_basis": "fixed_source_visual_review_v1",
                  "visual_corrections_sha256": CORRECTIONS_SHA256}
    result = dict(batch)
    result["extractions"] = [*batch["extractions"], extraction]
    result["failures"] = [item for item in batch["failures"]
                          if item["document_id"] != DOCUMENT_ID]
    result["extraction_count"] += 1
    result["failure_count"] -= 1
    result["transaction_count"] += 46
    result["eligible_transaction_count"] += 46
    if result["extraction_count"] + result["failure_count"] != result["report_count"]:
        raise ValueError("second Senate paper dispositions do not conserve")
    return result


AMOUNTS_TO_BOUNDS = {
    "1001_15000": (1001, 15000), "15001_50000": (15001, 50000),
    "50001_100000": (50001, 100000),
    "100001_250000": (100001, 250000),
    "250001_500000": (250001, 500000),
    "500001_1000000": (500001, 1000000),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("batch", "pending", "corrections", "ledger", "manifest",
                 "evidence_root", "output"):
        parser.add_argument(f"--{name.replace('_', '-')}", required=True, type=Path,
                            dest=name)
    args = parser.parse_args()
    raw = args.corrections.read_bytes()
    if hashlib.sha256(raw).hexdigest() != CORRECTIONS_SHA256:
        raise ValueError("second Senate paper corrections SHA changed")
    read = lambda path: json.loads(path.read_text(encoding="utf-8"))
    ledger = next(item for item in read(args.ledger)["reports"]
                  if item["document_id"] == DOCUMENT_ID)
    result = qualify(read(args.batch), read(args.pending), json.loads(raw),
                     ledger, read(args.manifest), args.evidence_root)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
