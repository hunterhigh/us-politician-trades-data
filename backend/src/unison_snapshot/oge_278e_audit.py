"""Read-only admission audit for public White House Form 278e extractions.

This audit separates complete source extraction from a canonical publication.
It never assigns a person ID, resolves amendments, or performs 278e/278-T
transaction de-duplication on behalf of a downstream candidate builder.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import re
from urllib.parse import urlsplit

from .oge_278e_public import (OCR_MINIMUM_CRITICAL_CONFIDENCE,
                               OCR_MINIMUM_ROW_MEAN_CONFIDENCE, SCHEMA,
                               SUPPORTED_PARSER_VERSIONS, TRUMP_2025_PARSER_VERSION,
                               TRUMP_2025_LEGACY_PARSER_VERSIONS,
                               _SIGNATURE,
                               _explicit_part6_owner, _name_key)
from .oge_annual import _VALUE_RANGES, _range
from .whitehouse_scanned_annual import (recovered_ocr_holding_valid,
                                        scanned_signature_evidence_valid,
                                        source_bound_existing_holding_valid,
                                        source_bound_holding_section_page_valid,
                                        source_bound_parser_version_valid)


AUDIT_SCHEMA = "whitehouse-public-278e-qualification/v1"
_OWNERS = {"Self", "Spouse", "Dependent Child", "Joint"}
_HOLDING_PARTS = {"part2", "part5", "part6"}
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_ROW_LOCATOR = re.compile(r"p([1-9]\d*)-y([1-9]\d*)\Z")
_ACCOUNT_SCOPE = re.compile(r"investment-account-([1-9]\d*)\Z")
_ACCOUNT_HEADING = re.compile(r"INVESTMENT\s+ACCOUNT\s*#\s*([1-9]\d*)\Z", re.I)
_PRINTED_ROW_NUMBER = re.compile(r"[1-9]\d*(?:\.[1-9]\d*)*\Z")
_TRUMP_V7_PRINTED_ROW_NUMBER = re.compile(r"[1-9]\d*\Z")
_TRUMP_V7_ACCOUNT_SCOPE = re.compile(r"investment-account-([1-8])\Z")
_TRUMP_V7_DESCRIPTION_NOISE = re.compile(r"[|_*{}\[\]\ufffd]")
# Fixed-v7 counterexamples whose high OCR score does not make the Description
# cell an identifiable asset.  They are source-bound parser artifacts, not a
# general company-name denylist.
_TRUMP_V7_INCOMPLETE_DESCRIPTIONS = {
    "ANALYTICS INC CLASS A",
    "COM INC",
    "COMMUNICATIONS CORP CLASS CLASS A",
    "CORP",
    "CORP CLASS A",
    "CORP NEW CLASS A",
    "CORPORATION",
    "DIGITAL INC",
    "ENERGY CORP NEW",
    "ENERGY INC",
    "GLOBAL INC",
    "GLOBAL INC CLASS CLASS A",
    "GROUP INC",
    "HLDGS INC",
    "INC",
    "INC CLASS CLASS A",
    "INDS INC",
    "INDUSTRIAL TRUST REI",
    "LABS INC",
    "MORRIS INTL INC",
    "OF AMER CORP",
    "REIT",
    "RESEARCH CORPORATION",
    "STORES INC",
    "SYS INC",
    "SYSTEMS INC",
    "TECHNOLOGIES INC",
    "TREE INC",
}
_TRUMP_SOURCE_BOUND_PARSER_VERSIONS = {
    *TRUMP_2025_LEGACY_PARSER_VERSIONS, TRUMP_2025_PARSER_VERSION}


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


def _part6_owner_evidence_valid(row: dict, extraction: dict) -> bool:
    evidence = row.get("owner_evidence")
    if not isinstance(evidence, list) or not evidence:
        return False
    for item in evidence:
        if not isinstance(item, dict) or item.get("owner") != row.get("owner") or (
                type(item.get("page_number")) is not int or item["page_number"] <= 0 or
                not isinstance(item.get("row_number"), str) or
                not isinstance(item.get("text"), str)):
            return False
        if not source_bound_holding_section_page_valid(
                {"section": "part6", "page_number": item["page_number"]}, extraction):
            return False
        if item.get("basis") == "explicit_part6_parent_account":
            if (_explicit_part6_owner(item["text"]) != row["owner"] or
                    not row.get("row_number", "").startswith(item["row_number"] + ".")):
                return False
        elif item.get("basis") == "explicit_part6_endnote":
            label = {"Spouse": r"Spousal asset", "Dependent Child": r"Dependent child asset",
                     "Self": r"Filer(?:'s|’s) asset"}.get(row["owner"])
            if (label is None or item["row_number"] != row.get("row_number") or
                    not re.match(r"^6\.\s+" + re.escape(item["row_number"]) +
                                 r"\s+" + label + r"\.", item["text"], re.I)):
                return False
        else:
            return False
    return True


def _account_scope_evidence_valid(row: dict, extraction: dict) -> bool:
    scope = row.get("account_scope")
    evidence = row.get("account_scope_evidence")
    scope_match = _ACCOUNT_SCOPE.fullmatch(scope) if isinstance(scope, str) else None
    heading_match = (_ACCOUNT_HEADING.fullmatch(" ".join(evidence.get("text", "").split()))
                     if isinstance(evidence, dict) and
                     isinstance(evidence.get("text"), str) else None)
    return bool(
        scope_match is not None and heading_match is not None and
        scope_match[1] == heading_match[1] and
        type(evidence.get("page_number")) is int and
        type(row.get("page_number")) is int and
        evidence["page_number"] <= row["page_number"] and
        source_bound_holding_section_page_valid(
            {"section": row.get("section"),
             "page_number": evidence["page_number"]}, extraction)
    )


def _asset_tokens(value: object) -> tuple[str, ...]:
    return tuple(re.findall(r"[A-Z0-9]+", value.upper())) if isinstance(value, str) else ()


def _trump_v7_description_confidence_valid(row: dict) -> bool:
    confidence = row.get("ocr_field_confidence", {}).get("description")
    return bool(
        isinstance(confidence, dict) and
        isinstance(confidence.get("mean"), (int, float)) and
        isinstance(confidence.get("min"), (int, float)) and
        confidence["mean"] >= OCR_MINIMUM_ROW_MEAN_CONFIDENCE and
        confidence["min"] >= OCR_MINIMUM_CRITICAL_CONFIDENCE and
        type(confidence.get("word_count")) is int and
        confidence["word_count"] > 0
    )


def _trump_v7_initial_pool_row(row: dict, extraction: dict) -> bool:
    """The deliberately narrow input pool; this is not an eligibility result."""

    return bool(
        extraction.get("parser_version") == TRUMP_2025_PARSER_VERSION and
        row.get("section") == "part6" and
        isinstance(row.get("row_number"), str) and
        _TRUMP_V7_PRINTED_ROW_NUMBER.fullmatch(row["row_number"]) and
        isinstance(row.get("account_scope"), str) and
        _TRUMP_V7_ACCOUNT_SCOPE.fullmatch(row["account_scope"]) and
        _trump_v7_description_confidence_valid(row)
    )


def _trump_v7_asset_description_valid(row: dict) -> bool:
    asset_name = row.get("asset_name")
    raw_description = row.get("raw_columns", {}).get("description")
    tokens = _asset_tokens(asset_name)
    normalized = " ".join(tokens)
    return bool(
        isinstance(asset_name, str) and asset_name.strip() == asset_name and
        raw_description == asset_name and tokens and
        not _TRUMP_V7_DESCRIPTION_NOISE.search(asset_name) and
        normalized not in _TRUMP_V7_INCOMPLETE_DESCRIPTIONS and
        not re.search(r"\b(?:TOTAL|SUBTOTAL)\b", normalized) and
        not normalized.startswith("INVESTMENT ACCOUNT ")
    )


def _trump_v7_value_geometry_valid(row: dict) -> bool:
    """Recheck the public Value-cell proof without trusting parser disposition."""

    geometry = row.get("value_geometry_evidence")
    if not isinstance(geometry, dict) or geometry.get(
            "method") != "source_bound_part6_value_column/v1":
        return False
    bounds = geometry.get("column_bounds")
    words = geometry.get("words")
    if not isinstance(bounds, dict) or not isinstance(words, list) or not words:
        return False
    eif, value, income = (bounds.get(name) for name in
                          ("eif_start", "value_start", "income_start"))
    if (not all(isinstance(item, (int, float)) for item in (eif, value, income)) or
            not 440 <= eif < value < income <= 580):
        return False
    raw_value = row.get("raw_columns", {}).get("value")
    if not isinstance(raw_value, str):
        return False
    value_texts = []
    expected_repairs = []
    for word in words:
        if not isinstance(word, dict):
            return False
        raw = word.get("original_text")
        extracted = word.get("value_text")
        x0, x1, top = word.get("x0"), word.get("x1"), word.get("top")
        confidence = word.get("ocr_confidence")
        repair = word.get("repair_method")
        if (not isinstance(raw, str) or not isinstance(extracted, str) or not extracted or
                not all(isinstance(item, (int, float)) for item in
                        (x0, x1, top, confidence)) or
                not x0 < x1 <= income - 2 or top <= 0):
            return False
        if repair == "split_eif_value_at_printed_dollar":
            split = raw.find("$")
            if (not eif - 8 <= x0 < value - 6 or x1 < value - 6 or split <= 0 or
                    extracted != raw[split:] or
                    not re.fullmatch(r"(?:N/A|Yes|No)?[_|\s]*", raw[:split], re.I)):
                return False
        elif repair == "strip_leading_table_border_before_dollar":
            if (not value - 6 <= x0 < income - 8 or not re.match(r"^[_|]+\$", raw) or
                    extracted != raw.lstrip("_|")):
                return False
        elif repair is None:
            if not value - 6 <= x0 < income - 8 or extracted != raw:
                return False
        else:
            return False
        if repair is not None:
            expected_repairs.append({
                "page_number": row.get("page_number"), "original_text": raw,
                "x0": x0, "x1": x1, "method": repair})
        value_texts.append(extracted)
    if " ".join(" ".join(value_texts).split()) != " ".join(raw_value.split()):
        return False
    value_confidence = row.get("ocr_field_confidence", {}).get("value")
    if (not isinstance(value_confidence, dict) or
            value_confidence.get("word_count") != len(words)):
        return False
    actual_repairs = row.get("ocr_word_repairs", [])
    return isinstance(actual_repairs, list) and all(
        repair in actual_repairs for repair in expected_repairs)


def _trump_v7_parser_recovery_valid(row: dict) -> bool:
    recovery = row.get("parser_recovery")
    reasons = recovery.get("original_quarantine_reasons") if isinstance(recovery, dict) else None
    return bool(
        isinstance(recovery, dict) and
        recovery.get("method") == "source_bound_part6_structured_row/v1" and
        isinstance(reasons, list) and reasons == sorted(set(reasons)) and
        set(reasons) <= {"holding_ocr_confidence_below_threshold"}
    )


def _asset_tokens_properly_contained(tokens: tuple[str, ...],
                                     other_tokens: tuple[str, ...]) -> bool:
    if not tokens or tokens == other_tokens:
        return False
    return any(other_tokens[index:index + len(tokens)] == tokens
               for index in range(len(other_tokens) - len(tokens) + 1))


def _trump_v7_holding_reasons(row: dict, extraction: dict, *,
                              locator_counts: Counter[str],
                              initial_name_counts: Counter[tuple[str, str]],
                              unresolved_relation_locators: set[str]) -> list[str]:
    if not _trump_v7_initial_pool_row(row, extraction):
        return ["holding_v7_outside_numeric_investment_account_pool"]
    reasons = []
    locator = row.get("source_row_locator")
    match = _SOURCE_ROW_LOCATOR.fullmatch(locator) if isinstance(locator, str) else None
    if (match is None or int(match[1]) != row.get("page_number") or
            locator_counts.get(locator, 0) != 1):
        reasons.append("holding_row_identity_unverified")
    if (not isinstance(row.get("account_scope"), str) or
            not _TRUMP_V7_ACCOUNT_SCOPE.fullmatch(row["account_scope"]) or
            not _account_scope_evidence_valid(row, extraction)):
        reasons.append("holding_account_scope_unverified")
    if not _trump_v7_description_confidence_valid(row):
        reasons.append("holding_asset_description_confidence_invalid")
    if not _trump_v7_asset_description_valid(row):
        reasons.append("holding_asset_description_incomplete_or_noisy")
    if not _trump_v7_value_geometry_valid(row):
        reasons.append("holding_value_geometry_evidence_invalid")
    if not _trump_v7_parser_recovery_valid(row):
        reasons.append("holding_parser_recovery_trace_invalid")
    name_key = (row.get("account_scope"), " ".join(_asset_tokens(row.get("asset_name"))))
    if initial_name_counts.get(name_key, 0) > 1:
        reasons.append("possible_same_asset_multiple_disclosed_rows")
    if row.get("source_row_locator") in unresolved_relation_locators:
        reasons.append("holding_asset_description_relation_unresolved")
    return reasons


def _critical_field_confidence_valid(row: dict, extraction: dict) -> bool:
    """Require confidence on the cells that determine a source-bound fact."""

    if extraction.get("parser_version") not in _TRUMP_SOURCE_BOUND_PARSER_VERSIONS:
        return True
    source_bound = (recovered_ocr_holding_valid(row, extraction) or
                    source_bound_existing_holding_valid(row, extraction))
    fields = row.get("critical_field_confidence" if source_bound else
                     "ocr_field_confidence")
    if not isinstance(fields, dict):
        return False
    minimum_key = "minimum" if source_bound else "min"
    for name in ("description", "value"):
        confidence = fields.get(name)
        if (not isinstance(confidence, dict) or
                not isinstance(confidence.get("mean"), (int, float)) or
                not isinstance(confidence.get(minimum_key), (int, float)) or
                confidence[minimum_key] < OCR_MINIMUM_CRITICAL_CONFIDENCE):
            return False
        # Asset names still need a readable field-wide signal.  The Value
        # cell instead has an exact closed-band grammar check below; a low-
        # scoring currency token must not outweigh three unambiguous tokens.
        if name == "description" and confidence["mean"] < OCR_MINIMUM_ROW_MEAN_CONFIDENCE:
            return False
        if not source_bound and (type(confidence.get("word_count")) is not int or
                                 confidence["word_count"] <= 0):
            return False
    row_confidence = fields.get("row_number")
    printed_number_valid = bool(
        isinstance(row_confidence, dict) and
        isinstance(row_confidence.get("mean"), (int, float)) and
        isinstance(row_confidence.get(minimum_key), (int, float)) and
        row_confidence["mean"] >= OCR_MINIMUM_ROW_MEAN_CONFIDENCE and
        row_confidence[minimum_key] >= OCR_MINIMUM_CRITICAL_CONFIDENCE and
        (source_bound or (type(row_confidence.get("word_count")) is int and
                          row_confidence["word_count"] > 0))
    )
    physical_identity_valid = bool(
        row.get("section") == "part6" and
        isinstance(row.get("row_number"), str) and
        _PRINTED_ROW_NUMBER.fullmatch(row["row_number"]) and
        isinstance(row.get("source_row_locator"), str) and
        _SOURCE_ROW_LOCATOR.fullmatch(row["source_row_locator"]) and
        _account_scope_evidence_valid(row, extraction)
    )
    if not printed_number_valid and not physical_identity_valid:
        return False
    return True


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
    if (extraction.get("schema_version") != SCHEMA or
            extraction.get("parser_version") not in SUPPORTED_PARSER_VERSIONS or
            not source_bound_parser_version_valid(extraction)):
        report_reasons.add("untrusted_extraction_version")
    ocr_method = str(extraction.get("extraction_method", "")).startswith(
        "tesseract_ocr_geometry")
    if ocr_method and not str(extraction.get("ocr_engine", "")).casefold().startswith("tesseract "):
        report_reasons.add("ocr_engine_unverified")
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
    electronic_signature_valid = (
        signature is not None and filed is not None and signature_date == filed and
        _name_key(signature[1]) == _name_key(extraction.get("filer_name", "")) and
        _name_key(signature[3]) == _name_key(extraction.get("filer_name", "")))
    if not electronic_signature_valid and not scanned_signature_evidence_valid(extraction):
        report_reasons.add("filer_signature_unverified")
    period_end = _date(extraction.get("report_period_end"))
    valuation = _date(extraction.get("holding_valuation_date"))
    if report_type == "Annual":
        year = extraction.get("cover_report_year")
        # Scanned public forms print the reporting calendar year itself.  The
        # native Integrity export prints the filing cycle and is one year ahead.
        annual_year = year if ocr_method else year - 1 if type(year) is int else None
        expected = (date(annual_year, 12, 31) if type(annual_year) is int and
                    2000 <= annual_year <= 2200 else None)
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
    transaction_locator_counts = Counter(
        row["source_row_locator"] for row in transactions + quarantined
        if row.get("section") == "part7" and
        isinstance(row.get("source_row_locator"), str))
    transaction_row_audit: list[dict] = []
    for row in transactions:
        row_reasons = []
        trade_date = _date(row.get("transaction_date"))
        low, high = row.get("amount_low"), row.get("amount_high")
        if (row.get("section") != "part7" or type(row.get("page_number")) is not int or
                row.get("page_number", 0) <= 0 or not isinstance(row.get("row_number"), str) or
                not isinstance(row.get("asset_name"), str) or not row["asset_name"].strip() or
                not isinstance(row.get("raw_columns"), dict) or
                row.get("transaction_type") not in {"purchase", "sale", "exchange"} or
                trade_date is None or (filed and trade_date > filed) or
                (period_end and trade_date > period_end) or
                (report_type == "Annual" and period_end and trade_date.year != period_end.year) or
                type(low) is not int or type(high) is not int or (low, high) not in _VALUE_RANGES):
            report_reasons.add("part7_row_invalid")
            row_reasons.append("part7_row_invalid")
        if ocr_method and (not isinstance(row.get("ocr_mean_confidence"), (int, float)) or
                           not isinstance(row.get("ocr_min_confidence"), (int, float)) or
                           row["ocr_mean_confidence"] < OCR_MINIMUM_ROW_MEAN_CONFIDENCE or
                           row["ocr_min_confidence"] < OCR_MINIMUM_CRITICAL_CONFIDENCE):
            report_reasons.add("part7_ocr_confidence_invalid")
            row_reasons.append("part7_ocr_confidence_invalid")
        locator = row.get("source_row_locator")
        locator_match = (_SOURCE_ROW_LOCATOR.fullmatch(locator)
                         if isinstance(locator, str) else None)
        if (extraction.get("parser_version") == TRUMP_2025_PARSER_VERSION and
                (locator_match is None or int(locator_match[1]) != row.get("page_number") or
                 transaction_locator_counts.get(locator, 0) != 1)):
            row_reasons.append("part7_row_identity_unverified")
        if (extraction.get("parser_version") == TRUMP_2025_PARSER_VERSION and
                not _account_scope_evidence_valid(row, extraction)):
            row_reasons.append("part7_account_scope_unverified")
        recovery = row.get("parser_recovery")
        repairs = recovery.get("field_repairs") if isinstance(recovery, dict) else None
        allowed_repairs = {
            "join_split_type_tokens", "join_split_date_tokens",
            "normalize_thousands_separator"}
        if (extraction.get("parser_version") == TRUMP_2025_PARSER_VERSION and
                (not isinstance(recovery, dict) or recovery.get("method") !=
                 "source_bound_part7_structured_row/v1" or
                 not isinstance(repairs, list) or any(
                     not isinstance(repair, dict) or
                     repair.get("method") not in allowed_repairs
                     for repair in repairs))):
            row_reasons.append("part7_parser_recovery_trace_invalid")
        geometry = row.get("transaction_geometry_evidence")
        bounds = geometry.get("column_bounds") if isinstance(geometry, dict) else None
        words = geometry.get("words") if isinstance(geometry, dict) else None
        if (extraction.get("parser_version") == TRUMP_2025_PARSER_VERSION and
                (not isinstance(geometry, dict) or geometry.get("method") !=
                 "source_bound_part7_columns/v1" or not isinstance(bounds, dict) or
                 not isinstance(words, list) or not words or
                 not all(isinstance(bounds.get(name), (int, float)) for name in
                         ("description_start", "type_start", "date_start", "amount_start")))):
            row_reasons.append("part7_geometry_evidence_invalid")
        transaction_row_audit.append({
            "section": row.get("section"), "page_number": row.get("page_number"),
            "row_number": row.get("row_number"), "asset_name": row.get("asset_name"),
            "owner": row.get("owner"), "transaction_type": row.get("transaction_type"),
            "transaction_date": row.get("transaction_date"),
            "amount_low": low, "amount_high": high,
            "source_candidate_eligible": not row_reasons,
            "reasons": sorted(set(row_reasons)),
        })
    if quarantined:
        report_reasons.add("rows_quarantined")
    row_audit: list[dict] = []
    valued_parent_indexes = {
        index for index, row in enumerate(holdings)
        if isinstance(row.get("row_number"), str) and any(
            other_index != index and other.get("section") == row.get("section") and
            (not row.get("account_scope") or not other.get("account_scope") or
             row.get("account_scope") == other.get("account_scope")) and
            isinstance(other.get("row_number"), str) and
            other["row_number"].startswith(row["row_number"] + ".")
            for other_index, other in enumerate(holdings))
    }
    all_dispositions = holdings + transactions + excluded + quarantined
    locator_counts: Counter[str] = Counter(
        row["source_row_locator"] for row in all_dispositions
        if row.get("section") == "part6" and isinstance(row.get("source_row_locator"), str))
    part6_holdings = [row for row in holdings if row.get("section") == "part6"]
    initial_pool_rows = [row for row in holdings
                         if _trump_v7_initial_pool_row(row, extraction)]
    initial_name_counts: Counter[tuple[str, str]] = Counter(
        (row["account_scope"], " ".join(_asset_tokens(row.get("asset_name"))))
        for row in initial_pool_rows)
    part6_token_rows = [(row.get("source_row_locator"), _asset_tokens(row.get("asset_name")))
                        for row in part6_holdings]
    unresolved_relation_locators = set()
    for row in initial_pool_rows:
        locator = row["source_row_locator"]
        tokens = _asset_tokens(row.get("asset_name"))
        # A shorter Description wholly embedded in another Part 6 Description
        # can be a clipped continuation (for example ``SYSTEMS INC`` versus
        # ``CISCO SYSTEMS INC``).  Defer it until row-relationship evidence is
        # available rather than guessing from the high OCR score.
        if any(other_locator != locator and
               _asset_tokens_properly_contained(tokens, other_tokens)
               for other_locator, other_tokens in part6_token_rows):
            unresolved_relation_locators.add(locator)
    for index, row in enumerate(holdings):
        reasons = []
        if row.get("section") not in _HOLDING_PARTS or type(row.get("page_number")) is not int or (
                row.get("page_number", 0) <= 0 or not isinstance(row.get("row_number"), str) or
                not row["row_number"] or not isinstance(row.get("asset_name"), str) or
                not row["asset_name"].strip() or not isinstance(row.get("raw_columns"), dict)):
            reasons.append("holding_row_evidence_invalid")
        if not source_bound_holding_section_page_valid(row, extraction):
            reasons.append("holding_section_page_mismatch")
        locator = row.get("source_row_locator")
        if locator is not None:
            match = _SOURCE_ROW_LOCATOR.fullmatch(locator) if isinstance(locator, str) else None
            if match is None or int(match[1]) != row.get("page_number"):
                reasons.append("holding_row_evidence_invalid")
        elif extraction.get("parser_version") in _TRUMP_SOURCE_BOUND_PARSER_VERSIONS:
            reasons.append("holding_row_evidence_invalid")
        source_bound_legacy_row = (recovered_ocr_holding_valid(row, extraction) or
                                   source_bound_existing_holding_valid(row, extraction))
        if (extraction.get("parser_version") == TRUMP_2025_PARSER_VERSION and
                not source_bound_legacy_row):
            reasons.extend(_trump_v7_holding_reasons(
                row, extraction, locator_counts=locator_counts,
                initial_name_counts=initial_name_counts,
                unresolved_relation_locators=unresolved_relation_locators))
        elif not _critical_field_confidence_valid(row, extraction):
            reasons.append("holding_critical_field_confidence_invalid")
        account_scope = row.get("account_scope")
        account_evidence = row.get("account_scope_evidence")
        if account_scope is not None or account_evidence is not None:
            if not _account_scope_evidence_valid(row, extraction):
                reasons.append("holding_row_evidence_invalid")
        if index in valued_parent_indexes:
            reasons.append("holding_parent_child_double_count")
        owner = row.get("owner")
        if owner == "Unknown" and row.get("section") == "part6":
            # Part 6 is filer-reported, but the form does not always identify
            # which family member owns the asset. Preserve that distinction.
            if row.get("owner_evidence") or row.get("owner_evidence_conflict"):
                reasons.append("holding_owner_evidence_invalid")
        elif owner not in _OWNERS:
            reasons.append("holding_owner_not_disclosed")
        elif ((row.get("section") == "part2" and owner != "Self") or
              (row.get("section") == "part5" and owner != "Spouse") or
              (row.get("section") == "part6" and
               not _part6_owner_evidence_valid(row, extraction))):
            reasons.append("holding_owner_evidence_invalid")
        low, high = row.get("value_low"), row.get("value_high")
        if type(low) is not int or type(high) is not int or (low, high) not in _VALUE_RANGES:
            reasons.append("holding_value_band_invalid")
        raw_value = row.get("raw_columns", {}).get("value")
        if not isinstance(raw_value, str) or _range(raw_value) != (low, high):
            reasons.append("holding_value_evidence_invalid")
        if row.get("report_period_end") != extraction.get("report_period_end") or (
                row.get("holding_valuation_date") != extraction.get("holding_valuation_date")):
            reasons.append("holding_period_conflicts_with_cover")
        if row.get("holding_valuation_status") != (
                "exact_period_end" if valuation else "not_exact_on_cover"):
            reasons.append("holding_valuation_status_invalid")
        if valuation is None:
            reasons.append("holding_valuation_date_not_exact")
        if (ocr_method and extraction.get("parser_version") not in
                _TRUMP_SOURCE_BOUND_PARSER_VERSIONS and
                not recovered_ocr_holding_valid(row, extraction) and (
                not isinstance(row.get("ocr_mean_confidence"), (int, float)) or
                not isinstance(row.get("ocr_min_confidence"), (int, float)) or
                row["ocr_mean_confidence"] < OCR_MINIMUM_ROW_MEAN_CONFIDENCE or
                row["ocr_min_confidence"] < OCR_MINIMUM_CRITICAL_CONFIDENCE)):
            reasons.append("holding_ocr_confidence_invalid")
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
            "transaction_row_audit": transaction_row_audit,
            "source_candidate_transaction_count": sum(
                row["source_candidate_eligible"] for row in transaction_row_audit),
            "holding_blocking_reasons": sorted(holding_reasons),
            "report_blocking_reasons": sorted(report_reasons),
            "production_status": "not_assessed_external_identity_amendments_and_snapshot_gate"}
