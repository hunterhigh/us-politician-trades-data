"""Offline, commit-pinned Senate electronic PTR shadow ledger.

No branch is written. Every selected report is re-parsed from its archived
HTML and compared with the extraction stored at the fixed review commit.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from .pipeline_ledger import (CANDIDATE_ROW_SCHEMA, OBSERVATION_SCHEMA,
                              RUN_MANIFEST_SCHEMA, idempotency_key,
                              validate_candidate_row, validate_run_manifest)
from .senate_candidate import (_resolve_amendments, _transaction,
                               CANDIDATE_BUILDER_VERSION)
from .senate_reports import (ELECTRONIC_EXTRACTION_SCHEMA, parse_electronic_ptr)

SOURCE_ID = "senate_efd"
PARSER_ID = "senate-efd-electronic-ptr"
RULES_VERSION = CANDIDATE_BUILDER_VERSION
LAYOUT = "senate-efd-html-ptr-table/v1"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


class SenateShadowError(ValueError):
    pass


def _git_blob(repo: Path, commit: str, path: str) -> bytes:
    path = path.replace("\\", "/")
    if (not _SHA.fullmatch(commit) or path.startswith("/") or
            ":" in path or ".." in path.split("/")):
        raise SenateShadowError("invalid fixed commit or artifact path")
    result = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{path}"],
                            capture_output=True)
    if result.returncode:
        raise SenateShadowError(f"fixed Git blob unavailable: {commit}:{path}")
    return result.stdout


def _json_blob(repo: Path, commit: str, path: str) -> dict:
    try:
        item = json.loads(_git_blob(repo, commit, path))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SenateShadowError(f"fixed JSON blob invalid: {path}") from exc
    if not isinstance(item, dict):
        raise SenateShadowError(f"fixed JSON blob is not an object: {path}")
    return item


def _observation(row_number: int, field: str, raw: object, normalized: object,
                 required: bool) -> dict:
    location = {"page": 1, "row_locator": f"transaction-{row_number}",
                "kind": "html_table_cell"}
    return {
        "schema_version": OBSERVATION_SCHEMA,
        "observation_id": f"{row_number}:{field}", "field": field,
        "raw_value": raw, "normalized_value": normalized,
        "method": "embedded_text", "confidence": 1.0, "selected": True,
        "required_for_projection": required, "locations": [location],
        "conditions": {"parser": PARSER_ID},
    }


def build_shadow(repo: Path, *, code_commit: str, evidence_commit: str,
                 review_commit: str, identity_path: str, roster_source_path: str,
                 document_ids: list[str], run_id: str,
                 started_at: str) -> tuple[list[dict], dict]:
    """Return candidate rows and a manifest stub (outputs added by writer)."""
    if not all(_SHA.fullmatch(value) for value in
               (code_commit, evidence_commit, review_commit)):
        raise SenateShadowError("all source commits must be exact 40-character SHAs")
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    if head.returncode or head.stdout.strip() != code_commit:
        raise SenateShadowError("code_commit does not match the executing checkout HEAD")
    dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=normal"],
                           capture_output=True, text=True)
    if dirty.returncode or dirty.stdout.strip():
        raise SenateShadowError("executing checkout has uncommitted code or artifacts")
    if not document_ids or len(set(document_ids)) != len(document_ids) or not all(
            _UUID.fullmatch(value) for value in document_ids):
        raise SenateShadowError("document IDs must be unique Senate UUIDs")
    if not identity_path.startswith("senate_efd/identities/"):
        raise SenateShadowError("identity batch must use its review path")
    identity_batch = _json_blob(repo, review_commit, identity_path)
    if identity_batch.get("schema_version") != "senate-efd-identities/v1":
        raise SenateShadowError("identity batch schema is unsupported")
    roster_sha = identity_batch.get("roster_sha256")
    if (not isinstance(roster_sha, str) or
            roster_source_path != f"senate_efd/members/{roster_sha}.xml" or
            hashlib.sha256(_git_blob(repo, evidence_commit, roster_source_path)).hexdigest() != roster_sha):
        raise SenateShadowError("review identity roster does not bind to fixed evidence")
    identities = identity_batch.get("identities")
    if not isinstance(identities, list):
        raise SenateShadowError("identity batch has no identities")
    selected = [item for item in identities if isinstance(item, dict)
                and item.get("document_id") in document_ids]
    if len(selected) != len(document_ids) or len({x["document_id"] for x in selected}) != len(selected):
        raise SenateShadowError("selected reports do not bind uniquely to review identities")
    if any(item.get("roster_sha256") != roster_sha for item in selected):
        raise SenateShadowError("selected report identity uses a different official roster")
    identity_by_id = {item["document_id"]: item for item in selected}
    extractions = []
    for document_id in sorted(document_ids):
        paths = subprocess.run(
            ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", review_commit,
             "--", f"senate_efd/extractions/{document_id}"],
            capture_output=True, text=True)
        if paths.returncode:
            raise SenateShadowError("cannot list fixed review extraction paths")
        matches = [path for path in paths.stdout.splitlines() if re.fullmatch(
            rf"senate_efd/extractions/{document_id}/[0-9a-f]{{64}}/"
            r"senate-efd-report-parser-[^/]+\.json", path)]
        if len(matches) != 1:
            raise SenateShadowError(f"document {document_id} has no unique fixed extraction")
        extraction = _json_blob(repo, review_commit, matches[0])
        source_sha = extraction.get("source_sha256")
        if (extraction.get("schema_version") != ELECTRONIC_EXTRACTION_SCHEMA or
                extraction.get("document_id") != document_id or
                not isinstance(source_sha, str) or
                matches[0].split("/")[3] != source_sha):
            raise SenateShadowError(f"document {document_id} extraction binding failed")
        prefix = f"senate_efd/reports/{document_id}/{source_sha}"
        metadata = _json_blob(repo, evidence_commit, prefix + ".metadata.json")
        content = _git_blob(repo, evidence_commit, prefix + ".html")
        parsed = parse_electronic_ptr(metadata, content)
        if parsed != extraction:
            raise SenateShadowError(f"document {document_id} reparse differs from fixed review")
        if not extraction.get("evidence_complete") or not isinstance(extraction.get("transactions"), list):
            raise SenateShadowError(f"document {document_id} has incomplete row evidence")
        if identity_by_id[document_id].get("filer_name") != extraction.get("filer_name"):
            raise SenateShadowError(f"document {document_id} filer conflicts with review identity")
        extractions.append(extraction)

    resolution = _resolve_amendments(extractions, identity_by_id)
    superseded = resolution["superseded"]
    unresolved = resolution["unresolved"]
    if superseded & unresolved:
        raise SenateShadowError("amendment resolution is internally inconsistent")
    rows = []
    documents = []
    counts = Counter()
    for extraction in extractions:
        document_id = extraction["document_id"]
        identity = identity_by_id[document_id]
        sha = extraction["source_sha256"]
        parser_version = extraction["parser_version"]
        parser = {"parser_id": PARSER_ID, "parser_version": parser_version,
                  "rules_version": RULES_VERSION, "layout_fingerprint": LAYOUT}
        key = idempotency_key(source_id=SOURCE_ID, source_sha256=sha, **parser)
        documents.append({"source_id": SOURCE_ID, "document_id": document_id,
                          "source_url": extraction["source_url"], "source_sha256": sha,
                          **parser, "idempotency_key": key,
                          "disposition": "parsed" if extraction["transactions"] else "no_rows"})
        for raw in extraction["transactions"]:
            number = raw["row_number"]
            if identity.get("status") != "matched_automatically" or identity.get("match_class") not in {"exact", "alias"}:
                disposition, reasons, fact = "quarantined", ["identity_not_automatically_matched"], None
            elif document_id in unresolved:
                disposition, reasons, fact = "quarantined", ["amendment_relationship_pending"], None
            elif document_id in superseded:
                disposition, reasons, fact = "excluded", ["superseded_by_verified_amendment"], None
            elif extraction.get("portal_listed_date") != _filed_date(extraction.get("filed_at_raw")):
                disposition, reasons, fact = "quarantined", ["filed_date_conflicts_catalog"], None
            else:
                fact, reasons = _transaction(raw, extraction, identity)
                disposition = "quarantined" if reasons else "qualified"
            required = ["transaction_date", "transaction_type", "asset_name",
                        "amount_low", "amount_high"]
            amount_low = fact.get("amount_low") if fact else None
            amount_high = fact.get("amount_high") if fact else None
            observations = [
                _observation(number, "transaction_date", raw.get("transaction_date"), raw.get("transaction_date"), True),
                _observation(number, "transaction_type", raw.get("transaction_type_raw"), raw.get("transaction_type"), True),
                _observation(number, "asset_name", raw.get("asset_name_raw"),
                             fact.get("asset_name") if fact else None, True),
                _observation(number, "amount_low", raw.get("amount_raw"), amount_low, True),
                _observation(number, "amount_high", raw.get("amount_raw"), amount_high, True),
                _observation(number, "owner", raw.get("owner_raw"), fact.get("owner") if fact else None, False),
                _observation(number, "ticker", raw.get("ticker_raw"), raw.get("ticker_raw"), False),
            ]
            location = {"page": 1, "row_locator": f"transaction-{number}",
                        "kind": "html_table_row"}
            candidate = {
                "schema_version": CANDIDATE_ROW_SCHEMA,
                "candidate_id": raw["extraction_id"], "run_id": run_id,
                "source": {"source_id": SOURCE_ID, "document_id": document_id,
                           "source_url": extraction["source_url"], "source_sha256": sha},
                "parser": parser, "idempotency_key": key,
                "disposition": disposition, "reasons": reasons,
                "unresolved_conflicts": [], "required_projection_fields": required,
                "evidence_locations": [location], "observations": observations,
            }
            validate_candidate_row(candidate)
            rows.append(candidate)
            counts[disposition] += 1
    if sum(len(item["transactions"]) for item in extractions) != len(rows):
        raise SenateShadowError("electronic PTR row accounting does not close")
    now = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schema_version": RUN_MANIFEST_SCHEMA, "run_id": run_id,
        "workflow": "senate-efd-electronic-ptr-offline-shadow", "trigger": "replay",
        "code_commit": code_commit, "evidence_commit": evidence_commit,
        "review_commit": review_commit, "identity_path": identity_path,
        "roster_source_path": roster_source_path,
        "started_at": started_at, "completed_at": now,
        "source_scope": [SOURCE_ID], "documents": documents,
        "counts": {
            "discovered_documents": len(documents), "archived_documents": len(documents),
            "parsed_documents": sum(x["disposition"] == "parsed" for x in documents),
            "failed_documents": 0,
            "no_row_documents": sum(x["disposition"] == "no_rows" for x in documents),
            "excluded_documents": 0,
            "qualified_rows": counts["qualified"], "quarantined_rows": counts["quarantined"],
            "excluded_rows": counts["excluded"], "unrecognized_rows": 0,
        },
        "accounted_rows": len(rows), "outputs": [],
        "amendment_resolution": resolution["chains"],
    }
    validate_run_manifest(manifest)
    return rows, manifest


def _filed_date(value: object) -> str | None:
    match = re.fullmatch(r"Filed (\d{2}/\d{2}/\d{4}) @ .+", value) if isinstance(value, str) else None
    if not match:
        return None
    return datetime.strptime(match.group(1), "%m/%d/%Y").date().isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--evidence-commit", required=True)
    parser.add_argument("--review-commit", required=True)
    parser.add_argument("--identity-path", required=True)
    parser.add_argument("--roster-source-path", required=True)
    parser.add_argument("--document-id", action="append", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    started_at = datetime.now(timezone.utc).isoformat()
    rows, manifest = build_shadow(
        args.repo, code_commit=args.code_commit,
        evidence_commit=args.evidence_commit, review_commit=args.review_commit,
        identity_path=args.identity_path, roster_source_path=args.roster_source_path,
        document_ids=args.document_id,
        run_id=args.run_id, started_at=started_at)
    root = args.output_root
    if root.exists():
        raise SenateShadowError("output root already exists")
    root.mkdir(parents=True)
    row_bytes = (json.dumps(rows, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False) + "\n").encode("utf-8")
    (root / "candidate_rows.json").write_bytes(row_bytes)
    manifest["outputs"] = [{"artifact_type": "candidate_rows",
                            "path": "candidate_rows.json",
                            "sha256": hashlib.sha256(row_bytes).hexdigest()}]
    validate_run_manifest(manifest)
    (root / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    print(json.dumps({"run_id": args.run_id, "accounted_rows": len(rows),
                      "counts": manifest["counts"]}, sort_keys=True))


if __name__ == "__main__":
    main()
