"""Read-only, exact shadow-row to fixed-candidate binding map.

This maps existing canonical IDs for audit. It never constructs new facts or
promotes a shadow observation into a frontend transaction.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any, Mapping

from .canonical_compare import CORE_FIELDS, git_blob_sha1
from .diff import load_rows
from .projection_audit import ProjectionAuditError, audit_bundle, scoped_identity


def _key(item: Mapping[str, Any]) -> tuple[str, str, str]:
    return (item["document_id"], item["source_sha256"], item["candidate_id"])


def build_binding_map(
    bundle_root: str | Path,
    candidates: Mapping[str, tuple[str | Path, str, str]],
    *, oge_identity_audit: Mapping[str, Any] | None = None,
    oge_disposition_audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind every qualified observation or name its fixed exclusion reason.

    Candidate specs are (path, Git blob SHA-1, review commit); callers verify
    that each SHA is a blob in the named fixed review tree. The OGE identity
    and disposition audits must themselves be built from that exact review tree.
    """
    audit = audit_bundle(bundle_root)
    expected_sources = set(audit["source_qualified_counts"])
    if set(candidates) != expected_sources:
        raise ProjectionAuditError("candidate specs must cover bundle qualified sources")
    if "oge" in expected_sources and (oge_identity_audit is None or
                                      oge_disposition_audit is None):
        raise ProjectionAuditError("OGE row identity and disposition audits are required")

    by_source: dict[str, dict[str, list[dict]]] = {}
    candidate_shas = {}
    for source_id, (path, expected_blob, review_commit) in candidates.items():
        raw = Path(path).read_bytes()
        if git_blob_sha1(raw) != expected_blob:
            raise ProjectionAuditError(f"{source_id}: fixed candidate blob mismatch")
        if (len(review_commit) != 40 or
                any(char not in "0123456789abcdef" for char in review_commit)):
            raise ProjectionAuditError(f"{source_id}: review commit is invalid")
        candidate = json.loads(raw)
        transactions = candidate.get("transactions")
        if (candidate.get("meta", {}).get("is_demo") is not False or
                not isinstance(transactions, list) or
                any(not isinstance(tx, dict) or tx.get("source_id") != source_id
                    for tx in transactions)):
            raise ProjectionAuditError(f"{source_id}: fixed candidate is invalid")
        by_id: dict[str, list[dict]] = defaultdict(list)
        for tx in transactions:
            by_id[tx.get("id")].append(tx)
        by_source[source_id] = by_id
        candidate_shas[source_id] = {"review_commit": review_commit,
                                     "candidate_blob_sha1": expected_blob}

    oge_matches: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    oge_missing: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    oge_reasons: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    if "oge" in expected_sources:
        if oge_identity_audit is None or oge_disposition_audit is None:
            raise ProjectionAuditError("OGE fixed audits are required")
        if (oge_identity_audit.get("bundle_run_id") != audit["bundle_run_id"] or
                oge_disposition_audit.get("bundle_run_id") != audit["bundle_run_id"] or
                oge_identity_audit.get("review_commit") != candidates["oge"][2] or
                oge_identity_audit.get("candidate_blob_sha1") != candidates["oge"][1] or
                oge_disposition_audit.get("production_action") != "none"):
            raise ProjectionAuditError("OGE fixed audits do not bind to this bundle and review")
        for item in oge_identity_audit.get("matched_canonical_rows", []):
            key = _key(item)
            if key in oge_matches:
                raise ProjectionAuditError("OGE matched row is repeated")
            oge_matches[key] = item
        for item in oge_identity_audit.get("missing_canonical_rows", []):
            key = _key(item)
            if key in oge_missing or key in oge_matches:
                raise ProjectionAuditError("OGE missing row is repeated or matched")
            oge_missing[key] = item
        for item in oge_disposition_audit.get("rows", []):
            key = _key(item)
            if key in oge_reasons:
                raise ProjectionAuditError("OGE disposition is repeated")
            oge_reasons[key] = item
        if (set(oge_reasons) != set(oge_missing) or
                len(oge_matches) + len(oge_missing) != audit["source_qualified_counts"]["oge"] or
                oge_disposition_audit.get("missing_rows") != len(oge_missing)):
            raise ProjectionAuditError("OGE row bindings and dispositions do not conserve qualified rows")

    rows = load_rows(Path(bundle_root) / "candidate_rows.json")
    matched_rows = []
    blocked_rows = []
    seen_canonical: set[tuple[str, str]] = set()
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        if row["disposition"] != "qualified":
            continue
        source = row["source"]
        source_id = source["source_id"]
        key = (source["document_id"], source["source_sha256"], row["candidate_id"])
        if source_id == "oge" and key in oge_missing:
            reason = oge_reasons[key].get("qualification_reasons")
            if not isinstance(reason, list) or len(reason) != 1:
                raise ProjectionAuditError("OGE missing row lacks one fixed qualification reason")
            blocked_rows.append({"scoped_row": list(scoped_identity(row)),
                                 "reason": reason[0],
                                 "extraction_id": oge_missing[key]["extraction_id"]})
            counts[source_id]["blocked"] += 1
            continue
        if source_id == "oge":
            binding = oge_matches.get(key)
            if binding is None:
                raise ProjectionAuditError("OGE qualified row has no fixed binding")
            canonical_id = binding["canonical_transaction_id"]
            if canonical_id != binding["extraction_id"]:
                raise ProjectionAuditError("OGE canonical ID differs from fixed extraction ID")
        else:
            canonical_id = row["candidate_id"]
        matches = [tx for tx in by_source[source_id].get(canonical_id, [])
                   if tx.get("filing_id") == source["document_id"] and
                      tx.get("source_url") == source["source_url"]]
        if len(matches) != 1:
            raise ProjectionAuditError(f"{source_id}: canonical ID/document/URL is not unique")
        selected = {observation["field"]: observation["normalized_value"]
                    for observation in row["observations"] if observation["selected"]}
        if (any(selected.get(field) is None for field in CORE_FIELDS) or
                any(matches[0].get(field) != value for field, value in selected.items()
                    if field in matches[0] and value is not None)):
            raise ProjectionAuditError(f"{source_id}: selected fact differs from fixed candidate")
        canonical_key = (source_id, canonical_id)
        if canonical_key in seen_canonical:
            raise ProjectionAuditError("two shadow rows bind one canonical transaction")
        seen_canonical.add(canonical_key)
        matched_rows.append({"scoped_row": list(scoped_identity(row)),
                             "canonical_transaction_id": canonical_id,
                             "review_commit": candidates[source_id][2]})
        counts[source_id]["matched"] += 1

    if len(matched_rows) + len(blocked_rows) != audit["qualified_rows"]:
        raise ProjectionAuditError("qualified rows are not conserved by binding map")
    return {
        "schema_version": "pipeline-fixed-canonical-binding/v1",
        "bundle_run_id": audit["bundle_run_id"],
        "bundle_code_commit": audit["bundle_code_commit"],
        "qualified_rows": audit["qualified_rows"],
        "matched_rows": matched_rows,
        "blocked_rows": blocked_rows,
        "counts_by_source": {source: dict(sorted(value.items()))
                             for source, value in sorted(counts.items())},
        "candidate_inputs": candidate_shas,
        "candidate_id_collisions": audit["candidate_id_collisions"],
        "projection_ready": False,
        "reason": "existing canonical IDs are audit bindings, not a new candidate projection",
    }
