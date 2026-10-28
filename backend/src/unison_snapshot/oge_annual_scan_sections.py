"""Read-only section and physical row observations for archived scanned OGE 278e.

The embedded PDF text layer is used only to locate printed rows and observe
fields.  A missing or malformed row remains unknown; no row is qualified.
"""
from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
import re
from math import isfinite

from .oge import OgeCatalogError


SCHEMA = "oge-278e-scan-sections/v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")
_SECTION = re.compile(r"\bPart\s+([2567])\s*:", re.I)
_ROW = re.compile(r"^([1-9]\d*(?:\.\d+)*)\.?\s*(.*)$")
_TRADE = re.compile(r"^(?P<asset>.+?)\s+(?P<kind>purchase|sale|exchange)\s+"
                    r"(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+"
                    r"(?P<amount>\$[\d,]+\s*[-\u2013]\s*\$[\d,]+)$", re.I)


def _native_empty_cells(page, cells: list) -> dict:
    """Prove no PDF marks within complete cells bounded by source rules.

    Native table snapping may put a cell edge anywhere inside a thick rule.
    Use the actual filled-rule interior faces, never a fitted margin.  Image
    pages, annotations, unknown drawings and incomplete boundaries stay unknown.
    """
    boxes = [list(box) for box in cells if box is not None]
    def bounds(obj):
        values = [obj.get(k) for k in ("x0", "top", "x1", "bottom")]
        return values if all(type(v) in (float, int) and isfinite(v) for v in values) else None
    def valid(box):
        return len(box) == 4 and all(type(v) in (float, int) and isfinite(v) for v in box) and box[0] < box[2] and box[1] < box[3]
    if len(boxes) != len(cells) or not boxes or not all(valid(box) for box in boxes):
        return {"status": "unknown", "reason": "cell_geometry_unresolved"}
    def intersects(a, b):
        return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]
    if any(intersects(a, b) for i, a in enumerate(boxes) for b in boxes[i+1:]):
        return {"status": "unknown", "reason": "cell_geometry_overlaps"}
    rules = [bounds(obj) for obj in page.objects.get("rect", [])
             if obj.get("fill") is True and obj.get("stroke") is False and bounds(obj) is not None]
    # pdfplumber's snapped cell coordinates can differ from the original rule
    # face by sub-millipoint rounding. Only matching uses this tolerance; the
    # inspected interiors still end at the original, unexpanded rule faces.
    precision = .001
    interiors = []
    for box in boxes:
        # Internal rules can terminate on the inside face of the outer frame.
        # A table extractor's snapped boundary lies inside that frame, beyond
        # the vertical rule's endpoint. Require a complete interior enclosure
        # below, rather than extending source lines to snapped centre points.
        vertical = [rule for rule in rules if 0 < rule[2]-rule[0] <= 2 and
                    rule[1] < box[3] and rule[3] > box[1]]
        left = [rule for rule in vertical if rule[0] - precision <= box[0] <= rule[2] + precision]
        right = [rule for rule in vertical if rule[0] - precision <= box[2] <= rule[2] + precision]
        if len(left) != 1 or len(right) != 1:
            return {"status": "unknown", "reason": "native_boundary_rules_unresolved", "cell_boxes": boxes}
        x0, x1 = left[0][2], right[0][0]
        horizontal = [rule for rule in rules if 0 < rule[3]-rule[1] <= 2 and
                      rule[0] <= x0 + precision and rule[2] >= x1 - precision]
        top = [rule for rule in horizontal if rule[1] - precision <= box[1] <= rule[3] + precision]
        bottom = [rule for rule in horizontal if rule[1] - precision <= box[3] <= rule[3] + precision]
        if len(top) != 1 or len(bottom) != 1:
            return {"status": "unknown", "reason": "native_boundary_rules_unresolved", "cell_boxes": boxes}
        if any(rule[1] > top[0][3] or rule[3] < bottom[0][1] for rule in (left[0], right[0])):
            return {"status": "unknown", "reason": "native_boundary_rules_unresolved", "cell_boxes": boxes}
        interior = [x0, top[0][3], x1, bottom[0][1]]
        if not valid(interior):
            return {"status": "unknown", "reason": "native_cell_interior_unresolved", "cell_boxes": boxes}
        interiors.append(interior)
    for annotation in page.annots or []:
        box = bounds(annotation)
        if box is None or any(intersects(box, cell) for cell in boxes):
            return {"status": "unknown", "reason": "annotation_may_cover_cells", "cell_boxes": boxes}
    for kind, objects in page.objects.items():
        if kind not in {"char", "rect", "line", "curve", "image"}:
            return {"status": "unknown", "reason": "unsupported_pdf_object_kind", "cell_boxes": boxes}
        for obj in objects:
            box = bounds(obj)
            if box is None:
                return {"status": "unknown", "reason": "pdf_object_geometry_unresolved", "cell_boxes": boxes}
            if kind in {"line", "curve"} or kind == "rect" and obj.get("stroke"):
                width = obj.get("linewidth", 0)
                if type(width) not in (int, float) or not isfinite(width) or width < 0:
                    return {"status": "unknown", "reason": "drawing_width_unresolved", "cell_boxes": boxes}
                box = [box[0]-width/2, box[1]-width/2, box[2]+width/2, box[3]+width/2]
            if any(intersects(box, cell) for cell in interiors):
                return {"status": "unknown", "reason": "source_mark_inside_cell", "object_kind": kind,
                        "object_bbox": box, "cell_boxes": boxes}
    return {"status": "source_objects_verified_empty", "cell_boxes": boxes, "native_interiors": interiors,
            "basis": "no_char_image_annotation_or_nonboundary_drawing_in_original_pdf_cells"}


def observe_holding_pages(pdf_path: Path, *, source_sha256: str,
                          document_id: str, page_numbers: list[int]) -> dict:
    """Read only explicitly selected, already located native holding pages.

    This is an input to the existing annual materializer, not an alternative
    annual parser or an OCR fallback.  Full tables and source text are retained
    so the consumer can replay the same selected pages against archived bytes.
    """
    if (not _SHA.fullmatch(source_sha256) or not _ID.fullmatch(document_id) or
            not isinstance(page_numbers, list) or not page_numbers or len(page_numbers) > 16 or
            any(type(page) is not int or page < 1 for page in page_numbers) or
            len(set(page_numbers)) != len(page_numbers)):
        raise OgeCatalogError("annual selected holding page inventory is invalid")
    content = Path(pdf_path).read_bytes()
    if hashlib.sha256(content).hexdigest() != source_sha256:
        raise OgeCatalogError("annual holding PDF differs from archived hash")
    import pdfplumber
    pages = []
    with pdfplumber.open(pdf_path) as document:
        if not 1 <= len(document.pages) <= 1200 or max(page_numbers) > len(document.pages):
            raise OgeCatalogError("annual selected holding page is outside original")
        for number in sorted(page_numbers):
            page = document.pages[number - 1]
            text = page.extract_text() or ""
            sections = {f"part{match[1]}" for match in _SECTION.finditer(text)}
            if len(sections) != 1 or not sections <= {"part2", "part5", "part6"}:
                raise OgeCatalogError("selected annual page is not a unique holding section")
            tables = page.find_tables()
            extracted = [table.extract() for table in tables]
            empty = []
            for table_index, (table, rows) in enumerate(zip(tables, extracted, strict=True)):
                for row_index, (row, row_text) in enumerate(zip(table.rows, rows, strict=True)):
                    if (len(row_text) >= 6 and re.fullmatch(r"[1-9]\d*(?:\.\d+)*\.?", row_text[0] or "") and
                            not any(row_text[1:])):
                        proof = _native_empty_cells(page, row.cells[1:6])
                        empty.append({"table_index": table_index, "table_row_index": row_index,
                                      "printed_number": row_text[0], **proof})
            pages.append({"page_number": number, "section": next(iter(sections)),
                          "page_text": text, "page_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                          "tables": extracted, "number_only_cell_evidence": empty})
        count = len(document.pages)
    return {"schema_version": "oge-278e-native-holding-pages/v2", "source_document_id": document_id,
            "source_sha256": source_sha256, "document_page_count": count,
            "method": "selected_archived_native_tables_no_new_ocr", "pages": pages}


def verify_part7_empty_slots(pdf_path: Path, sections: dict) -> dict[str, dict]:
    """Verify already located number-only slots against original PDF cells.

    Require matching source text, a unique printed locator in a five-column
    transaction table, and complete empty cells. Missing text proves nothing.
    """
    sha = sections.get("source_sha256")
    if not isinstance(sha, str) or not _SHA.fullmatch(sha) or hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest() != sha:
        raise OgeCatalogError("annual blank-slot original differs from archived source")
    selected = [slot for slot in sections.get("part7_slots", [])
                if re.fullmatch(r"\s*[1-9]\d*\.?\s*", " ".join(slot.get("raw_lines", [])))]
    if not selected:
        return {}
    import pdfplumber
    result = {}
    with pdfplumber.open(pdf_path) as document:
        if len(document.pages) != sections.get("page_count"):
            raise OgeCatalogError("annual blank-slot page inventory differs from original")
        for number in sorted({slot["page_number"] for slot in selected}):
            if type(number) is not int or not 1 <= number <= len(document.pages):
                raise OgeCatalogError("annual blank slot is outside original")
            page = document.pages[number - 1]
            text_sha = hashlib.sha256((page.extract_text() or "").encode()).hexdigest()
            matches = {}
            for table in page.find_tables():
                contents = table.extract()
                header = next((i for i, row in enumerate(contents)
                    if row == ["#", "Description", "Type", "Date", "Amount"]), None)
                if header is None:
                    continue
                for cells, row in zip(contents[header + 1:], table.rows[header + 1:], strict=True):
                    if len(cells) != 5 or any(cells[1:]):
                        continue
                    match = re.fullmatch(r"([1-9]\d*)\.?", str(cells[0]).strip())
                    if match:
                        matches.setdefault(match[1], []).append((row, cells[0]))
            for slot in (s for s in selected if s["page_number"] == number):
                if slot.get("source_page_text_sha256") != text_sha:
                    raise OgeCatalogError("annual blank-slot source page text differs from original")
                rows = matches.get(slot.get("printed_number"), [])
                if len(rows) != 1:
                    continue
                row, printed = rows[0]
                locator = row.cells[0]
                if locator is None or not locator[1] <= slot["top"] < slot["bottom"] <= locator[3]:
                    continue
                proof = _native_empty_cells(page, row.cells[1:])
                if proof.get("status") == "source_objects_verified_empty":
                    result[slot["slot_id"]] = {**proof, "source_sha256": sha,
                        "source_page_text_sha256": text_sha, "page_number": number,
                        "printed_number": str(printed), "slot_id": slot["slot_id"]}
    return result


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
