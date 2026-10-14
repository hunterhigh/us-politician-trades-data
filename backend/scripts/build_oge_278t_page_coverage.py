"""Bounded, read-only page coverage for a fixed archived OGE 278-T layout."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

from unison_snapshot.oge_278t_page_shadow import inspect_pdf
from unison_snapshot.oge_reports import (
    TRUMP_SEPT_2026_DOCUMENT_ID, TRUMP_SEPT_2026_SOURCE_SHA256,
)

SCHEMA = "oge-278t-page-coverage-sidecar/v1"
PAGE = 7


def _save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")), encoding="utf-8")


def build_sidecars(batch: dict, archive_root: Path, extraction_dir: Path,
                   output_dir: Path, review_root: Path) -> dict:
    """Inspect only the immutable source already selected in this archive batch."""
    if (batch.get("schema_version") != "oge-278t-archive-batch/v1" or
            not isinstance(batch.get("reports"), list)):
        raise ValueError("OGE archive batch is invalid")
    selected = [row for row in batch["reports"] if
                isinstance(row, dict) and
                row.get("document_id") == TRUMP_SEPT_2026_DOCUMENT_ID and
                row.get("sha256") == TRUMP_SEPT_2026_SOURCE_SHA256]
    if len(selected) > 1:
        raise ValueError("fixed OGE source occurs more than once in batch")
    existing_sidecar = (review_root / "oge" / "page_coverage" /
                        TRUMP_SEPT_2026_DOCUMENT_ID /
                        f"{TRUMP_SEPT_2026_SOURCE_SHA256}.json")
    if existing_sidecar.is_file():
        previous = json.loads(existing_sidecar.read_text(encoding="utf-8"))
        if (previous.get("schema_version") != SCHEMA or
                previous.get("source_sha256") != TRUMP_SEPT_2026_SOURCE_SHA256 or
                previous.get("selected_page_numbers") != [PAGE] or
                previous.get("production_mutation_authorized") is not False):
            raise ValueError("existing OGE page coverage sidecar is invalid")
        return {"schema_version": "oge-278t-page-coverage-batch/v1",
                "selected_count": len(selected), "sidecar_count": 0,
                "failure_count": 0, "backfilled_count": 0,
                "skipped_existing_sidecar_count": 1,
                "official_transaction_change": 0}
    backfilled = 0
    if not selected:
        metadata_path = (archive_root / "oge" / "reports" /
                         TRUMP_SEPT_2026_DOCUMENT_ID /
                         f"{TRUMP_SEPT_2026_SOURCE_SHA256}.json")
        if metadata_path.is_file():
            selected = [json.loads(metadata_path.read_text(encoding="utf-8"))]
            backfilled = 1
    summary = {"schema_version": "oge-278t-page-coverage-batch/v1",
               "selected_count": len(selected), "sidecar_count": 0,
               "failure_count": 0, "backfilled_count": backfilled,
               "skipped_existing_sidecar_count": 0,
               "official_transaction_change": 0}
    for metadata in selected:
        base = output_dir / TRUMP_SEPT_2026_DOCUMENT_ID
        target = base / f"{TRUMP_SEPT_2026_SOURCE_SHA256}.json"
        try:
            expected_archive_path = (
                f"oge/reports/{TRUMP_SEPT_2026_DOCUMENT_ID}/"
                f"{TRUMP_SEPT_2026_SOURCE_SHA256}.pdf")
            if (metadata.get("schema_version") != "oge-278t-archive/v1" or
                    metadata.get("archive_path") != expected_archive_path or
                    metadata.get("source_id") != "oge"):
                raise ValueError("fixed OGE archive metadata is invalid")
            pdf_path = archive_root / expected_archive_path
            if hashlib.sha256(pdf_path.read_bytes()).hexdigest() != \
                    TRUMP_SEPT_2026_SOURCE_SHA256:
                raise ValueError("archived OGE PDF hash differs")
            extraction_path = extraction_dir / f"{TRUMP_SEPT_2026_DOCUMENT_ID}.json"
            if not extraction_path.is_file():
                extraction_path = (review_root / "oge" / "extractions" /
                                   TRUMP_SEPT_2026_DOCUMENT_ID /
                                   TRUMP_SEPT_2026_SOURCE_SHA256 /
                                   "oge-278t-pdf-v6.json")
            extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
            if (extraction.get("document_id") != TRUMP_SEPT_2026_DOCUMENT_ID or
                    extraction.get("source_sha256") != TRUMP_SEPT_2026_SOURCE_SHA256):
                raise ValueError("OGE extraction source differs")
            # The immutable PDF's OCR text layer supplies number anchors.
            # Its rows do not carry individual hashes; the enclosing PDF and
            # extraction are both checked above before reconciliation.
            if pdfplumber is None:
                raise RuntimeError("pdfplumber is required for fixed OGE page coverage")
            with pdfplumber.open(pdf_path) as document:
                page = document.pages[PAGE - 1]
                anchors = [{"page_number": PAGE,
                            "geometry_top": float(word["top"]),
                            "raw_number": str(word["text"])}
                           for word in page.extract_words()
                           if float(word["x0"]) < float(page.width) * .2 and
                           re.fullmatch(r"[1-9][0-9]*", str(word["text"]))]
            page_ledger = inspect_pdf(
                pdf_path, page_numbers=[PAGE], raster_ocr_records=anchors,
                raster_ocr_source_sha256=TRUMP_SEPT_2026_SOURCE_SHA256)
            coverage = page_ledger["raster_ocr_coverage"]
            sidecar = {"schema_version": SCHEMA,
                       "source_id": "oge", "document_id": TRUMP_SEPT_2026_DOCUMENT_ID,
                       "source_sha256": TRUMP_SEPT_2026_SOURCE_SHA256,
                       "extraction_parser_version": extraction.get("parser_version"),
                       "selected_page_numbers": [PAGE],
                       "observation_basis": "archived_pdf_text_layer_number_anchors",
                       "page_ledger": page_ledger,
                       "physical_slot_count": coverage["physical_slot_count"],
                       "number_anchor_count": len(anchors),
                       "unmatched_slot_count": sum(
                           slot["status"] == "physical_slot_without_ocr_row"
                           for slot in coverage["slots"]),
                       "production_mutation_authorized": False}
            _save(target, sidecar)
            summary["sidecar_count"] += 1
        except Exception as exc:
            error = str(exc)
            error_key = hashlib.sha256(error.encode()).hexdigest()[:12]
            _save(base / f"{TRUMP_SEPT_2026_SOURCE_SHA256}.failure-{error_key}.json", {
                "schema_version": "oge-278t-page-coverage-failure/v1",
                "document_id": TRUMP_SEPT_2026_DOCUMENT_ID,
                "source_sha256": TRUMP_SEPT_2026_SOURCE_SHA256,
                "selected_page_numbers": [PAGE], "error": error,
                "production_mutation_authorized": False,
            })
            summary["failure_count"] += 1
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--extractions-dir", required=True, type=Path)
    parser.add_argument("--review-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    args = parser.parse_args()
    batch = json.loads(args.batch.read_text(encoding="utf-8"))
    result = build_sidecars(batch, args.archive, args.extractions_dir,
                            args.output_dir, args.review_root)
    _save(args.summary, result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
