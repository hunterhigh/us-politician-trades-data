"""Reconcile frozen White House annual extraction and candidate without writes to refs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.whitehouse_annual_fixed_replay import (
    DOCUMENT_ID, PDF_SHA256, replay_annual_candidate)


REVIEW = "edd5f8081c3c068dbfc1350179cd00a21d5441a7"
EVIDENCE = "5fe5f0adb3181c5d1552dff3f07336464c3043a7"
STEM = DOCUMENT_ID.removeprefix("wh-url:")
EXTRACTION_PATH = (f"whitehouse/extractions/{STEM}/{PDF_SHA256}/"
                   "whitehouse-278e-hybrid-geometry-v8.json")
ARCHIVE_PATH = f"whitehouse/disclosures/reports/{STEM}/{PDF_SHA256}.json"


def _read(commit: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{commit}:{path}"])


def _oid(commit: str, path: str) -> str:
    oid = subprocess.check_output(
        ["git", "rev-parse", f"{commit}:{path}"], text=True).strip()
    if len(oid) != 40:
        raise ValueError(f"frozen path missing: {path}")
    return oid


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    candidate_path = "candidates/disclosure-current.json"
    candidate = _read(REVIEW, candidate_path)
    extraction = _read(REVIEW, EXTRACTION_PATH)
    audit = json.loads(Path(__file__).resolve().parents[1].joinpath(
        "tests/fixtures/whitehouse_wh_url_8150_document_audit.json").read_bytes())
    checkpoint_tree = "c57ecdcd0e780fa7dc256fae6d0fe728735c8f09"
    if subprocess.check_output(["git", "cat-file", "-t", checkpoint_tree],
                               text=True).strip() != "tree":
        raise ValueError("fixed OCR checkpoint tree is unavailable")
    result = replay_annual_candidate(
        candidate, extraction, audit,
        candidate_blob=_oid(REVIEW, candidate_path),
        extraction_blob=_oid(REVIEW, EXTRACTION_PATH),
        checkpoint_tree=checkpoint_tree)
    archive = json.loads(_read(EVIDENCE, ARCHIVE_PATH))
    if (archive.get("sha256") != PDF_SHA256 or
            archive.get("document_id") != DOCUMENT_ID):
        raise ValueError("fixed archive metadata changed")
    chunks = archive.get("chunks")
    if (not isinstance(chunks, list) or len(chunks) != 9 or
            any(not isinstance(chunk, dict) or not str(
                chunk.get("archive_path", "")).endswith(f".part-{i:03d}.bin")
                for i, chunk in enumerate(chunks))):
        raise ValueError("fixed annual archive chunk layout changed")
    for chunk in chunks:
        _oid(EVIDENCE, chunk["archive_path"])
    result["archive_metadata_blob"] = _oid(EVIDENCE, ARCHIVE_PATH)
    result["archive_chunk_path_count"] = len(chunks)
    result["archive_pdf_byte_verification"] = "not_reverified_this_run"
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)


if __name__ == "__main__":
    main()
