"""Source-bound OCR observation of an OGE annual report's scanned cover.

This observes form and period text only. It never verifies receipt dates or
promotes annual holdings or Part 7 transactions.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .ocr_geometry import ocr_pdf_pages
from .oge import OgeCatalogError


SCHEMA = "oge-278e-scan-cover-observation/v1"
_SHA = re.compile(r"[0-9a-f]{64}")
_YEAR = re.compile(r"Year\s*\(Annual\s+Report\s+only\)\s*:\s*(20\d{2})", re.I)
_TYPE = re.compile(r"Report\s+Type\s*:\s*Annual\b", re.I)


def observe_native_cover_text(pdf_path: Path, *, source_sha256: str, document_id: str) -> dict:
    """Preserve full original cover text without running cover OCR."""
    if (not isinstance(source_sha256, str) or not _SHA.fullmatch(source_sha256) or
            not isinstance(document_id, str) or not re.fullmatch(r"[0-9a-f]{32}", document_id)):
        raise OgeCatalogError("annual native cover identity is invalid")
    raw = Path(pdf_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != source_sha256:
        raise OgeCatalogError("annual native cover differs from archived PDF")
    import pdfplumber
    with pdfplumber.open(pdf_path) as document:
        if not 1 <= len(document.pages) <= 1200:
            raise OgeCatalogError("annual native cover page count is invalid")
        text = document.pages[0].extract_text() or ""
        count = len(document.pages)
    return {"schema_version": "oge-278e-native-cover-text/v1", "source_document_id": document_id,
        "source_sha256": source_sha256, "page_number": 1, "document_page_count": count,
        "cover_text": text, "cover_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "method": "full_original_cover_embedded_text_no_new_ocr"}


def observe_scan_cover(pdf_path: Path, *, source_sha256: str, document_id: str,
                       catalog_filer: str, catalog_year: int,
                       tesseract: str | None = None) -> dict:
    if not _SHA.fullmatch(source_sha256):
        raise OgeCatalogError("OGE annual scan source hash is invalid")
    data = pdf_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != source_sha256:
        raise OgeCatalogError("OGE annual scan does not match archived hash")
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-4096:]:
        raise OgeCatalogError("OGE annual scan PDF envelope is invalid")
    try:
        import pdfplumber
    except ImportError:
        raise OgeCatalogError("OGE annual scan observation requires pdfplumber") from None
    with pdfplumber.open(pdf_path) as document:
        if not 1 <= len(document.pages) <= 100:
            raise OgeCatalogError("OGE annual scan page count is outside bounds")
        original = document.pages[0].extract_text() or ""
        pages, engine = ocr_pdf_pages(document, executable=tesseract,
                                      resolution=200, page_numbers=[1], max_pages=1)
        lines = pages[0].extract_text_lines()
        text = "\n".join(line["text"] for line in lines)
        selected = []
        for line in lines:
            if not re.search(r"278e|Report\s+Type|Annual|Year\s*\(|"
                             r"Last\s+Name|First\s+Name|Received", line["text"], re.I):
                continue
            selected.append({
                "text": line["text"], "top": round(line["top"], 2),
                "x0": round(min(word["x0"] for word in line["words"]), 2),
                "x1": round(max(word["x1"] for word in line["words"]), 2),
                "ocr_confidence": line["ocr_confidence"],
            })
        years = sorted(set(int(match) for match in _YEAR.findall(text)))
        return {
            "schema_version": SCHEMA,
            "document_id": document_id,
            "source_sha256": source_sha256,
            "catalog_filer": catalog_filer,
            "catalog_year_label": catalog_year,
            "page_count": len(document.pages),
            "cover_page_number": 1,
            "ocr_engine": engine,
            "ocr_resolution_dpi": 200,
            "native_text_sha256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
            "native_text_length": len(original),
            "form_278e_text_observed": bool(re.search(r"\b278e\b", text, re.I)),
            "annual_type_text_observed": bool(_TYPE.search(text)),
            "annual_year_text_candidates": years,
            "selected_cover_lines": selected,
            "receipt_date_verified": False,
            "production_qualification_authorized": False,
            "status": "cover_ocr_observed" if selected else "cover_ocr_unresolved",
        }
