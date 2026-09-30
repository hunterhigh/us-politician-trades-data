"""Read-only document census for wh-url rows in a frozen candidate snapshot."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import re
import unicodedata


SCHEMA = "whitehouse-wh-url-document-audit/v1"


def _key(row: dict) -> tuple:
    name = unicodedata.normalize("NFKC", row.get("asset_name") or "").casefold()
    return (row.get("person_id"), re.sub(r"[^a-z0-9]", "", name),
            row.get("transaction_date"), row.get("transaction_type"),
            row.get("amount_low"), row.get("amount_high"))


def _coarse_key(row: dict) -> tuple:
    return (row.get("person_id"), row.get("transaction_date"),
            row.get("transaction_type"), row.get("amount_low"), row.get("amount_high"))


def audit_wh_url_documents(candidate_bytes: bytes, index_bytes: bytes,
                           evidence_paths: list[str], review_paths: list[str], *,
                           candidate_commit: str, evidence_commit: str) -> dict:
    candidate = json.loads(candidate_bytes)
    index = json.loads(index_bytes)
    if (not re.fullmatch(r"[0-9a-f]{40}", candidate_commit) or
            not re.fullmatch(r"[0-9a-f]{40}", evidence_commit) or
            index.get("source_id") != "whitehouse_public_disclosures"):
        raise ValueError("source snapshot identity is invalid")
    rows = candidate["transactions"]
    wh = [row for row in rows if str(row.get("filing_id", "")).startswith("wh-url:")]
    other = [row for row in rows if not str(row.get("filing_id", "")).startswith("wh-url:")]
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for row in wh:
        by_doc[row["filing_id"]].append(row)
    indexed = {row["source_document_id"]: row for row in index["reports"]}
    if len(indexed) != len(index["reports"]):
        raise ValueError("duplicate public index document id")
    if not set(by_doc).issubset(indexed):
        raise ValueError("candidate document missing from public index")
    oge_keys = {_key(row) for row in other if row.get("source_id") == "oge"}
    oge_by_person: dict[str, list[dict]] = defaultdict(list)
    for row in other:
        if row.get("source_id") == "oge":
            oge_by_person[row.get("person_id")].append(row)
    annual = [row for row in wh if indexed[row["filing_id"]]["document_type_from_label"] == "278e_annual"]
    periodic = [row for row in wh if indexed[row["filing_id"]]["document_type_from_label"] == "278t"]
    annual_keys = {_key(row) for row in annual}
    periodic_keys = {_key(row) for row in periodic}
    periodic_coarse = {_coarse_key(row) for row in periodic}
    documents = []
    for document_id, group in sorted(by_doc.items()):
        source = indexed.get(document_id)
        if source is None:
            raise ValueError(f"candidate document missing from public index: {document_id}")
        url = source["document_url"]
        if (any(row.get("source_url") != url for row in group) or
                len({row.get("person_id") for row in group}) != 1):
            raise ValueError(f"candidate rows cross source document: {document_id}")
        kind = source["document_type_from_label"]
        if kind not in {"278e_annual", "278t"}:
            raise ValueError(f"unsupported public document class: {document_id}")
        stem = document_id.removeprefix("wh-url:")
        prefix = f"whitehouse/disclosures/reports/{stem}/"
        archived = [path for path in evidence_paths if path.startswith(prefix)]
        hashes = {part.split("/")[-1].split(".")[0] for part in archived}
        if len(hashes) != 1 or not archived or not any(path.endswith(".json") for path in archived):
            raise ValueError(f"archived document identity missing: {document_id}")
        pdf_sha = next(iter(hashes))
        if not re.fullmatch(r"[0-9a-f]{64}", pdf_sha):
            raise ValueError(f"archived PDF SHA invalid: {document_id}")
        pdf = [path for path in archived if path.endswith(".pdf")]
        parts = [path for path in archived if re.search(r"\.part-\d+\.bin$", path)]
        if (len(pdf), len(parts)) not in {(1, 0), (0, 9)}:
            raise ValueError(f"archived PDF layout unknown: {document_id}")
        extraction_prefix = f"whitehouse/extractions/{stem}/"
        extraction_count = sum(path.startswith(extraction_prefix) for path in review_paths)
        if extraction_count == 0:
            raise ValueError(f"review extraction absent: {document_id}")
        documents.append({
            "document_id": document_id, "document_type_from_public_label": kind,
            "source_url": url, "person_id": group[0]["person_id"],
            "candidate_row_count": len(group),
            "candidate_transaction_date_span": [
                min(row["transaction_date"] for row in group),
                max(row["transaction_date"] for row in group)],
            "candidate_filed_at_values": sorted({row.get("filed_at") for row in group}),
            "archive_pdf_sha256_from_path": pdf_sha,
            "archive_layout": "single_pdf" if pdf else "nine_ordered_parts",
            "review_extraction_file_count": extraction_count,
            "oge_same_person_candidate_row_count": len(oge_by_person[group[0]["person_id"]]),
            "oge_same_person_date_span": ([
                min(row["transaction_date"] for row in oge_by_person[group[0]["person_id"]]),
                max(row["transaction_date"] for row in oge_by_person[group[0]["person_id"]])]
                if oge_by_person[group[0]["person_id"]] else None),
            "exact_tuple_matches_oge_candidate": sum(_key(row) in oge_keys for row in group),
            "exact_tuple_matches_other_wh_class": sum(
                _key(row) in (periodic_keys if kind == "278e_annual" else annual_keys)
                for row in group),
        })
    return {
        "schema_version": SCHEMA,
        "candidate_commit": candidate_commit,
        "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "evidence_commit": evidence_commit,
        "white_house_index_sha256": hashlib.sha256(index_bytes).hexdigest(),
        "white_house_public_page_sha256": index["page_sha256"],
        "wh_url_row_count": len(wh), "document_count": len(documents),
        "annual_row_count": len(annual), "periodic_row_count": len(periodic),
        "exact_tuple_matches_oge_candidate": sum(_key(row) in oge_keys for row in wh),
        "annual_rows_with_coarse_periodic_candidate": sum(
            _coarse_key(row) in periodic_coarse for row in annual),
        "documents": documents,
        "oge_document_identity_status": "unverified_for_all_candidate_documents",
        "promotion_or_deletion_authorized": False,
    }
