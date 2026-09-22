"""Read-only eligibility audit for public White House OGE 278-T extractions.

The official OGE catalog proves a filer identity, not an individual filing's
date or the equivalence of two PDFs.  This module never publishes facts.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import re
from urllib.parse import urlsplit

from .oge import OgeCatalogError
from .oge_candidate import _identity_key, _person_id
from .oge_whitehouse import AGENCIES, SCHEMA as COVERAGE_SCHEMA
from .whitehouse_278t import (
    EXTRACTION_SCHEMA, PARSER_VERSION, _first_last,
    quarantine_duplicate_report_groups,
)


AUDIT_SCHEMA = "whitehouse-278t-eligibility-audit/v1"
_OFFICE_LABELS = {
    "white house": "white house office",
    "white house office": "white house office",
    "office of the vice president": "office of the vice president",
}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_EXTRACTION_ID = re.compile(r"oge-278t:[0-9a-f]{24}")
_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-^/]{0,31}")


def _words(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", value.casefold().replace("&", " and ")).split())


def _role(value: str) -> str:
    # Integrity.gov prints the administration after the position.  It is not
    # part of the OGE catalog's position_title field.
    core = re.sub(r",\s*[A-Za-z]+(?:[-–][A-Za-z]+)?\s*\(20\d{2}\)\s*$", "", value)
    return _words(core)


def _trade_keys(row: dict, person_id: str) -> set[tuple]:
    common = (person_id, row.get("owner"), row.get("transaction_type"),
              row.get("transaction_date"), row.get("amount_low"), row.get("amount_high"))
    keys = {(common, "asset", _words(row.get("asset_name") or ""))}
    ticker = row.get("ticker")
    if isinstance(ticker, str) and ticker:
        keys.add((common, "ticker", ticker.upper()))
    return keys


def _day(value: object) -> date | None:
    if not isinstance(value, str) or not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _row_reasons(row: dict, filing_day: date | None) -> list[str]:
    reasons = []
    if not isinstance(row.get("extraction_id"), str) or not _EXTRACTION_ID.fullmatch(
            row["extraction_id"]):
        reasons.append("transaction_extraction_id_invalid")
    if type(row.get("row_number")) is not int or row["row_number"] <= 0:
        reasons.append("transaction_row_number_invalid")
    if row.get("owner") not in {"Self", "Spouse", "Dependent Child"}:
        reasons.append("transaction_owner_unsupported")
    if row.get("transaction_type") not in {"purchase", "sale"}:
        reasons.append("transaction_type_unsupported")
    transaction_day = _day(row.get("transaction_date"))
    if transaction_day is None:
        reasons.append("transaction_date_invalid")
    elif filing_day is not None and transaction_day > filing_day:
        reasons.append("transaction_after_filer_signature")
    if (type(row.get("amount_low")) is not int or
            type(row.get("amount_high")) is not int or
            row["amount_low"] <= 0 or row["amount_high"] < row["amount_low"]):
        reasons.append("transaction_amount_range_invalid")
    if not isinstance(row.get("asset_name"), str) or not row["asset_name"].strip():
        reasons.append("transaction_asset_missing")
    ticker = row.get("ticker")
    if ticker is not None and (not isinstance(ticker, str) or not _TICKER.fullmatch(ticker)):
        reasons.append("transaction_ticker_invalid")
    return reasons


def _catalog_identity(report: dict, catalog_rows: list[dict]) -> tuple[dict | None, list[str]]:
    name = report.get("pdf_filer_name")
    role = report.get("pdf_position_title")
    office = report.get("pdf_agency_label")
    if not all(isinstance(value, str) and value.strip() for value in (name, role, office)):
        return None, ["pdf_identity_fields_missing"]
    agency = _OFFICE_LABELS.get(_words(office))
    if agency is None:
        return None, ["pdf_agency_unrecognized"]
    named = [row for row in catalog_rows
             if row["agency"].casefold() == agency and
             _first_last(row["filer_name"]) == _first_last(name)]
    if not named:
        return None, ["catalog_filer_identity_missing"]
    matched = [row for row in named if _role(row["position_title"]) == _role(role)]
    if not matched:
        return None, ["catalog_position_mismatch"]
    identities: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in matched:
        identities[_identity_key(row)].append(row)
    if len(identities) != 1:
        return None, ["catalog_identity_ambiguous"]
    selected = next(iter(identities.values()))
    return {**selected[0], "matched_catalog_entry_ids": sorted({
        row["catalog_entry_id"] for row in selected
        if isinstance(row.get("catalog_entry_id"), str)})}, []


def audit_whitehouse_278t(extractions: list[dict], oge_whitehouse_coverage: dict,
                          existing_oge_candidate: dict) -> dict:
    """Assess each archived public PDF and row without modifying source data.

    The caller must provide a verified OGE coverage artifact and the current
    production OGE candidate.  This function has no network or file writes.
    """

    if (not isinstance(extractions, list) or
            not isinstance(oge_whitehouse_coverage, dict) or
            oge_whitehouse_coverage.get("schema_version") != COVERAGE_SCHEMA or
            not isinstance(oge_whitehouse_coverage.get("reports"), list)):
        raise OgeCatalogError("White House 278-T audit input is invalid")
    if (not isinstance(existing_oge_candidate, dict) or
            not isinstance(existing_oge_candidate.get("meta"), dict) or
            existing_oge_candidate["meta"].get("is_demo") is not False or
            not isinstance(existing_oge_candidate.get("transactions"), list)):
        raise OgeCatalogError("White House 278-T audit requires a production OGE candidate")
    catalog_rows = [row for row in oge_whitehouse_coverage["reports"]
                    if isinstance(row, dict) and row.get("document_type") == "278t" and
                    isinstance(row.get("agency"), str) and
                    row["agency"].casefold() in AGENCIES]
    if not catalog_rows or any(not all(isinstance(row.get(field), str) and row[field]
                                       for field in ("filer_name", "agency", "position_title"))
                               for row in catalog_rows):
        raise OgeCatalogError("White House 278-T catalog identities are invalid")
    if any(not isinstance(item, dict) or item.get("schema_version") != EXTRACTION_SCHEMA or
           item.get("parser_version") != PARSER_VERSION or item.get("source_id") != "oge" or
           not isinstance(item.get("filer_name"), str) or not item["filer_name"].strip() or
           not isinstance(item.get("transactions"), list) or
           any(not isinstance(row, dict) for row in item["transactions"]) or
           not isinstance(item.get("quarantined"), list) or
           any(not isinstance(row, dict) for row in item["quarantined"]) or
           not isinstance(item.get("document_reasons"), list) or
           any(not isinstance(reason, str) for reason in item["document_reasons"])
           for item in extractions):
        raise OgeCatalogError("White House 278-T extraction contract is invalid")

    screened = quarantine_duplicate_report_groups(extractions)
    existing_keys = set()
    for row in existing_oge_candidate["transactions"]:
        if not isinstance(row, dict):
            raise OgeCatalogError("Existing OGE candidate transaction is invalid")
        if row.get("source_id") == "oge" and isinstance(row.get("person_id"), str):
            existing_keys.update(_trade_keys(row, row["person_id"]))

    reports = []
    row_keys: dict[tuple, list[tuple[int, int]]] = defaultdict(list)
    for report in screened:
        identity, identity_reasons = _catalog_identity(report, catalog_rows)
        reasons = sorted(set(report["document_reasons"] + identity_reasons))
        filing_day = _day(report.get("filed_at"))
        if filing_day is None:
            reasons.append("filed_at_missing_or_invalid")
        try:
            source = urlsplit(report["source_url"] if isinstance(report.get("source_url"), str) else "")
        except ValueError:
            source = urlsplit("")
        if (source.scheme != "https" or source.netloc.casefold() not in {
                "whitehouse.gov", "www.whitehouse.gov"} or source.fragment or
                not source.path.startswith("/wp-content/uploads/") or
                not source.path.lower().endswith(".pdf")):
            reasons.append("source_url_invalid")
        if not isinstance(report.get("source_sha256"), str) or not _SHA256.fullmatch(
                report["source_sha256"]):
            reasons.append("source_sha256_invalid")
        if not isinstance(report.get("document_id"), str) or not report["document_id"].strip():
            reasons.append("document_id_missing")
        if not report["transactions"] and not report["quarantined"]:
            reasons.append("no_transaction_rows_found")
        if report.get("evidence_complete") is not True:
            reasons = sorted(set(reasons + ["source_extraction_incomplete"]))
        person_id = _person_id(_identity_key(identity)) if identity else None
        catalog = ({"filer_name": identity["filer_name"],
                    "position_title": identity["position_title"],
                    "agency": identity["agency"],
                    "person_id": person_id,
                    "catalog_entry_ids": identity["matched_catalog_entry_ids"]}
                   if identity else None)
        rows = []
        for source_row in report["transactions"]:
            if not isinstance(source_row, dict):
                raise OgeCatalogError("White House 278-T transaction row is invalid")
            field_reasons = _row_reasons(source_row, filing_day)
            row_reasons = list(reasons) + field_reasons
            if person_id and not field_reasons:
                keys = _trade_keys(source_row, person_id)
                if keys & existing_keys:
                    row_reasons.append("possible_existing_oge_transaction_duplicate")
                for key in keys:
                    row_keys[key].append((len(reports), len(rows)))
            rows.append({"extraction_id": source_row.get("extraction_id"),
                         "row_number": source_row.get("row_number"),
                         "status": "quarantined" if row_reasons else "eligible",
                         "reasons": sorted(set(row_reasons))})
        for source_row in report["quarantined"]:
            original_reasons = source_row.get("reasons")
            if (not isinstance(original_reasons, list) or
                    any(not isinstance(reason, str) for reason in original_reasons)):
                original_reasons = ["source_row_quarantine_reason_invalid"]
            rows.append({"extraction_id": source_row.get("extraction_id"),
                         "row_number": source_row.get("row_number"),
                         "status": "quarantined",
                         "reasons": sorted(set(reasons + original_reasons))})
        reports.append({
            "document_id": report.get("document_id"),
            "source_url": report.get("source_url"),
            "source_sha256": report.get("source_sha256"),
            "filed_at": report.get("filed_at"),
            "matched_catalog_identity": catalog,
            "document_reasons": reasons,
            "status": "quarantined" if reasons else "eligible",
            "rows": rows,
        })

    for locations in row_keys.values():
        if len({report_index for report_index, _ in locations}) <= 1:
            continue
        for report_index, row_index in locations:
            row = reports[report_index]["rows"][row_index]
            row["status"] = "quarantined"
            row["reasons"] = sorted(set(row["reasons"] +
                                        ["possible_whitehouse_report_transaction_duplicate"]))
    for report in reports:
        if report["status"] == "eligible" and not any(
                row["status"] == "eligible" for row in report["rows"]):
            report["status"] = "quarantined"
            report["document_reasons"] = sorted(set(
                report["document_reasons"] + ["no_eligible_transaction_rows"]))
    return {
        "schema_version": AUDIT_SCHEMA,
        "source_id": "oge",
        "catalog_sha256": oge_whitehouse_coverage.get("catalog_sha256"),
        "report_count": len(reports),
        "eligible_report_count": sum(row["status"] == "eligible" for row in reports),
        "eligible_row_count": sum(row["status"] == "eligible" for report in reports
                                  for row in report["rows"]),
        "quarantined_row_count": sum(row["status"] == "quarantined" for report in reports
                                     for row in report["rows"]),
        "reports": reports,
    }
