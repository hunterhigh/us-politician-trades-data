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
from copy import deepcopy
from urllib.parse import urlsplit

from unison_snapshot.builder import normalize
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.whitehouse_278t_audit import audit_whitehouse_278t
from unison_snapshot.whitehouse_278t_candidate import build_whitehouse_278t_review_candidate
from unison_snapshot.whitehouse_278t import (
    PARSER_VERSION as TRADE_PARSER,
    SUPPORTED_PARSER_VERSIONS as TRADE_PARSERS,
)
from unison_snapshot.oge_278e_public import (PARSER_VERSION as ANNUAL_PARSER,
                                             SCHEMA as ANNUAL_SCHEMA,
                                             SUPPORTED_PARSER_VERSIONS as ANNUAL_PARSERS)


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DOCUMENT = re.compile(r"wh-url:([0-9a-f]{24})\Z")
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


def _verify_review(review_root: Path, *, expected_report_count: int | None) -> tuple[list[dict], list[dict], dict, dict]:
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
            status["pending_count"] < 0):
        raise ValueError("White House extraction status is invalid")
    reports = [row for row in coverage["reports"]
               if row.get("document_type_from_label") == "278t"]
    if (not reports or (expected_report_count is not None and
                        len(reports) != expected_report_count)):
        raise ValueError("White House 278-T report count differs from the expected batch")
    seen_ids = set()
    expected_paths = {}
    expected_folders = set()
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
        folder = Path("whitehouse/extractions") / match[1] / versions[0]
        parser_version = row.get("extraction_parser_version")
        if parser_version is None:
            available = [version for version in TRADE_PARSERS
                         if (review_root / folder /
                             f"{version.replace('/', '-')}.json").is_file()]
            parser_version = available[0] if available else TRADE_PARSER
        if parser_version not in TRADE_PARSERS:
            raise ValueError("White House 278-T extraction parser is unsupported")
        expected_relative = folder / f"{parser_version.replace('/', '-')}.json"
        relative_value = row.get("extraction_path")
        relative = Path(relative_value) if isinstance(relative_value, str) else expected_relative
        if relative != expected_relative:
            raise ValueError("White House 278-T extraction path is invalid")
        expected_paths[relative.as_posix()] = (row, parser_version)
        expected_folders.add(folder.as_posix())
    root = review_root / "whitehouse/extractions"
    supported_names = {f"{version.replace('/', '-')}.json" for version in TRADE_PARSERS}
    for path in root.glob("*/*/*.json"):
        if path.name not in supported_names:
            continue
        relative = path.relative_to(review_root)
        if (relative.as_posix() not in expected_paths and
                relative.parent.as_posix() not in expected_folders):
            raise ValueError("White House 278-T extraction files do not match coverage")
    extractions = []
    manifest = []
    for relative, (row, parser_version) in sorted(expected_paths.items()):
        path = review_root / relative
        if not path.is_file():
            raise ValueError("White House 278-T extraction file is missing")
        extraction, raw_sha = _read_object(path)
        if (extraction.get("document_id") != row["document_id"] or
                extraction.get("parser_version") != parser_version or
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
    annual_extractions = []
    annual_manifest = []
    for row in coverage["reports"]:
        if (not isinstance(row, dict) or
                not str(row.get("document_type_from_label", "")).startswith("278e_") or
                row.get("review_state") not in {"extracted_review_only", "extracted_with_issues"}):
            continue
        document_id = row.get("document_id")
        match = _DOCUMENT.fullmatch(document_id) if isinstance(document_id, str) else None
        versions = row.get("archive_sha256_versions")
        if (match is None or not isinstance(versions, list) or len(versions) != 1 or
                not isinstance(versions[0], str) or not _SHA256.fullmatch(versions[0])):
            raise ValueError("White House annual dedup source is not uniquely archived")
        parser_version = row.get("extraction_parser_version") or ANNUAL_PARSER
        if parser_version not in ANNUAL_PARSERS:
            raise ValueError("White House annual dedup parser is unsupported")
        expected_relative = (Path("whitehouse/extractions") / match[1] / versions[0] /
                             f"{parser_version.replace('/', '-')}.json")
        relative_value = row.get("extraction_path")
        relative = Path(relative_value) if isinstance(relative_value, str) else expected_relative
        if relative != expected_relative:
            raise ValueError("White House annual dedup extraction path is invalid")
        annual, raw_sha = _read_object(review_root / relative)
        if (annual.get("schema_version") != ANNUAL_SCHEMA or
                annual.get("parser_version") != parser_version or
                annual.get("source_url") != row.get("document_url") or
                annual.get("source_sha256") != versions[0]):
            raise ValueError("White House annual dedup extraction is not bound to its PDF")
        annual_extractions.append(annual)
        annual_manifest.append(f"{relative.as_posix()} {raw_sha}")
    provenance["whitehouse_annual_dedup_set_sha256"] = hashlib.sha256(
        "\n".join(sorted(annual_manifest)).encode("utf-8")).hexdigest()
    return extractions, annual_extractions, oge_coverage, provenance


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


def _strip_prior_whitehouse(candidate: dict, audit: dict) -> dict:
    """Recover the hash-bound direct OGE base before rebuilding overlays."""
    base = deepcopy(candidate)
    base["reported_holdings"] = [
        row for row in base["reported_holdings"]
        if not str(row.get("id", "")).startswith("wh-annual:")]
    annual_referenced = {row["person_id"] for name in ("transactions", "reported_holdings")
                         for row in base[name]}
    base["people"] = [row for row in base["people"] if row.get("id") in annual_referenced]
    oge_health = [row for row in base.get("source_health", []) if row.get("source_id") == "oge"]
    if len(oge_health) != 1 or not isinstance(oge_health[0].get("detail"), str):
        raise ValueError("Prior White House OGE health record is invalid")
    oge_health[0]["detail"] = oge_health[0]["detail"].split("; White House annual:", 1)[0]
    if hashlib.sha256(_json_bytes(base)).hexdigest() != audit.get("candidate_sha256"):
        raise ValueError("Prior White House transaction overlay differs from its audit")
    promoted = {row.get("extraction_id") for report in audit.get("reports", [])
                for row in report.get("rows", []) if row.get("status") == "promoted"}
    whitehouse_transactions = {row.get("id") for row in base.get("transactions", [])
                               if urlsplit(row.get("source_url") or "").hostname in
                               {"whitehouse.gov", "www.whitehouse.gov"}}
    if (None in promoted or whitehouse_transactions != promoted or
            len(promoted) != audit.get("promoted_transaction_count")):
        raise ValueError("Prior White House candidate cannot be safely separated")
    base["transactions"] = [row for row in base["transactions"] if row.get("id") not in promoted]
    referenced = {row["person_id"] for name in ("transactions", "reported_holdings")
                  for row in base[name]}
    base["people"] = [row for row in base["people"] if row.get("id") in referenced]
    detail = oge_health[0]["detail"]
    prefix, separator, remainder = detail.partition("Prior direct OGE status: ")
    direct, marker, _ = remainder.partition("; White House public 278-T:")
    if prefix or not separator or not marker or not direct:
        raise ValueError("Prior direct OGE status cannot be recovered")
    oge_health[0]["detail"] = direct
    normalized = normalize(base, allow_production=True, allow_empty_production=True,
                           allow_market=bool(base.get("security_market_data")))
    if normalized != base:
        raise ValueError("Recovered direct OGE candidate changed during normalization")
    if len(base["transactions"]) != audit.get("base_transaction_count"):
        raise ValueError("Recovered direct OGE transaction count differs from prior base")
    return base


def run(*, oge_candidate: Path, review_root: Path, candidate_out: Path,
        audit_out: Path, expected_report_count: int | None = None) -> dict:
    """Validate local evidence, then atomically replace each output file."""
    if expected_report_count is not None and (type(expected_report_count) is not int or
                                              expected_report_count <= 0):
        raise ValueError("Expected White House report count must be positive")
    extractions, annual_extractions, oge_coverage, provenance = _verify_review(
        review_root, expected_report_count=expected_report_count)
    base, base_sha = _read_object(oge_candidate)
    if any(urlsplit(row.get("source_url") or "").hostname in
           {"whitehouse.gov", "www.whitehouse.gov"}
           for row in base.get("transactions", [])):
        if not audit_out.is_file() or candidate_out.resolve() != oge_candidate.resolve():
            raise ValueError("White House rows already exist without an idempotent audit pair")
        prior, _ = _read_object(audit_out)
        if (prior.get("candidate_sha256") == base_sha and
                prior.get("review_input_sha256") == provenance):
            normalize(base, allow_production=True,
                      allow_market=bool(base.get("security_market_data")))
            return {"idempotent": True, "report_count": prior["report_count"],
                    "promoted_transaction_count": prior["promoted_transaction_count"],
                    "quarantined_row_count": prior["quarantined_row_count"]}
        base = _strip_prior_whitehouse(base, prior)
        base_sha = hashlib.sha256(_json_bytes(base)).hexdigest()
    eligibility = audit_whitehouse_278t(extractions, oge_coverage, base, annual_extractions)
    candidate, conservation = build_whitehouse_278t_review_candidate(
        base, extractions, eligibility, oge_coverage,
        data_cutoff_at=base["meta"]["data_cutoff_at"],
        annual_extractions=annual_extractions)
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
