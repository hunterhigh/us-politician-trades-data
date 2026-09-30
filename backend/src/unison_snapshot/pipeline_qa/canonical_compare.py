"""Strict, read-only comparison of shadow observations with pinned review candidates.

A value-only match is diagnostic evidence, never a canonical row binding.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .projection_audit import ProjectionAuditError, audit_bundle
from .diff import load_rows

CORE_FIELDS = ("asset_name", "transaction_date", "transaction_type", "amount_low", "amount_high")
OPTIONAL_EXACT_FIELDS = ("owner", "ticker")


def git_blob_sha1(raw: bytes) -> str:
    """Compute the Git object ID for an exact candidate file."""
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def compare_fixed_candidates(bundle_root: str | Path,
                             candidates: Mapping[str, tuple[str | Path, str, str]]) -> dict[str, Any]:
    """Compare bundle rows to exact review blobs, without creating transactions.

    Each candidate entry is (local path, expected Git blob SHA-1, review commit).
    Callers must independently verify the review commit's tree contains that blob.
    """
    audit = audit_bundle(bundle_root)
    root = Path(bundle_root).resolve()
    rows = load_rows(root / "candidate_rows.json")
    expected_sources = set(audit["source_qualified_counts"])
    if set(candidates) != expected_sources:
        raise ProjectionAuditError("candidate inputs must cover exactly the bundle sources")

    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["disposition"] == "qualified":
            by_source[row["source"]["source_id"]].append(row)

    results = {}
    for source_id in sorted(expected_sources):
        path, expected_blob, review_commit = candidates[source_id]
        raw = Path(path).read_bytes()
        actual_blob = git_blob_sha1(raw)
        if actual_blob != expected_blob:
            raise ProjectionAuditError(f"{source_id}: pinned review candidate blob mismatch")
        if len(review_commit) != 40 or any(c not in "0123456789abcdef" for c in review_commit):
            raise ProjectionAuditError(f"{source_id}: review commit must be a full lowercase SHA")
        candidate = json.loads(raw)
        if candidate.get("meta", {}).get("is_demo") is not False:
            raise ProjectionAuditError(f"{source_id}: review candidate is not production-mode input")
        txs = candidate.get("transactions")
        if not isinstance(txs, list) or any(not isinstance(tx, dict) or tx.get("source_id") != source_id
                                                for tx in txs):
            raise ProjectionAuditError(f"{source_id}: invalid source candidate transactions")
        id_index = defaultdict(list)
        doc_index = defaultdict(list)
        for tx in txs:
            id_index[tx.get("id")].append(tx)
            doc_index[tx.get("filing_id")].append(tx)
        counts: Counter[str] = Counter()
        field_differences: Counter[str] = Counter()
        assigned_values: dict[str, list[str]] = defaultdict(list)
        for row in by_source[source_id]:
            source = row["source"]
            possible = [tx for tx in doc_index[source["document_id"]]
                        if tx.get("source_url") == source["source_url"]]
            if not possible:
                counts["missing_canonical_document"] += 1
                continue
            direct = [tx for tx in id_index[row["candidate_id"]] if tx in possible]
            if len(direct) > 1:
                counts["ambiguous_direct_id"] += 1
                continue
            selected = {o["field"]: o["normalized_value"] for o in row["observations"]
                        if o["selected"]}
            if len(direct) == 1:
                counts["matched_direct_id"] += 1
                for field, value in selected.items():
                    if field in direct[0] and value is not None and direct[0][field] != value:
                        field_differences[field] += 1
                continue
            if any(selected.get(field) is None for field in CORE_FIELDS):
                raise ProjectionAuditError(f"{source_id}: qualified row lacks comparison fields")
            exact = [tx for tx in possible
                     if all(tx.get(field) == selected[field] for field in CORE_FIELDS)
                     and all(tx.get(field) == selected[field] for field in OPTIONAL_EXACT_FIELDS
                             if field in selected and selected[field] is not None)]
            if len(exact) == 1:
                counts["provisional_value_match"] += 1
                assigned_values[exact[0]["id"]].append(row["candidate_id"])
            elif len(exact) > 1:
                counts["ambiguous_value_match"] += 1
            else:
                counts["unmatched_values_in_present_document"] += 1
        reused = {tx_id: row_ids for tx_id, row_ids in assigned_values.items() if len(row_ids) > 1}
        if reused:
            raise ProjectionAuditError(f"{source_id}: value match assigns one canonical transaction more than once")
        results[source_id] = {
            "review_commit": review_commit,
            "candidate_blob_sha1": actual_blob,
            "qualified_rows": len(by_source[source_id]),
            "candidate_transactions": len(txs),
            "counts": dict(sorted(counts.items())),
            "direct_id_field_differences": dict(sorted(field_differences.items())),
            "value_matches_are_bindings": False,
        }
    return {
        "schema_version": "pipeline-fixed-candidate-comparison/v1",
        "bundle_run_id": audit["bundle_run_id"],
        "candidate_id_collisions": audit["candidate_id_collisions"],
        "sources": results,
        "projection_ready": False,
    }
