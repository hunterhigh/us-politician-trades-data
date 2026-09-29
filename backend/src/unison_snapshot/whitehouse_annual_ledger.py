"""Build validated, review-only ledger rows for OGE 278e Parts 6 and 7."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any

from .pipeline_ledger import (CANDIDATE_ROW_SCHEMA, OBSERVATION_SCHEMA, RUN_MANIFEST_SCHEMA,
                              idempotency_key, validate_candidate_row,
                              validate_run_manifest)


RULES_VERSION = "whitehouse-278e-part6-part7-shadow/v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _scalar(value: object) -> str | int | float | bool | None:
    if value is None or type(value) in (str, int, float, bool):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _candidate_id(source_sha256: str, row: dict, index: int) -> str:
    locator = row.get("source_row_locator") or (
        f"p{row.get('page_number')}#${row.get('row_number')}")
    identity = f"{source_sha256}|{row.get('section')}|{locator}|{index}"
    return "oge-278e-row:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _location(row: dict) -> dict[str, Any]:
    page = row.get("page_number")
    if type(page) is not int or page < 1:
        raise ValueError("Part 6/7 ledger row requires a verified positive page number")
    location: dict[str, Any] = {"page": page, "kind": "pdf_row"}
    locator = row.get("source_row_locator")
    if isinstance(locator, str) and locator:
        location["row_locator"] = locator
    row_number = row.get("row_number")
    if isinstance(row_number, str) and row_number:
        location["row_locator"] = location.get("row_locator", row_number)
    return location


def _field_values(row: dict, part: str) -> tuple[dict[str, object], dict[str, object], list[str]]:
    raw = row.get("raw_columns") if isinstance(row.get("raw_columns"), dict) else {}
    if part == "part6":
        values = {
            "asset_name": row.get("asset_name"), "owner": row.get("owner"),
            "row_number": row.get("row_number"), "value_band": raw.get("value"),
            "value_low": row.get("value_low"), "value_high": row.get("value_high"),
            "report_period_end": row.get("report_period_end"),
            "holding_valuation_date": row.get("holding_valuation_date"),
        }
        raw_values = {
            "asset_name": raw.get("description"), "owner": row.get("owner"),
            "row_number": row.get("row_number"), "value_band": raw.get("value"),
            "value_low": row.get("value_low"), "value_high": row.get("value_high"),
            "report_period_end": row.get("report_period_end"),
            "holding_valuation_date": row.get("holding_valuation_date"),
        }
        required = ["asset_name", "owner", "value_low", "value_high",
                    "report_period_end", "holding_valuation_date"]
    else:
        values = {
            "asset_name": row.get("asset_name"), "owner": row.get("owner"),
            "row_number": row.get("row_number"),
            "transaction_type": row.get("transaction_type"),
            "transaction_date": row.get("transaction_date"),
            "amount_band": raw.get("amount"), "amount_low": row.get("amount_low"),
            "amount_high": row.get("amount_high"),
        }
        raw_values = {
            "asset_name": raw.get("description"), "owner": row.get("owner"),
            "row_number": row.get("row_number"),
            "transaction_type": raw.get("type"),
            "transaction_date": raw.get("date"), "amount_band": raw.get("amount"),
            "amount_low": row.get("amount_low"), "amount_high": row.get("amount_high"),
        }
        required = ["asset_name", "transaction_type", "transaction_date",
                    "amount_low", "amount_high"]
    return {name: _scalar(value) for name, value in raw_values.items()}, values, required


def _reasons(row: dict, decision: dict | None, disposition: str) -> list[str]:
    if disposition == "qualified":
        return []
    reasons = decision.get("reasons") if isinstance(decision, dict) else None
    if not isinstance(reasons, list) or not reasons:
        reason = row.get("reason")
        reasons = [reason] if isinstance(reason, str) and reason else row.get("reasons")
    if not isinstance(reasons, list) or not reasons:
        reasons = ["source_row_not_qualified"]
    return sorted({str(reason) for reason in reasons if str(reason)})


def _row_ledger_record(row: dict, decision: dict | None, extraction: dict,
                       *, run_id: str, parser_id: str,
                       layout_fingerprint: str, index: int) -> dict:
    part = row["section"]
    location = _location(row)
    qualified = isinstance(decision, dict) and decision.get("source_candidate_eligible") is True
    source_disposition = row.get("disposition")
    if (source_disposition is None and
            "table_header_unrecognized" in row.get("reasons", [])):
        source_disposition = "unrecognized"
    if (source_disposition is None and
            "table_header_unrecognized" in row.get("reasons", [])):
        source_disposition = "unrecognized"
    if source_disposition == "excluded":
        disposition = "excluded"
    elif source_disposition == "unrecognized":
        disposition = "unrecognized"
    elif qualified:
        disposition = "qualified"
    else:
        disposition = "quarantined"
    reasons = _reasons(row, decision, disposition)
    raw_values, normalized_values, required = _field_values(row, part)
    method = "ocr" if str(extraction.get("extraction_method", "")).startswith("tesseract") else "embedded_text"
    parser_version = extraction.get("parser_version")
    source_sha = extraction.get("source_sha256")
    source_id = extraction.get("source_id") or "whitehouse_public"
    document_id = extraction.get("source_document_id") or source_sha
    key = idempotency_key(
        source_id=source_id, source_sha256=source_sha, parser_id=parser_id,
        parser_version=parser_version, rules_version=RULES_VERSION,
        layout_fingerprint=layout_fingerprint)
    candidate_id = _candidate_id(source_sha, row, index)
    observations = []
    for field, value in normalized_values.items():
        original = raw_values.get(field)
        if field == "owner":
            original = row.get("owner")
        if field == "row_number":
            original = row.get("row_number")
        confidence_data = row.get("ocr_field_confidence", {}).get(field)
        confidence = None
        if isinstance(confidence_data, dict):
            score = confidence_data.get("mean")
            if isinstance(score, (int, float)):
                confidence = max(0.0, min(1.0, float(score) / 100.0))
        elif isinstance(row.get("ocr_mean_confidence"), (int, float)):
            confidence = max(0.0, min(1.0, float(row["ocr_mean_confidence"]) / 100.0))
        observations.append({
            "schema_version": OBSERVATION_SCHEMA,
            "observation_id": hashlib.sha256(
                f"{candidate_id}|{field}".encode("utf-8")).hexdigest()[:24],
            "field": field, "raw_value": _scalar(original),
            "normalized_value": _scalar(value), "method": method,
            "confidence": confidence, "selected": True,
            "required_for_projection": field in required,
            "locations": [location],
            "conditions": {"section": part,
                           "owner_evidence": row.get("owner_evidence", []),
                           "owner_is_filer": True if row.get("owner") == "Self" else
                           False if row.get("owner") in {"Spouse", "Dependent Child"} else None},
        })
    unresolved = [] if disposition == "qualified" else sorted({
        "source_row" if "identity" in reason or "locator" in reason else
        "owner" if "owner" in reason else
        "asset_name" if "asset" in reason or "description" in reason else
        "value" if "value" in reason or "amount" in reason else
        "row_qualification" for reason in reasons})
    return {
        "schema_version": CANDIDATE_ROW_SCHEMA,
        "candidate_id": candidate_id, "run_id": run_id,
        "idempotency_key": key,
        "source": {"source_id": source_id, "document_id": document_id,
                   "source_url": extraction.get("source_url"),
                   "source_sha256": source_sha},
        "parser": {"parser_id": parser_id, "parser_version": parser_version,
                   "rules_version": RULES_VERSION,
                   "layout_fingerprint": layout_fingerprint},
        "disposition": disposition, "reasons": reasons,
        "unresolved_conflicts": unresolved,
        "observations": observations, "evidence_locations": [location],
        "required_projection_fields": required,
    }


def build_annual_shadow_ledger(extraction: dict, audit: dict, *, run_id: str,
                               code_commit: str, started_at: str,
                               completed_at: str | None = None,
                               trigger: str = "local") -> dict:
    """Create validated Part 6/7 ledger records without promotion or writes."""
    if not isinstance(extraction, dict) or not isinstance(audit, dict):
        raise ValueError("annual extraction and audit must be objects")
    source_sha = extraction.get("source_sha256")
    if not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha):
        raise ValueError("annual shadow ledger requires a source SHA-256")
    parser_version = extraction.get("parser_version")
    if not isinstance(parser_version, str) or not parser_version:
        raise ValueError("annual shadow ledger requires a parser version")
    parser_id = "whitehouse-278e-public"
    layout_fingerprint = "whitehouse-278e-layout:" + parser_version
    holdings = extraction.get("holdings", [])
    transactions = extraction.get("transactions", [])
    excluded = extraction.get("excluded", [])
    quarantined = extraction.get("quarantined", [])
    collections = (holdings, transactions, excluded, quarantined)
    if any(not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
           for rows in collections):
        raise ValueError("annual row dispositions must be arrays of objects")
    holding_decisions = audit.get("holding_row_audit", [])
    transaction_decisions = audit.get("transaction_row_audit", [])
    rows: list[dict] = []
    identity_occurrences: Counter[tuple[str, str]] = Counter()
    for section, items, decisions in (
            ("part6", holdings, holding_decisions), ("part7", transactions, transaction_decisions),
            ("part6", excluded, []), ("part7", excluded, []),
            ("part6", quarantined, []), ("part7", quarantined, [])):
        for index, row in enumerate(items):
            if row.get("section") != section:
                continue
            source_disposition = "excluded" if items is excluded else (
                "quarantined" if items is quarantined else None)
            if (items is quarantined and
                    "table_header_unrecognized" in row.get("reasons", [])):
                source_disposition = "unrecognized"
            if (items is quarantined and
                    "table_header_unrecognized" in row.get("reasons", [])):
                source_disposition = "unrecognized"
            ledger_row = dict(row)
            if source_disposition:
                ledger_row["disposition"] = source_disposition
            decision = decisions[index] if index < len(decisions) else None
            if decisions and (section == "part6" and items is holdings or
                              section == "part7" and items is transactions):
                decision = decisions[index]
            row_identity = row.get("source_row_locator") or (
                f"p{row.get('page_number')}#{row.get('row_number')}")
            occurrence_key = (section, str(row_identity))
            occurrence = identity_occurrences[occurrence_key]
            identity_occurrences[occurrence_key] += 1
            rows.append(_row_ledger_record(
                ledger_row, decision, extraction, run_id=run_id,
                parser_id=parser_id, layout_fingerprint=layout_fingerprint,
                index=occurrence))
    for row in rows:
        validate_candidate_row(row)
    row_counts = {name: sum(row["disposition"] == disposition for row in rows)
                  for name, disposition in (("qualified_rows", "qualified"),
                                            ("quarantined_rows", "quarantined"),
                                            ("excluded_rows", "excluded"),
                                            ("unrecognized_rows", "unrecognized"))}
    document_id = extraction.get("source_document_id") or source_sha
    document_key = idempotency_key(
        source_id=extraction.get("source_id") or "whitehouse_public",
        source_sha256=source_sha, parser_id=parser_id,
        parser_version=parser_version, rules_version=RULES_VERSION,
        layout_fingerprint=layout_fingerprint)
    has_rows = bool(rows)
    document_disposition = "parsed" if has_rows else "no_rows"
    counts = {"discovered_documents": 1, "archived_documents": 1,
              "parsed_documents": int(document_disposition == "parsed"),
              "failed_documents": 0, "no_row_documents": int(document_disposition == "no_rows"),
              "excluded_documents": 0, **row_counts}
    manifest = {
        "schema_version": RUN_MANIFEST_SCHEMA, "run_id": run_id,
        "workflow": "oge-278e-part6-part7-shadow", "code_commit": code_commit,
        "trigger": trigger, "started_at": started_at, "completed_at": completed_at,
        "source_scope": [extraction.get("source_id") or "whitehouse_public"],
        "documents": [{
            "source_id": extraction.get("source_id") or "whitehouse_public",
            "document_id": document_id, "source_url": extraction.get("source_url"),
            "source_sha256": source_sha, "parser_id": parser_id,
            "parser_version": parser_version, "rules_version": RULES_VERSION,
            "layout_fingerprint": layout_fingerprint, "idempotency_key": document_key,
            "disposition": document_disposition,
            **({"reason": "no_part6_part7_rows"} if not has_rows else {}),
        }],
        "counts": counts, "accounted_rows": len(rows), "outputs": [],
    }
    validate_run_manifest(manifest)
    return {"manifest": manifest, "rows": rows,
            "publication_status": "shadow_only_not_promoted",
            "part7_cross_report_dedup_required": audit.get(
                "part7_cross_report_dedup_required") is True,
            "part7_publication_status": "pending_278t_reconciliation" if audit.get(
                "part7_cross_report_dedup_required") is True else "not_pending"}
