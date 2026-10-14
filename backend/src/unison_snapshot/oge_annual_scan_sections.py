"""Read-only section and physical row observations for archived scanned OGE 278e.

The embedded PDF text layer is used only to locate printed rows and observe
fields.  A missing or malformed row remains unknown; no row is qualified.
"""
from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
import re

from .oge import OgeCatalogError


SCHEMA = "oge-278e-scan-sections/v1"
HARRIS_2023_DOCUMENT_ID = "1462ab8c5542e5e7852589b0007899fc"
HARRIS_2023_SOURCE_SHA256 = "2d8f441b87c97812d2f7ab298657f834b636a14b78594b0cddffbefd8fa1de28"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")
_SECTION = re.compile(r"\bPart\s+([2567])\s*:", re.I)
_ROW = re.compile(r"^([1-9]\d*(?:\.\d+)*)\.?\s*(.*)$")
_TRADE = re.compile(r"^(?P<asset>.+?)\s+(?P<kind>purchase|sale|exchange)\s+"
                    r"(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+"
                    r"(?P<amount>\$[\d,]+\s*[-–]\s*\$[\d,]+)$", re.I)


def _line_text(line: dict) -> str:
    return " ".join(str(line.get("text", "")).split())


def _row_blocks(lines: list[dict], header_index: int) -> list[dict]:
    """Use printed left-column anchors; attach wrapped text to its row."""
    blocks: list[dict] = []
    for line in lines[header_index + 1:]:
        text = _line_text(line)
        if not text:
            continue
        match = _ROW.fullmatch(text) if float(line.get("x0", 1000)) < 55 else None
        if match:
            blocks.append({"number": match[1], "raw_lines": [text],
                           "top": round(float(line["top"]), 2),
                           "x0": round(float(line["x0"]), 2),
                           "bottom": round(float(line["bottom"]), 2)})
        elif blocks:
            blocks[-1]["raw_lines"].append(text)
            blocks[-1]["bottom"] = round(float(line["bottom"]), 2)
    return blocks


def _trade_observation(block: dict) -> dict:
    raw = " ".join(block["raw_lines"])
    body = re.sub(r"^[1-9]\d*\.\s*", "", raw)
    if not body.strip():
        return {"classification": "blank_template", "field_status": "not_applicable"}
    match = _TRADE.fullmatch(body)
    if match is None:
        return {"classification": "filled_unknown", "field_status": "unreadable_or_wrapped"}
    return {"classification": "filled_transaction",
            "field_status": "embedded_text_observed_unverified",
            "asset_raw": match["asset"], "transaction_type_raw": match["kind"],
            "transaction_date_raw": match["date"], "amount_raw": match["amount"]}


def observe_scan_sections(pdf_path: Path, *, source_sha256: str,
                          document_id: str) -> dict:
    if not _SHA.fullmatch(source_sha256) or not _ID.fullmatch(document_id):
        raise OgeCatalogError("OGE annual section source identity is invalid")
    content = pdf_path.read_bytes()
    if hashlib.sha256(content).hexdigest() != source_sha256:
        raise OgeCatalogError("OGE annual section PDF does not match archived hash")
    if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-4096:]:
        raise OgeCatalogError("OGE annual section PDF envelope is invalid")
    try:
        import pdfplumber
    except ImportError:
        raise OgeCatalogError("OGE annual section observation requires pdfplumber") from None
    pages = []
    part7_slots = []
    with pdfplumber.open(pdf_path) as document:
        if not 1 <= len(document.pages) <= 100:
            raise OgeCatalogError("OGE annual section page count is outside bounds")
        for page_number, page in enumerate(document.pages, 1):
            page_text = page.extract_text() or ""
            lines = page.extract_text_lines()
            # The printed section title, not the repeated "Instructions for Part"
            # running header, chooses the section.
            titles = [_SECTION.search(_line_text(line)) for line in lines]
            sections = [f"part{match[1]}" for match in titles if match]
            section = sections[0] if len(set(sections)) == 1 and sections else None
            header_index = next((index for index, line in enumerate(lines)
                                 if _line_text(line).startswith("# Description")), None)
            blocks = (_row_blocks(lines, header_index) if section and
                      header_index is not None else [])
            status = ("section_and_rows_observed" if blocks else
                      "section_header_only" if section else "outside_target_sections")
            item = {"page_number": page_number, "section": section,
                    "page_text_sha256": hashlib.sha256(page_text.encode("utf-8")).hexdigest(),
                    "text_method": "embedded_pdf_text_not_live_ocr",
                    "row_discovery_status": status,
                    "printed_row_anchor_count": len(blocks),
                    "row_numbers": [block["number"] for block in blocks]}
            pages.append(item)
            if section != "part7":
                continue
            numbers = Counter(block["number"] for block in blocks)
            for block in blocks:
                number = block["number"]
                observation = _trade_observation(block)
                if numbers[number] > 1:
                    observation = {"classification": "filled_unknown",
                                   "field_status": "duplicate_printed_number"}
                slot_id = "oge-annual-scan-slot:" + hashlib.sha256(
                    f"{source_sha256}|{page_number}|{number}|{block['top']}".encode()
                ).hexdigest()[:24]
                part7_slots.append({"slot_id": slot_id, "page_number": page_number,
                                    "printed_number": number,
                                    "source_page_text_sha256": item["page_text_sha256"],
                                    "top": block["top"], "bottom": block["bottom"],
                                    "x0": block["x0"],
                                    "raw_lines": block["raw_lines"], **observation})
    section_pages = {part: [page["page_number"] for page in pages
                            if page["section"] == part]
                     for part in ("part2", "part5", "part6", "part7")}
    counts = Counter(slot["classification"] for slot in part7_slots)
    return {"schema_version": SCHEMA, "source_id": "oge",
            "source_document_id": document_id, "source_sha256": source_sha256,
            "page_count": len(pages), "section_pages": section_pages,
            "pages": pages, "part7_slots": part7_slots,
            "part7_slot_counts": dict(sorted(counts.items())),
            "part7_physical_slot_count": len(part7_slots),
            "part7_coverage_status": "observed_embedded_text_only",
            "annual_filing_date_status": "unverified",
            "official_278t_overlap_status": "unresolved",
            "production_eligible_transaction_count": 0,
            "publication_status": "shadow_only_not_promoted"}
