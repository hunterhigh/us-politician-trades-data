"""Read-only House PTR shadow ledger from immutable evidence/review Git commits."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

from .codec import encode
from .house_ptr import QUALIFICATION_SCHEMA, SCHEMA
from .pipeline_ledger import (CANDIDATE_ROW_SCHEMA, OBSERVATION_SCHEMA,
                              RUN_MANIFEST_SCHEMA, idempotency_key,
                              validate_candidate_row, validate_run_manifest)

SOURCE = "house_clerk"
RULES_VERSION = "house-qualification-archived/v1"
REQUIRED = ("asset_name", "transaction_date", "transaction_type", "amount_low", "amount_high")
SHA = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")


class HouseShadowError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise HouseShadowError(message)


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if result.returncode:
        raise HouseShadowError(f"Git read failed: {' '.join(args[:2])}")
    return result.stdout


def _json_at(repo: Path, commit: str, path: str) -> dict:
    try:
        value = json.loads(_git(repo, "show", f"{commit}:{path}"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HouseShadowError(f"Invalid archived JSON: {path}") from exc
    _require(isinstance(value, dict), f"Archived JSON is not an object: {path}")
    return value


def _one_path(repo: Path, commit: str, prefix: str, suffix: str,
              *, required: bool = True) -> str | None:
    paths = _git(repo, "ls-tree", "-r", "--name-only", commit, "--", prefix).decode().splitlines()
    matches = [path for path in paths if path.endswith(suffix)]
    _require(len(matches) <= 1, f"Multiple archived versions match {prefix}{suffix}; specify an immutable source SHA")
    if required:
        _require(bool(matches), f"Missing archived input: {prefix}{suffix}")
    return matches[0] if matches else None


def _location(row: dict) -> dict:
    evidence = row.get("evidence")
    _require(isinstance(evidence, dict) and type(evidence.get("page")) is int
             and evidence["page"] > 0, "House row has no page evidence")
    return {"page": evidence["page"], "row_locator": row["extraction_id"],
            "kind": "house_ptr_extracted_row"}


def _observation(row: dict, field: str, location: dict, method: str) -> dict:
    value = row.get(field)
    if field == "amount_low" or field == "amount_high":
        raw = row.get("amount_raw")
    elif field == "transaction_type":
        raw = row.get("transaction_type_raw")
    else:
        raw = value
    return {"schema_version": OBSERVATION_SCHEMA,
            "observation_id": f"{row['extraction_id']}:{field}", "field": field,
            "raw_value": raw, "normalized_value": value, "method": method,
            "confidence": (row.get("ocr_confidence") / 100 if method == "ocr" and
                           type(row.get("ocr_confidence")) in (int, float) else None),
            "selected": True, "required_for_projection": field in REQUIRED,
            "locations": [location], "conditions": {}}


def build_document(metadata: dict, pdf: bytes, extraction: dict | None,
                   qualification: dict | None, failure: dict | None,
                   *, run_id: str) -> tuple[dict, list[dict]]:
    """Bind one exact PDF to its terminal review result; never infer unseen rows."""
    source_sha = hashlib.sha256(pdf).hexdigest()
    _require(metadata.get("sha256") == source_sha, "House PDF hash differs from metadata")
    _require(metadata.get("source_id") == SOURCE, "House metadata source is invalid")
    document_id = metadata.get("document_id")
    _require(isinstance(document_id, str) and document_id.isdigit(), "House document ID is invalid")
    _require(isinstance(metadata.get("source_url"), str) and
             metadata["source_url"].startswith("https://disclosures-clerk.house.gov/"),
             "House source URL is invalid")
    _require(bool(extraction) != bool(failure), "House document needs exactly one extraction or failure")
    rows: list[dict] = []
    if failure is not None:
        _require(qualification is None, "Failed House document cannot have qualification")
        _require(failure.get("schema_version") == "house-ptr-parse-failure/v1" and
                 failure.get("document_id") == document_id and
                 failure.get("source_sha256") == source_sha, "House failure provenance mismatch")
        parser_version = failure.get("parser_version")
        _require(isinstance(parser_version, str) and bool(parser_version), "House failure parser missing")
        parser = {"parser_id": "house-ptr", "parser_version": parser_version,
                  "rules_version": "not_applied_parse_failure", "layout_fingerprint": "unresolved_layout"}
        disposition, reason = "failed", "archived_parse_failure: " + str(failure.get("error") or "unknown")
        row_count_status = "unknown_parse_failed"
    else:
        _require(qualification is not None, "House extraction has no qualification")
        source = extraction.get("source") or {}
        _require(extraction.get("schema_version") == SCHEMA and
                 extraction.get("source_sha256") == source_sha and
                 source.get("source_id") == SOURCE and
                 source.get("document_id") == document_id and
                 source.get("source_url") == metadata["source_url"] and
                 source.get("archive_path") == metadata.get("archive_path"),
                 "House extraction provenance mismatch")
        _require(qualification.get("schema_version") == QUALIFICATION_SCHEMA and
                 qualification.get("source_sha256") == source_sha and
                 qualification.get("document_id") == document_id and
                 qualification.get("parser_version") == extraction.get("parser_version"),
                 "House qualification provenance mismatch")
        extracted = extraction.get("transactions")
        _require(isinstance(extracted, list), "House extraction rows missing")
        ids = [row.get("extraction_id") for row in extracted if isinstance(row, dict)]
        _require(len(ids) == len(extracted) and len(set(ids)) == len(ids) and
                 all(isinstance(value, str) and value for value in ids),
                 "House extraction row IDs are incomplete or duplicated")
        qualified = qualification.get("transactions")
        quarantined = qualification.get("quarantined")
        _require(isinstance(qualified, list) and isinstance(quarantined, list),
                 "House qualification rows missing")
        qualified_by_id = {row.get("id"): row for row in qualified if isinstance(row, dict)}
        quarantine_by_id = {row.get("extraction_id"): row for row in quarantined if isinstance(row, dict)}
        _require(len(qualified_by_id) == len(qualified) and
                 len(quarantine_by_id) == len(quarantined) and
                 set(ids) == set(qualified_by_id) | set(quarantine_by_id) and
                 not (set(qualified_by_id) & set(quarantine_by_id)),
                 "House extracted rows do not conserve qualification dispositions")
        summary = qualification.get("qualification") or {}
        _require(summary.get("qualified_count") == len(qualified) and
                 summary.get("quarantined_count") == len(quarantined),
                 "House qualification summary does not conserve rows")
        _require(not qualified or summary.get("production_eligible") is True,
                 "House qualified rows lack production-eligible qualification")
        parser_version = extraction.get("parser_version")
        _require(isinstance(parser_version, str) and bool(parser_version), "House parser version missing")
        method = "ocr" if str(extraction.get("extraction_method", "")).startswith("tesseract") else "embedded_text"
        parser = {"parser_id": "house-ptr", "parser_version": parser_version,
                  "rules_version": RULES_VERSION,
                  "layout_fingerprint": ("legacy_checkbox" if "legacy" in parser_version else "electronic_ptr")}
        for raw_row in extracted:
            row_id = raw_row["extraction_id"]
            location = _location(raw_row)
            accepted = qualified_by_id.get(row_id)
            blocked = quarantine_by_id.get(row_id)
            if accepted is not None:
                _require(accepted.get("filing_id") == document_id and
                         accepted.get("source_id") == SOURCE and
                         accepted.get("verification_status") == "official_matched" and
                         accepted.get("person_id") ==
                         (qualification.get("identity") or {}).get("person_id") and
                         accepted.get("source_url") == metadata["source_url"] and
                         all(accepted.get(field) == raw_row.get(field) for field in REQUIRED),
                         f"House qualified projection mismatch: {row_id}")
            else:
                _require(blocked.get("source_sha256") == source_sha and
                         isinstance(blocked.get("reasons"), list) and blocked["reasons"],
                         f"House quarantine provenance mismatch: {row_id}")
            candidate = {"schema_version": CANDIDATE_ROW_SCHEMA, "candidate_id": row_id,
                         "run_id": run_id,
                         "source": {"source_id": SOURCE, "document_id": document_id,
                                    "source_url": metadata["source_url"], "source_sha256": source_sha},
                         "parser": parser,
                         "idempotency_key": idempotency_key(source_id=SOURCE, source_sha256=source_sha, **parser),
                         "disposition": "qualified" if accepted is not None else "quarantined",
                         "reasons": [] if accepted is not None else blocked["reasons"],
                         "unresolved_conflicts": [],
                         "required_projection_fields": list(REQUIRED),
                         "evidence_locations": [location],
                         "observations": [_observation(raw_row, field, location, method) for field in REQUIRED],
                         "source_evidence": raw_row.get("evidence")}
            rows.append(validate_candidate_row(candidate))
        explicit_zero = (extraction.get("document_disposition") or {}).get("status") == "explicit_no_transactions"
        _require(bool(extracted) or explicit_zero, "House empty extraction lacks explicit zero declaration")
        if explicit_zero:
            _require(not extracted, "House explicit zero declaration conflicts with extracted rows")
            if summary.get("status") == "qualified_no_transactions" and summary.get("production_eligible") is True:
                disposition, reason = "no_rows", None
            else:
                disposition, reason = "failed", "explicit_zero_identity_or_qualification_unresolved"
        else:
            disposition, reason = "parsed", None
        row_count_status = "observed_extraction"
    key = idempotency_key(source_id=SOURCE, source_sha256=source_sha, **parser)
    document = {"source_id": SOURCE, "document_id": document_id,
                "source_url": metadata["source_url"], "source_sha256": source_sha,
                **parser, "idempotency_key": key, "disposition": disposition,
                "row_count_status": row_count_status}
    if reason:
        document["reason"] = reason
    return document, rows


def build_run(repo: Path, *, code_commit: str, evidence_commit: str, review_commit: str,
              document_ids: list[str], run_id: str, started_at: str) -> tuple[dict, list[dict]]:
    _require(bool(document_ids) and len(set(document_ids)) == len(document_ids),
             "Specify unique House document IDs")
    for commit in (code_commit, evidence_commit, review_commit):
        _require(bool(COMMIT.fullmatch(commit)), "All source commits must be full SHA-1 IDs")
        _require(_git(repo, "rev-parse", f"{commit}^{{commit}}").decode().strip() == commit,
                 "Pinned ref is not a commit")
    _require(_git(repo, "rev-parse", "HEAD").decode().strip() == code_commit,
             "Shadow code_commit must equal the running checkout HEAD")
    _require(not _git(repo, "status", "--porcelain", "--untracked-files=no").strip(),
             "Shadow checkout has uncommitted tracked changes")
    documents, rows = [], []
    for document_id in document_ids:
        _require(bool(re.fullmatch(r"[0-9]+", document_id)), "House document ID is invalid")
        base = f"house_clerk/documents/2026/{document_id}/"
        metadata_path = _one_path(repo, evidence_commit, base, ".json")
        metadata = _json_at(repo, evidence_commit, metadata_path)
        sha = metadata.get("sha256")
        _require(isinstance(sha, str) and bool(SHA.fullmatch(sha)), "House metadata SHA invalid")
        _require(metadata_path.endswith(f"/{sha}.json"), "House metadata path disagrees with SHA")
        pdf_path = metadata_path[:-5] + ".pdf"
        _require(metadata.get("archive_path") == pdf_path, "House metadata PDF path mismatch")
        pdf = _git(repo, "show", f"{evidence_commit}:{pdf_path}")
        stem = f"2026/{document_id}/{sha}.json"
        extraction_path = _one_path(repo, review_commit, "house_clerk/extractions/" + stem,
                                    stem, required=False)
        qualification_path = _one_path(repo, review_commit, "house_clerk/qualifications/" + stem,
                                       stem, required=False)
        failure_path = _one_path(repo, review_commit, "house_clerk/failures/" + stem,
                                 stem, required=False)
        document, document_rows = build_document(
            metadata, pdf,
            _json_at(repo, review_commit, extraction_path) if extraction_path else None,
            _json_at(repo, review_commit, qualification_path) if qualification_path else None,
            _json_at(repo, review_commit, failure_path) if failure_path else None,
            run_id=run_id)
        documents.append(document)
        rows.extend(document_rows)
    counts = {"discovered_documents": len(documents), "archived_documents": len(documents),
              "parsed_documents": sum(d["disposition"] == "parsed" for d in documents),
              "failed_documents": sum(d["disposition"] == "failed" for d in documents),
              "no_row_documents": sum(d["disposition"] == "no_rows" for d in documents),
              "excluded_documents": 0,
              **{f"{status}_rows": sum(r["disposition"] == status for r in rows)
                 for status in ("qualified", "quarantined", "excluded", "unrecognized")}}
    rows_bytes = encode(rows)
    manifest = {"schema_version": RUN_MANIFEST_SCHEMA, "run_id": run_id,
                "workflow": "house-ptr-offline-shadow", "trigger": "replay",
                "code_commit": code_commit, "evidence_commit": evidence_commit,
                "review_commit": review_commit, "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "source_scope": [SOURCE], "documents": documents,
                "counts": counts, "accounted_rows": len(rows),
                "outputs": [{"artifact_type": "candidate_rows", "path": "candidate_rows.json",
                             "sha256": hashlib.sha256(rows_bytes).hexdigest()}]}
    return validate_run_manifest(manifest), rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--evidence-commit", required=True)
    parser.add_argument("--review-commit", required=True)
    parser.add_argument("--document-id", action="append", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    started = datetime.now(timezone.utc).isoformat()
    try:
        manifest, rows = build_run(args.repo, code_commit=args.code_commit,
                                   evidence_commit=args.evidence_commit,
                                   review_commit=args.review_commit,
                                   document_ids=args.document_id, run_id=args.run_id,
                                   started_at=started)
        _require(not args.output_dir.exists(), "Output directory already exists")
        args.output_dir.mkdir(parents=True)
        (args.output_dir / "candidate_rows.json").write_bytes(encode(rows))
        (args.output_dir / "manifest.json").write_bytes(encode(manifest))
    except (HouseShadowError, OSError, ValueError) as exc:
        parser.exit(2, f"House shadow failed: {exc}\n")
    print(json.dumps({"run_id": args.run_id, "documents": len(manifest["documents"]),
                      "rows": len(rows), "counts": manifest["counts"]}))


if __name__ == "__main__":
    main()
