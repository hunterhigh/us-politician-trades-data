"""Source-bound date observations for six annual-catalog OGE records.

The normalized dates below are fixed visual transcriptions of the official
cover images, not OCR guesses.  None is an agency receipt or ``filed_at``.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .oge import OgeCatalogError


SCHEMA = "oge-278e-scan-date-blocks/v1"
# Official PDF SHA-256, cover type, and source-bound visual transcription.
# A null means that this role has no reliably readable date on this cover.
def cover_observations():
    path = Path(__file__).resolve().parents[2] / 'data/source_observations/oge_annual/cover_dates.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    if (value.get('schema_version') != 'reviewed-annual-cover-observations/v1' or
            value.get('qualification_effect') != 'none'):
        raise OgeCatalogError('Annual cover observations cannot qualify filing dates')
    return {doc: (row['source_sha256'], row['cover_kind'], row['dates'])
        for doc, row in value['reports'].items()}


def observe_scan_date_blocks(pdf_path: Path, *, source_sha256: str,
                             document_id: str) -> dict:
    pinned = cover_observations().get(document_id)
    if pinned is None or source_sha256 != pinned[0]:
        raise OgeCatalogError("OGE scanned annual date source is not pinned")
    content = pdf_path.read_bytes()
    if hashlib.sha256(content).hexdigest() != source_sha256:
        raise OgeCatalogError("OGE scanned annual date PDF hash changed")
    if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-4096:]:
        raise OgeCatalogError("OGE scanned annual date PDF envelope is invalid")
    try:
        import pdfplumber
    except ImportError:
        raise OgeCatalogError("OGE scanned annual dates require pdfplumber") from None
    with pdfplumber.open(pdf_path) as document:
        if not 1 <= len(document.pages) <= 100:
            raise OgeCatalogError("OGE scanned annual page count is outside bounds")
        cover = document.pages[0]
        text = cover.extract_text() or ""
        page_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        # These are semantic cover areas, not evidence that a printed date is
        # machine-readable.  Source-specific visual readings are kept separate.
        if pinned[1] == "amendment_letter":
            regions = {"filer_signature": (0.10, 0.55, 0.85, 0.71),
                       "agency_ethics_review": None,
                       "oge_certification": (0.10, 0.72, 0.90, 0.88),
                       "agency_receipt": (0.10, 0.88, 0.90, 0.96)}
        else:
            regions = {"filer_signature": (0.45, 0.40, 0.80, 0.53),
                       "agency_ethics_review": (0.45, 0.52, 0.80, 0.65),
                       "oge_certification": (0.10, 0.71, 0.85, 0.82),
                       "agency_receipt": (0.04, 0.82, 0.96, 0.96)}
        blocks = {}
        for role, region in regions.items():
            value = pinned[2].get(role)
            raw = None
            if region is not None:
                box = (cover.width * region[0], cover.height * region[1],
                       cover.width * region[2], cover.height * region[3])
                raw = cover.crop(box).extract_text() or ""
            blocks[role] = {
                "page_number": 1,
                "page_text_sha256": page_sha,
                "region_fraction": list(region) if region is not None else None,
                "embedded_text_raw": raw,
                "date": value,
                "date_status": ("fixed_visual_transcription_review_only" if value else "unknown"),
                "evidence_method": ("official_pdf_visual_audit" if value else
                                    "cover_region_unresolved"),
            }
    return {"schema_version": SCHEMA, "source_id": "oge",
            "source_document_id": document_id, "source_sha256": source_sha256,
            "cover_kind": pinned[1], "page_count": len(document.pages),
            "cover_page_text_sha256": page_sha, "date_blocks": blocks,
            "agency_receipt_verified": False, "filed_at": None,
            "part7_filing_date_unverified": True,
            "visual_transcription_is_automatic_fact": False,
            "production_eligible_transaction_count": 0,
            "publication_status": "shadow_only_not_promoted"}
