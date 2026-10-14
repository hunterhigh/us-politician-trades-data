"""Source-bound date observations for six annual-catalog OGE records.

The normalized dates below are fixed visual transcriptions of the official
cover images, not OCR guesses.  None is an agency receipt or ``filed_at``.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .oge import OgeCatalogError


SCHEMA = "oge-278e-scan-date-blocks/v1"
# Official PDF SHA-256, cover type, and source-bound visual transcription.
# A null means that this role has no reliably readable date on this cover.
FIXED_COVERS = {
    "1462ab8c5542e5e7852589b0007899fc": (
        "2d8f441b87c97812d2f7ab298657f834b636a14b78594b0cddffbefd8fa1de28",
        "annual_form", {"filer_signature": "2023-05-08",
                        "agency_ethics_review": "2023-05-12",
                        "oge_certification": "2023-05-15"}),
    "59bcdfb898190fac8525884100747a4c": (
        "f39c873856f8efbc93b1c3885fd9354427adfb8b90636edecd206b72e5e1bb77",
        "annual_form", {"filer_signature": "2022-05-06",
                        "agency_ethics_review": "2022-05-10",
                        "oge_certification": "2022-05-12"}),
    "98c022b90c25091e852586d9004a7bfc": (
        "4811d4c9e69e74730fd2354883907745f0939fc28875645ce816c6e447f61a08",
        "amendment_letter", {"filer_signature": "2021-05-18",
                             "agency_ethics_review": None,
                             "oge_certification": "2021-05-18"}),
    "9a9b9318751632fa852586d9002ec0f9": (
        "3e66f57c0650c6e50250d32b5c822aa8460985cf5fb7934b96ce9db665b87238",
        "annual_form", {"filer_signature": "2021-05-17",
                        "agency_ethics_review": "2021-05-17",
                        "oge_certification": "2021-05-17"}),
    "e0e994cfa156d7cf852589b000789259": (
        "8f7a606ddf0016b66b3e5a72bb4cc906b162c798c062ef163e0a2bf8b52974ea",
        "annual_form", {"filer_signature": "2023-05-14",
                        "agency_ethics_review": "2023-05-15",
                        "oge_certification": "2023-05-15"}),
    "e53c72b0e534b2e7852588410074776c": (
        "3cffee2d61c1659fcc9d2845dec47cdd388cf0bc0c23e5ee48eec01a861fb4c3",
        "annual_form", {"filer_signature": None,
                        "agency_ethics_review": "2022-05-10",
                        "oge_certification": "2022-05-12"}),
}


def observe_scan_date_blocks(pdf_path: Path, *, source_sha256: str,
                             document_id: str) -> dict:
    pinned = FIXED_COVERS.get(document_id)
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
