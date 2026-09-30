"""Read-only row dispositions for frozen White House 278-T PDFs and extractions."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import re


SCHEMA = "whitehouse-278t-shadow-replay/v1"
ROW_FIELDS = ("asset_name", "owner", "transaction_type", "transaction_date",
              "amount_low", "amount_high")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def replay_278t_shadow(candidate_bytes: bytes, document_audit: dict,
                       bundles: dict[str, tuple[bytes, bytes, str]]) -> dict:
    """Return all source-row dispositions without changing a candidate.

    bundles maps wh-url document IDs to (PDF bytes, review extraction JSON
    bytes, expected Git blob SHA for that extraction).
    """
    if (document_audit.get("schema_version") != "whitehouse-wh-url-document-audit/v1" or
            document_audit.get("candidate_sha256") != _sha256(candidate_bytes)):
        raise ValueError("candidate and document census are not the same snapshot")
    expected = {doc["document_id"]: doc for doc in document_audit["documents"]
                if doc["document_type_from_public_label"] == "278t"}
    if set(bundles) != set(expected):
        raise ValueError("White House 278-T document set is incomplete")
    candidate = json.loads(candidate_bytes)
    by_document: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in candidate["transactions"]:
        doc_id = row.get("filing_id", "")
        if doc_id not in expected:
            continue
        row_id = row.get("id")
        if not isinstance(row_id, str) or row_id in by_document[doc_id]:
            raise ValueError(f"candidate row ID invalid or repeated: {doc_id}")
        by_document[doc_id][row_id] = row
    rows: list[dict] = []
    documents: list[dict] = []
    totals: Counter[str] = Counter()
    for doc_id, expected_doc in sorted(expected.items()):
        pdf_bytes, extraction_bytes, expected_blob = bundles[doc_id]
        pdf_sha = _sha256(pdf_bytes)
        if (pdf_sha != expected_doc["archive_pdf_sha256_from_path"] or
                not pdf_bytes.startswith(b"%PDF-")):
            raise ValueError(f"PDF bytes differ from archive identity: {doc_id}")
        if (not re.fullmatch(r"[0-9a-f]{40}", expected_blob) or
                _git_blob_sha(extraction_bytes) != expected_blob):
            raise ValueError(f"review extraction blob differs: {doc_id}")
        extraction = json.loads(extraction_bytes)
        if (extraction.get("document_id") != doc_id or
                extraction.get("source_sha256") != pdf_sha or
                extraction.get("source_url") != expected_doc["source_url"] or
                extraction.get("source_row_count") !=
                len(extraction.get("transactions", [])) + len(extraction.get("quarantined", []))):
            raise ValueError(f"review extraction source or row count differs: {doc_id}")
        document_rows = extraction["transactions"] + extraction["quarantined"]
        source_ids = [row.get("extraction_id") for row in document_rows]
        if (any(not isinstance(row_id, str) for row_id in source_ids) or
                len(source_ids) != len(set(source_ids))):
            raise ValueError(f"review source row IDs invalid or repeated: {doc_id}")
        seen_candidate: set[str] = set()
        dispositions: Counter[str] = Counter()
        for collection, source_rows in (("transactions", extraction["transactions"]),
                                        ("quarantined", extraction["quarantined"])):
            for source_row in source_rows:
                row_id = source_row["extraction_id"]
                candidate_row = by_document[doc_id].get(row_id)
                if collection == "quarantined":
                    disposition = "review_quarantined"
                    if candidate_row is not None:
                        disposition = "quarantine_candidate_conflict"
                elif candidate_row is None:
                    disposition = "parsed_absent_from_candidate"
                else:
                    seen_candidate.add(row_id)
                    disposition = "candidate_existing" if (
                        all(source_row.get(field) == candidate_row.get(field)
                            for field in ROW_FIELDS) and
                        candidate_row.get("source_url") == expected_doc["source_url"]
                    ) else "candidate_field_conflict"
                dispositions[disposition] += 1
                rows.append({
                    "document_id": doc_id,
                    "extraction_id": row_id,
                    "page_number": source_row.get("page_number"),
                    "row_number": source_row.get("row_number"),
                    "review_collection": collection,
                    "source_row_sha256": _sha256(json.dumps(
                        source_row, sort_keys=True, ensure_ascii=False,
                        separators=(",", ":")).encode()),
                    "disposition": disposition,
                    "reasons": source_row.get("reasons", []) if collection == "quarantined" else [],
                })
        missing = set(by_document[doc_id]) - seen_candidate
        if missing:
            dispositions["candidate_without_review_transaction"] += len(missing)
        if len(by_document[doc_id]) != expected_doc["candidate_row_count"]:
            raise ValueError(f"candidate document count differs: {doc_id}")
        totals.update(dispositions)
        documents.append({
            "document_id": doc_id, "source_sha256": pdf_sha,
            "review_extraction_git_blob": expected_blob,
            "parser_version": extraction["parser_version"],
            "source_row_count": extraction["source_row_count"],
            "parsed_row_count": len(extraction["transactions"]),
            "review_quarantine_count": len(extraction["quarantined"]),
            "existing_candidate_row_count": len(by_document[doc_id]),
            "dispositions": dict(sorted(dispositions.items())),
        })
    if sum(doc["source_row_count"] for doc in documents) != len(rows):
        raise ValueError("source row conservation failed")
    return {
        "schema_version": SCHEMA,
        "candidate_commit": document_audit["candidate_commit"],
        "candidate_sha256": _sha256(candidate_bytes),
        "evidence_commit": document_audit["evidence_commit"],
        "document_count": len(documents), "source_row_count": len(rows),
        "dispositions": dict(sorted(totals.items())),
        "documents": documents, "rows": rows,
        "oge_same_document_identity": "unknown",
        "production_mutation_authorized": False,
    }
