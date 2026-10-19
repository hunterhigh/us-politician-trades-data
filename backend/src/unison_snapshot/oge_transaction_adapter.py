"""Shadow adapter from archived OGE 278-T extracts to the common ledger."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import date
from typing import Any

from .oge import OgeCatalogError
from .oge_reports import (
    GENERIC_PARSER_VERSION,
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

OBSERVED_PARSER_VERSION = "oge-278t-observed/v1"
LEGACY_REFERENCE_SCHEMA = "oge-278t-legacy-disposition-reference/v1"


def legacy_disposition_reference(extraction: dict, catalog_record: dict) -> dict:
    """Bind normalized old output without inventing a physical row inventory."""
    from .oge_reports import PARSER_VERSION, EXTRACTION_SCHEMA
    collapse_direct_catalog_records([catalog_record])
    if (extraction.get("schema_version") != EXTRACTION_SCHEMA or extraction.get("parser_version") != PARSER_VERSION or
            "source_rows" in extraction or extraction.get("source_id") != "oge" or
            not re.fullmatch(r"[0-9a-f]{64}", extraction.get("source_sha256") or "") or
            any(extraction.get(a) != catalog_record.get(b) for a, b in (
                ("document_id", "source_document_id"), ("source_url", "document_url"),
                ("catalog_filer_name", "filer_name"), ("agency", "agency"), ("position_title", "position_title")))):
        raise OgeCatalogError("legacy disposition reference differs from the archived catalog source")
    for field in ("transactions", "quarantined"):
        if not isinstance(extraction.get(field), list) or any(not isinstance(row, dict) for row in extraction[field]):
            raise OgeCatalogError("legacy disposition reference rows are malformed")
    rows = extraction["transactions"] + extraction["quarantined"]
    ids = [row.get("extraction_id") for row in rows]
    if (any(not isinstance(value, str) or
            not re.fullmatch(r"oge-278t:[0-9a-f]{24}", value) for value in ids) or len(set(ids)) != len(ids) or
            any(type(row.get("page_number")) is not int or row["page_number"] < 1 for row in rows)):
        raise OgeCatalogError("legacy disposition reference has invalid or repeated source locators")
    raw = json.dumps(extraction, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {"schema_version": LEGACY_REFERENCE_SCHEMA, "source_extraction_sha256": hashlib.sha256(raw).hexdigest(),
            "normalized_transaction_count": len(extraction["transactions"]),
            "quarantined_reference_count": len(extraction["quarantined"]),
            "physical_inventory_status": "not_saved", "qualification_policy": "reference_only_not_requalified",
            "source_extraction": deepcopy(extraction)}


def _validated_legacy_reference(extraction: dict, record: dict) -> dict | None:
    reference = extraction.get("legacy_disposition_reference")
    if reference is None:
        return None
    if (extraction.get("parser_version") != OBSERVED_PARSER_VERSION or not isinstance(reference, dict) or
            not isinstance(reference.get("source_extraction"), dict)):
        raise OgeCatalogError("legacy disposition reference is not a materialized source")
    original = reference["source_extraction"]
    if reference != legacy_disposition_reference(original, record) or any(
            original.get(field) != extraction.get(field) for field in (
                "document_id", "source_sha256", "source_url", "source_id", "catalog_filer_name", "agency", "position_title")):
        raise OgeCatalogError("legacy disposition reference was altered or crossed sources")
    return reference


def generic_cover_evidence_valid(extraction: dict) -> bool:
    """Replay role evidence; a serialized v7 label alone grants no eligibility."""
    from .oge_reports import read_278t_cover_dates
    evidence = extraction.get("cover_date_evidence")
    count = extraction.get("document_page_count")
    if not isinstance(evidence, dict) or type(count) is not int or count < 1:
        return False
    try:
        replay = read_278t_cover_dates(evidence.get("cover_text"),
                                     source_sha256=extraction.get("source_sha256"))
    except (OgeCatalogError, TypeError):
        return False
    return (replay == evidence and not replay["document_reasons"]
            and replay["filed_at"] == extraction.get("filed_at")
            and isinstance(extraction.get("source_rows"), list)
            and all(isinstance(row, dict) and type(row.get("page_number")) is int
                    and 1 <= row["page_number"] <= count for row in extraction["source_rows"]))


def _amount_readings(readings: list[dict]) -> list[tuple[dict, list, bool]]:
    from .oge_278t_amount_categories import observe_template_amount
    recognized = []
    for read in readings:
        if read.get("geometry_status") == "ambiguous":
            continue
        shape = read["shape"]
        if shape.get("status") == "ocr_shape_only" and isinstance(shape.get("values"), list):
            recognized.append((read, shape["values"], False))
        category = observe_template_amount(read.get("raw_ocr_text", ""))
        if category["status"] == "open_upper_template_shape":
            recognized.append((read, [category["minimum_exclusive"], None], True))
    return recognized


def open_amount_qualified(row: dict, parser_version: str) -> bool:
    from .oge_278t_amount_categories import observe_template_amount
    if parser_version not in {GENERIC_PARSER_VERSION, OBSERVED_PARSER_VERSION}:
        return False
    raw = row.get("amount_raw")
    if not isinstance(raw, str):
        return False
    category = observe_template_amount(raw)
    return (category["status"] == "open_upper_template_shape"
            and type(row.get("amount_low")) is int
            and row["amount_low"] == category["minimum_exclusive"]
            and row.get("amount_low_exclusive") is True and row.get("amount_high") is None)


def amount_range_qualified(row: dict, parser_version: str) -> bool:
    return (open_amount_qualified(row, parser_version) or
            type(row.get("amount_low")) is int and type(row.get("amount_high")) is int
            and (row["amount_low"], row["amount_high"]) in _AMOUNT_RANGES
            and row.get("amount_low_exclusive") is not True)


def _observation_input_extraction(extraction: dict) -> dict:
    """Older empty-disposition caches have no physical inventory to preserve.

    An empty inventory here describes the old parser output, never the PDF.
    Saved observations still account for observed/unknown source coverage.
    """
    if "source_rows" not in extraction and extraction.get("transactions") == [] and extraction.get("quarantined") == []:
        result = deepcopy(extraction)
        result["source_rows"] = []
        result["legacy_inventory_basis"] = "empty_legacy_dispositions_without_physical_inventory"
        return result
    return extraction


def materialize_oge_278t_observations(extraction: dict, catalog_record: dict, *,
                                     observations: list[dict], document_page_count: int,
                                     secondary_observations: list[dict] | None = None,
                                     archived_pdf_path: str | None = None,
                                     formal_reference_ids: list[str] | None = None) -> dict:
    """Interpret saved source-bound readings without scanning or replacing old facts.

    The candidate builder calls this function itself. A serialized version label
    is not sufficient authority to bypass its original extraction checks.
    """
    from .oge_278t_field_shadow import observe_content_rows
    from .oge_reports import PARSER_VERSION, _owner_heading, parse_table_rows

    if extraction.get("parser_version") not in {PARSER_VERSION, GENERIC_PARSER_VERSION}:
        raise OgeCatalogError("observed interpretation requires an original generic extraction")
    if (extraction.get("parser_version") == GENERIC_PARSER_VERSION and
            extraction.get("document_page_count") != document_page_count):
        raise OgeCatalogError("saved page count differs from generic extraction")
    legacy_reference = (legacy_disposition_reference(extraction, catalog_record)
                        if "source_rows" not in extraction and
                        (extraction.get("transactions") or extraction.get("quarantined")) else None)
    extraction = _observation_input_extraction(extraction) if legacy_reference is None else extraction
    # Validate the old report/catalog contract before overlaying observations.
    if legacy_reference is None:
        adapt_oge_278t_extraction(extraction, catalog_record)
    sha = extraction["source_sha256"]
    page_count_verified = False
    if archived_pdf_path is not None:
        from pathlib import Path
        import pdfplumber
        source_path = Path(archived_pdf_path)
        if hashlib.sha256(source_path.read_bytes()).hexdigest() != sha:
            raise OgeCatalogError("page count PDF differs from observation source")
        # Read only the archived page inventory, never re-extract text or OCR.
        with pdfplumber.open(source_path) as document:
            if len(document.pages) != document_page_count:
                raise OgeCatalogError("saved page count differs from archived PDF")
        page_count_verified = True
    if type(document_page_count) is not int or document_page_count < 1 or any(
            type(row.get("page_number")) is not int or not 1 <= row["page_number"] <= document_page_count
            for row in (extraction.get("source_rows", []) +
                        (extraction["transactions"] + extraction["quarantined"] if legacy_reference else []))):
        raise OgeCatalogError("saved page count does not contain original source rows")
    if not observations or any(item.get("source_sha256") != sha for item in observations):
        raise OgeCatalogError("saved observations differ from extraction source")
    try:
        content = observe_content_rows(observations, document_page_count=document_page_count,
                                       secondary_observations=secondary_observations)
    except ValueError as exc:
        raise OgeCatalogError(f"saved observation contract is invalid: {exc}") from exc
    result = deepcopy(extraction)
    if legacy_reference is not None:
        result["source_rows"] = []  # Populated only by actual saved observations below.
        result["transactions"], result["quarantined"] = [], []
        result["source_rows_basis"] = "saved_observation_rows_only"
        result["legacy_disposition_reference"] = legacy_reference
    result["source_parser_version"] = extraction["parser_version"]
    result["parser_version"] = OBSERVED_PARSER_VERSION
    old_rows = extraction["transactions"] + extraction["quarantined"]
    all_reference_ids = {row["extraction_id"] for row in old_rows}
    if formal_reference_ids is not None and (not isinstance(formal_reference_ids, list) or
            any(not isinstance(value, str) for value in formal_reference_ids)):
        raise OgeCatalogError("formal reference IDs must come from the existing candidate")
    protected_ids = set(formal_reference_ids or [])
    if legacy_reference is not None:
        old_rows = [row for row in old_rows if row["extraction_id"] in protected_ids]
    old_by_key: dict[str, list[dict]] = {}
    for row in old_rows:
        old_by_key.setdefault(_row_key(row), []).append(row)
    dispositions = {(row["page_number"], row["slot_key"]): row
                    for row in content["slot_content"]}
    receipt_rows = []
    owner = None
    owner_reference = None
    owner_conflicted = False
    previous_page = None
    used_old_ids = set()
    unknown_pages = []
    account_established = False
    account_scope_lost = False
    established_account_context = None
    old_source_keys = {_row_key(row) for row in extraction.get("source_rows", [])}
    raw_pages = {page["page_number"]: page for item in observations for page in item["pages"]}

    def defer(physical, reference, reasons):
        if _row_key(physical) not in old_source_keys:
            result["source_rows"].append(physical)
            result["quarantined"].append({**physical, "observation_reference": reference,
                                           "reasons": sorted(set(reasons))})

    names = ("printed_number", "description", "type", "transaction_date", "notification", "amount")
    for page in sorted(content["field_observations"]["pages"], key=lambda p: p["page_number"]):
        page_number = page["page_number"]
        if previous_page is None or page_number != previous_page + 1:
            owner_conflicted = owner_conflicted or owner_reference is not None
            owner = None
            owner_reference = None
            if previous_page is not None:
                account_scope_lost = account_scope_lost or account_established
        previous_page = page_number
        if page.get("status") != "field_shapes_observed":
            unknown_pages.append(page_number)
            owner_conflicted = owner_conflicted or owner_reference is not None
            owner = None
            owner_reference = None
            account_scope_lost = account_scope_lost or account_established
            continue
        for slot_ordinal, slot in enumerate(page.get("slots", []), 1):
            slot_key = str(slot_ordinal)
            observation = dispositions[(page_number, slot_key)]
            cells = [slot["raw_columns"].get(name, "") for name in names]
            physical = {"page_number": page_number, "cells": cells}
            reference = {"source_sha256": sha, "page_number": page_number,
                         "slot_key": slot_key,
                         "field_observation_ref": observation.get("field_observation_ref")}
            receipt = {**reference, "disposition": "unresolved", "reasons": []}
            receipt_rows.append(receipt)
            matches = old_by_key.get(_row_key(physical), [])
            if len(matches) == 1 and matches[0].get("extraction_id") not in used_old_ids:
                # Preserve existing IDs and qualified/unqualified facts verbatim.
                existing = matches[0]
                used_old_ids.add(existing.get("extraction_id"))
                if legacy_reference is not None:
                    result["source_rows"].append(physical)
                    kind = "transactions" if existing in extraction["transactions"] else "quarantined"
                    result[kind].append(deepcopy(existing))
                receipt.update(disposition="retained_legacy", extraction_id=existing.get("extraction_id"))
                relation = observation.get("relation_evidence")
                if relation and relation.get("status") != "supported_observation_relation":
                    receipt.update(disposition="unresolved", reasons=["retained_legacy_with_observation_conflict", *relation.get("blocking_reasons", [])])
                continue
            structural = observation.get("structural_evidence", {})
            ordered_description = structural.get("description_text")
            owner_heading_text = " ".join((ordered_description or "").casefold().split())
            heading = _owner_heading(owner_heading_text) if (not any((cells[2], cells[3], cells[5])) and
                re.fullmatch(r"(?:filer|self|spouse|dependent child(?:ren)?)(?:'s)? (?:assets|accounts?)", owner_heading_text)) else None
            if heading:
                readings = slot["field_readings"]["description"]["observations"]
                descriptions = {r["shape"].get("value") for r in readings
                                if r["shape"].get("status") == "ocr_text_only"}
                words = slot.get("word_evidence", {})
                safe = (structural.get("status") == "supported_label" and
                        len(descriptions) == 1 and words.get("status") == "source_words_linked"
                        and not words.get("crossing_words") and not words.get("unassigned_word_indices")
                        and not observation.get("word_center_rounding_ambiguous")
                        and not raw_pages[page_number]["slots"][slot_ordinal - 1].get("column_model_outside_word_indices")
                        and not any(r.get("geometry_status") == "ambiguous" for r in readings))
                owner = heading if safe else None
                owner_reference = reference if safe else None
                owner_conflicted = not safe
                if safe:
                    account_established = account_scope_lost = False
                    established_account_context = None
                    receipt.update(disposition="excluded", reasons=["explicit_owner_heading"])
                else:
                    receipt["reasons"] = ["owner_heading_observation_unresolved"]
                    defer(physical, reference, receipt["reasons"])
                continue
            if observation["disposition"] in {"heading", "note", "blank"}:
                if (observation["disposition"] == "heading" and
                        observation.get("account_context", {}).get("status") == "observed"):
                    account_established, account_scope_lost = True, False
                    established_account_context = deepcopy(observation["account_context"])
                receipt.update(disposition="excluded", reasons=[observation["reason"]])
                continue
            if observation["disposition"] != "data_candidate":
                receipt["reasons"] = [observation["reason"]]
                defer(physical, reference, receipt["reasons"])
                # Unknown OCR is not evidence that an optional account title
                # was present. Actual lost scope or an explicit ambiguous
                # account/continuation label does require resolution.
                label = ordered_description or cells[1]
                explicit_scope = bool(re.fullmatch(r"(?:investment|retirement) account\s*#[0-9]+", label, re.I)
                                      or re.match(r"\s*(?:continued|continuation|cont[.'’]?d)\b", label, re.I))
                explicit_owner = bool(re.fullmatch(
                    r"(?:filer|self|spouse|dependent child(?:ren)?)(?:'s)? (?:assets|accounts?)", label, re.I))
                account_scope_lost = account_scope_lost or explicit_scope or explicit_owner
                if explicit_scope or explicit_owner:
                    owner_conflicted = owner_conflicted or owner_reference is not None or explicit_owner
                    owner = None
                    owner_reference = None
                continue
            reasons = []
            relation = observation.get("relation_evidence", {})
            amount_readings = _amount_readings(slot["field_readings"]["amount"]["observations"])
            amount_keys = {(tuple(value), exclusive) for _, value, exclusive in amount_readings}
            open_amount = len(amount_keys) == 1 and next(iter(amount_keys))[1] is True
            if relation.get("status") != "supported_observation_relation":
                blockers = relation.get("blocking_reasons") or ["field_relation_unresolved"]
                reasons.extend(reason for reason in blockers
                               if not (open_amount and reason == "amount_observation_missing"))
            values = {}
            field_evidence = {}
            for field in ("description", "type", "transaction_date", "amount", "printed_number"):
                readings = slot["field_readings"][field]["observations"]
                recognized = ([value for _, value, _ in amount_readings] if field == "amount" else
                              [r["shape"].get("values", r["shape"].get("value"))
                              for r in readings if r.get("geometry_status") != "ambiguous"
                              and r["shape"].get("status") in {"ocr_shape_only", "ocr_text_only"}])
                distinct = {json.dumps(value, sort_keys=True) for value in recognized}
                if len(distinct) != 1:
                    reasons.append(field + "_reading_unresolved")
                else:
                    values[field] = recognized[0]
                    field_evidence[field] = [r["source_ref"] for r in readings
                                             if r.get("geometry_status") != "ambiguous"
                                             and r["shape"].get("status") in {"ocr_shape_only", "ocr_text_only"}]
                    if field == "amount":
                        field_evidence[field] = [read["source_ref"] for read, _, _ in amount_readings]
            account = observation.get("account_context", {})
            if account.get("status") == "observed":
                account_established = True
                established_account_context = deepcopy(account)
            effective_account = deepcopy(account)
            if account_scope_lost:
                effective_account.update(status="unknown", reason="established_or_explicit_account_scope_unresolved")
            elif account_established and established_account_context is not None:
                effective_account = deepcopy(established_account_context)
                if account.get("status") != "observed":
                    effective_account["coverage_warning"] = "non_scope_unknown_slot_retained_separately"
            elif account.get("status") in {"none_observed", "unknown"}:
                effective_account.update(status="not_observed", title=None,
                    reason="optional_account_title_not_observed",
                    coverage_warning="preceding_region_or_title_not_observed_no_absence_claim")
            receipt["source_account_context"] = deepcopy(account)
            receipt["effective_account_context"] = effective_account
            if account_scope_lost:
                reasons.append("account_context_unresolved")
            account_title = effective_account.get("title") if effective_account.get("status") == "observed" else None
            if owner_conflicted:
                reasons.append("owner_heading_observation_unresolved")
            # An unmatched old row might be the same disclosure with OCR variation.
            # Never mint a second formal ID based only on a printed row number.
            if matches or any(row.get("page_number") == page_number and
                              row.get("extraction_id") not in used_old_ids for row in old_rows):
                reasons.append("legacy_row_correspondence_unresolved")
            if protected_ids - all_reference_ids:
                reasons.append("legacy_formal_reference_unbound")
            amount = values.get("amount")
            if not isinstance(amount, list) or (tuple(amount) not in _AMOUNT_RANGES and not open_amount):
                reasons.append("amount_range_not_qualified")
            if reasons:
                receipt["reasons"] = sorted(set(reasons))
                defer(physical, reference, reasons)
                continue
            normalized_cells = [str(values["printed_number"]), values["description"],
                                values["type"], date.fromisoformat(values["transaction_date"]).strftime("%m/%d/%Y"),
                                cells[4], f"Over ${amount[0]:,}" if open_amount else f"${amount[0]:,} - ${amount[1]:,}"]
            good, bad = parse_table_rows([(page_number, normalized_cells)], source_sha=sha,
                                         default_owner=owner or "Undisclosed", allow_open_amounts=True,
                                         account_title=account_title, allow_exchange=True)
            row = (good + bad)[0]
            row["cells"] = cells
            row["observation_reference"] = reference
            row["field_observation_references"] = field_evidence
            row["owner_observation_reference"] = owner_reference
            row["account_context"] = effective_account
            if owner is None:
                row["owner_disclosure_basis"] = "not_disclosed_in_bound_six_column_table"
            # Use the existing formal identity algorithm, never a page/slot ID.
            result["source_rows"].append(physical)
            result["transactions" if good else "quarantined"].append(row)
            receipt.update(disposition="materialized" if good else "quarantined",
                           extraction_id=row["extraction_id"], reasons=row.get("reasons", []))
    unresolved = sum(row["disposition"] == "unresolved" for row in receipt_rows)
    if any(row["disposition"] in {"materialized", "quarantined"} for row in receipt_rows):
        result["legacy_document_reasons"] = deepcopy(extraction.get("document_reasons", []))
        result["document_reasons"] = [reason for reason in extraction.get("document_reasons", [])
                                      if reason not in {"transaction_table_not_found", "no_transaction_rows_found"}]
        if result["legacy_document_reasons"] != result["document_reasons"]:
            # Only the table-discovery blocker has just acquired source evidence;
            # date/identity/other document blockers remain effective.
            result["evidence_complete"] = not result["document_reasons"]
    result["observation_receipt"] = {
        "schema_version": "oge-278t-observed-facts/v1", "source_sha256": sha,
        "rows": receipt_rows, "unresolved_slot_count": unresolved,
        "observed_slot_count": len(content["slot_content"]),
        "unknown_regions": [row for row in content["slot_content"] if row["page_number"] in unknown_pages],
        "unobserved_pages": content["source_report_ledger"]["unobserved_pages"],
        "geometry_unknown_pages": unknown_pages,
        "page_count_basis": "source_sha256_verified_pdf" if page_count_verified else "declared_only",
        "legacy_inventory_basis": ("normalized_disposition_reference_without_physical_inventory" if legacy_reference else
                                   extraction.get("legacy_inventory_basis", "saved_physical_inventory")),
        **({"legacy_reference_sha256": legacy_reference["source_extraction_sha256"],
            "legacy_normalized_transaction_count": legacy_reference["normalized_transaction_count"],
            "legacy_quarantined_reference_count": legacy_reference["quarantined_reference_count"],
            "legacy_normalized_not_sent_to_qualification": True,
            "protected_formal_reference_ids": sorted(protected_ids),
            "unique_source_row_count": None} if legacy_reference else {}),
        "legacy_document_reasons": result.get("legacy_document_reasons", []),
        "initial_account_scope_basis": "optional_account_title_not_observed_no_absence_claim",
        "coverage_complete": not legacy_reference and page_count_verified and not unresolved and not unknown_pages and not content["source_report_ledger"]["unobserved_pages"],
    }
    return result


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
                              run_id: str = "offline-replay",
                              raster_observations: list[dict] | None = None,
                              document_page_count: int | None = None,
                              secondary_field_observations: list[dict] | None = None) -> dict:
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
    legacy_reference = _validated_legacy_reference(extraction, catalog_record)
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
    if (extraction.get("parser_version") == GENERIC_PARSER_VERSION or
            extraction.get("source_parser_version") == GENERIC_PARSER_VERSION) and not generic_cover_evidence_valid(extraction):
        report_reasons.append("cover_date_evidence_invalid")
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
                if normalized.get("transaction_type") not in {"purchase", "sale", "exchange"}:
                    reasons.append("transaction_type_not_qualified")
                if not amount_range_qualified(normalized, parser_version):
                    reasons.append("amount_range_not_qualified")
                if (parsed_day is None or filed_day is None or parsed_day > filed_day):
                    reasons.append("transaction_date_not_qualified")
                if not isinstance(normalized.get("asset_name"), str) or not normalized["asset_name"].strip():
                    reasons.append("asset_name_not_qualified")
                if normalized.get("owner") not in {"Self", "Spouse", "Dependent Child"} and not (
                        parser_version in {OBSERVED_PARSER_VERSION, GENERIC_PARSER_VERSION} and normalized.get("owner") == "Undisclosed"
                        and normalized.get("owner_disclosure_basis") == "not_disclosed_in_bound_six_column_table"
                        and (parser_version == GENERIC_PARSER_VERSION and generic_cover_evidence_valid(extraction) or
                             normalized.get("observation_reference", {}).get("source_sha256") == source_sha)):
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
        is_open = open_amount_qualified(normalized, parser_version)
        required = ["asset_name", "transaction_type", "transaction_date", "owner", "amount_low"]
        required.append("amount_low_exclusive" if is_open else "amount_high")
        if is_open:
            fields["amount_low_exclusive"] = (raw_cells[5] if len(raw_cells) > 5 else None, True)
        observations = []
        for field, (raw_value, value) in fields.items():
            observation_id = f"{physical_index}:{field}"
            observations.append({
                "schema_version": OBSERVATION_SCHEMA,
                "observation_id": observation_id,
                "field": field,
                "raw_value": raw_value if isinstance(raw_value, (str, int, float, bool)) else None,
                "normalized_value": value if isinstance(value, (str, int, float, bool)) else None,
                "method": "ocr" if normalized.get("observation_reference") else "embedded_text",
                "confidence": None,
                "selected": value is not None,
                "required_for_projection": field in required,
                "locations": [{"page": raw["page_number"],
                               "row_locator": str(physical_index),
                               "kind": evidence_kind}],
                "conditions": {"source_field": field,
                               **({"observation_reference": normalized["observation_reference"]}
                                  if normalized.get("observation_reference") else {})},
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
            "required_projection_fields": required if disposition == "qualified" else [],
        }
        validate_candidate_row(candidate)
        output_rows.append(candidate)
        accounted[disposition] += 1
    legacy_rows = ({row["extraction_id"]: row for row in
                   legacy_reference["source_extraction"]["transactions"] +
                   legacy_reference["source_extraction"]["quarantined"]} if legacy_reference else {})
    if any(legacy_rows.get(row.get("extraction_id")) != row
           for queue in dispositions.values() for _, row, _ in queue):
        raise OgeCatalogError("OGE parser disposition has rows absent from source row inventory")
    if sum(accounted.values()) != len(source_rows):
        raise OgeCatalogError("OGE 278-T row disposition accounting failed")
    result = {"schema_version": "oge-278t-ledger-adapter/v1",
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
    if legacy_reference:
        result["legacy_disposition_reference"] = {key: value for key, value in legacy_reference.items()
                                                    if key != "source_extraction"}
        result["physical_inventory_status"] = "observed_rows_only_legacy_inventory_not_saved"
    if raster_observations is not None:
        result.update(_source_content_relations(
            extraction, raster_observations, document_page_count=document_page_count,
            secondary_field_observations=secondary_field_observations))
    elif document_page_count is not None or secondary_field_observations is not None:
        raise OgeCatalogError("content relations require saved raster observations")
    return result


def _source_content_relations(extraction: dict, observations: list[dict], *,
                              document_page_count: int,
                              secondary_field_observations: list[dict] | None) -> dict:
    """Connect original grids to the existing adapter without changing its facts."""
    from .oge_278t_field_shadow import observe_content_rows
    from .oge_278t_page_shadow import reconcile_raster_ocr_rows

    if any(item.get("source_sha256") != extraction["source_sha256"] for item in observations):
        raise OgeCatalogError("content observations differ from extraction source")
    observed = observe_content_rows(
        observations, document_page_count=document_page_count,
        secondary_observations=secondary_field_observations)
    ledger = observed["source_report_ledger"]
    census = {"source_sha256": extraction["source_sha256"], "results": [
        {"page_number": page["page_number"],
         "status": "physical_slots_located" if page["slots"] else "coverage_unknown",
         "physical_row_bands": [slot["bounds"] for slot in page["slots"]]}
        for page in ledger["pages"]]}
    source_indices, positioned, relations = [], [], []
    for index, row in enumerate(extraction["source_rows"]):
        if not 1 <= row["page_number"] <= document_page_count:
            raise OgeCatalogError("parser row outside bound document page range")
        frame = row.get("coordinate_frame", extraction.get("coordinate_frame"))
        reason = ("parser_row_geometry_unavailable" if row.get("geometry_top") is None else
                  "parser_coordinate_frame_unavailable" if frame != "pdf_points" else None)
        relations.append({"source_row_index": index, "status": reason or "outside_observed_grid",
                          "slot_refs": [], "logical_row_keys": []})
        if reason is None:
            positioned.append(row)
            source_indices.append(index)
    reconciled = reconcile_raster_ocr_rows(
        census, positioned, expected_sha256=extraction["source_sha256"],
        ocr_source_sha256=extraction["source_sha256"])
    local_rows = {}
    for row in ledger["logical_rows"]:
        for ref in row["slot_refs"]:
            local_rows.setdefault((ref["page_number"], ref["slot_key"]), []).append(row["local_key"])
    slot_matches = []
    for slot in reconciled["slots"]:
        refs = {"page_number": slot["page_number"], "slot_key": str(slot["slot_index"])}
        indices = [source_indices[index] for index in slot["ocr_row_indices"]]
        keys = local_rows.get((refs["page_number"], refs["slot_key"]), [])
        slot_matches.append({**refs, "source_row_indices": indices,
                             "status": slot["status"], "logical_row_keys": keys})
        for index in indices:
            relations[index].update({"slot_refs": [refs], "logical_row_keys": keys,
                "status": "multiple_parser_rows_in_slot" if len(indices) > 1 else
                "geometry_match_only" if len(keys) == 1 else "slot_relation_unresolved"})
    return {**observed, "parser_row_relations": relations, "slot_parser_matches": slot_matches}
