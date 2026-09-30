"""Read-only, source-bound replay of legacy OGE candidate rows outside the shadow run."""
from __future__ import annotations

from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory

from unison_snapshot.oge_reports import parse_archived_pdf
from unison_snapshot.oge_transaction_adapter import adapt_oge_278t_extraction


SCHEMA = "oge-legacy-gap-replay/v1"
FIXED_CANDIDATE_SHA256 = "432440d25925813792c22a788c631739bf3bc0e97423e03e30d929dc0b67237f"
FIXED_COVERAGE_SHA256 = "4aa03b58a891246aa8162ed8bb064da86c66260cb58e00259005f4da82826c05"
DOCUMENT_IDS = (
    "15fba8df3b20c726852585fd0027e8b9",
    "3d0b2362b045514a852585fd0027e8b7",
    "55011bdfb915ddb2852585fd0027e8bc",
    "6fbf27dfe659d38085258618002d5f5a",
    "da923a207578947e852586040027e676",
)
_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_SHA64 = re.compile(r"[0-9a-f]{64}\Z")


class LegacyGapError(ValueError):
    """A pinned artifact or row mapping does not close."""


def _git_bytes(repo: Path, commit: str, path: str) -> bytes:
    if not _SHA40.fullmatch(commit) or path.startswith("/") or ".." in Path(path).parts:
        raise LegacyGapError("invalid pinned Git object reference")
    result = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{path}"],
                            capture_output=True, check=False)
    if result.returncode:
        raise LegacyGapError(f"pinned Git object unavailable: {path}")
    return result.stdout


def _read_json(raw: bytes, label: str) -> dict:
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise LegacyGapError(f"{label} is not JSON") from exc
    if not isinstance(value, dict):
        raise LegacyGapError(f"{label} must be an object")
    return value


def _transaction_fields(row: dict) -> tuple:
    return tuple(row.get(key) for key in (
        "asset_name", "ticker", "owner", "transaction_date", "transaction_type",
        "amount_low", "amount_high"))


def audit_legacy_oge_gap(*, repo: Path, candidate_path: Path, coverage_path: Path,
                         evidence_commit: str, review_commit: str) -> dict:
    """Re-hash fixed PDFs, replay current parser, and reconcile all 124 old IDs.

    The output is an audit. It does not change candidate status or authorize
    publication. Any parser drift or unmatched legacy ID raises an error.
    """
    if not all(isinstance(value, str) and _SHA40.fullmatch(value)
               for value in (evidence_commit, review_commit)):
        raise LegacyGapError("full immutable evidence and review commits are required")
    candidate_bytes = candidate_path.read_bytes()
    coverage_bytes = coverage_path.read_bytes()
    if (hashlib.sha256(candidate_bytes).hexdigest() != FIXED_CANDIDATE_SHA256 or
            hashlib.sha256(coverage_bytes).hexdigest() != FIXED_COVERAGE_SHA256):
        raise LegacyGapError("fixed candidate or coverage byte hash differs")
    candidate = _read_json(candidate_bytes, "candidate")
    coverage = _read_json(coverage_bytes, "coverage")
    if (candidate.get("meta", {}).get("is_demo") is not False or
            coverage.get("schema_version") != "pipeline-fixed-candidate-coverage/v1" or
            coverage.get("sources", {}).get("oge", {}).get("outside_by_channel", {}).get(
                "other_fixed_candidate") != 124):
        raise LegacyGapError("fixed candidate or coverage input is invalid")
    outside = set(coverage["sources"]["oge"]["outside_transaction_ids"])
    old_by_doc: dict[str, list[dict]] = {key: [] for key in DOCUMENT_IDS}
    for row in candidate.get("transactions", []):
        if row.get("id") in outside and not str(row.get("filing_id", "")).startswith("wh-url:"):
            if row.get("filing_id") not in old_by_doc or row.get("source_id") != "oge":
                raise LegacyGapError("non-White-House outside row has an unexpected document")
            old_by_doc[row["filing_id"]].append(row)
    if sum(map(len, old_by_doc.values())) != 124:
        raise LegacyGapError("124 legacy candidate rows did not close")

    documents = []
    all_ids: set[str] = set()
    totals: Counter[str] = Counter()
    for document_id in DOCUMENT_IDS:
        prefix = f"oge/reports/{document_id}/"
        tree = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only",
                               evidence_commit, "--", prefix], capture_output=True,
                              text=True, check=True)
        paths = tree.stdout.splitlines()
        metadata_paths = [path for path in paths if path.endswith(".json")]
        pdf_paths = [path for path in paths if path.endswith(".pdf")]
        if len(metadata_paths) != 1 or len(pdf_paths) != 1:
            raise LegacyGapError(f"{document_id}: immutable archive pair missing")
        metadata_raw = _git_bytes(repo, evidence_commit, metadata_paths[0])
        metadata = _read_json(metadata_raw, "archive metadata")
        pdf_raw = _git_bytes(repo, evidence_commit, pdf_paths[0])
        pdf_sha = hashlib.sha256(pdf_raw).hexdigest()
        if (not _SHA64.fullmatch(pdf_sha) or metadata.get("document_id") != document_id or
                metadata.get("sha256") != pdf_sha or metadata.get("byte_length") != len(pdf_raw) or
                metadata.get("archive_path") != pdf_paths[0]):
            raise LegacyGapError(f"{document_id}: PDF bytes differ from archive metadata")
        extraction_path = (f"oge/extractions/{document_id}/{pdf_sha}/"
                           "oge-278t-pdf-v2.json")
        old_extract_raw = _git_bytes(repo, review_commit, extraction_path)
        old_extract = _read_json(old_extract_raw, "review extraction")
        if (old_extract.get("source_sha256") != pdf_sha or
                old_extract.get("document_id") != document_id or
                old_extract.get("source_url") != metadata.get("document_url")):
            raise LegacyGapError(f"{document_id}: review extraction is not source-bound")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / pdf_paths[0]).parent.mkdir(parents=True)
            (root / pdf_paths[0]).write_bytes(pdf_raw)
            (root / metadata_paths[0]).write_bytes(metadata_raw)
            replay = parse_archived_pdf(root, root / metadata_paths[0])
        if any((old_extract[key] != [{field: item.get(field) for field in old_row}
                                      for item, old_row in zip(replay.get(key, []), old_extract[key])]
                if key in {"transactions", "quarantined"} and
                isinstance(old_extract[key], list) and
                len(old_extract[key]) == len(replay.get(key, []))
                else old_extract[key] != replay.get(key)) for key in old_extract):
            raise LegacyGapError(f"{document_id}: current parser differs from fixed review extraction")
        catalog = {
            "source_id": "oge", "access_method": "direct_pdf",
            "document_type": "278_transaction", "source_document_id": document_id,
            "document_url": metadata["document_url"], "filer_name": metadata["filer_name"],
            "agency": metadata["agency"], "position_title": metadata["position_title"],
            "catalog_added_date": metadata["catalog_added_date"],
            "amended_label": metadata.get("amended_label"),
            "pending_final_oge_disposition": metadata.get("pending_final_oge_disposition"),
        }
        ledger = adapt_oge_278t_extraction(replay, catalog, run_id="oge-legacy-fixed-replay")
        old_rows = old_by_doc[document_id]
        by_id = {row.get("extraction_id"): row for row in replay["transactions"]}
        if len(by_id) != len(replay["transactions"]) or set(by_id) != {row["id"] for row in old_rows}:
            raise LegacyGapError(f"{document_id}: fixed candidate IDs differ from source extraction")
        for old in old_rows:
            source = by_id[old["id"]]
            if (_transaction_fields(old) != _transaction_fields(source) or
                    old.get("source_url") != metadata["document_url"] or
                    old.get("filed_at") != f'{replay["filed_at"]}T00:00:00Z'):
                raise LegacyGapError(f"{document_id}: candidate fact differs from fixed extraction")
            if old["id"] in all_ids:
                raise LegacyGapError("legacy transaction ID repeats across reports")
            all_ids.add(old["id"])
        if ledger["counts"]["qualified"] != len(old_rows):
            raise LegacyGapError(f"{document_id}: ledger qualification differs from old candidate")
        totals.update(ledger["counts"])
        quarantine_reasons = Counter(reason for row in ledger["rows"]
                                     if row["disposition"] == "quarantined"
                                     for reason in row["reasons"])
        documents.append({
            "document_id": document_id, "pdf_sha256": pdf_sha,
            "pdf_byte_length": len(pdf_raw), "review_extraction_sha256": hashlib.sha256(
                old_extract_raw).hexdigest(),
            "candidate_row_count": len(old_rows), "candidate_transaction_ids": sorted(allrow["id"] for allrow in old_rows),
            "replayed_source_row_count": ledger["row_count"], "replayed_dispositions": ledger["counts"],
            "replayed_quarantine_reasons": dict(sorted(quarantine_reasons.items())),
            "parser_version": replay["parser_version"],
        })
    if len(all_ids) != 124:
        raise LegacyGapError("legacy transaction IDs did not conserve")
    return {
        "schema_version": SCHEMA, "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "coverage_sha256": hashlib.sha256(coverage_bytes).hexdigest(),
        "evidence_commit": evidence_commit, "review_commit": review_commit,
        "candidate_row_count": len(all_ids), "source_row_count": sum(
            item["replayed_source_row_count"] for item in documents),
        "dispositions": dict(sorted(totals.items())), "documents": documents,
        "production_refs_written": False, "projection_ready": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--coverage", required=True, type=Path)
    parser.add_argument("--evidence-commit", required=True)
    parser.add_argument("--review-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit_legacy_oge_gap(
        repo=args.repo, candidate_path=args.candidate, coverage_path=args.coverage,
        evidence_commit=args.evidence_commit, review_commit=args.review_commit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
