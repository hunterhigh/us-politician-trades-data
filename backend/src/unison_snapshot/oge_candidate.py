"""Conservative source candidate builder for archived OGE 278-T reports."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import re

from .builder import timestamp
from .oge import OgeCatalogError, SCHEMA as CATALOG_SCHEMA
from .oge_reports import (
    EXTRACTION_SCHEMA, GENERIC_PARSER_VERSION, PARSER_VERSION, TRUMP_SEPT_2026_DOCUMENT_ID,
    TRUMP_SEPT_2026_FILING_DATE_EVIDENCE, TRUMP_SEPT_2026_PARSER_VERSION,
    TRUMP_SEPT_2026_SECOND_PASS_VERSION,
    TRUMP_SEPT_2026_STRUCTURAL_PASS_VERSION,
    TRUMP_SEPT_2026_PAGE7_PASS_VERSION,
    TRUMP_SEPT_2026_SOURCE_SHA256, TRUMP_SEPT_2026_SOURCE_URL,
    collapse_direct_catalog_records,
)


_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-^/]{0,31}")
TRUMP_PERSON_ID = "oge:076544f8ba0638cf"


def _fixed_trump_sept_source(extraction: dict, record: dict | None) -> bool:
    return (
        record is not None
        and extraction.get("document_id") == TRUMP_SEPT_2026_DOCUMENT_ID
        and record.get("source_document_id") == TRUMP_SEPT_2026_DOCUMENT_ID
        and extraction.get("source_url") == TRUMP_SEPT_2026_SOURCE_URL
        and record.get("document_url") == TRUMP_SEPT_2026_SOURCE_URL
        and extraction.get("source_sha256") == TRUMP_SEPT_2026_SOURCE_SHA256
        and extraction.get("catalog_filer_name") == record.get("filer_name") ==
        "Trump, Donald J"
        and extraction.get("agency") == record.get("agency") == "White House Office"
        and extraction.get("position_title") == record.get("position_title") == "President"
        and extraction.get("filed_at") == "2026-09-08"
        and extraction.get("filing_date_evidence") ==
        TRUMP_SEPT_2026_FILING_DATE_EVIDENCE
    )


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


def _legacy_default_owner_evidence(legacy: dict, old: dict) -> dict | None:
    """Distinguish the v2 default from an explicit owner using its whole input."""
    from .oge_reports import parse_table_rows, _ACCOUNT_HEADING
    from .oge_transaction_adapter import _row_key, _cells
    if (legacy.get("parser_version") != PARSER_VERSION or old.get("owner") != "Self"
            or old.get("owner_observation_reference") or old.get("owner_disclosure_basis")):
        return None
    inventory = legacy.get("source_rows")
    if not isinstance(inventory, list):
        return None
    physical = [(row["page_number"], _cells(row["cells"])) for row in inventory]
    locators = [index for index, (page, cells) in enumerate(physical)
                if _row_key({"page_number": page, "cells": cells}) == _row_key(old)]
    if len(locators) != 1:
        return None
    # A malformed preceding label does not prove absence of explicit scope.
    for _, cells in physical[:locators[0]]:
        if (len(cells) == 6 and cells[1] and not any((cells[2], cells[3], cells[5]))
                and not _ACCOUNT_HEADING.fullmatch(cells[1])
                and not re.fullmatch(r"(?:filer|self|spouse|dependent child(?:ren)?)(?:'s)? (?:assets|accounts?)", cells[1], re.I)
                and "intentionally" not in cells[1].casefold()):
            return None
    replays = []
    for default in ("Self", "Undisclosed"):
        good, bad = parse_table_rows(physical, source_sha=legacy["source_sha256"], default_owner=default)
        matches = [row for row in good + bad if _row_key(row) == _row_key(old)]
        if len(matches) != 1:
            return None
        replays.append(matches[0])
    fields = ("asset_name", "ticker", "transaction_type", "transaction_date", "amount_low", "amount_high")
    if (replays[0]["owner"] != "Self" or replays[1]["owner"] != "Undisclosed"
            or any(replays[0].get(field) != old.get(field) for field in fields)):
        return None
    return {"source_sha256": legacy["source_sha256"], "document_id": legacy["document_id"],
            "legacy_parser_version": PARSER_VERSION, "source_rows_index": locators[0],
            "page_number": old["page_number"], "cells": deepcopy(old["cells"]),
            "source_rows_sha256": hashlib.sha256(json.dumps(inventory, ensure_ascii=False,
                sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "basis": "whole_saved_source_input_replayed_with_two_defaults_no_explicit_owner"}


def _inherit_generic_legacy(extraction: dict, legacy: dict | None,
                            formal: list[dict], record: dict) -> tuple[dict, dict]:
    """Only an exact source row can carry an old formal identity into v7."""
    from .oge_transaction_adapter import _row_key, adapt_oge_278t_extraction
    result = deepcopy(extraction)
    receipt = {"retained_formal_ids": [row["id"] for row in formal], "field_corrections": [],
               "inherited_ids": [], "unbound_formal_ids": [], "blocked_new_count": 0}
    if len(set(receipt["retained_formal_ids"])) != len(formal):
        raise OgeCatalogError("existing formal report repeats an identity")
    if not formal:
        return result, receipt
    bound = {}
    if legacy is not None:
        if (not isinstance(legacy, dict) or any(legacy.get(field) != extraction.get(field)
                for field in ("document_id", "source_id", "source_sha256", "source_url",
                              "catalog_filer_name", "agency", "position_title")) or
                legacy.get("schema_version") != EXTRACTION_SCHEMA):
            raise OgeCatalogError("legacy extraction differs from generic source")
        # This validates inventory closure/catalog binding without re-extraction.
        adapt_oge_278t_extraction(legacy, record)
        for old in legacy["transactions"]:
            identifier = old.get("extraction_id")
            if identifier in bound:
                raise OgeCatalogError("legacy extraction repeats a formal identity")
            bound[identifier] = old
    proven = {}
    compare = ("owner", "ticker", "transaction_type", "transaction_date",
               "amount_low", "amount_high", "amount_low_exclusive")
    for fact in formal:
        old = bound.get(fact["id"])
        if (old is not None and isinstance(old.get("cells"), list)
                and all(fact.get(field) == old.get(field) for field in compare)
                and fact.get("asset_name") == (f"{old['asset_name']} — {old['account_title']}"
                                               if old.get("account_title") else old.get("asset_name"))
                and fact.get("person_id") == (TRUMP_PERSON_ID if _fixed_trump_sept_source(legacy, record)
                                               else _person_id(_identity_key(record)))
                and fact.get("filed_at") == f"{legacy.get('filed_at')}T00:00:00Z"):
            key = (_row_key(old), old.get("account_title"))
            proven.setdefault(key, []).append(old)
        else:
            receipt["unbound_formal_ids"].append(fact["id"])
    all_new = result["transactions"] + result["quarantined"]
    counts = {}
    for row in all_new:
        key = (_row_key(row), row.get("account_title"))
        counts[key] = counts.get(key, 0) + 1
    for key, rows in proven.items():
        if len(rows) == 1 and counts.get(key) == 1:
            receipt["inherited_ids"].append(rows[0]["extraction_id"])
    inherited = set(receipt["inherited_ids"])
    unmatched_pages = {old.get("page_number") for rows in proven.values() for old in rows
                       if old["extraction_id"] not in inherited}
    qualified, quarantined = [], []
    for disposition, rows in (("qualified", result["transactions"]), ("quarantined", result["quarantined"])):
        for row in rows:
            key = (_row_key(row), row.get("account_title"))
            matches = proven.get(key, [])
            if len(matches) == 1 and matches[0]["extraction_id"] in inherited:
                retained = deepcopy(matches[0])
                retained["legacy_inheritance"] = {"basis": "same_source_exact_cells_and_formal_projection",
                    "source_sha256": extraction["source_sha256"], "new_parser_version": GENERIC_PARSER_VERSION,
                    "new_observation_reasons": row.get("reasons", [])}
                proof = (_legacy_default_owner_evidence(legacy, retained) if
                         row.get("owner") == "Undisclosed" and row.get("owner_disclosure_basis") ==
                         "not_disclosed_in_bound_six_column_table" else None)
                if proof is not None:
                    correction = {"extraction_id": retained["extraction_id"], "field": "owner",
                        "old_value": "Self", "new_value": "Undisclosed",
                        "rule": "approved_v2_default_self_to_undisclosed/v1", "source_reference": proof}
                    retained["owner"] = "Undisclosed"
                    retained["owner_disclosure_basis"] = row["owner_disclosure_basis"]
                    retained["legacy_inheritance"]["field_corrections"] = [correction]
                    receipt["field_corrections"].append(correction)
                qualified.append(retained)
            elif receipt["unbound_formal_ids"] or legacy is None or row.get("page_number") in unmatched_pages:
                reason = "legacy_source_binding_missing" if receipt["unbound_formal_ids"] or legacy is None else "legacy_row_correspondence_unresolved"
                quarantined.append({**row, "reasons": sorted(set(row.get("reasons", []) + [reason]))})
                receipt["blocked_new_count"] += 1
            else:
                (qualified if disposition == "qualified" else quarantined).append(row)
    result["transactions"], result["quarantined"] = qualified, quarantined
    return result, receipt


def build_oge_candidate(catalog: dict, extractions: list[dict], base: dict, *,
                        data_cutoff_at: str,
                        catalog_history: list[dict] | None = None,
                        saved_observations_by_document: dict | None = None,
                        legacy_extractions_by_document: dict | None = None) -> tuple[dict, dict]:
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
    saved = saved_observations_by_document or {}
    if not isinstance(saved, dict) or set(saved) - set(direct_by_id):
        raise OgeCatalogError("saved observation documents are outside the catalog")
    retained_from_history = len(set(direct_by_id) - set(current_direct_by_id))
    legacy_inputs = legacy_extractions_by_document or {}
    if not isinstance(legacy_inputs, dict) or set(legacy_inputs) - set(direct_by_id):
        raise OgeCatalogError("legacy extraction documents are outside the catalog")

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
        generic = extraction.get("parser_version") == GENERIC_PARSER_VERSION
        retained = [row for row in base.get("transactions", []) if generic and
                    row.get("source_id") == "oge" and row.get("filing_id") == document_id
                    and row.get("verification_status") == "official_matched"]
        if any(row.get("source_url") != extraction.get("source_url") for row in retained):
            raise OgeCatalogError("existing formal facts differ from generic report URL")
        inheritance = None
        if generic and record is not None:
            extraction, inheritance = _inherit_generic_legacy(extraction, legacy_inputs.get(document_id), retained, record)
        observed = document_id in saved
        if observed:
            from .oge_transaction_adapter import materialize_oge_278t_observations
            extraction = materialize_oge_278t_observations(extraction, record, **saved[document_id])
        reasons = []
        fixed_trump = _fixed_trump_sept_source(extraction, record)
        supported_parser = (
            observed or extraction.get("parser_version") in {PARSER_VERSION, GENERIC_PARSER_VERSION}
            or (extraction.get("parser_version") in {
                TRUMP_SEPT_2026_PARSER_VERSION, TRUMP_SEPT_2026_SECOND_PASS_VERSION,
                TRUMP_SEPT_2026_STRUCTURAL_PASS_VERSION,
                TRUMP_SEPT_2026_PAGE7_PASS_VERSION}
                and fixed_trump)
        )
        if (extraction.get("schema_version") != EXTRACTION_SCHEMA or
                not supported_parser or
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
        from .oge_transaction_adapter import generic_cover_evidence_valid, open_amount_qualified, amount_range_qualified
        if generic and not generic_cover_evidence_valid(extraction):
            reasons.append("cover_date_evidence_invalid")
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
        retained_by_id = {row["id"]: row for row in retained}
        row_quarantines = list(source_quarantine)
        for row in source_rows:
            row_reasons = list(reasons)
            if not isinstance(row, dict):
                row_quarantines.append({"reasons": ["transaction_row_invalid"]})
                continue
            extraction_id = row.get("extraction_id")
            if extraction_id in retained_by_id and row.get("legacy_inheritance"):
                if extraction_id in promoted_ids:
                    raise OgeCatalogError("inherited formal identity is duplicated")
                retained_fact = deepcopy(retained_by_id.pop(extraction_id))
                for correction in row["legacy_inheritance"].get("field_corrections", []):
                    if (correction["rule"] != "approved_v2_default_self_to_undisclosed/v1" or
                            correction["field"] != "owner" or retained_fact["owner"] != correction["old_value"]):
                        raise OgeCatalogError("unsupported inherited formal field correction")
                    retained_fact["owner"] = correction["new_value"]
                transactions.append(retained_fact)
                promoted_ids.add(extraction_id)
                report_promoted += 1
                continue
            if (not isinstance(extraction_id, str) or extraction_id in seen_extractions or
                    not extraction_id.startswith("oge-278t:")):
                row_reasons.append("extraction_id_invalid_or_duplicated")
            else:
                seen_extractions.add(extraction_id)
            if row.get("transaction_type") not in {"purchase", "sale", "exchange"}:
                row_reasons.append("transaction_type_not_supported")
            if row.get("owner") not in {"Self", "Spouse", "Dependent Child"} and not (
                    (observed or generic) and row.get("owner") == "Undisclosed"
                    and row.get("owner_disclosure_basis") == "not_disclosed_in_bound_six_column_table"
                    and (generic and generic_cover_evidence_valid(extraction) or
                         row.get("observation_reference", {}).get("source_sha256") == extraction.get("source_sha256"))):
                row_reasons.append("owner_not_supported")
            is_open = open_amount_qualified(row, extraction.get("parser_version"))
            if not is_open and (type(row.get("amount_low")) is not int or type(row.get("amount_high")) is not int):
                row_reasons.append("amount_range_invalid")
            elif (generic or observed) and not amount_range_qualified(row, extraction.get("parser_version")):
                row_reasons.append("amount_range_invalid")
            asset_name = row.get("asset_name")
            if not isinstance(asset_name, str) or not asset_name.strip():
                row_reasons.append("asset_name_invalid")
            ticker = row.get("ticker")
            if ticker is not None and (not isinstance(ticker, str) or not _TICKER.fullmatch(ticker)):
                row_reasons.append("ticker_invalid")
            try:
                transaction_day = datetime.strptime(row.get("transaction_date"), "%Y-%m-%d").date()
                if filed_day is not None and transaction_day > filed_day:
                    row_reasons.append("date_sequence_invalid")
            except (TypeError, ValueError):
                row_reasons.append("transaction_date_invalid")
            if row_reasons or key is None or record is None:
                row_quarantines.append({"extraction_id": extraction_id,
                                        "reasons": sorted(set(row_reasons))})
                continue
            person_id = TRUMP_PERSON_ID if fixed_trump else _person_id(key)
            people.setdefault(person_id, {
                "id": person_id,
                "display_name": "Donald Trump" if fixed_trump else record["filer_name"],
                "short_name": "Trump" if fixed_trump else _short_name(record["filer_name"]),
                "role": ("President of the United States of America" if fixed_trump else
                         record["position_title"]),
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
                "asset_name": f"{asset_name} — {row['account_title']}" if row.get("account_title") else asset_name,
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
                **({"amount_low_exclusive": True} if is_open else {}),
                "position_effect": "unknown",
                "position_effect_basis": None,
                "source_id": "oge",
                "source": "U.S. Office of Government Ethics",
                "source_url": extraction["source_url"],
                "verification_status": "official_matched",
            })
            report_promoted += 1
        for fact in retained_by_id.values():
            if fact["id"] in promoted_ids:
                raise OgeCatalogError("retained formal identity is duplicated")
            transactions.append(deepcopy(fact))
            promoted_ids.add(fact["id"])
            report_promoted += 1
        if retained:
            needed_people = {row["person_id"] for row in retained}
            for person in base.get("people", []):
                if person.get("id") in needed_people:
                    people[person["id"]] = deepcopy(person)
            if needed_people - set(people):
                raise OgeCatalogError("retained formal people are absent from base")
        audit_reports.append({
            "document_id": document_id,
            **({"legacy_inheritance": inheritance} if inheritance is not None else {}),
            **({"cover_date_evidence": extraction.get("cover_date_evidence"),
                "source_sha256": extraction["source_sha256"]} if generic else {}),
            **({"source_sha256": extraction["source_sha256"],
                "observation_receipt": extraction["observation_receipt"]}
               if observed else {}),
            **({"source_sha256": extraction["source_sha256"]}
               if "manual_filing_date_evidence" in extraction else {}),
            **({"manual_filing_date_evidence": extraction["manual_filing_date_evidence"]}
               if "manual_filing_date_evidence" in extraction else {}),
            **({"filing_date_evidence": TRUMP_SEPT_2026_FILING_DATE_EVIDENCE}
               if fixed_trump else {}),
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
