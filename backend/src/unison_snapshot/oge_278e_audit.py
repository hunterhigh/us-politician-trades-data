"""Read-only admission audit for public White House Form 278e extractions.

This audit separates complete source extraction from a canonical publication.
It never assigns a person ID, resolves amendments, or performs 278e/278-T
transaction de-duplication on behalf of a downstream candidate builder.
"""
from __future__ import annotations

from datetime import date, datetime
import re
from urllib.parse import urlsplit

from .oge_278e_public import PARSER_VERSION, SCHEMA, _SIGNATURE, _name_key
from .oge_annual import _VALUE_RANGES


AUDIT_SCHEMA = "whitehouse-public-278e-qualification/v1"
_OWNERS = {"Self", "Spouse", "Dependent Child", "Joint"}
_HOLDING_PARTS = {"part2", "part5", "part6"}
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _rows(value: object) -> list[dict] | None:
    return value if isinstance(value, list) and all(isinstance(row, dict) for row in value) else None


def _signature_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%m/%d/%Y").date()
    except ValueError:
        return None


def audit_public_278e(extraction: dict) -> dict:
    """Return per-report and per-holding qualification without changing input.

    A source-qualified report still requires stable-person identity, amendment
    resolution, ticker mapping, official-archive reference, and production
    snapshot validation elsewhere. ``source_report_eligible`` never bypasses
    those checks.
    """
    if not isinstance(extraction, dict):
        raise ValueError("278e extraction must be an object")
    holdings = _rows(extraction.get("holdings"))
    transactions = _rows(extraction.get("transactions"))
    excluded = _rows(extraction.get("excluded"))
    quarantined = _rows(extraction.get("quarantined"))
    if None in (holdings, transactions, excluded, quarantined):
        raise ValueError("278e extraction dispositions must be arrays of objects")
    holdings = holdings or []
    transactions = transactions or []
    excluded = excluded or []
    quarantined = quarantined or []
    report_type = extraction.get("report_type")
    holding_reasons: set[str] = set()
    report_reasons: set[str] = set()
    if extraction.get("schema_version") != SCHEMA or extraction.get("parser_version") != PARSER_VERSION:
        report_reasons.add("untrusted_extraction_version")
    source_url = extraction.get("source_url")
    parsed_url = urlsplit(source_url) if isinstance(source_url, str) else None
    if (not parsed_url or parsed_url.scheme != "https" or
            parsed_url.hostname != "www.whitehouse.gov" or
            not parsed_url.path.startswith("/wp-content/uploads/") or
            not parsed_url.path.casefold().endswith(".pdf") or
            not isinstance(extraction.get("source_sha256"), str) or
            not _SHA.fullmatch(extraction["source_sha256"])):
        report_reasons.add("source_evidence_invalid")
    if not isinstance(extraction.get("filer_name"), str) or not extraction["filer_name"].strip():
        report_reasons.add("filer_identity_missing")
    if not isinstance(extraction.get("position_line_raw"), str) or not extraction["position_line_raw"].strip():
        report_reasons.add("filing_position_missing")
    filed = _date(extraction.get("filing_date"))
    signature_text = extraction.get("signature_text")
    signature = _SIGNATURE.fullmatch(signature_text) if isinstance(signature_text, str) else None
    signature_date = _signature_date(signature[2]) if signature else None
    if (signature is None or filed is None or signature_date != filed or
            _name_key(signature[1]) != _name_key(extraction.get("filer_name", "")) or
            _name_key(signature[3]) != _name_key(extraction.get("filer_name", ""))):
        report_reasons.add("filer_signature_unverified")
    period_end = _date(extraction.get("report_period_end"))
    valuation = _date(extraction.get("holding_valuation_date"))
    if report_type == "Annual":
        year = extraction.get("cover_report_year")
        expected = date(year - 1, 12, 31) if type(year) is int and 2000 <= year <= 2200 else None
        if period_end != expected or valuation != expected or expected is None or (filed and filed < expected):
            holding_reasons.add("annual_period_or_valuation_unverified")
    elif report_type == "New Entrant":
        if _date(extraction.get("appointment_date")) is None:
            report_reasons.add("appointment_date_unverified")
        if period_end is not None or valuation is not None:
            report_reasons.add("new_entrant_snapshot_date_invented")
        holding_reasons.add("holding_valuation_date_not_exact")
    elif report_type in {"Termination", "Annual Term"}:
        terminated = _date(extraction.get("termination_date"))
        if terminated is None or period_end != terminated:
            report_reasons.add("termination_period_unverified")
        if valuation is not None:
            report_reasons.add("termination_valuation_date_invented")
        if filed and terminated and filed < terminated:
            report_reasons.add("termination_signed_before_effective_date")
        holding_reasons.add("holding_valuation_date_not_exact")
    else:
        report_reasons.add("report_type_unsupported")
    printed = extraction.get("printed_row_count")
    reconciled = (type(printed) is int and printed >= 0 and printed ==
                  len(holdings) + len(transactions) + len(excluded) + len(quarantined))
    if not reconciled:
        report_reasons.add("printed_rows_not_conserved")
    sections = extraction.get("section_pages")
    empty_sections = extraction.get("explicit_empty_sections")
    if (not isinstance(empty_sections, list) or
            any(not isinstance(part, str) for part in empty_sections)):
        report_reasons.add("explicit_empty_sections_invalid")
        empty_sections = []
    if not isinstance(sections, dict) or any(type(sections.get(part)) is not int or sections[part] <= 0
                                             for part in _HOLDING_PARTS):
        holding_reasons.add("asset_sections_unverified")
    for part in _HOLDING_PARTS:
        if (part not in empty_sections and not any(row.get("section") == part
                                                  for row in holdings + excluded + quarantined)):
            holding_reasons.add(f"asset_section_unreconciled:{part}")
    if report_type in {"Annual", "Termination", "Annual Term"} and (
            not isinstance(sections, dict) or type(sections.get("part7")) is not int or
            sections["part7"] <= 0):
        report_reasons.add("part7_section_unverified")
    if report_type in {"Annual", "Termination", "Annual Term"} and (
            "part7" not in empty_sections and
            not any(row.get("section") == "part7" for row in transactions + quarantined)):
        report_reasons.add("part7_section_unreconciled")
    document_reasons = extraction.get("document_reasons")
    if not isinstance(document_reasons, list) or any(not isinstance(item, str) for item in document_reasons):
        report_reasons.add("document_reasons_invalid")
    else:
        report_reasons.update(f"document:{item}" for item in document_reasons)
    if any(row.get("section") in _HOLDING_PARTS for row in quarantined):
        holding_reasons.add("asset_rows_quarantined")
    if any(row.get("section") == "part7" for row in quarantined):
        report_reasons.add("part7_rows_quarantined")
    for row in transactions:
        trade_date = _date(row.get("transaction_date"))
        low, high = row.get("amount_low"), row.get("amount_high")
        if (row.get("section") != "part7" or type(row.get("page_number")) is not int or
                row.get("page_number", 0) <= 0 or not isinstance(row.get("row_number"), str) or
                row.get("transaction_type") not in {"purchase", "sale", "exchange"} or
                trade_date is None or (filed and trade_date > filed) or
                (period_end and trade_date > period_end) or
                (report_type == "Annual" and period_end and trade_date.year != period_end.year) or
                type(low) is not int or type(high) is not int or (low, high) not in _VALUE_RANGES):
            report_reasons.add("part7_row_invalid")
    if quarantined:
        report_reasons.add("rows_quarantined")
    row_audit: list[dict] = []
    for row in holdings:
        reasons = []
        if row.get("section") not in _HOLDING_PARTS or type(row.get("page_number")) is not int or (
                row.get("page_number", 0) <= 0 or not isinstance(row.get("row_number"), str) or
                not row["row_number"] or not isinstance(row.get("asset_name"), str) or
                not row["asset_name"].strip() or not isinstance(row.get("raw_columns"), dict)):
            reasons.append("holding_row_evidence_invalid")
        if row.get("owner") not in _OWNERS:
            reasons.append("holding_owner_not_disclosed")
        low, high = row.get("value_low"), row.get("value_high")
        if type(low) is not int or type(high) is not int or (low, high) not in _VALUE_RANGES:
            reasons.append("holding_value_band_invalid")
        if row.get("report_period_end") != extraction.get("report_period_end") or (
                row.get("holding_valuation_date") != extraction.get("holding_valuation_date")):
            reasons.append("holding_period_conflicts_with_cover")
        if row.get("holding_valuation_status") != (
                "exact_period_end" if valuation else "not_exact_on_cover"):
            reasons.append("holding_valuation_status_invalid")
        if valuation is None:
            reasons.append("holding_valuation_date_not_exact")
        row_audit.append({"section": row.get("section"), "page_number": row.get("page_number"),
                          "row_number": row.get("row_number"), "asset_name": row.get("asset_name"),
                          "owner": row.get("owner"), "value_low": low, "value_high": high,
                          "source_candidate_eligible": not reasons, "reasons": sorted(set(reasons))})
    if any(not row["source_candidate_eligible"] for row in row_audit):
        holding_reasons.add("holding_rows_not_individually_qualified")
    if not reconciled:
        holding_reasons.add("printed_rows_not_conserved")
    if report_reasons & {"source_evidence_invalid", "filer_identity_missing",
                         "filing_position_missing", "filer_signature_unverified",
                         "untrusted_extraction_version", "rows_quarantined"}:
        # A transaction-only quarantine does not invalidate the asset snapshot;
        # all other report-wide evidence failures do.
        holding_reasons.update(report_reasons & {"source_evidence_invalid", "filer_identity_missing",
                                                 "filing_position_missing", "filer_signature_unverified",
                                                 "untrusted_extraction_version"})
    if isinstance(document_reasons, list):
        holding_reasons.update(reason for reason in report_reasons
                               if reason.startswith("document:asset_") or reason == "document:table_header_unrecognized")
    dedup_required = bool(extraction.get("requires_cross_report_dedup") or transactions or
                          any(row.get("section") == "part7" for row in quarantined))
    if dedup_required:
        report_reasons.add("part7_cross_278t_dedup_pending")
    report_reasons.update(holding_reasons)
    holding_eligible = not holding_reasons
    report_eligible = not report_reasons
    return {"schema_version": AUDIT_SCHEMA, "source_url": source_url,
            "source_sha256": extraction.get("source_sha256"),
            "filer_name": extraction.get("filer_name"), "report_type": report_type,
            "filing_date": extraction.get("filing_date"),
            "report_period_end": extraction.get("report_period_end"),
            "holding_valuation_date": extraction.get("holding_valuation_date"),
            "printed_row_count": printed,
            "disposition_counts": {"holdings": len(holdings), "transactions": len(transactions),
                                   "excluded": len(excluded), "quarantined": len(quarantined)},
            "printed_rows_conserved": reconciled,
            "holding_row_audit": row_audit,
            "source_candidate_holding_count": sum(row["source_candidate_eligible"] for row in row_audit),
            "source_holdings_eligible": holding_eligible,
            "source_report_eligible": report_eligible,
            "part7_cross_report_dedup_required": dedup_required,
            "holding_blocking_reasons": sorted(holding_reasons),
            "report_blocking_reasons": sorted(report_reasons),
            "production_status": "not_assessed_external_identity_amendments_and_snapshot_gate"}
