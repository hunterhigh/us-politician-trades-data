"""Reconcile native annual holding tables with every parsed row disposition.

Coverage is separate from field qualification and transaction completeness.
Raster content, unmatched table rows and text outside the body stay unknown.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from .oge import OgeCatalogError

SCHEMA = "oge-278e-native-holding-coverage/v1"
METHOD = "source_replayed_native_holding_table_inventory"
HOLDING_PARTS = {"part2", "part5", "part6"}


def cell_inventory(rows: list[list]) -> Counter:
    from .oge_annual import _compact
    return Counter(json.dumps([_compact(cell) for cell in row], ensure_ascii=False,
                              separators=(",", ":")) for row in rows)


def inventory_sha256(rows: list[list]) -> str:
    canonical = sorted(cell_inventory(rows).items())
    return hashlib.sha256(json.dumps(canonical, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def extracted_cells(extraction: dict, number: int, part: str) -> list[list]:
    return [row.get("cells", []) for collection in ("holdings", "excluded", "quarantined")
            for row in extraction.get(collection, [])
            if row.get("page_number") == number and row.get("section") == part]


def observe_native_holding_coverage(pdf_path: Path, extraction: dict) -> dict:
    """Read source tables independently of the parser's detected row list."""
    from .oge_annual import _compact, _header, _section
    import pdfplumber

    raw = Path(pdf_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != extraction.get("source_sha256"):
        raise OgeCatalogError("annual coverage original differs from archived source")
    pages = []
    with pdfplumber.open(pdf_path) as document:
        if len(document.pages) != extraction.get("page_count"):
            raise OgeCatalogError("annual coverage page inventory differs from original")
        for number, page in enumerate(document.pages, 1):
            text = page.extract_text() or ""
            section = _section(text)
            item = {"page_number": number,
                    "page_text_sha256": hashlib.sha256(text.encode()).hexdigest()}
            titles = set(re.findall(r"(?im)^\s*Part\s+([1-9])\s*:", text))
            if section is None:
                item["role"] = ("cover" if number == 1 else
                    "other_disclosure_section" if len(titles) == 1 and titles <= set("13489") else
                    "instructions" if re.search(r"\bInstructions\s+(?:for\s+)?Part\b", text, re.I) else
                    "unknown")
            elif section[0] not in HOLDING_PARTS:
                item.update(role="target_section", section=section[0], status="not_holding_scope")
            else:
                part = section[0]
                item.update(role="target_section", section=part, status="unresolved")
                reasons = []
                if titles != {part[-1]}:
                    reasons.append("section_role_ambiguous")
                if page.images or page.curves or page.annots:
                    reasons.append("nontext_holding_content_unresolved")
                tables = page.find_tables()
                source_rows = []
                if len(tables) != 1:
                    reasons.append("holding_table_geometry_unresolved")
                else:
                    table = tables[0]
                    rows = table.extract()
                    header = _header(rows, part)
                    if header is None:
                        reasons.append("holding_table_header_unresolved")
                    else:
                        index = header[0]
                        source_rows = [row for row in rows[index + 1:]
                                       if any(_compact(cell) for cell in row)]
                        body_top = table.rows[index].bbox[3]
                        left, _, right, bottom = table.bbox
                        outside = [char for char in page.chars if char.get("text", "").strip()
                            and (char["top"] + char["bottom"]) / 2 >= body_top
                            and not (left <= (char["x0"] + char["x1"]) / 2 <= right
                                     and (char["top"] + char["bottom"]) / 2 <= bottom)]
                        if outside:
                            reasons.append("holding_body_text_outside_table")
                        if any(re.search(r"\$\s*\d|\d{1,2}/\d{1,2}/\d{4}", _compact(cell))
                               for row in rows[:index] for cell in row):
                            reasons.append("financial_content_before_holding_header")
                        from .oge_annual_scan_sections import _native_empty_cells
                        empty_proofs = []
                        for row_index, row in enumerate(rows[index + 1:], index + 1):
                            if any(_compact(cell) for cell in row):
                                continue
                            physical = table.rows[row_index]
                            # None denotes an absent/merged column, not a cell.
                            # Require the present cells to tile the complete band.
                            cells = sorted(cell for cell in physical.cells if cell is not None)
                            tiled = (cells and cells[0][0] == left and cells[-1][2] == right
                                     and all(a[2] == b[0] and a[1] == b[1] and a[3] == b[3]
                                             for a, b in zip(cells, cells[1:])))
                            proof = _native_empty_cells(page, cells) if tiled else {"status": "unknown"}
                            if proof["status"] != "source_objects_verified_empty":
                                reasons.append("unnumbered_empty_holding_band_unverified")
                            else:
                                empty_proofs.append({"table_row_index": row_index, **proof})
                        item["verified_empty_bands"] = empty_proofs
                observed = extracted_cells(extraction, number, part)
                if cell_inventory(source_rows) != cell_inventory(observed):
                    reasons.append("holding_source_row_dispositions_differ")
                rejected = any(row.get("page_number") == number and row.get("section") == part
                               for row in extraction.get("quarantined", []))
                item.update(status="unresolved" if reasons else
                            "rows_with_quarantine" if rejected else "rows_accounted",
                    holding_inventory={"source_row_count": len(source_rows),
                        "disposition_row_count": len(observed),
                        "source_cells_sha256": inventory_sha256(source_rows),
                        "disposition_cells_sha256": inventory_sha256(observed),
                        "reasons": sorted(set(reasons))})
            pages.append(item)
    return {"schema_version": SCHEMA, "source_sha256": extraction["source_sha256"],
            "page_count": len(pages), "method": METHOD, "pages": pages}
