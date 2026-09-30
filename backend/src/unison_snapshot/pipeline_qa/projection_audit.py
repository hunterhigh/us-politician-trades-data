"""Read-only readiness audit for a fixed v1 shadow bundle.

The ledger records source observations, not frontend transactions. This module
never manufactures canonical facts or writes production refs.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from unison_snapshot.pipeline_ledger import LedgerValidationError, validate_candidate_row
from .diff import check_conservation, load_manifest, load_rows, verify_manifest_outputs


class ProjectionAuditError(ValueError):
    """A fixed bundle cannot be audited without ambiguous row identity."""


CANONICAL_TRANSACTION_FIELDS = frozenset(
    "id filing_id person_id owner asset_name ticker ticker_mapping_basis "
    "instrument_type option_type strike_price expiration_date transaction_type "
    "transaction_date filed_at amount_low amount_high position_effect "
    "position_effect_basis source_id source source_url verification_status".split()
)
# These need report/identity/revision context. They cannot be guessed from a row.
REQUIRED_EXTERNAL_BINDINGS = (
    "canonical_transaction_id", "person_id", "filing_id", "filed_at",
    "identity_provenance", "revision_disposition", "duplicate_disposition",
)


def scoped_identity(row: dict[str, Any]) -> tuple[str, str, str, str]:
    """Identify a row within an exact official document and byte revision."""
    source = row["source"]
    return (source["source_id"], source["document_id"],
            source["source_sha256"], row["candidate_id"])


def audit_bundle(root: str | Path) -> dict[str, Any]:
    """Verify immutable outputs/accounting, then list blockers to canonical use."""
    root = Path(root).resolve()
    manifest = load_manifest(root / "manifest.json")
    output_check = verify_manifest_outputs(manifest, root, "bundle")
    if output_check["status"] != "passed":
        raise ProjectionAuditError(f"bundle outputs failed integrity: {output_check['failures']}")
    outputs = [entry for entry in manifest["outputs"]
               if entry["artifact_type"] == "candidate_rows"]
    if len(outputs) != 1:
        raise ProjectionAuditError("bundle must declare one candidate_rows output")
    rows = load_rows(root / outputs[0]["path"])
    conservation = check_conservation(rows, manifest, "bundle")
    if conservation["mismatches"] or conservation["invalid_dispositions"]:
        raise ProjectionAuditError("bundle row conservation failed")

    seen_scoped: set[tuple[str, str, str, str]] = set()
    documents = {(doc["source_id"], doc["document_id"], doc["source_sha256"]): doc
                 for doc in manifest["documents"]}
    if len(documents) != len(manifest["documents"]):
        raise ProjectionAuditError("bundle repeats an exact source document")
    by_candidate: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    source_counts: Counter[str] = Counter()
    selected_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for number, row in enumerate(rows, 1):
        try:
            validate_candidate_row(row)
        except LedgerValidationError as exc:
            raise ProjectionAuditError(f"row {number}: {exc}") from exc
        source = row["source"]
        document = documents.get((source["source_id"], source["document_id"],
                                  source["source_sha256"]))
        if (row["run_id"] not in {manifest["run_id"],
                                 *(run["run_id"] for run in manifest.get("source_runs", []))} or
                document is None or document["disposition"] != "parsed" or
                document["source_url"] != source["source_url"] or
                document["idempotency_key"] != row["idempotency_key"]):
            raise ProjectionAuditError(f"row {number}: not bound to a parsed source document")
        if row["disposition"] != "qualified":
            continue
        identity = scoped_identity(row)
        if identity in seen_scoped:
            raise ProjectionAuditError(f"duplicate scoped qualified row: {identity}")
        seen_scoped.add(identity)
        by_candidate[row["candidate_id"]].append(identity)
        source_id = row["source"]["source_id"]
        source_counts[source_id] += 1
        fields = {item["field"] for item in row["observations"]
                  if item["selected"] and item["normalized_value"] is not None}
        selected_counts[source_id].update(fields)

    collisions = [
        {"candidate_id": candidate_id,
         "scoped_rows": [list(identity) for identity in sorted(identities)]}
        for candidate_id, identities in sorted(by_candidate.items())
        if len(identities) > 1
    ]
    return {
        "schema_version": "pipeline-projection-readiness/v1",
        "bundle_run_id": manifest["run_id"],
        "bundle_code_commit": manifest["code_commit"],
        "source_qualified_counts": dict(sorted(source_counts.items())),
        "qualified_rows": len(seen_scoped),
        "unique_candidate_ids": len(by_candidate),
        "candidate_id_collisions": collisions,
        "selected_normalized_field_counts": {
            source: dict(sorted(counts.items()))
            for source, counts in sorted(selected_counts.items())},
        "canonical_transaction_fields": sorted(CANONICAL_TRANSACTION_FIELDS),
        "required_external_bindings": list(REQUIRED_EXTERNAL_BINDINGS),
        "projection_ready": False,
        "blocking_reasons": [
            "ledger_rows_are_observations_without_canonical_person_filing_and_revision_binding",
            "candidate_id_must_not_be_used_as_a_global_transaction_id",
            "official_document_duplicates_and_amendments_need_independent_resolution",
        ],
        "verified_output_count": output_check["checked"],
        "accounted_rows": conservation["actual_accounted_rows"],
    }
