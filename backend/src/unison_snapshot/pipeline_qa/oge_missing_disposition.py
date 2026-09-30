"""Explain missing OGE canonical IDs from fixed review qualification evidence."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any

from .canonical_compare import git_blob_sha1
from .projection_audit import ProjectionAuditError


DISCOVERY_PATH = ("oge/discoveries/"
                  "0b93cb104ceee9f017016c0fc1ecbfb9c7a4f49962c8c61517b8be33251f5a01.json")


def _identity(record: dict[str, Any]) -> tuple[str, str, str]:
    return tuple(" ".join(record[field].casefold().replace(",", " ").split())
                 for field in ("filer_name", "agency", "position_title"))


def explain_missing_rows(identity_audit: dict, qualification: dict,
                         catalog: dict, candidate: dict) -> dict[str, Any]:
    """Require every missing extraction ID to have exactly one quarantine reason."""
    missing = identity_audit.get("missing_canonical_rows")
    reports = qualification.get("reports")
    catalog_records = catalog.get("transactions")
    canonical = candidate.get("transactions")
    if not all(isinstance(value, list) for value in (missing, reports, catalog_records, canonical)):
        raise ProjectionAuditError("OGE disposition inputs are incomplete")
    by_report = {row["document_id"]: row for row in reports}
    if len(by_report) != len(reports):
        raise ProjectionAuditError("qualification reports duplicate a document ID")
    by_catalog: dict[str, list[dict]] = defaultdict(list)
    variants: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for record in catalog_records:
        if record.get("access_method") != "direct_pdf":
            continue
        by_catalog[record["source_document_id"]].append(record)
        variants[_identity(record)[0]].add(_identity(record))
    by_canonical: dict[str, list[dict]] = defaultdict(list)
    for transaction in canonical:
        by_canonical[transaction.get("id")].append(transaction)
    collisions = identity_audit.get("candidate_id_collisions", [])
    peers: dict[tuple[str, str], tuple[str, str]] = {}
    for collision in collisions:
        scoped = collision["scoped_rows"]
        if len(scoped) != 2:
            raise ProjectionAuditError("candidate ID collision is not a documented pair")
        for left, right in ((scoped[0], scoped[1]), (scoped[1], scoped[0])):
            peers[(left[1], left[3])] = (right[1], right[2])

    explained = []
    by_document: dict[str, list[dict]] = defaultdict(list)
    reason_counts: Counter[str] = Counter()
    seen_missing: set[tuple[str, str, str]] = set()
    for row in missing:
        document_id = row["document_id"]
        source_sha = row["source_sha256"]
        extraction_id = row["extraction_id"]
        key = (document_id, source_sha, extraction_id)
        if key in seen_missing:
            raise ProjectionAuditError("missing OGE extraction ID is repeated")
        seen_missing.add(key)
        report = by_report.get(document_id)
        catalog_matches = by_catalog.get(document_id, [])
        if report is None or len(catalog_matches) != 1:
            raise ProjectionAuditError(f"{document_id}: fixed qualification/catalog binding missing")
        catalog_record = catalog_matches[0]
        quarantine = [item for item in report["quarantined"]
                      if item.get("extraction_id") == extraction_id]
        if len(quarantine) != 1 or report["promoted_count"] != 0:
            raise ProjectionAuditError(f"{document_id}: missing ID lacks unique quarantine")
        reasons = quarantine[0]["reasons"]
        if not isinstance(reasons, list) or not reasons:
            raise ProjectionAuditError(f"{document_id}: quarantine has no reason")
        source_candidate = [tx for tx in by_canonical[extraction_id]
                            if tx.get("filing_id") == document_id]
        if source_candidate:
            raise ProjectionAuditError(f"{document_id}: ID is actually in fixed candidate")
        peer_document_id = None
        if "extraction_id_invalid_or_duplicated" in reasons:
            peer = peers.get((document_id, row["candidate_id"]))
            if peer is None or peer[1] != source_sha:
                raise ProjectionAuditError(f"{document_id}: duplicate lacks exact source-hash peer")
            peer_document_id = peer[0]
            peer_facts = [tx for tx in by_canonical[extraction_id]
                          if tx.get("filing_id") == peer_document_id]
            if len(peer_facts) != 1:
                raise ProjectionAuditError(f"{document_id}: duplicate peer is not in fixed candidate")
        identity_variants = sorted(variants[_identity(catalog_record)[0]])
        if "identity_ambiguous" in reasons and len(identity_variants) < 2:
            raise ProjectionAuditError(f"{document_id}: identity ambiguity lacks catalog variants")
        detail = {
            "document_id": document_id,
            "source_sha256": source_sha,
            "candidate_id": row["candidate_id"],
            "extraction_id": extraction_id,
            "qualification_reasons": sorted(reasons),
            "report_reasons": sorted(report["document_reasons"]),
            "fixed_review_disposition": "quarantined",
            "duplicate_peer_document_id": peer_document_id,
        }
        explained.append(detail)
        by_document[document_id].append(detail)
        reason_counts.update(reasons)
    if len(explained) != identity_audit.get("counts", {}).get("canonical_id_missing"):
        raise ProjectionAuditError("missing canonical row count does not conserve")
    documents = []
    for document_id, details in sorted(by_document.items()):
        report = by_report[document_id]
        record = by_catalog[document_id][0]
        if (len(details) != report["quarantined_count"] or
                len(details) != identity_audit["missing_canonical_documents"][document_id]):
            raise ProjectionAuditError(f"{document_id}: quarantine document count does not conserve")
        documents.append({
            "document_id": document_id,
            "source_sha256": details[0]["source_sha256"],
            "missing_rows": len(details),
            "qualification_report_reasons": sorted(report["document_reasons"]),
            "qualification_row_reasons": dict(sorted(Counter(
                reason for item in details for reason in item["qualification_reasons"]).items())),
            "catalog_filer_name": record["filer_name"],
            "catalog_agency": record["agency"],
            "catalog_position_title": record["position_title"],
            "catalog_amended_label": record.get("amended_label"),
            "catalog_pending_final_oge_disposition": record.get("pending_final_oge_disposition"),
            "same_name_catalog_identity_variants": [list(variant) for variant in
                                                     sorted(variants[_identity(record)[0]])],
            "duplicate_peer_document_ids": sorted({item["duplicate_peer_document_id"]
                                                    for item in details
                                                    if item["duplicate_peer_document_id"]}),
        })
    return {
        "schema_version": "pipeline-oge-missing-disposition-audit/v1",
        "bundle_run_id": identity_audit["bundle_run_id"],
        "missing_rows": len(explained),
        "reason_counts": dict(sorted(reason_counts.items())),
        "documents": documents,
        "rows": sorted(explained, key=lambda item: (item["document_id"], item["extraction_id"])),
        "production_action": "none",
        "projection_ready": False,
    }


def audit_fixed_missing_dispositions(identity_audit_path: str | Path,
                                     identity_audit_sha256: str,
                                     qualification_path: str | Path,
                                     discovery_path: str | Path,
                                     candidate_path: str | Path,
                                     review_tree_path: str | Path,
                                     review_tree_sha: str) -> dict[str, Any]:
    """Verify exact files against a fixed Git tree and explain missing IDs."""
    raw_identity = Path(identity_audit_path).read_bytes()
    if hashlib.sha256(raw_identity).hexdigest() != identity_audit_sha256:
        raise ProjectionAuditError("OGE identity audit digest mismatch")
    tree = json.loads(Path(review_tree_path).read_text(encoding="utf-8"))
    if tree.get("sha") != review_tree_sha or tree.get("truncated") is not False:
        raise ProjectionAuditError("fixed review tree is wrong or truncated")
    blobs = {entry["path"]: entry for entry in tree["tree"] if entry.get("type") == "blob"}
    sources = ((qualification_path, "oge/qualifications/current.json"),
               (discovery_path, DISCOVERY_PATH),
               (candidate_path, "candidates/sources/oge-current.json"))
    values = []
    for local_path, git_path in sources:
        raw = Path(local_path).read_bytes()
        entry = blobs.get(git_path)
        if entry is None or entry["size"] != len(raw) or entry["sha"] != git_blob_sha1(raw):
            raise ProjectionAuditError(f"fixed review blob mismatch: {git_path}")
        values.append(json.loads(raw))
    result = explain_missing_rows(json.loads(raw_identity), *values)
    result["identity_audit_sha256"] = identity_audit_sha256
    result["review_tree_sha"] = review_tree_sha
    result["fixed_review_blobs"] = {git_path: blobs[git_path]["sha"] for _, git_path in sources}
    return result
