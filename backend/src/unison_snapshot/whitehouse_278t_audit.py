"""Read-only eligibility audit for public White House OGE 278-T extractions.

The official OGE catalog proves a filer identity, not an individual filing's
date or the equivalence of two PDFs.  This module never publishes facts.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import math
import re
from urllib.parse import urlsplit

from .oge import OgeCatalogError
from .oge_candidate import _identity_key, _person_id
from .oge_whitehouse import AGENCIES, SCHEMA as COVERAGE_SCHEMA
from .whitehouse_278t import (
    EXTRACTION_SCHEMA, MIN_OCR_ROW_CONFIDENCE,
    SUPPORTED_PARSER_VERSIONS, TRUMP_081225_DOCUMENT_ID,
    TRUMP_081225_PARSER_VERSION, TRUMP_081225_SOURCE_SHA256,
    TRUMP_081225_SOURCE_URL, TRUMP_2026_PARSER_VERSION, _first_last,
    quarantine_duplicate_report_groups,
    trump_2026_profile,
)


AUDIT_SCHEMA = "whitehouse-278t-eligibility-audit/v2"
TRUMP_TARGET_DOCUMENT_ID = TRUMP_081225_DOCUMENT_ID
TRUMP_TARGET_SOURCE_URL = TRUMP_081225_SOURCE_URL
TRUMP_TARGET_SOURCE_SHA256 = TRUMP_081225_SOURCE_SHA256
TRUMP_TARGET_FILED_AT = "2025-08-12"
TRUMP_PERSON_ID = "oge:076544f8ba0638cf"
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


def source_bound_filing_date(report: dict) -> str | None:
    """Verify the fixed Trump scan's first-page certification-date evidence."""
    try:
        profile = trump_2026_profile(
            report.get("document_id"), report.get("source_url"),
            report.get("source_sha256"))
    except OgeCatalogError:
        return None
    if profile is not None:
        evidence = report.get("filing_date_evidence")
        if (report.get("parser_version") != TRUMP_2026_PARSER_VERSION or
                report.get("signature_method") !=
                "official_disclosure_date_source_bound" or
                not isinstance(evidence, dict) or
                evidence.get("page_number") is not None or
                evidence.get("label") != "Official White House disclosure listing date" or
                evidence.get("raw") != profile["report_date_raw"] or
                evidence.get("normalized") != profile["report_date"] or
                evidence.get("basis") != "exact_sha_official_disclosure_link_label" or
                evidence.get("source_url") != profile["source_url"] or
                evidence.get("source_sha256") != profile["source_sha256"]):
            return None
        return profile["report_date"]
    if (report.get("document_id") != TRUMP_TARGET_DOCUMENT_ID or
            report.get("source_url") != TRUMP_TARGET_SOURCE_URL or
            report.get("source_sha256") != TRUMP_TARGET_SOURCE_SHA256 or
            report.get("parser_version") != TRUMP_081225_PARSER_VERSION or
            report.get("signature_method") != "handwritten_source_bound"):
        return None
    evidence = report.get("filing_date_evidence")
    if not isinstance(evidence, dict):
        return None
    geometry = evidence.get("geometry")
    if (evidence.get("page_number") != 1 or
            evidence.get("label") != "Filer's Certification Date" or
            evidence.get("raw") != "8/12/25" or
            evidence.get("normalized") != TRUMP_TARGET_FILED_AT or
            evidence.get("source_url") != TRUMP_TARGET_SOURCE_URL or
            evidence.get("source_sha256") != TRUMP_TARGET_SOURCE_SHA256 or
            not isinstance(geometry, dict) or
            geometry.get("coordinate_space") != "pdf_points" or
            any(type(geometry.get(key)) not in (int, float) or
                not math.isfinite(geometry[key])
                for key in ("x0", "top", "x1", "bottom")) or
            not (geometry["x0"] < geometry["x1"] and geometry["top"] < geometry["bottom"])):
        return None
    return TRUMP_TARGET_FILED_AT


def source_bound_filer_identity(report: dict) -> bool:
    """Verify the fixed scan's filer identity without inventing an agency cell."""
    try:
        profile = trump_2026_profile(
            report.get("document_id"), report.get("source_url"),
            report.get("source_sha256"))
    except OgeCatalogError:
        return False
    if profile is not None:
        evidence = report.get("filer_identity_evidence")
        return bool(
            report.get("parser_version") == TRUMP_2026_PARSER_VERSION and
            report.get("pdf_filer_name") == "Donald J Trump" and
            report.get("pdf_position_title") ==
            "President of the United States of America" and
            report.get("pdf_agency_label") is None and
            isinstance(evidence, dict) and evidence.get("page_number") == 1 and
            evidence.get("pdf_filer_name") == report["pdf_filer_name"] and
            evidence.get("pdf_position_title") == report["pdf_position_title"] and
            evidence.get("agency_basis") ==
            "exact_sha_official_disclosure_catalog_alias" and
            evidence.get("source_url") == profile["source_url"] and
            evidence.get("source_sha256") == profile["source_sha256"])
    if (report.get("document_id") != TRUMP_TARGET_DOCUMENT_ID or
            report.get("source_url") != TRUMP_TARGET_SOURCE_URL or
            report.get("source_sha256") != TRUMP_TARGET_SOURCE_SHA256 or
            report.get("parser_version") != TRUMP_081225_PARSER_VERSION or
            report.get("pdf_filer_name") != "Donald J Trump" or
            report.get("pdf_position_title") != "President of the United States of America" or
            report.get("pdf_agency_label") is not None):
        return False
    evidence = report.get("filer_identity_evidence")
    return bool(
        isinstance(evidence, dict) and evidence.get("page_number") == 1 and
        evidence.get("pdf_filer_name") == report["pdf_filer_name"] and
        evidence.get("pdf_position_title") == report["pdf_position_title"] and
        evidence.get("agency_basis") == "exact_sha_official_oge_catalog_alias" and
        evidence.get("source_url") == TRUMP_TARGET_SOURCE_URL and
        evidence.get("source_sha256") == TRUMP_TARGET_SOURCE_SHA256)


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
    source_bound_identity = source_bound_filer_identity(report)
    if not all(isinstance(value, str) and value.strip() for value in (name, role)):
        return None, ["pdf_identity_fields_missing"]
    if source_bound_identity:
        agency = "white house office"
    elif not isinstance(office, str) or not office.strip():
        return None, ["pdf_identity_fields_missing"]
    else:
        agency = _OFFICE_LABELS.get(_words(office))
    if agency is None:
        return None, ["pdf_agency_unrecognized"]
    named = [row for row in catalog_rows
             if row["agency"].casefold() == agency and
             _first_last(row["filer_name"]) == _first_last(name)]
    if not named:
        return None, ["catalog_filer_identity_missing"]
    matched = [row for row in named if (
        _role(row["position_title"]) == "president" if source_bound_identity
        else _role(row["position_title"]) == _role(role))]
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
                          existing_oge_candidate: dict,
                          annual_extractions: list[dict] | None = None) -> dict:
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
           item.get("parser_version") not in SUPPORTED_PARSER_VERSIONS or
           item.get("source_id") != "oge" or
           not isinstance(item.get("filer_name"), str) or not item["filer_name"].strip() or
           not isinstance(item.get("transactions"), list) or
           any(not isinstance(row, dict) for row in item["transactions"]) or
           not isinstance(item.get("quarantined"), list) or
           any(not isinstance(row, dict) for row in item["quarantined"]) or
           not isinstance(item.get("document_reasons"), list) or
           any(not isinstance(reason, str) for reason in item["document_reasons"])
           for item in extractions):
        raise OgeCatalogError("White House 278-T extraction contract is invalid")
    for item in extractions:
        rows = [*item["transactions"], *item["quarantined"]]
        if str(item.get("extraction_method", "")).startswith(
                "tesseract_ocr_geometry"):
            geometry = [row.get("geometry_row_index") for row in rows]
            if (not isinstance(item.get("ocr_engine"), str) or
                    not item["ocr_engine"].casefold().startswith("tesseract ") or
                    item.get("source_row_count") != len(rows) or
                    any(type(value) is not int for value in geometry) or
                    sorted(geometry) != list(range(1, len(rows) + 1)) or
                    any(not isinstance(row.get("ocr_confidence"), (int, float)) or
                        row["ocr_confidence"] < MIN_OCR_ROW_CONFIDENCE
                        for row in item["transactions"])):
                raise OgeCatalogError("White House 278-T OCR geometry contract is invalid")
        if item.get("parser_version") == TRUMP_081225_PARSER_VERSION:
            printed = [row.get("row_number") for row in rows]
            if (item.get("document_id") != TRUMP_TARGET_DOCUMENT_ID or
                    item.get("source_url") != TRUMP_TARGET_SOURCE_URL or
                    item.get("source_sha256") != TRUMP_TARGET_SOURCE_SHA256 or
                    item.get("extraction_method") != "tesseract_ocr_geometry" or
                    item.get("page_count") != 22 or
                    item.get("filed_at") != TRUMP_TARGET_FILED_AT or
                    source_bound_filing_date(item) != TRUMP_TARGET_FILED_AT or
                    not source_bound_filer_identity(item) or
                    item.get("source_row_count") != 507 or len(rows) != 507 or
                    any(type(value) is not int for value in printed) or
                    sorted(printed) != list(range(1, 508))):
                raise OgeCatalogError("Trump 08/12/25 278-T source-bound contract is invalid")
        if item.get("parser_version") == TRUMP_2026_PARSER_VERSION:
            try:
                profile = trump_2026_profile(
                    item.get("document_id"), item.get("source_url"),
                    item.get("source_sha256"))
            except OgeCatalogError:
                profile = None
            printed = [row.get("row_number") for row in rows]
            if (profile is None or item.get("page_count") != profile["page_count"] or
                    item.get("filed_at") != profile["report_date"] or
                    source_bound_filing_date(item) != profile["report_date"] or
                    not source_bound_filer_identity(item) or
                    item.get("source_row_count") != len(rows) or not rows or
                    any(type(value) is not int for value in printed) or
                    sorted(printed) != list(range(1, len(rows) + 1))):
                raise OgeCatalogError("Trump 2026 278-T source-bound contract is invalid")

    screened = quarantine_duplicate_report_groups(extractions)
    document_ids = [report.get("document_id") for report in screened]
    if (any(not isinstance(value, str) or not value.strip() for value in document_ids) or
            len(set(document_ids)) != len(document_ids)):
        raise OgeCatalogError("White House 278-T document identities are missing or duplicated")
    existing_keys = set()
    for row in existing_oge_candidate["transactions"]:
        if not isinstance(row, dict):
            raise OgeCatalogError("Existing OGE candidate transaction is invalid")
        if row.get("source_id") == "oge" and isinstance(row.get("person_id"), str):
            existing_keys.update(_trade_keys(row, row["person_id"]))

    annual_keys = set()
    annual_part7_row_count = 0
    annual_part7_incomparable_row_count = 0
    for annual in annual_extractions or []:
        if (not isinstance(annual, dict) or
                not isinstance(annual.get("transactions"), list) or
                not isinstance(annual.get("quarantined", []), list)):
            annual_part7_incomparable_row_count += 1
            continue
        filer = _first_last(annual.get("filer_name") or "")
        if filer is None:
            annual_part7_incomparable_row_count += len(annual["transactions"])
            annual_part7_incomparable_row_count += sum(
                isinstance(row, dict) and row.get("section") == "part7"
                for row in annual.get("quarantined", []))
            continue
        annual_rows = list(annual["transactions"])
        annual_rows.extend(row for row in annual.get("quarantined", [])
                           if isinstance(row, dict) and row.get("section") == "part7")
        for row in annual_rows:
            if not isinstance(row, dict):
                annual_part7_incomparable_row_count += 1
                continue
            asset = _words(row.get("asset_name") or "")
            if (asset and row.get("transaction_type") in {"purchase", "sale"} and
                    _day(row.get("transaction_date")) is not None and
                    type(row.get("amount_low")) is int and
                    type(row.get("amount_high")) is int):
                annual_part7_row_count += 1
                annual_keys.add((filer, row.get("owner"), row["transaction_type"],
                                 row["transaction_date"], row["amount_low"],
                                 row["amount_high"], asset))
            else:
                annual_part7_incomparable_row_count += 1

    reports = []
    row_keys: dict[tuple, list[tuple[int, int]]] = defaultdict(list)
    annual_overlap_count = 0
    for report in screened:
        identity, identity_reasons = _catalog_identity(report, catalog_rows)
        reasons = sorted(set(report["document_reasons"] + identity_reasons))
        filing_day = _day(report.get("filed_at"))
        if filing_day is None:
            reasons.append("filed_at_missing_or_invalid")
        if (report.get("signature_method") in {
                "handwritten_source_bound", "official_disclosure_date_source_bound"} and
                source_bound_filing_date(report) != report.get("filed_at")):
            reasons.append("source_bound_filing_date_evidence_invalid")
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
        person_id = (TRUMP_PERSON_ID if identity and source_bound_filer_identity(report) else
                     _person_id(_identity_key(identity)) if identity else None)
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
                ignore_dedup = report.get("parser_version") == TRUMP_2026_PARSER_VERSION
                if not ignore_dedup and keys & existing_keys:
                    row_reasons.append("possible_existing_oge_transaction_duplicate")
                filer = _first_last(report.get("pdf_filer_name") or "")
                annual_common = (filer, source_row.get("transaction_type"),
                                 source_row.get("transaction_date"),
                                 source_row.get("amount_low"), source_row.get("amount_high"),
                                 _words(source_row.get("asset_name") or ""))
                annual_overlap = any(
                    (annual_common[0], owner, *annual_common[1:]) in annual_keys
                    for owner in (source_row.get("owner"), "Unknown"))
                # A public 278-T is the authoritative transaction source.  An
                # exact match in an unpublished annual Part 7 is recorded so
                # the annual row remains suppressed later; it does not block
                # this otherwise qualified 278-T row.
                if annual_overlap:
                    annual_overlap_count += 1
                if not ignore_dedup:
                    for key in keys:
                        row_keys[key].append((len(reports), len(rows)))
            else:
                annual_overlap = False
            rows.append({"extraction_id": source_row.get("extraction_id"),
                         "row_number": source_row.get("row_number"),
                         "status": "quarantined" if row_reasons else "eligible",
                         "annual_part7_overlap": annual_overlap,
                         "reasons": sorted(set(row_reasons))})
        for source_row in report["quarantined"]:
            original_reasons = source_row.get("reasons")
            if (not isinstance(original_reasons, list) or
                    any(not isinstance(reason, str) for reason in original_reasons)):
                original_reasons = ["source_row_quarantine_reason_invalid"]
            rows.append({"extraction_id": source_row.get("extraction_id"),
                         "row_number": source_row.get("row_number"),
                         "status": "quarantined",
                         "annual_part7_overlap": False,
                         "reasons": sorted(set(reasons + original_reasons))})
        reports.append({
            "document_id": report.get("document_id"),
            "source_url": report.get("source_url"),
            "source_sha256": report.get("source_sha256"),
            "filed_at": report.get("filed_at"),
            "matched_catalog_identity": catalog,
            "document_reasons": reasons,
            "status": "quarantined" if reasons else "eligible",
            "source_row_count": len(rows),
            "eligible_row_count": sum(row["status"] == "eligible" for row in rows),
            "quarantined_row_count": sum(row["status"] == "quarantined" for row in rows),
            "rows": rows,
        })

    for locations in row_keys.values():
        if len(set(locations)) <= 1:
            continue
        for report_index, row_index in locations:
            row = reports[report_index]["rows"][row_index]
            row["status"] = "quarantined"
            reason = ("possible_whitehouse_report_transaction_duplicate"
                      if len({index for index, _ in locations}) > 1 else
                      "possible_whitehouse_same_report_transaction_duplicate")
            row["reasons"] = sorted(set(row["reasons"] +
                                        [reason]))
    for report in reports:
        if report["status"] == "eligible" and not any(
                row["status"] == "eligible" for row in report["rows"]):
            report["status"] = "quarantined"
            report["document_reasons"] = sorted(set(
                report["document_reasons"] + ["no_eligible_transaction_rows"]))
        report["eligible_row_count"] = sum(
            row["status"] == "eligible" for row in report["rows"])
        report["quarantined_row_count"] = sum(
            row["status"] == "quarantined" for row in report["rows"])
        if (report["eligible_row_count"] + report["quarantined_row_count"] !=
                report["source_row_count"]):
            raise OgeCatalogError("White House 278-T report rows are not conserved")
    source_row_count = sum(report["source_row_count"] for report in reports)
    eligible_row_count = sum(report["eligible_row_count"] for report in reports)
    quarantined_row_count = sum(report["quarantined_row_count"] for report in reports)
    if eligible_row_count + quarantined_row_count != source_row_count:
        raise OgeCatalogError("White House 278-T audit rows are not conserved")
    return {
        "schema_version": AUDIT_SCHEMA,
        "source_id": "oge",
        "catalog_sha256": oge_whitehouse_coverage.get("catalog_sha256"),
        "report_count": len(reports),
        "eligible_report_count": sum(row["status"] == "eligible" for row in reports),
        "source_row_count": source_row_count,
        "eligible_row_count": eligible_row_count,
        "quarantined_row_count": quarantined_row_count,
        "row_conservation_complete": True,
        "annual_part7_comparable_row_count": annual_part7_row_count,
        "annual_part7_incomparable_row_count": annual_part7_incomparable_row_count,
        "annual_part7_overlap_count": annual_overlap_count,
        "reports": reports,
    }
