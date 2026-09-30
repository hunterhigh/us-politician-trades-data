"""Conservatively census grid slots in four fixed, unparsed Senate paper viewers.

Every slot retains its original image coordinates and crop digest. Image and
OCR signals here are observations only; no transaction or candidate is made.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path

from PIL import Image

from audit_senate_paper_pages import _asset_ink, _git_bytes
from audit_senate_paper_first_report import INVENTORY_SHA256, _raw_crop
from unison_snapshot.senate_paper_sample import EVIDENCE_COMMIT


VIEWER_ONLY_IDS = (
    "3a4c5095-028a-4614-a692-836719da4e63",
    "a0d25e8f-fe54-4328-a7ea-504da008742b",
    "d02263c3-381d-4ee9-8d84-2c44d9baa59e",
    "ec20cd93-6702-4a29-b3a6-983f4b17f365",
)
INVENTORY_LF_SHA256 = "c4500abba79e67fe7d8579ac8b378900f7177dd2ac6dc25b90203fcc2328ef8f"
LEGACY_EXTRACTED_IDS = (
    "929216d5-5dbd-429c-858c-1e9332924627",
    "d337c392-e0aa-428e-be93-44a327b90d08",
    "f028d2ce-4ab7-41a8-a67a-91675b6941d7",
    "f873aeb4-adbb-4934-a188-79416a2e4c76",
)


def audit(repo: Path, inventory_path: Path,
          document_ids: tuple[str, ...] = VIEWER_ONLY_IDS) -> dict:
    raw = inventory_path.read_bytes()
    # The fixed inventory was first hashed from a Windows CRLF checkout;
    # Git stores LF. Verify the normalized bytes so both checkouts replay it.
    if hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() != INVENTORY_LF_SHA256:
        raise ValueError("fixed Senate paper inventory changed")
    inventory = json.loads(raw)
    if inventory.get("evidence_commit") != EVIDENCE_COMMIT:
        raise ValueError("fixed Senate paper evidence commit changed")
    by_id = {item["document_id"]: item for item in inventory["reports"]}
    if document_ids not in (VIEWER_ONLY_IDS, LEGACY_EXTRACTED_IDS) or not set(document_ids) <= set(by_id):
        raise ValueError("fixed Senate paper report selection is invalid")
    reports = []
    for document_id in document_ids:
        report = by_id[document_id]
        manifest_path = f"senate_efd/paper_pages/{document_id}/manifest.json"
        manifest = json.loads(_git_bytes(repo, manifest_path))
        if (manifest.get("manifest_sha256") != report["manifest_sha256"] or
                len(manifest.get("pages", [])) != report["page_count"]):
            raise ValueError(f"fixed page manifest changed: {document_id}")
        slots = []
        example_count = 0
        table_pages = 0
        for page in report["pages"]:
            number = page["page_number"]
            archived = manifest["pages"][number - 1]
            content = _git_bytes(repo, archived["archive_path"])
            if (archived["sha256"] != page["page_sha256"] or
                    hashlib.sha256(content).hexdigest() != page["page_sha256"]):
                raise ValueError(f"fixed page image changed: {document_id}/{number}")
            if not page["table_grid_detected"]:
                if number != 1:
                    raise ValueError(f"unexpected non-table page: {document_id}/{number}")
                continue
            table_pages += 1
            image = Image.open(BytesIO(content)).convert("L")
            grid = page["grid_line_centers"]
            if not isinstance(grid, list) or len(grid) < 2 or any(
                    b <= a for a, b in zip(grid, grid[1:])):
                raise ValueError(f"invalid grid lines: {document_id}/{number}")
            skip = 2 if page["printed_examples_detected"] else 0
            example_count += skip
            bands = {item["grid_slot"]: item for item in page["filled_grid_bands"]}
            for index, (top, bottom) in enumerate(zip(grid, grid[1:]), start=1):
                if index <= skip:
                    continue
                slot = index - skip
                band = bands.get(slot)
                ink = _asset_ink(image, top, bottom)
                asset_ocr = band["asset_ocr"] if band else ""
                date_ocr = band["date_ocr"] if band else ""
                if asset_ocr.strip().endswith(":") and not date_ocr.strip():
                    disposition = "heading_text_candidate_quarantined"
                elif band is None and ink < 150:
                    disposition = "blank_appearance_unresolved"
                else:
                    disposition = "content_or_noise_unresolved"
                slots.append({
                    "page_number": number,
                    "page_sha256": page["page_sha256"],
                    "source_url": page["source_url"],
                    "grid_slot": slot,
                    "row_bounds_px": [top, bottom],
                    "row_crop": _raw_crop(image, (525, top, 2920, bottom)),
                    "asset_crop": _raw_crop(image, (590, top + 8, 1385, bottom - 8)),
                    "date_crop": _raw_crop(image, (1650, top + 8, 1940, bottom - 8)),
                    "asset_ink_sample_count": ink,
                    "asset_ocr_raw": asset_ocr,
                    "date_ocr_raw": date_ocr,
                    "inventory_signal": band is not None,
                    "disposition": disposition,
                    "candidate_transaction_id": None,
                })
        expected_slots = sum(len(p["grid_line_centers"]) - 1 -
                             (2 if p["printed_examples_detected"] else 0)
                             for p in report["pages"] if p["table_grid_detected"])
        if len(slots) != expected_slots or table_pages != report["page_count"] - 1:
            raise ValueError(f"physical slot count changed: {document_id}")
        counts = {state: sum(item["disposition"] == state for item in slots)
                  for state in ("heading_text_candidate_quarantined",
                                "blank_appearance_unresolved",
                                "content_or_noise_unresolved")}
        if sum(counts.values()) != len(slots):
            raise ValueError(f"physical slot accounting failed: {document_id}")
        reports.append({
            "document_id": document_id,
            "entrypoint_source_sha256": report["entrypoint_source_sha256"],
            "manifest_sha256": report["manifest_sha256"],
            "page_count": report["page_count"],
            "table_page_count": table_pages,
            "printed_example_band_count": example_count,
            "physical_grid_slot_count": len(slots),
            "disposition_counts": counts,
            "slots": slots,
        })
    return {
        "schema_version": ("senate-paper-viewer-remainders/v1"
                           if document_ids == VIEWER_ONLY_IDS else
                           "senate-paper-legacy-extracted-grid/v1"),
        "evidence_commit": EVIDENCE_COMMIT,
        "inventory_sha256": INVENTORY_SHA256,
        "interpretation": "all slots unresolved; no transaction candidates",
        "reports": reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--legacy-extracted", action="store_true",
                        help="census the four paper reports with legacy extraction")
    args = parser.parse_args()
    result = audit(args.repo, args.inventory,
                   LEGACY_EXTRACTED_IDS if args.legacy_extracted else VIEWER_ONLY_IDS)
    args.output.write_text(json.dumps(result, sort_keys=True, ensure_ascii=False,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({item["document_id"]: item["disposition_counts"]
                      for item in result["reports"]}, sort_keys=True))


if __name__ == "__main__":
    main()
