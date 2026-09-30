"""Read-only coverage of fixed source candidates by a verified binding map."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any, Mapping

from .canonical_compare import git_blob_sha1
from .projection_audit import ProjectionAuditError


def audit_candidate_coverage(
    binding_map: Mapping[str, Any],
    candidates: Mapping[str, str | Path],
) -> dict[str, Any]:
    """Count existing transactions outside the shadow bundle's exact bindings.

    `binding_map` is the output of build_binding_map on a fixed bundle. This
    function does not create source facts or a frontend candidate.
    """
    specs = binding_map.get("candidate_inputs")
    matched = binding_map.get("matched_rows")
    if (binding_map.get("schema_version") != "pipeline-fixed-canonical-binding/v1" or
            binding_map.get("projection_ready") is not False or
            not isinstance(specs, dict) or not isinstance(matched, list) or
            set(candidates) != set(specs)):
        raise ProjectionAuditError("fixed binding map or candidate coverage input is invalid")

    bound: dict[str, set[str]] = {source: set() for source in specs}
    for row in matched:
        source = row["scoped_row"][0]
        transaction_id = row["canonical_transaction_id"]
        if (source not in bound or row["review_commit"] != specs[source]["review_commit"] or
                transaction_id in bound[source]):
            raise ProjectionAuditError("binding map repeats or misattributes a canonical ID")
        bound[source].add(transaction_id)

    sources = {}
    total = Counter()
    for source in sorted(specs):
        raw = Path(candidates[source]).read_bytes()
        if git_blob_sha1(raw) != specs[source]["candidate_blob_sha1"]:
            raise ProjectionAuditError(f"{source}: fixed source candidate blob mismatch")
        candidate = json.loads(raw)
        transactions = candidate.get("transactions")
        if (candidate.get("meta", {}).get("is_demo") is not False or
                not isinstance(transactions, list)):
            raise ProjectionAuditError(f"{source}: invalid fixed source candidate")
        by_id = {}
        for transaction in transactions:
            if (not isinstance(transaction, dict) or transaction.get("source_id") != source or
                    not isinstance(transaction.get("id"), str) or
                    transaction["id"] in by_id):
                raise ProjectionAuditError(f"{source}: source candidate ID is invalid or repeated")
            by_id[transaction["id"]] = transaction
        if not bound[source] <= by_id.keys():
            raise ProjectionAuditError(f"{source}: binding refers to missing canonical ID")
        uncovered = [transaction for transaction in transactions
                     if transaction["id"] not in bound[source]]
        channels = Counter("whitehouse_url" if source == "oge" and
                           transaction.get("filing_id", "").startswith("wh-url:")
                           else "other_fixed_candidate" for transaction in uncovered)
        sources[source] = {
            "existing_transactions": len(transactions),
            "bound_to_shadow": len(bound[source]),
            "outside_shadow": len(uncovered),
            "outside_by_channel": dict(sorted(channels.items())),
            "outside_transaction_ids": sorted(transaction["id"] for transaction in uncovered),
        }
        total.update(existing=len(transactions), bound=len(bound[source]),
                     outside=len(uncovered))
    return {
        "schema_version": "pipeline-fixed-candidate-coverage/v1",
        "bundle_run_id": binding_map["bundle_run_id"],
        "counts": dict(total),
        "sources": sources,
        "candidate_coverage_complete": total["outside"] == 0,
        "projection_ready": False,
    }
