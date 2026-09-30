"""Read-only OGE shadow-row identity audit using pinned review extraction blobs."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any

from .canonical_compare import CORE_FIELDS, OPTIONAL_EXACT_FIELDS, git_blob_sha1
from .diff import load_manifest, load_rows
from .projection_audit import ProjectionAuditError, audit_bundle

ROW_FIELDS = ("asset_name", "transaction_date", "transaction_type",
              "owner", "amount_low", "amount_high")


def audit_oge_row_identity(bundle_root: str | Path, extraction_root: str | Path,
                           review_tree_path: str | Path, review_tree_sha: str,
                           review_commit: str, candidate_path: str | Path,
                           candidate_blob_sha1: str) -> dict[str, Any]:
    """Bind observations to old extraction IDs, then check pinned candidate IDs.

    The review tree SHA must be independently checked against review_commit's
    commit object. The routine verifies every local extraction blob against that
    tree and never creates, edits, or publishes a canonical transaction.
    """
    root = Path(bundle_root).resolve()
    base = audit_bundle(root)
    manifest = load_manifest(root / "manifest.json")
    if (len(review_commit) != 40 or len(review_tree_sha) != 40 or
            any(char not in "0123456789abcdef" for char in review_commit + review_tree_sha)):
        raise ProjectionAuditError("review commit and tree must be full lowercase Git SHAs")
    oge_runs = [run for run in manifest.get("source_runs", [])
                if run.get("workflow") == "oge-direct-278t-shadow"]
    if oge_runs and (len(oge_runs) != 1 or oge_runs[0].get("review_commit") != review_commit):
        raise ProjectionAuditError("review commit differs from fixed OGE source run")
    rows = [r for r in load_rows(root / "candidate_rows.json")
            if r["source"]["source_id"] == "oge" and r["disposition"] == "qualified"]
    if not rows:
        raise ProjectionAuditError("bundle has no qualified OGE rows")
    tree = json.loads(Path(review_tree_path).read_text(encoding="utf-8"))
    if tree.get("sha") != review_tree_sha or tree.get("truncated") is not False:
        raise ProjectionAuditError("review tree SHA mismatches or tree is truncated")
    blobs = {entry["path"]: entry for entry in tree["tree"] if entry.get("type") == "blob"}
    doc_manifest = {(d["document_id"], d["source_sha256"]): d
                    for d in manifest["documents"] if d["source_id"] == "oge"}
    if len(doc_manifest) != sum(d["source_id"] == "oge" for d in manifest["documents"]):
        raise ProjectionAuditError("OGE document manifest has duplicate exact identities")
    candidate_raw = Path(candidate_path).read_bytes()
    candidate_tree_entry = blobs.get("candidates/sources/oge-current.json")
    if (candidate_tree_entry is None or candidate_tree_entry["sha"] != candidate_blob_sha1 or
            git_blob_sha1(candidate_raw) != candidate_blob_sha1):
        raise ProjectionAuditError("OGE fixed candidate Git blob mismatch")
    candidate = json.loads(candidate_raw)
    if candidate.get("meta", {}).get("is_demo") is not False:
        raise ProjectionAuditError("OGE fixed candidate is not production-mode input")
    transactions = candidate.get("transactions")
    if not isinstance(transactions, list) or any(t.get("source_id") != "oge" for t in transactions):
        raise ProjectionAuditError("OGE fixed candidate transactions are invalid")
    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for transaction in transactions:
        by_id[transaction.get("id")].append(transaction)
        by_doc[transaction.get("filing_id")].append(transaction)

    extraction_root = Path(extraction_root).resolve()
    cache: dict[tuple[str, str], dict[str, Any]] = {}
    seen_extraction: set[tuple[str, str, str]] = set()
    counts: Counter[str] = Counter()
    missing_docs: Counter[str] = Counter()
    mismatches: Counter[str] = Counter()
    old_value_ambiguous = 0
    missing_rows = []
    matched_rows = []
    total_extraction_bytes = 0
    for ledger in rows:
        source = ledger["source"]
        document_id, source_sha = source["document_id"], source["source_sha256"]
        document = doc_manifest.get((document_id, source_sha))
        if document is None:
            raise ProjectionAuditError("OGE row has no exact bundle document")
        pair = (document_id, source_sha)
        if pair not in cache:
            version = document["parser_version"].replace("/", "-")
            path = (f"oge/extractions/{document_id}/{source_sha}/{version}.json")
            entry = blobs.get(path)
            local = extraction_root / f"{document_id}.json"
            if entry is None or not local.is_file():
                raise ProjectionAuditError(f"{document_id}: fixed review extraction is missing")
            raw = local.read_bytes()
            if len(raw) != entry["size"] or git_blob_sha1(raw) != entry["sha"]:
                raise ProjectionAuditError(f"{document_id}: fixed review extraction blob mismatch")
            extraction = json.loads(raw)
            if (extraction.get("document_id") != document_id or
                    extraction.get("source_sha256") != source_sha or
                    extraction.get("source_url") != source["source_url"] or
                    extraction.get("parser_version") != document["parser_version"]):
                raise ProjectionAuditError(f"{document_id}: extraction source binding mismatch")
            if not isinstance(extraction.get("transactions"), list):
                raise ProjectionAuditError(f"{document_id}: extraction transactions are invalid")
            locator_index: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
            for transaction in extraction["transactions"]:
                locator_index[(transaction.get("page_number"),
                               transaction.get("row_number"))].append(transaction)
            cache[pair] = {"locator_index": locator_index, "blob_sha1": entry["sha"]}
            total_extraction_bytes += len(raw)
        observations = {o["field"]: o["normalized_value"] for o in ledger["observations"]
                        if o["selected"]}
        locations = ledger["evidence_locations"]
        if len(locations) != 1 or observations.get("row_number") is None:
            raise ProjectionAuditError(f"{document_id}: qualified row locator is incomplete")
        locator = locations[0]["row_locator"]
        if not isinstance(locator, str) or not locator.isdecimal() or int(locator) < 1:
            raise ProjectionAuditError(f"{document_id}: physical row index is invalid")
        physical_index = int(locator)
        if ledger["candidate_id"] != f"{source_sha[:16]}:{physical_index}":
            raise ProjectionAuditError(f"{document_id}: candidate ID differs from physical row index")
        matched = cache[pair]["locator_index"].get(
            (locations[0]["page"], observations["row_number"]), [])
        if len(matched) != 1:
            raise ProjectionAuditError(f"{document_id}: row locator is missing or ambiguous")
        transaction = matched[0]
        extraction_id = transaction.get("extraction_id")
        scoped_extraction = (document_id, source_sha, extraction_id)
        if not isinstance(extraction_id, str) or scoped_extraction in seen_extraction:
            raise ProjectionAuditError(f"{document_id}: extracted row ID missing or duplicated")
        seen_extraction.add(scoped_extraction)
        differences = [field for field in ROW_FIELDS
                       if transaction.get(field) != observations.get(field)]
        if differences:
            raise ProjectionAuditError(f"{document_id}: ledger/extraction field differences: {differences}")
        counts["extraction_id_bound"] += 1
        possible = [tx for tx in by_doc[document_id]
                    if tx.get("source_url") == source["source_url"]]
        value_matches = [tx for tx in possible
                         if all(tx.get(field) == observations[field] for field in CORE_FIELDS)
                         and all(tx.get(field) == observations[field] for field in OPTIONAL_EXACT_FIELDS
                                 if field in observations and observations[field] is not None)]
        if len(value_matches) > 1:
            old_value_ambiguous += 1
        canonical = [tx for tx in by_id[extraction_id]
                     if tx.get("filing_id") == document_id and
                     tx.get("source_url") == source["source_url"]]
        if len(canonical) > 1:
            raise ProjectionAuditError(f"{document_id}: canonical ID is ambiguous")
        if canonical:
            counts["canonical_id_matched"] += 1
            mismatches.update(field for field in ROW_FIELDS
                              if canonical[0].get(field) != observations.get(field))
            matched_rows.append({"document_id": document_id,
                                 "source_sha256": source_sha,
                                 "candidate_id": ledger["candidate_id"],
                                 "extraction_id": extraction_id,
                                 "canonical_transaction_id": canonical[0]["id"]})
        else:
            counts["canonical_id_missing"] += 1
            missing_docs[document_id] += 1
            missing_rows.append({"document_id": document_id,
                                 "source_sha256": source_sha,
                                 "candidate_id": ledger["candidate_id"],
                                 "extraction_id": extraction_id})
    if mismatches:
        raise ProjectionAuditError(f"canonical ID field differences: {dict(mismatches)}")
    return {
        "schema_version": "pipeline-oge-row-identity-audit/v1",
        "bundle_run_id": base["bundle_run_id"],
        "review_commit": review_commit,
        "review_tree_sha": review_tree_sha,
        "candidate_blob_sha1": candidate_blob_sha1,
        "qualified_rows": len(rows),
        "fixed_extraction_documents": len(cache),
        "fixed_extraction_bytes": total_extraction_bytes,
        "counts": dict(sorted(counts.items())),
        "previously_value_ambiguous_rows": old_value_ambiguous,
        "canonical_field_differences": dict(mismatches),
        "missing_canonical_documents": dict(sorted(missing_docs.items())),
        "missing_canonical_rows": missing_rows,
        "matched_canonical_rows": matched_rows,
        "candidate_id_collisions": [collision for collision in base["candidate_id_collisions"]
                                    if collision["scoped_rows"][0][0] == "oge"],
        "projection_ready": False,
    }
