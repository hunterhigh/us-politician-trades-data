"""Build a local OGE review candidate from complete White House 278-T evidence.

Example:
  python backend/scripts/build_whitehouse_278t_candidate.py \
    --oge-candidate /tmp/oge-current.json --review-root /tmp/review \
    --candidate-out /tmp/oge-current.json \
    --audit-out /tmp/whitehouse-278t-conservation.json

All inputs are local.  The command never fetches, pushes, or publishes Git data.
The candidate output may replace the pure OGE input after every gate passes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit

from unison_snapshot.builder import normalize
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.whitehouse_278t_audit import audit_whitehouse_278t
from unison_snapshot.whitehouse_278t_candidate import build_whitehouse_278t_review_candidate


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DOCUMENT = re.compile(r"wh-url:([0-9a-f]{24})\Z")
_EXTRACTION_FILE = "whitehouse-278t-pdf-v1.json"
_COVERAGE_SCHEMA = "whitehouse-public-coverage/v1"
_STATUS_SCHEMA = "whitehouse-public-extraction-status/v1"


def _read_object(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value, hashlib.sha256(raw).hexdigest()


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def _verify_review(review_root: Path, *, expected_report_count: int | None) -> tuple[list[dict], dict, dict]:
    coverage, coverage_sha = _read_object(review_root / "whitehouse/coverage-current.json")
    status, status_sha = _read_object(review_root / "whitehouse/extraction-status.json")
    oge_coverage, oge_coverage_sha = _read_object(
        review_root / "oge/whitehouse/coverage-current.json")
    if (coverage.get("schema_version") != _COVERAGE_SCHEMA or
            coverage.get("source_id") != "whitehouse_public_disclosures" or
            not isinstance(coverage.get("reports"), list) or
            coverage.get("report_link_count") != len(coverage["reports"])):
        raise ValueError("White House public coverage is incomplete or invalid")
    states = Counter(row.get("review_state") for row in coverage["reports"]
                     if isinstance(row, dict))
    if (sum(states.values()) != len(coverage["reports"]) or
            coverage.get("counts_by_review_state") != dict(sorted(states.items()))):
        raise ValueError("White House coverage review-state counts do not close")
    if (status.get("schema_version") != _STATUS_SCHEMA or
            status.get("source_id") != "whitehouse_public_disclosures" or
            type(status.get("pending_count")) is not int or
            status["pending_count"] != 0):
        raise ValueError("White House extraction status has pending or invalid work")
    reports = [row for row in coverage["reports"]
               if row.get("document_type_from_label") == "278t"]
    if (not reports or (expected_report_count is not None and
                        len(reports) != expected_report_count)):
        raise ValueError("White House 278-T report count differs from the expected batch")
    seen_ids = set()
    expected_paths = {}
    for row in reports:
        document_id = row.get("document_id")
        match = _DOCUMENT.fullmatch(document_id) if isinstance(document_id, str) else None
        source_url = row.get("document_url")
        versions = row.get("archive_sha256_versions")
        source = urlsplit(source_url) if isinstance(source_url, str) else urlsplit("")
        if (match is None or document_id in seen_ids or
                not isinstance(source_url, str) or
                document_id != "wh-url:" + hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:24] or
                source.scheme != "https" or source.netloc.casefold() != "www.whitehouse.gov" or
                source.query or source.fragment or
                not source.path.startswith("/wp-content/uploads/") or
                not source.path.lower().endswith(".pdf") or
                not isinstance(versions, list) or len(versions) != 1 or
                not isinstance(versions[0], str) or not _SHA256.fullmatch(versions[0]) or
                row.get("review_state") not in
                {"extracted_review_only", "extracted_with_issues"}):
            raise ValueError("White House 278-T coverage has an unarchived or unextracted report")
        seen_ids.add(document_id)
        relative = (Path("whitehouse/extractions") / match[1] / versions[0] /
                    _EXTRACTION_FILE)
        expected_paths[relative.as_posix()] = row
    root = review_root / "whitehouse/extractions"
    actual_paths = {path.relative_to(review_root).as_posix(): path
                    for path in root.glob(f"*/*/{_EXTRACTION_FILE}")}
    if set(actual_paths) != set(expected_paths):
        raise ValueError("White House 278-T extraction files do not match coverage exactly")
    extractions = []
    manifest = []
    for relative, row in sorted(expected_paths.items()):
        extraction, raw_sha = _read_object(actual_paths[relative])
        if (extraction.get("document_id") != row["document_id"] or
                extraction.get("source_url") != row["document_url"] or
                extraction.get("source_sha256") != row["archive_sha256_versions"][0]):
            raise ValueError("White House 278-T extraction is not bound to coverage and archive SHA")
        extractions.append(extraction)
        manifest.append(f"{relative} {raw_sha}")
    provenance = {
        "whitehouse_coverage_sha256": coverage_sha,
        "whitehouse_extraction_status_sha256": status_sha,
        "oge_whitehouse_coverage_sha256": oge_coverage_sha,
        "whitehouse_extraction_set_sha256": hashlib.sha256(
            "\n".join(manifest).encode("utf-8")).hexdigest(),
    }
    return extractions, oge_coverage, provenance


def _stage(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                     suffix=".tmp", delete=False) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        return Path(handle.name)


def _write_pair(candidate_out: Path, candidate_raw: bytes,
                audit_out: Path, audit_raw: bytes) -> None:
    if candidate_out.resolve() == audit_out.resolve():
        raise ValueError("Candidate and audit output paths must differ")
    temporary = []
    try:
        candidate_temp = _stage(candidate_out, candidate_raw)
        temporary.append(candidate_temp)
        audit_temp = _stage(audit_out, audit_raw)
        temporary.append(audit_temp)
        # If the second replace fails, the original candidate remains in
        # place. A retry recomputes or recognizes the completed pair.
        os.replace(audit_temp, audit_out)
        os.replace(candidate_temp, candidate_out)
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)


def run(*, oge_candidate: Path, review_root: Path, candidate_out: Path,
        audit_out: Path, expected_report_count: int | None = None) -> dict:
    """Validate local evidence, then atomically replace each output file."""
    if expected_report_count is not None and (type(expected_report_count) is not int or
                                              expected_report_count <= 0):
        raise ValueError("Expected White House report count must be positive")
    extractions, oge_coverage, provenance = _verify_review(
        review_root, expected_report_count=expected_report_count)
    base, base_sha = _read_object(oge_candidate)
    if any(urlsplit(row.get("source_url") or "").hostname in
           {"whitehouse.gov", "www.whitehouse.gov"}
           for row in base.get("transactions", [])):
        if not audit_out.is_file() or candidate_out.resolve() != oge_candidate.resolve():
            raise ValueError("White House rows already exist without an idempotent audit pair")
        prior, _ = _read_object(audit_out)
        if (prior.get("candidate_sha256") != base_sha or
                prior.get("review_input_sha256") != provenance):
            raise ValueError("Prior White House candidate differs from current review inputs")
        normalize(base, allow_production=True,
                  allow_market=bool(base.get("security_market_data")))
        return {"idempotent": True, "report_count": prior["report_count"],
                "promoted_transaction_count": prior["promoted_transaction_count"],
                "quarantined_row_count": prior["quarantined_row_count"]}
    eligibility = audit_whitehouse_278t(extractions, oge_coverage, base)
    candidate, conservation = build_whitehouse_278t_review_candidate(
        base, extractions, eligibility, oge_coverage,
        data_cutoff_at=base["meta"]["data_cutoff_at"])
    normalized = normalize(candidate, allow_production=True,
                           allow_market=bool(candidate["security_market_data"]))
    if normalized != candidate:
        raise ValueError("White House OGE candidate changed during normalization")
    candidate_raw = _json_bytes(candidate)
    conservation["review_input_sha256"] = provenance
    conservation["pure_oge_candidate_sha256"] = base_sha
    conservation["candidate_sha256"] = hashlib.sha256(candidate_raw).hexdigest()
    audit_raw = _json_bytes(conservation)
    _write_pair(candidate_out, candidate_raw, audit_out, audit_raw)
    return {"idempotent": False, "report_count": conservation["report_count"],
            "promoted_transaction_count": conservation["promoted_transaction_count"],
            "quarantined_row_count": conservation["quarantined_row_count"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oge-candidate", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, required=True)
    parser.add_argument("--candidate-out", type=Path, required=True)
    parser.add_argument("--audit-out", type=Path, required=True)
    parser.add_argument("--expected-report-count", type=int)
    args = parser.parse_args(argv)
    try:
        result = run(oge_candidate=args.oge_candidate, review_root=args.review_root,
                     candidate_out=args.candidate_out, audit_out=args.audit_out,
                     expected_report_count=args.expected_report_count)
    except (OgeCatalogError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"White House 278-T candidate failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
