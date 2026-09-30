"""Shadow adapter from archived OGE 278-T extracts to the common ledger."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Any

from .oge import OgeCatalogError
from .oge_reports import (
    TRUMP_SEPT_2026_DOCUMENT_ID, TRUMP_SEPT_2026_SOURCE_SHA256,
    collapse_direct_catalog_records,
)
from .pipeline_ledger import (
    CANDIDATE_ROW_SCHEMA, OBSERVATION_SCHEMA, idempotency_key,
    validate_candidate_row,
)


PARSER_ID = "oge-278t-pdf"
RULES_VERSION = "oge-278t-row-qualification/v1"
_STRUCTURAL_LABELS = ("description", "type", "date", "notification", "amount")
_AMOUNT_RANGES = {(1001, 15000), (15001, 50000), (50001, 100000),
                  (100001, 250000), (250001, 500000), (500001, 1000000),
                  (1000001, 5000000), (5000001, 25000000),
                  (25000001, 50000000)}


def _cells(value: object) -> list[str]:
    if not isinstance(value, list):
        raise OgeCatalogError("OGE 278-T source row cells are missing")
    return [" ".join(item.split()) if isinstance(item, str) else ""
            for item in value]


def _structural_disposition(cells: list[str]) -> tuple[str, str] | None:
    if not any(cells):
        return "excluded", "blank_extracted_row"
    joined = " ".join(cells).casefold()
    if cells[0] == "#" or all(label in joined for label in _STRUCTURAL_LABELS):
        return "excluded", "table_header"
    if len(cells) == 6 and not any((cells[2], cells[3], cells[5])):
        description = cells[1].casefold()
        if re.fullmatch(r"(?:investment|retirement) account\s*#[0-9]+", description):
            return "excluded", "account_section_heading"
        if "intentionally left blank" in description or "left intentionally blank" in description:
            return "excluded", "explicit_blank_notice"
    if cells[0].casefold().startswith(("endnot", "transacti")):
        return "excluded", "report_footer"
    return None


def _row_key(row: dict[str, Any]) -> str:
    payload = [row.get("page_number"), row.get("cells")]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def adapt_oge_278t_extraction(extraction: dict, catalog_record: dict, *,
                              run_id: str = "offline-replay") -> dict:
    """Map a parsed, archived 278-T document to ledger rows with full accounting.

    This adapter is offline only. It requires the parser's extracted table rows,
    exact catalog binding, and source bytes hash; it has no collection or
    publication capability.
    """
    if not isinstance(extraction, dict) or not isinstance(catalog_record, dict):
        raise OgeCatalogError("OGE 278-T adapter inputs must be objects")
    if not isinstance(run_id, str) or not run_id.strip():
        raise OgeCatalogError("OGE 278-T adapter requires a run ID")
    collapse_direct_catalog_records([catalog_record])
    source_sha = extraction.get("source_sha256")
    if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha):
        raise OgeCatalogError("OGE 278-T adapter requires the archived source hash")
    source_rows = extraction.get("source_rows")
    transactions = extraction.get("transactions")
    quarantined = extraction.get("quarantined")
    if (not isinstance(source_rows, list) or not isinstance(transactions, list) or
            not isinstance(quarantined, list)):
        raise OgeCatalogError("OGE 278-T adapter requires source rows and parser dispositions")
    if (extraction.get("source_id") != "oge" or
            catalog_record.get("source_id", "oge") != "oge" or
            catalog_record.get("document_type", "278_transaction") != "278_transaction" or
            catalog_record.get("access_method") != "direct_pdf" or
            catalog_record.get("source_document_id") != extraction.get("document_id") or
            catalog_record.get("document_url") != extraction.get("source_url") or
            catalog_record.get("filer_name") != extraction.get("catalog_filer_name") or
            catalog_record.get("agency") != extraction.get("agency") or
            catalog_record.get("position_title") != extraction.get("position_title")):
        report_reasons = ["catalog_identity_or_document_mismatch"]
    else:
        report_reasons = []
    if extraction.get("evidence_complete") is not True or extraction.get("document_reasons"):
        report_reasons.append("document_evidence_incomplete")
    if catalog_record.get("pending_final_oge_disposition") is True:
        report_reasons.append("pending_final_oge_disposition")
    if catalog_record.get("amended_label"):
        report_reasons.append("amendment_relationship_unresolved")
    filed_at = extraction.get("filed_at")
    try:
        filed_day = date.fromisoformat(filed_at)
    except (TypeError, ValueError):
        filed_day = None
        report_reasons.append("filed_at_invalid")

    dispositions: dict[str, list[tuple[str, dict, list[str]]]] = {}
    for disposition, rows in (("qualified", transactions), ("quarantined", quarantined)):
        for row in rows:
            if not isinstance(row, dict):
                raise OgeCatalogError("OGE parser emitted a malformed row")
            key = json.dumps([row.get("page_number"), row.get("cells")],
                             ensure_ascii=False, separators=(",", ":"))
            dispositions.setdefault(key, []).append((disposition, row,
                                                        list(row.get("reasons", []))))

    parser_version = extraction.get("parser_version")
    if not isinstance(parser_version, str) or not parser_version:
        raise OgeCatalogError("OGE 278-T adapter requires a parser version")
    layout = hashlib.sha256(b"oge-278t-six-column-table/v1").hexdigest()
    idempotency = idempotency_key(
        source_id="oge", source_sha256=source_sha, parser_id=PARSER_ID,
        parser_version=parser_version, rules_version=RULES_VERSION,
        layout_fingerprint=layout)
    output_rows = []
    accounted = {"qualified": 0, "quarantined": 0, "excluded": 0, "unrecognized": 0}
    for physical_index, raw in enumerate(source_rows, start=1):
        if not isinstance(raw, dict) or type(raw.get("page_number")) is not int:
            raise OgeCatalogError("OGE 278-T adapter source row locator is invalid")
        recovery = raw.get("source_bound_row_recovery")
        if recovery is not None and (extraction.get("document_id") != TRUMP_SEPT_2026_DOCUMENT_ID or
                source_sha != TRUMP_SEPT_2026_SOURCE_SHA256 or
                not isinstance(recovery, dict) or
                recovery.get("basis") != "fixed_source_visual_table_row_recovery" or
                recovery.get("page_number") != raw["page_number"] or
                not isinstance(recovery.get("printed_row_number"), int)):
            raise OgeCatalogError("OGE visual source row recovery is not source-bound")
        evidence_kind = "visual_table_row_recovery" if recovery else "table_row"
        raw_cells = _cells(raw.get("cells"))
        key = _row_key({"page_number": raw["page_number"], "cells": raw_cells})
        parsed = dispositions.get(key, [])
        reasons: list[str] = []
        normalized: dict[str, object] = {}
        if parsed:
            disposition, parser_row, parser_reasons = parsed.pop(0)
            if recovery is not None and (disposition != "qualified" or
                    parser_row.get("source_bound_row_recovery") != recovery or
                    parser_row.get("cells") != raw.get("cells") or
                    parser_row.get("row_number") != recovery["printed_row_number"]):
                raise OgeCatalogError("OGE visual source row recovery differs from parser disposition")
            reasons.extend(parser_reasons)
            normalized = parser_row
            if disposition == "qualified":
                transaction_day = normalized.get("transaction_date")
                try:
                    parsed_day = date.fromisoformat(transaction_day)
                except (TypeError, ValueError):
                    parsed_day = None
                if normalized.get("transaction_type") not in {"purchase", "sale"}:
                    reasons.append("transaction_type_not_qualified")
                if (type(normalized.get("amount_low")) is not int or
                        type(normalized.get("amount_high")) is not int or
                        (normalized.get("amount_low"), normalized.get("amount_high"))
                        not in _AMOUNT_RANGES):
                    reasons.append("amount_range_not_qualified")
                if (parsed_day is None or filed_day is None or parsed_day > filed_day):
                    reasons.append("transaction_date_not_qualified")
                if not isinstance(normalized.get("asset_name"), str) or not normalized["asset_name"].strip():
                    reasons.append("asset_name_not_qualified")
                if normalized.get("owner") not in {"Self", "Spouse", "Dependent Child"}:
                    reasons.append("owner_not_qualified")
                if reasons:
                    disposition = "quarantined"
        else:
            if recovery is not None:
                raise OgeCatalogError("OGE visual source row recovery has no parser disposition")
            structural = _structural_disposition(raw_cells)
            if structural:
                disposition, reason = structural
                reasons.append(reason)
            else:
                disposition = "unrecognized"
                reasons.append("parser_did_not_dispose_extracted_row")
        if report_reasons and disposition == "qualified":
            disposition = "quarantined"
            reasons.extend(report_reasons)
        reasons = sorted(set(reasons))
        fields = {
            "row_number": (raw_cells[0] if raw_cells else None,
                           normalized.get("row_number")),
            "asset_name": (raw_cells[1] if len(raw_cells) > 1 else None,
                           normalized.get("asset_name")),
            "transaction_type": (raw_cells[2] if len(raw_cells) > 2 else None,
                                 normalized.get("transaction_type")),
            "transaction_date": (raw_cells[3] if len(raw_cells) > 3 else None,
                                 normalized.get("transaction_date")),
            "owner": (normalized.get("owner"), normalized.get("owner")),
            "amount_low": (raw_cells[5] if len(raw_cells) > 5 else None,
                           normalized.get("amount_low")),
            "amount_high": (raw_cells[5] if len(raw_cells) > 5 else None,
                            normalized.get("amount_high")),
        }
        observations = []
        for field, (raw_value, value) in fields.items():
            observation_id = f"{physical_index}:{field}"
            observations.append({
                "schema_version": OBSERVATION_SCHEMA,
                "observation_id": observation_id,
                "field": field,
                "raw_value": raw_value if isinstance(raw_value, (str, int, float, bool)) else None,
                "normalized_value": value if isinstance(value, (str, int, float, bool)) else None,
                "method": "embedded_text",
                "confidence": None,
                "selected": value is not None,
                "required_for_projection": field in {"asset_name", "transaction_type",
                    "transaction_date", "owner", "amount_low", "amount_high"},
                "locations": [{"page": raw["page_number"],
                               "row_locator": str(physical_index),
                               "kind": evidence_kind}],
                "conditions": {"source_field": field},
            })
        candidate = {
            "schema_version": CANDIDATE_ROW_SCHEMA,
            "candidate_id": f"{source_sha[:16]}:{physical_index}",
            "run_id": run_id,
            "source": {"source_id": "oge", "document_id": extraction["document_id"],
                       "source_url": extraction["source_url"], "source_sha256": source_sha},
            "parser": {"parser_id": PARSER_ID, "parser_version": parser_version,
                       "rules_version": RULES_VERSION, "layout_fingerprint": layout},
            "idempotency_key": idempotency,
            "disposition": disposition,
            "reasons": reasons,
            "unresolved_conflicts": [],
            "observations": observations,
            "evidence_locations": [{"page": raw["page_number"],
                                    "row_locator": str(physical_index),
                                    "kind": evidence_kind}],
            "required_projection_fields": (["asset_name", "transaction_type", "transaction_date",
                                             "owner", "amount_low", "amount_high"]
                                            if disposition == "qualified" else []),
        }
        validate_candidate_row(candidate)
        output_rows.append(candidate)
        accounted[disposition] += 1
    if any(queue for queue in dispositions.values()):
        raise OgeCatalogError("OGE parser disposition has rows absent from source row inventory")
    if sum(accounted.values()) != len(source_rows):
        raise OgeCatalogError("OGE 278-T row disposition accounting failed")
    return {"schema_version": "oge-278t-ledger-adapter/v1",
            "document_id": extraction.get("document_id"),
            "source_sha256": source_sha,
            "parser_id": PARSER_ID,
            "parser_version": parser_version,
            "rules_version": RULES_VERSION,
            "layout_fingerprint": layout,
            "idempotency_key": idempotency,
            "row_count": len(source_rows),
            "counts": accounted,
            "rows": output_rows}
