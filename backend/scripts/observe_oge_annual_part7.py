"""Attach a fixed, read-only Part 7 slot audit to OGE annual review.

The page census and visual transcription are frozen audit fixtures, not live
OCR.  Every input must match the pinned official PDF and v1 extraction bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from unison_snapshot.oge_annual_part7_ledger import observe_annual_part7


DOCUMENT_ID = "40ce0f66f853096985258e27002ddfbb"
SOURCE_SHA = "bcad0b4e58789135b758b5a73fc9584ff4bf9bff9cf52b8e4ca9d4189dbe9e3d"
EXTRACTION_SHA = "52c843f25dd21af193f4fdcdf4faa6c7907dff0780531550e34bf983cc1abbf7"
FIXTURES = {
    "extraction_v2": ("oge_annual_vance_2026_v2.json",
                      "dac37e08e47c4a0b5c3d8406b4eec6e73100d81b831286df2937b959461756ca"),
    "visual_transcription": ("oge_annual_vance_2026_part7_evidence.json",
                             "567cde361b07dddce9d622415d2de36a694ba80f6eda14afd46c92e05ebc908d"),
    "page_census": ("oge_annual_vance_2026_page_census.json",
                    "a074afe715f777a2f35749030aa6f382082ecb833bc4efe590c13f58d7b28226"),
}
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
EXTRACTION_REL = (f"oge/annual/extractions/{DOCUMENT_ID}/{SOURCE_SHA}/"
                  "oge-278e-tables-v1.json")
PDF_REL = f"oge/annual/reports/{DOCUMENT_ID}/{SOURCE_SHA}.pdf"
SIDECAR_REL = (f"oge/annual/part7-observations/{DOCUMENT_ID}/{SOURCE_SHA}/"
               "slot-ledger-v1.json")
STATUS_REL = "oge/annual/part7-observation-status.json"


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_fixture(name: str) -> dict:
    filename, expected_sha = FIXTURES[name]
    path = FIXTURE_ROOT / filename
    if _hash_file(path) != expected_sha:
        raise ValueError(f"fixed audit fixture drift: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def observe_fixed_report(evidence_root: Path, review_root: Path) -> dict:
    """Write sidecar on exact match; otherwise write an explicit status only."""
    evidence = evidence_root.resolve()
    review = review_root.resolve()
    pdf = evidence / PDF_REL
    extracted = review / EXTRACTION_REL
    status = {
        "schema_version": "oge-278e-part7-observation-status/v1",
        "scope": "fixed_vance_2026_annual_only",
        "source_document_id": DOCUMENT_ID,
        "expected_source_sha256": SOURCE_SHA,
        "expected_extraction_sha256": EXTRACTION_SHA,
        "audit_method": "frozen_visual_transcription_and_page_census_not_live_ocr",
        "sidecar_path": None,
        "production_eligible_transaction_count": 0,
    }
    try:
        if not pdf.is_file():
            raise FileNotFoundError("archived_source_pdf_missing")
        actual_pdf_sha = _hash_file(pdf)
        status["observed_source_sha256"] = actual_pdf_sha
        if actual_pdf_sha != SOURCE_SHA:
            raise ValueError("archived_source_pdf_sha_mismatch")
        if not extracted.is_file():
            raise FileNotFoundError("annual_v1_extraction_missing")
        actual_extraction_sha = _hash_file(extracted)
        status["observed_extraction_sha256"] = actual_extraction_sha
        if actual_extraction_sha != EXTRACTION_SHA:
            raise ValueError("annual_v1_extraction_sha_mismatch")
        old = json.loads(extracted.read_text(encoding="utf-8"))
        shadow = _read_fixture("extraction_v2")
        if (old.get("source_sha256") != SOURCE_SHA or
                old.get("source_document_id") != DOCUMENT_ID or
                old.get("transactions") != shadow.get("transactions")):
            raise ValueError("annual_v1_part7_rows_do_not_match_fixed_audit")
        visual = _read_fixture("visual_transcription")
        census = _read_fixture("page_census")
        ledger = observe_annual_part7(shadow, visual, census)
        sidecar = review / SIDECAR_REL
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(ledger, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")), encoding="utf-8")
        status.update({"status": "observed_shadow_only",
                       "sidecar_path": SIDECAR_REL,
                       "filled_transaction_count": ledger["filled_transaction_count"],
                       "blank_template_count": ledger["blank_template_count"],
                       "unaccounted_slot_count": ledger["unaccounted_slot_count"]})
    except (FileNotFoundError, ValueError, json.JSONDecodeError, KeyError) as error:
        status.update({"status": "coverage_unknown", "reason": str(error)})
    target = review / STATUS_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(status, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")), encoding="utf-8")
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(observe_fixed_report(args.evidence_root, args.review_root),
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
