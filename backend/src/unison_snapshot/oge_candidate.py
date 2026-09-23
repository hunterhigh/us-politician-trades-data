"""Conservative source candidate builder for archived OGE 278-T reports."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import re

from .builder import timestamp
from .oge import OgeCatalogError, SCHEMA as CATALOG_SCHEMA
from .oge_reports import (
    EXTRACTION_SCHEMA, PARSER_VERSION, collapse_direct_catalog_records,
)


_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-^/]{0,31}")


def _identity_key(record: dict) -> tuple[str, str, str]:
    values = []
    for field in ("filer_name", "agency", "position_title"):
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            raise OgeCatalogError(f"OGE identity has invalid {field}")
        values.append(" ".join(value.casefold().replace(",", " ").split()))
    return tuple(values)  # type: ignore[return-value]


def _person_id(key: tuple[str, str, str]) -> str:
    return "oge:" + hashlib.sha256("|".join(key).encode()).hexdigest()[:16]


def _short_name(value: str) -> str:
    if "," in value:
        surname = value.split(",", 1)[0].strip()
        if surname:
            return surname
    parts = value.split()
    if not parts:
        raise OgeCatalogError("OGE identity has no display name")
    return parts[-1]


def _instrument(asset_name: str, ticker: str | None) -> str:
    value = asset_name.casefold()
    if " etf" in value or value.endswith("etf"):
        return "ETF"
    if "mutual fund" in value or "fund" in value:
        return "Mutual Fund"
    if "bond" in value or "treasury" in value or "note" in value:
        return "Bond"
    if ticker or "stock" in value or "inc." in value or "corp." in value:
        return "Stock"
    return "Unspecified"


def build_oge_candidate(catalog: dict, extractions: list[dict], base: dict, *,
                        data_cutoff_at: str,
                        catalog_history: list[dict] | None = None) -> tuple[dict, dict]:
    """Build a frontend-compatible OGE projection and a quarantine audit."""

    if not isinstance(catalog, dict) or catalog.get("schema_version") != CATALOG_SCHEMA:
        raise OgeCatalogError("OGE candidate catalog is invalid")
    if not isinstance(extractions, list) or any(not isinstance(row, dict) for row in extractions):
        raise OgeCatalogError("OGE candidate extractions are invalid")
    try:
        cutoff = timestamp(data_cutoff_at)
    except (TypeError, ValueError):
        raise OgeCatalogError("OGE candidate cutoff is invalid") from None
    if not isinstance(base, dict) or base.get("meta", {}).get("is_demo") is not False:
        raise OgeCatalogError("OGE candidate base must be production data")

    catalog_rows = catalog.get("transactions")
    if not isinstance(catalog_rows, list):
        raise OgeCatalogError("OGE candidate catalog has no transaction rows")
    direct_occurrences = []
    request_required = 0
    for record in catalog_rows:
        if not isinstance(record, dict):
            raise OgeCatalogError("OGE candidate catalog row is invalid")
        if record.get("access_method") == "request_required":
            request_required += 1
            continue
        if record.get("access_method") != "direct_pdf":
            raise OgeCatalogError("OGE candidate catalog access method is invalid")
        direct_occurrences.append(record)
    current_direct, duplicate_occurrences = collapse_direct_catalog_records(direct_occurrences)
    current_direct_by_id = {record["source_document_id"]: record for record in current_direct}
    all_direct = list(current_direct)
    history_count = 0
    for historical in catalog_history or []:
        if not isinstance(historical, dict) or historical.get("schema_version") != CATALOG_SCHEMA:
            raise OgeCatalogError("OGE candidate catalog history is invalid")
        rows = historical.get("transactions")
        if not isinstance(rows, list):
            raise OgeCatalogError("OGE candidate catalog history has no transaction rows")
        historical_direct = [record for record in rows if isinstance(record, dict) and
                             record.get("access_method") == "direct_pdf"]
        all_direct.extend(historical_direct)
        history_count += 1
    direct, _ = collapse_direct_catalog_records(all_direct)
    direct_by_id = {record["source_document_id"]: record for record in direct}
    retained_from_history = len(set(direct_by_id) - set(current_direct_by_id))

    identity_variants: dict[str, set[tuple[str, str, str]]] = {}
    for record in direct_by_id.values():
        key = _identity_key(record)
        identity_variants.setdefault(key[0], set()).add(key)

    transactions = []
    people: dict[str, dict] = {}
    audit_reports = []
    seen_extractions: set[str] = set()
    seen_document_ids: set[str] = set()
    promoted_ids: set[str] = set()
    for extraction in sorted(extractions, key=lambda row: str(row.get("document_id"))):
        document_id = extraction.get("document_id")
        if not isinstance(document_id, str) or document_id in seen_document_ids:
            raise OgeCatalogError("OGE candidate extraction document ID is missing or duplicated")
        seen_document_ids.add(document_id)
        record = direct_by_id.get(document_id)
        reasons = []
        if (extraction.get("schema_version") != EXTRACTION_SCHEMA or
                extraction.get("parser_version") != PARSER_VERSION or
                extraction.get("source_id") != "oge"):
            reasons.append("extraction_contract_invalid")
        if record is None:
            reasons.append("catalog_record_missing")
        elif extraction.get("source_url") != record.get("document_url"):
            reasons.append("source_url_mismatch")
        elif any(extraction.get(extraction_field) != record.get(catalog_field)
                 for extraction_field, catalog_field in (
                     ("catalog_filer_name", "filer_name"), ("agency", "agency"),
                     ("position_title", "position_title"),
                     ("catalog_added_date", "catalog_added_date"))):
            reasons.append("catalog_binding_mismatch")
        if extraction.get("evidence_complete") is not True or extraction.get("document_reasons"):
            reasons.append("document_evidence_incomplete")
        if record is not None and record.get("pending_final_oge_disposition") is True:
            reasons.append("pending_final_oge_disposition")
        if record is not None and record.get("amended_label"):
            reasons.append("amendment_relationship_unresolved")
        key = _identity_key(record) if record is not None else None
        if key is not None and len(identity_variants.get(key[0], set())) != 1:
            reasons.append("identity_ambiguous")
        filed = extraction.get("filed_at")
        try:
            filed_day = datetime.strptime(filed, "%Y-%m-%d").date()
            if datetime.combine(filed_day, datetime.min.time(), cutoff.tzinfo) > cutoff:
                reasons.append("filing_exceeds_cutoff")
        except (TypeError, ValueError):
            filed_day = None
            reasons.append("filed_at_invalid")

        source_rows = extraction.get("transactions")
        source_quarantine = extraction.get("quarantined")
        if not isinstance(source_rows, list) or not isinstance(source_quarantine, list):
            reasons.append("extraction_rows_invalid")
            source_rows, source_quarantine = [], []
        report_promoted = 0
        row_quarantines = list(source_quarantine)
        for row in source_rows:
            row_reasons = list(reasons)
            if not isinstance(row, dict):
                row_quarantines.append({"reasons": ["transaction_row_invalid"]})
                continue
            extraction_id = row.get("extraction_id")
            if (not isinstance(extraction_id, str) or extraction_id in seen_extractions or
                    not extraction_id.startswith("oge-278t:")):
                row_reasons.append("extraction_id_invalid_or_duplicated")
            else:
                seen_extractions.add(extraction_id)
            if row.get("transaction_type") not in {"purchase", "sale"}:
                row_reasons.append("transaction_type_not_supported")
            if row.get("owner") not in {"Self", "Spouse", "Dependent Child"}:
                row_reasons.append("owner_not_supported")
            if type(row.get("amount_low")) is not int or type(row.get("amount_high")) is not int:
                row_reasons.append("amount_range_invalid")
            asset_name = row.get("asset_name")
            if not isinstance(asset_name, str) or not asset_name.strip():
                row_reasons.append("asset_name_invalid")
            ticker = row.get("ticker")
            if ticker is not None and (not isinstance(ticker, str) or not _TICKER.fullmatch(ticker)):
                row_reasons.append("ticker_invalid")
            try:
                transaction_day = datetime.strptime(row.get("transaction_date"), "%Y-%m-%d").date()
                if filed_day is None or transaction_day > filed_day:
                    row_reasons.append("date_sequence_invalid")
            except (TypeError, ValueError):
                row_reasons.append("transaction_date_invalid")
            if row_reasons or key is None or record is None:
                row_quarantines.append({"extraction_id": extraction_id,
                                        "reasons": sorted(set(row_reasons))})
                continue
            person_id = _person_id(key)
            people.setdefault(person_id, {
                "id": person_id,
                "display_name": record["filer_name"],
                "short_name": _short_name(record["filer_name"]),
                "role": record["position_title"],
                "office_type": record["agency"],
                "chamber": None,
                "party": None,
                "state": None,
                "disclosure_authority": "oge",
                "priority": False,
                "priority_reason": None,
                "portrait_url": None,
            })
            fact_id = extraction_id
            if fact_id in promoted_ids:
                raise OgeCatalogError("OGE candidate duplicates a promoted transaction ID")
            promoted_ids.add(fact_id)
            transactions.append({
                "id": fact_id,
                "filing_id": document_id,
                "person_id": person_id,
                "owner": row["owner"],
                "asset_name": asset_name,
                "ticker": ticker,
                "ticker_mapping_basis": "filing_explicit" if ticker else None,
                "instrument_type": _instrument(asset_name, ticker),
                "option_type": None,
                "strike_price": None,
                "expiration_date": None,
                "transaction_type": row["transaction_type"],
                "transaction_date": row["transaction_date"],
                "filed_at": f"{filed}T00:00:00Z",
                "amount_low": row["amount_low"],
                "amount_high": row["amount_high"],
                "position_effect": "unknown",
                "position_effect_basis": None,
                "source_id": "oge",
                "source": "U.S. Office of Government Ethics",
                "source_url": extraction["source_url"],
                "verification_status": "official_matched",
            })
            report_promoted += 1
        audit_reports.append({
            "document_id": document_id,
            "promoted_count": report_promoted,
            "quarantined_count": len(row_quarantines),
            "document_reasons": sorted(set(reasons)),
            "quarantined": row_quarantines,
        })

    if seen_document_ids != set(direct_by_id):
        raise OgeCatalogError("OGE candidate extractions do not close over direct report history")

    candidate = deepcopy(base)
    candidate["meta"]["data_cutoff_at"] = data_cutoff_at
    candidate["meta"]["subtitle"] = "OGE 真实 278-T 候选数据；申请型文件和异常记录保持隔离"
    candidate["people"] = [people[key] for key in sorted(people)]
    candidate["transactions"] = sorted(transactions, key=lambda row: row["id"])
    candidate["reported_holdings"] = []
    candidate["security_market_data"] = []
    health = {
        "source_id": "oge",
        "source": "U.S. Office of Government Ethics",
        "source_type": "official_disclosure",
        "source_url": "https://www.oge.gov/web/OGE.nsf/Officials%20Individual%20Disclosures%20Search%20Collection?OpenForm",
        "status": "partial",
        "last_checked_at": data_cutoff_at,
        "last_successful_sync_at": data_cutoff_at,
        "data_cutoff_at": data_cutoff_at,
        "detail": (f"{len(direct_by_id)} direct 278-T documents; {len(extractions)} PDFs extracted; "
                   f"{len(transactions)} transactions qualified; {request_required} request-required "
                   f"catalog entries were not automated; {retained_from_history} direct documents "
                   "were retained from archived catalog history."),
    }
    current_health = candidate.get("source_health")
    if not isinstance(current_health, list):
        raise OgeCatalogError("OGE candidate base has no source health array")
    candidate["source_health"] = [health if row.get("source_id") == "oge" else row
                                  for row in current_health]
    if not any(row.get("source_id") == "oge" for row in current_health):
        candidate["source_health"].append(health)
    audit = {
        "schema_version": "oge-candidate-audit/v1",
        "source_id": "oge",
        "data_cutoff_at": data_cutoff_at,
        "catalog_direct_count": len(direct_by_id),
        "catalog_direct_occurrence_count": len(direct_occurrences),
        "current_catalog_direct_count": len(current_direct_by_id),
        "current_catalog_direct_occurrence_count": len(direct_occurrences),
        "duplicate_catalog_occurrence_count": duplicate_occurrences,
        "retained_historical_direct_count": retained_from_history,
        "catalog_history_count": history_count,
        "request_required_count": request_required,
        "extraction_count": len(extractions),
        "promoted_transaction_count": len(transactions),
        "quarantined_row_count": sum(row["quarantined_count"] for row in audit_reports),
        "reports": audit_reports,
    }
    return candidate, audit
