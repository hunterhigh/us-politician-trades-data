"""A bounded, read-only observation of one archived Senate paper PTR row.

This is an evidence replay sample, not a paper-report extractor or candidate
source.  The printed row locator was checked against the fixed GIF.  OCR alone
does not establish the form's transaction-type or amount check marks.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess
import tempfile

from .senate import SenateEfdError
from .senate_paper import tesseract_words


EVIDENCE_COMMIT = "80f086267677b8fde34314f24024bdc505d97cb0"
DOCUMENT_ID = "068274e1-4b7a-4453-a242-563dde4c10d8"
PAGE_NUMBER = 2
PAGE_SHA256 = "327fe3996f395a7ad9b5355ff813bcfe33126e8cfdbbb91616655399e8574c89"
PAGE_PATH = (
    f"senate_efd/paper_pages/{DOCUMENT_ID}/{PAGE_NUMBER}/{PAGE_SHA256}.gif"
)
OCR_VERSION = "tesseract v5.4.0.20240606"
ROW_BOUNDS = (2210, 2355)  # Fixed page pixels, row printed as "2" in the GIF.


def _words_in_cell(words: list[dict], left: int, right: int) -> list[dict]:
    chosen = [word for word in words
              if ROW_BOUNDS[0] <= word["top"] + word["height"] / 2 < ROW_BOUNDS[1]
              and left <= word["left"] + word["width"] / 2 < right]
    return sorted(chosen, key=lambda word: (word["top"], word["left"], word["text"]))


def _tokens(words: list[dict]) -> list[dict]:
    return [{key: word[key] for key in ("text", "left", "top", "width", "height", "confidence")}
            for word in words]


def observe_fixed_row(image: bytes, words: list[dict], *, ocr_version: str) -> dict:
    """Retain OCR observations and quarantine this unverified paper row."""
    if hashlib.sha256(image).hexdigest() != PAGE_SHA256:
        raise SenateEfdError("Senate paper sample image hash differs from fixed evidence")
    if image[:6] not in (b"GIF87a", b"GIF89a") or len(image) < 10 or (
        int.from_bytes(image[6:8], "little"), int.from_bytes(image[8:10], "little")
    ) != (3400, 4400):
        raise SenateEfdError("Senate paper sample image dimensions are invalid")
    if ocr_version != OCR_VERSION:
        raise SenateEfdError("Senate paper sample OCR version differs from fixed replay")

    return _record_row(words, ocr_version=ocr_version)


def _record_row(words: list[dict], *, ocr_version: str) -> dict:
    """Build the row record after the image and OCR version are verified."""

    asset_words = _words_in_cell(words, 700, 1385)
    date_words = _words_in_cell(words, 1650, 1940)
    type_words = _words_in_cell(words, 1380, 1665)
    amount_words = _words_in_cell(words, 1930, 2925)
    asset_raw = " ".join(word["text"] for word in asset_words)
    date_raw = " ".join(word["text"] for word in date_words)
    if not asset_raw or not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", date_raw):
        raise SenateEfdError("Senate paper sample asset or date OCR observation missing")
    return {
        "schema_version": "senate-paper-fixed-row-observation/v1",
        "source_id": "senate_efd",
        "evidence_commit": EVIDENCE_COMMIT,
        "document_id": DOCUMENT_ID,
        "page_number": PAGE_NUMBER,
        "page_sha256": PAGE_SHA256,
        "printed_row_number": 2,
        "row_locator_basis": "fixed GIF visual inspection; not machine census",
        "row_bounds_px": list(ROW_BOUNDS),
        "ocr_version": ocr_version,
        "asset_name_ocr": asset_raw,
        "transaction_date_ocr": date_raw,
        "asset_words": _tokens(asset_words),
        "date_words": _tokens(date_words),
        "type_cell_ocr_words": _tokens(type_words),
        "amount_cell_ocr_words": _tokens(amount_words),
        "transaction_type": None,
        "amount_raw": None,
        "qualification_status": "quarantined",
        "quarantine_reasons": [
            "paper_transaction_type_mark_unverified",
            "paper_amount_mark_unverified",
            "paper_report_row_census_incomplete",
        ],
    }


def replay_fixed_row(repo: Path, *, executable: str = "tesseract") -> dict:
    """Read the pinned Git evidence object and replay the exact OCR command."""
    try:
        image = subprocess.run(
            ["git", "show", f"{EVIDENCE_COMMIT}:{PAGE_PATH}"],
            cwd=repo, capture_output=True, check=True, timeout=60,
        ).stdout
        version_output = subprocess.run(
            [executable, "--version"], capture_output=True, text=True,
            check=True, timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise SenateEfdError(f"Senate paper fixed evidence replay failed: {exc}") from None
    version = version_output.splitlines()[0].strip() if version_output else ""
    # Never run OCR on a different page, even if Git returns an unexpected blob.
    if hashlib.sha256(image).hexdigest() != PAGE_SHA256:
        raise SenateEfdError("Senate paper sample image hash differs from fixed evidence")
    with tempfile.TemporaryDirectory(prefix="senate-paper-sample-") as dirname:
        image_path = Path(dirname) / "page.gif"
        image_path.write_bytes(image)
        words = tesseract_words(image_path, executable=executable)
    return observe_fixed_row(image, words, ocr_version=version)
