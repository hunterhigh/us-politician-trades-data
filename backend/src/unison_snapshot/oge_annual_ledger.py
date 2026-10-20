"""Read-only Part 6/7 disposition ledger for archived OGE 278e annual reports.

This adapter accounts for parser-detected rows.  It does not establish that the
PDF's complete row population was detected and cannot publish candidates.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import re
from datetime import date
from urllib.parse import urlsplit

from .oge_annual import (PARSER_VERSION, SCHEMA, SHADOW_PARSER_VERSION, QUALIFIED_PARSER_VERSION,
                         read_annual_band, read_annual_cover_dates)
from .pipeline_ledger import (CANDIDATE_ROW_SCHEMA, OBSERVATION_SCHEMA,
                              RUN_MANIFEST_SCHEMA, idempotency_key,
                              validate_candidate_row, validate_run_manifest)


RULES_VERSION = "oge-278e-annual-shadow/v1"
QUALIFIED_RULES_VERSION = "oge-278e-row-qualification/v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PARTS = ("part6", "part7")


def _validate_source(extraction: dict) -> None:
    source_url = extraction.get("source_url") if isinstance(extraction, dict) else None
    parsed_url = urlsplit(source_url) if isinstance(source_url, str) else None
    document_id = extraction.get("source_document_id") if isinstance(extraction, dict) else None
    source_sha = extraction.get("source_sha256") if isinstance(extraction, dict) else None
    if not isinstance(extraction, dict) or extraction.get("schema_version") != SCHEMA or (
            extraction.get("parser_version") not in {PARSER_VERSION, SHADOW_PARSER_VERSION, QUALIFIED_PARSER_VERSION} or
            extraction.get("source_id") != "oge" or
            extraction.get("form_type") != "278e" or
            extraction.get("report_type") != "Annual" or
            not isinstance(document_id, str) or
            re.fullmatch(r"[0-9a-f]{32}", document_id) is None or
            parsed_url is None or parsed_url.scheme != "https" or
            parsed_url.hostname not in {"extapps2.oge.gov", "www2.oge.gov",
                                            "oge.gov", "www.oge.gov"} or
            not parsed_url.path.lower().endswith(".pdf") or
            not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha) or
            extraction.get("evidence_archive_path") !=
            f"oge/annual/reports/{document_id}/{source_sha}.pdf"):
        raise ValueError("OGE annual shadow input identity is invalid")
    for collection in ("holdings", "transactions", "excluded", "quarantined"):
        rows = extraction.get(collection)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"OGE annual {collection} must be an array of objects")
        if any(row.get("section") not in {"part2", "part5", *_PARTS} for row in rows):
            raise ValueError(f"OGE annual {collection} has an unknown part")
        allowed = ({"part2", "part5", "part6"} if collection == "holdings" else
                   {"part7"} if collection == "transactions" else
                   {"part2", "part5", *_PARTS})
        if any(row["section"] not in allowed for row in rows):
            raise ValueError(f"OGE annual {collection} contains a row from the wrong part")


def _detected_rows(extraction: dict, *, qualified: bool = False) -> list[tuple[str, dict]]:
    result = []
    for collection in ("holdings", "transactions", "excluded", "quarantined"):
        for row in extraction[collection]:
            if row["section"] in ({"part2", "part5", *_PARTS} if qualified else _PARTS):
                result.append((collection, row))
    return result


def annual_filing_date_evidence(extraction: dict, scan_dates: dict | None = None) -> dict:
    """Use actual filer-role text; ignore receipt and fixed visual date values."""
    cover = extraction.get("cover_date_evidence")
    if isinstance(cover, dict) and isinstance(cover.get("cover_text"), str):
        replay = read_annual_cover_dates(cover.get("cover_text", ""), source_sha256=extraction["source_sha256"])
        if replay == cover and replay["filed_at"] is not None and replay["filed_at"] == extraction.get("filing_date"):
            return {"date": replay["filed_at"], "method": "native_cover_roles", "source_reference": cover}
    if scan_dates is not None:
        from .oge_annual_scan_dates import FIXED_COVERS, SCHEMA as DATE_SCHEMA
        pinned = FIXED_COVERS.get(extraction["source_document_id"])
        if (not isinstance(scan_dates, dict) or scan_dates.get("schema_version") != DATE_SCHEMA or
                scan_dates.get("source_document_id") != extraction["source_document_id"] or
                scan_dates.get("source_sha256") != extraction["source_sha256"] or
                not pinned or pinned[0] != extraction["source_sha256"] or
                scan_dates.get("cover_kind") != "annual_form" or
                scan_dates.get("page_count") != extraction.get("page_count")):
            raise ValueError("annual scan date observation differs from source")
        block = scan_dates.get("date_blocks", {}).get("filer_signature", {})
        raw = block.get("embedded_text_raw")
        cover_hash = extraction.get("source_cover_text_sha256")
        if cover_hash is None:
            page_roles = extraction.get("source_coverage_evidence", {}).get("pages", [])
            cover_hash = next((row.get("page_text_sha256") for row in page_roles if row.get("page_number") == 1), None)
        if (not isinstance(cover_hash, str) or not _SHA.fullmatch(cover_hash) or
                scan_dates.get("cover_page_text_sha256") != cover_hash or
                block.get("page_text_sha256") != cover_hash):
            raise ValueError("annual scan date cover page binding differs from extraction")
        role = (re.search(r"Filer(?:['’]s)?\s+Certification|Signature\s+of\s+(?:Filer|Reporting\s+Individual)", raw, re.I)
                if isinstance(raw, str) else None)
        if role and block.get("page_number") == 1:
            stop = re.search(r"\b(?:Agency|Other\s+Review|OGE\s+Certification)\b", raw[role.end():], re.I)
            bounded = raw[role.end():role.end()+stop.start()] if stop else raw[role.end():]
            candidates = re.findall(r"\bDate\s*:\s*(\d{1,2}/\d{1,2}/(?:19|20)\d{2})(?!\d)", bounded, re.I)
            values = set()
            for value in candidates:
                from .oge_annual import _date
                values.add(_date(value))
            if len(values) == 1 and None not in values:
                return {"date": next(iter(values)), "method": "automatic_embedded_text_in_existing_filer_region",
                    "source_reference": {"schema_version": scan_dates["schema_version"],
                        "source_sha256": extraction["source_sha256"], "source_document_id": extraction["source_document_id"],
                        "page_number": 1, "raw_text": raw, "raw_text_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                        "region_fraction": block.get("region_fraction"),
                        "observation_sha256": hashlib.sha256(json.dumps(scan_dates, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}}
    return {"date": None, "method": "filer_date_unresolved_receipt_not_substituted"}


def annual_holdings_coverage_complete(extraction: dict) -> bool:
    evidence = extraction.get("source_coverage_evidence", {})
    pages = evidence.get("pages")
    count = extraction.get("page_count")
    if (extraction.get("parser_version") != QUALIFIED_PARSER_VERSION or
            evidence.get("schema_version") != "oge-278e-native-page-roles/v1" or
            evidence.get("source_sha256") != extraction["source_sha256"] or
            evidence.get("method") != "same_pass_full_archived_pdf_native_text_and_tables" or
            type(count) is not int or evidence.get("page_count") != count or
            not isinstance(pages, list) or [row.get("page_number") for row in pages] != list(range(1, count + 1))):
        return False
    holding_parts = {"part2", "part5", "part6"}
    if not holding_parts <= {row.get("section") for row in pages if row.get("role") == "target_section"}:
        return False
    if any(type(row.get("page_number")) is not int or not 1 <= row["page_number"] <= count
           for row in extraction["holdings"]):
        return False
    if any(row["section"] in holding_parts for row in extraction["quarantined"]):
        return False
    return all(isinstance(row.get("page_text_sha256"), str) and _SHA.fullmatch(row["page_text_sha256"]) and
               row.get("role") in {"cover", "target_section", "other_disclosure_section", "instructions"} and
               not (row.get("section") in holding_parts and row.get("status") != "rows_accounted") for row in pages)


def _qualified_row_reasons(collection: str, row: dict, extraction: dict, filed: dict) -> list[str]:
    if collection in {"quarantined", "excluded"}:
        values = row.get("reasons", [row.get("reason")]) or ["source_row_not_qualified"]
        return sorted({value for value in values if isinstance(value, str) and value}) or ["source_row_not_qualified"]
    reasons = []
    if type(row.get("page_number")) is not int or not 1 <= row["page_number"] <= extraction["page_count"]:
        reasons.append("source_row_page_outside_inventory")
    if not isinstance(row.get("asset_name"), str) or not row["asset_name"].strip():
        reasons.append("asset_name_missing")
    if row.get("owner") not in {"Self", "Spouse", "Dependent Child", "Joint"}:
        reasons.append("owner_unverified")
    if row.get("account_context", {}).get("status") == "unknown":
        reasons.append("account_scope_unresolved")
    if not filed["date"]:
        reasons.append("filing_date_unverified")
    holding = row["section"] != "part7"
    prefix = "value" if holding else "amount"
    band = read_annual_band(row.get("raw_value" if holding else "raw_amount", ""))
    marker = prefix + "_low_exclusive"
    if (band is None or row.get(prefix + "_low") != band["low"] or row.get(prefix + "_high") != band["high"] or
            ("low_exclusive" in band and (type(row.get(marker)) is not bool or row[marker] != band["low_exclusive"])) or
            ("low_exclusive" not in band and marker in row)):
        reasons.append(prefix + "_band_unverified")
    if holding:
        if row.get("report_period_end") != extraction.get("report_period_end"):
            reasons.append("report_period_unverified")
        elif filed["date"] and filed["date"] < extraction["report_period_end"]:
            reasons.append("filing_date_before_report_period_end")
    else:
        if row.get("transaction_type") not in {"purchase", "sale", "exchange"}:
            reasons.append("transaction_type_unverified")
        elif not isinstance(row.get("raw_type"), str) or row["raw_type"].strip().casefold() != row["transaction_type"]:
            reasons.append("transaction_type_raw_mismatch")
        try:
            when = date.fromisoformat(row.get("transaction_date"))
            if when.year != extraction.get("annual_year") or filed["date"] and when > date.fromisoformat(filed["date"]):
                reasons.append("transaction_date_unverified")
        except (TypeError, ValueError):
            reasons.append("transaction_date_unverified")
        from .oge_annual import _date
        if _date(row.get("raw_date", "")) != row.get("transaction_date"):
            reasons.append("transaction_date_raw_mismatch")
        reasons.append("pending_278t_reconciliation")
    return sorted(set(reasons))


def _row_reasons(collection: str, row: dict, extraction: dict,
                 parent_numbers: set[str], duplicate_numbers: set[tuple]) -> list[str]:
    part = row["section"]
    if collection in {"quarantined", "excluded"}:
        reasons = row.get("reasons", [row.get("reason")])
        return sorted(set(reason for reason in reasons if isinstance(reason, str) and reason)) or [
            "source_row_not_qualified"]
    reasons: set[str] = set()
    if not row.get("asset_name"):
        reasons.add("asset_name_missing")
    number = row.get("row_number")
    if not isinstance(number, str) or not number:
        reasons.add("row_number_unverified")
    elif (part, row.get("page_number"), number) in duplicate_numbers:
        reasons.add("duplicate_printed_row_number")
    if part == "part6":
        if row.get("owner") not in {"Self", "Spouse", "Dependent Child", "Joint"}:
            reasons.add("owner_unverified")
        if type(row.get("value_low")) is not int or type(row.get("value_high")) is not int:
            reasons.add("value_band_unverified")
        if row.get("report_period_end") != extraction.get("report_period_end"):
            reasons.add("report_period_unverified")
        if isinstance(number, str) and "." in number and number.rsplit(".", 1)[0] in parent_numbers:
            reasons.add("parent_account_row_unresolved")
    else:
        numbering = extraction.get("part7_numbering")
        if not isinstance(numbering, dict) or numbering.get("row_reconciliation_complete") is not True:
            reasons.add("part7_numbering_unreconciled")
        if extraction.get("filing_date") is None:
            reasons.add("filing_date_unverified")
        reasons.add("pending_278t_reconciliation")
        # The annual extraction is review-only.  An opt-in marker supplied in
        # its JSON is not an authorization to promote Part 7 rows.
        reasons.add("part7_opt_in_not_enabled")
    return sorted(reasons)


def build_oge_annual_shadow_ledger(extraction: dict, *, run_id: str,
                                   code_commit: str, started_at: str,
                                   completed_at: str | None = None,
                                   trigger: str = "replay", qualified: bool = False,
                                   scan_date_observation: dict | None = None) -> dict:
    """Return validated rows and manifest; never claim full-report completeness."""
    _validate_source(extraction)
    if qualified and (type(extraction.get("annual_year")) is not int or
            extraction.get("report_period_end") != f"{extraction['annual_year']}-12-31" or
            type(extraction.get("page_count")) is not int or extraction["page_count"] < 1):
        raise ValueError("annual qualification period or page inventory is invalid")
    source_sha = extraction["source_sha256"]
    document_id = extraction["source_document_id"]
    parser_version = extraction["parser_version"]
    rules_version = QUALIFIED_RULES_VERSION if qualified else RULES_VERSION
    parser_id = "oge-278e-tables"
    layout = "oge-278e-tables:" + parser_version
    key = idempotency_key(source_id="oge", source_sha256=source_sha,
                          parser_id=parser_id, parser_version=parser_version,
                          rules_version=rules_version, layout_fingerprint=layout)
    detected = _detected_rows(extraction, qualified=qualified)
    unlocated = [row for _, row in detected if type(row.get("page_number")) is not int or row["page_number"] < 1]
    if qualified:
        detected = [(collection, row) for collection, row in detected if row not in unlocated]
    filed = annual_filing_date_evidence(extraction, scan_date_observation) if qualified else None
    parent_numbers = {row.get("row_number") for collection, row in detected
                      if row["section"] == "part6" and collection == "quarantined" and
                      isinstance(row.get("row_number"), str)}
    number_counts = Counter((row["section"], row.get("page_number"), row.get("row_number"),
                             row.get("account_context", {}).get("title") if qualified else None)
                            for _, row in detected if row.get("row_number"))
    duplicate_numbers = {identity if qualified else identity[:3] for identity, count in number_counts.items() if count > 1}
    identity_counts: Counter[tuple] = Counter()
    rows = []
    for collection, row in detected:
        part = row["section"]
        page = row.get("page_number")
        if type(page) is not int or page < 1:
            raise ValueError("OGE annual source row lacks a positive PDF page")
        number = row.get("row_number")
        identity = (part, page, str(number),
                    json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        occurrence = identity_counts[identity]
        identity_counts[identity] += 1
        candidate_id = "oge-annual-row:" + hashlib.sha256(
            f"{document_id}|{source_sha}|{identity}|{occurrence}".encode()).hexdigest()[:24]
        location = {"kind": "pdf_row", "page": page}
        if isinstance(number, str) and number:
            location["row_locator"] = number
        reasons = (_qualified_row_reasons(collection, row, extraction, filed) if qualified else
                   _row_reasons(collection, row, extraction, parent_numbers, duplicate_numbers))
        if qualified and (part, page, number, row.get("account_context", {}).get("title")) in duplicate_numbers:
            reasons.append("duplicate_printed_row_number")
        disposition = ("excluded" if collection == "excluded" else
                       "unrecognized" if "table_header_unrecognized" in reasons or
                       "table_count_unrecognized" in reasons else
                       "qualified" if not reasons else "quarantined")
        fields = ("asset_name", "owner", "row_number", "value_low", "value_high",
                  "raw_value", "report_period_end") if part != "part7" else (
                  "asset_name", "owner", "row_number", "transaction_type",
                  "transaction_date", "amount_low", "amount_high", "raw_type",
                  "raw_date", "raw_amount")
        raw_fields = ({"value_low": "raw_value", "value_high": "raw_value"}
                      if part != "part7" else
                      {"transaction_type": "raw_type", "transaction_date": "raw_date",
                       "amount_low": "raw_amount", "amount_high": "raw_amount"})
        prefix = "value" if part != "part7" else "amount"
        is_open = qualified and row.get(prefix + "_high") is None and type(row.get(prefix + "_low_exclusive")) is bool
        if is_open:
            fields = (*fields, prefix + "_low_exclusive")
        required = ({"asset_name", "owner", "value_low", "value_high", "report_period_end"} if part != "part7" else
                    {"asset_name", "transaction_type", "transaction_date", "amount_low", "amount_high"})
        if is_open:
            required.discard(prefix + "_high")
            required.add(prefix + "_low_exclusive")
        observations = []
        for field in fields:
            value = row.get(field)
            raw_value = row.get(raw_fields.get(field, field))
            observations.append({
                "schema_version": OBSERVATION_SCHEMA,
                "observation_id": hashlib.sha256(f"{candidate_id}|{field}".encode()).hexdigest()[:24],
                "field": field, "raw_value": raw_value, "normalized_value": value,
                "method": "embedded_text", "confidence": None, "selected": True,
                "required_for_projection": field in required,
                "locations": [location], "conditions": {"section": part,
                    "extraction_collection": collection,
                    **({"value_disclosure_status": row["value_disclosure_status"]}
                       if qualified and "value_disclosure_status" in row else {})},
            })
        if collection == "quarantined":
            observations.append({
                "schema_version": OBSERVATION_SCHEMA,
                "observation_id": hashlib.sha256(f"{candidate_id}|raw_cells".encode()).hexdigest()[:24],
                "field": "raw_cells", "raw_value": json.dumps(row.get("cells", []), ensure_ascii=False),
                "normalized_value": None, "method": "embedded_text", "confidence": None,
                "selected": True, "required_for_projection": False,
                "locations": [location], "conditions": {"section": part},
            })
        item = {
            "schema_version": CANDIDATE_ROW_SCHEMA, "candidate_id": candidate_id,
            "run_id": run_id, "idempotency_key": key,
            "source": {"source_id": "oge", "document_id": document_id,
                       "source_url": extraction["source_url"], "source_sha256": source_sha},
            "parser": {"parser_id": parser_id, "parser_version": parser_version,
                       "rules_version": rules_version, "layout_fingerprint": layout},
            "disposition": disposition, "reasons": reasons,
            "unresolved_conflicts": [] if disposition == "qualified" else reasons,
            "observations": observations, "evidence_locations": [location],
            "required_projection_fields": [observation["field"] for observation in observations
                                           if observation["required_for_projection"]],
            **({"annual_relationships": {"account_context": row.get("account_context"),
                "owner_context": row.get("owner_context"), "owner_basis": row.get("owner_basis"),
                "hierarchy_evidence": row.get("hierarchy_evidence"),
                "saved_scan_reference": row.get("saved_scan_reference"),
                **({"native_empty_cell_evidence": row["native_empty_cell_evidence"]} if "native_empty_cell_evidence" in row else {}),
                **({"saved_native_reference": row["saved_native_reference"]} if "saved_native_reference" in row else {})}} if qualified else {}),
        }
        validate_candidate_row(item)
        rows.append(item)
    dispositions = Counter(row["disposition"] for row in rows)
    counts = {"discovered_documents": 1, "archived_documents": 1,
              "parsed_documents": 1, "failed_documents": 0,
              "no_row_documents": 0, "excluded_documents": 0,
              **{f"{name}_rows": dispositions[name] for name in
                 ("qualified", "quarantined", "excluded", "unrecognized")}}
    manifest = {
        "schema_version": RUN_MANIFEST_SCHEMA, "run_id": run_id,
        "workflow": "oge-278e-annual-shadow", "code_commit": code_commit,
        "trigger": trigger, "started_at": started_at, "completed_at": completed_at,
        "source_scope": ["oge"], "documents": [{
            "source_id": "oge", "document_id": document_id,
            "source_url": extraction["source_url"], "source_sha256": source_sha,
            "parser_id": parser_id, "parser_version": parser_version,
            "rules_version": rules_version, "layout_fingerprint": layout,
            "idempotency_key": key, "disposition": "parsed",
        }], "counts": counts, "accounted_rows": len(rows), "outputs": [],
    }
    validate_run_manifest(manifest)
    return {
        "manifest": manifest, "rows": rows,
        "part_counts": {part: {name: sum(row["disposition"] == name and
                            row["observations"][0]["conditions"]["section"] == part
                            for row in rows) for name in
                            ("qualified", "quarantined", "excluded", "unrecognized")}
                        for part in (("part2", "part5", *_PARTS) if qualified else _PARTS)},
        "report_completeness": {"status": "not_verified_complete",
            "reason": "independent_source_row_census_missing",
            "detected_part6_rows": sum(row["section"] == "part6" for _, row in detected),
            "detected_part7_rows": sum(row["section"] == "part7" for _, row in detected)},
        "part6_relationships": {
            "unresolved_parent_account_children": sum(
                "parent_account_row_unresolved" in row["reasons"] for row in rows),
            "asset_endnotes": "not_verified",
            "aggregate_dedup": "not_verified",
        },
        "revision_reconciliation": "not_verified",
        "part7_opt_in": "source_rows_pending_278t_relationship" if qualified else "not_enabled",
        "publication_status": "row_qualification_ready_for_existing_candidate" if qualified else "shadow_only_not_promoted",
        **({"qualification_mode": "source_rows_to_existing_annual_candidate/v1",
            "filing_date_evidence": filed, "holding_coverage_complete": annual_holdings_coverage_complete(extraction),
            "unlocated_source_issues": unlocated, "detected_source_rows": [row for _, row in detected]}
           if qualified else {}),
    }
