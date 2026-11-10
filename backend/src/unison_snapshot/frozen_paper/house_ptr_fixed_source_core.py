"""Shared source-cell and disclosure-row rules for reviewed printed House PTRs."""

from __future__ import annotations

import hashlib
import io
import json
import re


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def review_slot(item: dict) -> tuple[int, int]:
    page, band = item.get("page"), item.get("band")
    if type(page) is not int or type(band) is not int or page < 1 or band < 1:
        raise ValueError("fixed House paper review has an invalid slot")
    return page, band


def paper_disclosure_id(source_sha256: str, page: int, band: int) -> str:
    """Existing printed-row identity; shared by reviewed and automatic paper paths."""
    fingerprint = f"{source_sha256}:{page}:{band}"
    return "house-ptr:" + hashlib.sha256(fingerprint.encode("ascii")).hexdigest()[:24]


def disclosure_transaction(page: int, band: int, event_text: str, asset: str,
                           title: str, direction: str, amount: dict,
                           source_url: str, *, document_id: str,
                           source_sha256: str, person_id: str, owner: str,
                           filed_at: str) -> dict:
    row_id = paper_disclosure_id(source_sha256, page, band)
    return {
        "id": row_id, "filing_id": document_id, "person_id": person_id,
        "owner": owner, "asset_name": f"{asset} (Account: {title})",
        "ticker": None, "ticker_mapping_basis": None, "instrument_type": None,
        "option_type": None, "strike_price": None, "expiration_date": None,
        "transaction_type": direction, "transaction_date": event_text,
        "filed_at": filed_at,
        "amount_low": amount["low"], "amount_high": amount["high"],
        "position_effect": "unknown", "position_effect_basis": None,
        "source_id": "house_clerk", "source": "U.S. House Clerk",
        "source_url": source_url, "verification_status": "official_matched",
    }


def check_source_cells(pdf_bytes: bytes, observation: dict,
                       *, page_count: int, allow_unobserved: bool = False, verify_crop_hashes: bool = False) -> dict[tuple[int, int], dict]:
    import pdfplumber

    if observation.get("render_dpi") != 200 or len(observation.get("pages", [])) != page_count:
        raise ValueError("fixed House paper page observation changed")
    slots = {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if len(pdf.pages) != page_count:
            raise ValueError("fixed House paper source page count changed")
        for page_record, pdf_page in zip(observation["pages"], pdf.pages, strict=True):
            number = page_record.get("page")
            if (allow_unobserved and number == pdf_page.page_number and
                    page_record.get("region_status") == "coverage_unknown" and not page_record.get("rows")):
                continue
            if (number != pdf_page.page_number or
                    page_record.get("coordinate_frame") != "original_page" or
                    page_record.get("cell_status") != "raw_observed_unverified"):
                raise ValueError("fixed House paper page geometry changed")
            rendered = pdf_page.to_image(resolution=200).original if verify_crop_hashes else None
            for row in page_record["rows"]:
                band = row["physical_band_index"]
                key = number, band
                if key in slots:
                    raise ValueError("fixed House paper has duplicate physical slots")
                slots[key] = row
                cells = row.get("cells")
                if cells is None:
                    if row["disposition"] != "blank" and not allow_unobserved:
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
                    if rendered is not None:
                        encoded = io.BytesIO()
                        rendered.crop(tuple(box)).save(encoded, format="PNG")
                        if hashlib.sha256(encoded.getvalue()).hexdigest() != cell["crop_sha256"]:
                            raise ValueError("House paper crop differs from bound PDF")
                    # The observation digest pins the existing crop. Ghostscript
                    # raster output may differ across operating systems.
    return slots
