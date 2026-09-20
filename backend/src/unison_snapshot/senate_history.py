"""Plan a bounded Senate amendment predecessor backfill from archived catalogs."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re


PLAN_SCHEMA = "senate-amendment-backfill-plan/v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")


class SenateHistoryError(ValueError):
    """Historical Senate catalog inputs cannot produce a trustworthy plan."""


def _date(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return parsed if parsed.strftime("%Y-%m-%d") == value else None


def plan_amendment_predecessors(
        historical_discovery: object, historical_identities: object,
        current_status: object, current_audit: object, identities_doc: object,
        extractions: list[dict], *, expected_target_count: int) -> dict:
    """Find exact unamended catalog predecessors without downloading report bodies."""

    if (not isinstance(expected_target_count, int) or isinstance(expected_target_count, bool) or
            expected_target_count < 1):
        raise SenateHistoryError("Expected Senate amendment target count is invalid")
    if (not isinstance(historical_discovery, dict) or
            historical_discovery.get("schema_version") != "senate-efd-discovery/v1" or
            historical_discovery.get("source_id") != "senate_efd" or
            not isinstance(historical_discovery.get("reports"), list)):
        raise SenateHistoryError("Historical Senate discovery is invalid")
    historical_metadata = historical_discovery.get("metadata")
    historical_sha = historical_metadata.get("sha256") if isinstance(historical_metadata, dict) else None
    reports = historical_discovery["reports"]
    records_total = historical_discovery.get("records_total")
    if (not isinstance(historical_sha, str) or not _SHA256.fullmatch(historical_sha) or
            historical_metadata.get("source_id") != "senate_efd" or
            historical_metadata.get("record_count") != records_total or
            historical_discovery.get("catalog_rows_covered") != records_total or
            records_total != len(reports)):
        raise SenateHistoryError("Historical Senate discovery has no valid content hash")
    historical_discovery_body = {
        key: value for key, value in historical_discovery.items() if key != "metadata"
    }
    recomputed_historical_sha = hashlib.sha256(json.dumps(
        historical_discovery_body, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    if recomputed_historical_sha != historical_sha:
        raise SenateHistoryError("Historical Senate discovery content hash does not match")
    historical_by_document = {}
    for report in reports:
        document_id = report.get("document_id") if isinstance(report, dict) else None
        if not isinstance(document_id, str) or document_id in historical_by_document:
            raise SenateHistoryError("Historical Senate discovery contains invalid documents")
        historical_by_document[document_id] = report
    if (not isinstance(historical_identities, dict) or
            historical_identities.get("schema_version") != "senate-efd-identities/v1" or
            historical_identities.get("catalog_sha256") != historical_sha or
            not isinstance(historical_identities.get("identities"), list) or
            historical_identities.get("report_count") != records_total):
        raise SenateHistoryError("Historical Senate identities do not match the discovery")
    historical_identity_by_document = {}
    for identity in historical_identities["identities"]:
        document_id = identity.get("document_id") if isinstance(identity, dict) else None
        if (not isinstance(document_id, str) or document_id not in historical_by_document or
                document_id in historical_identity_by_document):
            raise SenateHistoryError("Historical Senate identities contain invalid documents")
        historical_identity_by_document[document_id] = identity
    if len(historical_identity_by_document) != records_total:
        raise SenateHistoryError("Historical Senate identities are incomplete")
    if (not isinstance(current_status, dict) or
            current_status.get("schema_version") != "senate-review-run/v1" or
            current_status.get("source_id") != "senate_efd" or
            current_status.get("status") != "catalog_ready_for_review"):
        raise SenateHistoryError("Current Senate review status is invalid")
    if (not isinstance(current_audit, dict) or
            current_audit.get("schema_version") != "senate-efd-candidate-audit/v2" or
            not isinstance(current_audit.get("quarantined_reports"), list)):
        raise SenateHistoryError("Current Senate candidate audit is invalid")
    current_catalog_sha = current_audit.get("catalog_sha256")
    if not isinstance(current_catalog_sha, str) or not _SHA256.fullmatch(current_catalog_sha):
        raise SenateHistoryError("Current Senate candidate audit has no catalog hash")
    if (current_status.get("catalog_sha256") != current_catalog_sha or
            current_status.get("identity_binding_sha256") != current_audit.get("identity_binding_sha256") or
            current_status.get("identity_roster_sha256") != current_audit.get("roster_sha256") or
            current_status.get("identity_congress_roster_sha256") != current_audit.get("congress_roster_sha256") or
            current_status.get("report_parser_version") != current_audit.get("parser_version") or
            not isinstance(current_audit.get("builder_version"), str) or
            not isinstance(current_audit.get("candidate_snapshot_id"), str) or
            not _SHA256.fullmatch(current_audit["candidate_snapshot_id"])):
        raise SenateHistoryError("Current Senate status and candidate audit are not bound")
    if (not isinstance(identities_doc, dict) or
            identities_doc.get("schema_version") != "senate-efd-identities/v1" or
            identities_doc.get("catalog_sha256") != current_catalog_sha or
            identities_doc.get("roster_sha256") != current_audit.get("roster_sha256") or
            identities_doc.get("congress_roster_sha256") != current_audit.get("congress_roster_sha256") or
            identities_doc.get("report_count") != current_status.get("catalog_record_count") or
            not isinstance(identities_doc.get("identities"), list)):
        raise SenateHistoryError("Current Senate identities do not match the candidate audit")
    if (historical_identities.get("roster_sha256") != identities_doc.get("roster_sha256") or
            historical_identities.get("congress_roster_sha256") !=
            identities_doc.get("congress_roster_sha256")):
        raise SenateHistoryError("Historical and current Senate identities use different rosters")

    identity_by_document: dict[str, dict] = {}
    for identity in identities_doc["identities"]:
        document_id = identity.get("document_id") if isinstance(identity, dict) else None
        if not isinstance(document_id, str) or document_id in identity_by_document:
            raise SenateHistoryError("Current Senate identities contain invalid documents")
        identity_by_document[document_id] = identity
    extraction_by_document: dict[str, dict] = {}
    for extraction in extractions:
        document_id = extraction.get("document_id") if isinstance(extraction, dict) else None
        if not isinstance(document_id, str) or document_id in extraction_by_document:
            raise SenateHistoryError("Current Senate extractions contain invalid documents")
        extraction_by_document[document_id] = extraction
    if (len(extraction_by_document) != current_status.get("report_evidence_count") or
            sum(len(item.get("transactions", [])) for item in extractions) !=
            current_audit.get("input_transaction_count")):
        raise SenateHistoryError("Current Senate extractions do not match the candidate audit")

    pending_documents = []
    for item in current_audit["quarantined_reports"]:
        if (not isinstance(item, dict) or not isinstance(item.get("document_id"), str) or
                not isinstance(item.get("reasons"), list)):
            raise SenateHistoryError("Current Senate audit contains an invalid quarantine row")
        if "amendment_relationship_pending" in item["reasons"]:
            extraction = extraction_by_document.get(item["document_id"])
            if (not isinstance(extraction, dict) or
                    item.get("source_sha256") != extraction.get("source_sha256")):
                raise SenateHistoryError("Amendment quarantine is not bound to its extraction source")
            pending_documents.append(item["document_id"])
    if len(pending_documents) != len(set(pending_documents)):
        raise SenateHistoryError("Current Senate audit duplicates amendment quarantine rows")

    targets = []
    for document_id in sorted(pending_documents):
        extraction = extraction_by_document.get(document_id)
        identity = identity_by_document.get(document_id)
        if not isinstance(extraction, dict) or not isinstance(identity, dict):
            raise SenateHistoryError("Amendment quarantine does not have bound identity and extraction")
        if extraction.get("report_amendment_number") != 1:
            continue
        if (identity.get("status") != "matched_automatically" or
                identity.get("match_class") not in {"exact", "alias"}):
            raise SenateHistoryError("Amendment backfill target has no automatic official identity")
        filer_name = identity.get("filer_name")
        person_id = identity.get("person_id")
        title_date = extraction.get("report_title_date")
        portal_date = _date(extraction.get("portal_listed_date"))
        if not isinstance(filer_name, str) or not filer_name or _date(title_date) is None or portal_date is None:
            raise SenateHistoryError("Amendment backfill target has invalid official catalog fields")
        candidates = []
        for report in reports:
            candidate_date = _date(report.get("portal_listed_date"))
            historical_identity = historical_identity_by_document[report["document_id"]]
            if (report.get("document_id") not in extraction_by_document and
                    historical_identity.get("status") == "matched_automatically" and
                    historical_identity.get("match_class") in {"exact", "alias"} and
                    historical_identity.get("person_id") == person_id and
                    report.get("report_label_date") == title_date and
                    report.get("report_amendment_number") is None and
                    report.get("access_method") == "electronic_ptr" and
                    candidate_date is not None and candidate_date <= portal_date):
                candidates.append(report)
        candidates.sort(key=lambda item: (item["portal_listed_date"], item["document_id"]))
        targets.append({
            "amendment_document_id": document_id,
            "person_id": person_id,
            "filer_name": filer_name,
            "report_title_date": title_date,
            "amendment_portal_listed_date": extraction.get("portal_listed_date"),
            "candidate_count": len(candidates),
            "candidate_predecessors": candidates,
        })

    reasons = []
    if len(targets) != expected_target_count:
        reasons.append("target_count_mismatch")
    if any(item["candidate_count"] != 1 for item in targets):
        reasons.append("predecessor_not_unique")
    selected_ids = [item["candidate_predecessors"][0]["document_id"]
                    for item in targets if item["candidate_count"] == 1]
    if len(selected_ids) != len(set(selected_ids)):
        reasons.append("predecessor_reused")
    status = "ready" if not reasons else "attention"
    historical_discovery_content_sha = hashlib.sha256(json.dumps(
        historical_discovery, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    historical_identities_sha = hashlib.sha256(json.dumps(
        historical_identities, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    current_audit_sha = hashlib.sha256(json.dumps(
        current_audit, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    fingerprint = json.dumps({
        "historical_catalog_sha256": historical_sha,
        "historical_discovery_sha256": historical_discovery_content_sha,
        "historical_identities_sha256": historical_identities_sha,
        "current_catalog_sha256": current_catalog_sha,
        "current_audit_sha256": current_audit_sha,
        "candidate_snapshot_id": current_audit["candidate_snapshot_id"],
        "identity_binding_sha256": current_audit["identity_binding_sha256"],
        "parser_version": current_audit["parser_version"],
        "builder_version": current_audit["builder_version"],
        "expected_target_count": expected_target_count,
        "targets": targets,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "schema_version": PLAN_SCHEMA,
        "source_id": "senate_efd",
        "status": status,
        "reasons": sorted(set(reasons)),
        "historical_catalog_sha256": historical_sha,
        "historical_discovery_sha256": historical_discovery_content_sha,
        "historical_identities_sha256": historical_identities_sha,
        "current_catalog_sha256": current_catalog_sha,
        "current_audit_sha256": current_audit_sha,
        "candidate_snapshot_id": current_audit["candidate_snapshot_id"],
        "identity_binding_sha256": current_audit["identity_binding_sha256"],
        "roster_sha256": current_audit["roster_sha256"],
        "congress_roster_sha256": current_audit.get("congress_roster_sha256"),
        "parser_version": current_audit["parser_version"],
        "builder_version": current_audit["builder_version"],
        "expected_target_count": expected_target_count,
        "target_count": len(targets),
        "unique_predecessor_count": len(selected_ids),
        "selected_document_ids": sorted(selected_ids),
        "targets": targets,
        "plan_sha256": hashlib.sha256(fingerprint).hexdigest(),
    }


def load_amendment_predecessor_plan(
        review_root: Path, historical_discovery_path: Path,
        historical_identities_path: Path, *, expected_target_count: int) -> dict:
    """Load the immutable current review inputs and build a catalog-only plan."""

    try:
        status = json.loads((review_root / "status" / "senate_efd.json").read_text(encoding="utf-8"))
        audit = json.loads((review_root / "senate_efd" / "qualifications" /
                            "current.json").read_text(encoding="utf-8"))
        discovery = json.loads(historical_discovery_path.read_text(encoding="utf-8"))
        historical_identities = json.loads(historical_identities_path.read_text(encoding="utf-8"))
        catalog_sha = status["catalog_sha256"]
        binding = status["identity_binding_sha256"]
        identities = json.loads((review_root / "senate_efd" / "identities" /
                                 catalog_sha / f"{binding}.json").read_text(encoding="utf-8"))
        parser_version = status["report_parser_version"]
        paths = sorted((review_root / "senate_efd" / "extractions").glob(
            f"*/*/{parser_version}.json"))
        extractions = []
        for path in paths:
            extraction = json.loads(path.read_text(encoding="utf-8"))
            if (path.parent.parent.name != extraction.get("document_id") or
                    path.parent.name != extraction.get("source_sha256") or
                    path.stem != extraction.get("parser_version") or
                    extraction.get("parser_version") != parser_version or
                    extraction.get("evidence_complete") is not True):
                raise SenateHistoryError("Senate amendment extraction path is not content bound")
            extractions.append(extraction)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError):
        raise SenateHistoryError("Senate amendment planning inputs are incomplete") from None
    return plan_amendment_predecessors(
        discovery, historical_identities, status, audit, identities, extractions,
        expected_target_count=expected_target_count,
    )
