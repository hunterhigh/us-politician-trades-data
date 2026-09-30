"""Read-only Part 6/7 disposition ledger for archived OGE 278e annual reports.

This adapter accounts for parser-detected rows.  It does not establish that the
PDF's complete row population was detected and cannot publish candidates.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import re
from urllib.parse import urlsplit

from .oge_annual import PARSER_VERSION, SCHEMA, SHADOW_PARSER_VERSION
from .pipeline_ledger import (CANDIDATE_ROW_SCHEMA, OBSERVATION_SCHEMA,
                              RUN_MANIFEST_SCHEMA, idempotency_key,
                              validate_candidate_row, validate_run_manifest)


RULES_VERSION = "oge-278e-annual-shadow/v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PARTS = ("part6", "part7")


def _validate_source(extraction: dict) -> None:
    source_url = extraction.get("source_url") if isinstance(extraction, dict) else None
    parsed_url = urlsplit(source_url) if isinstance(source_url, str) else None
    document_id = extraction.get("source_document_id") if isinstance(extraction, dict) else None
    source_sha = extraction.get("source_sha256") if isinstance(extraction, dict) else None
    if not isinstance(extraction, dict) or extraction.get("schema_version") != SCHEMA or (
            extraction.get("parser_version") not in {PARSER_VERSION, SHADOW_PARSER_VERSION} or
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


def _detected_rows(extraction: dict) -> list[tuple[str, dict]]:
    result = []
    for collection in ("holdings", "transactions", "excluded", "quarantined"):
        for row in extraction[collection]:
            if row["section"] in _PARTS:
                result.append((collection, row))
    return result


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
                                   trigger: str = "replay") -> dict:
    """Return validated rows and manifest; never claim full-report completeness."""
    _validate_source(extraction)
    source_sha = extraction["source_sha256"]
    document_id = extraction["source_document_id"]
    parser_version = extraction["parser_version"]
    parser_id = "oge-278e-tables"
    layout = "oge-278e-tables:" + parser_version
    key = idempotency_key(source_id="oge", source_sha256=source_sha,
                          parser_id=parser_id, parser_version=parser_version,
                          rules_version=RULES_VERSION, layout_fingerprint=layout)
    detected = _detected_rows(extraction)
    parent_numbers = {row.get("row_number") for collection, row in detected
                      if row["section"] == "part6" and collection == "quarantined" and
                      isinstance(row.get("row_number"), str)}
    number_counts = Counter((row["section"], row.get("page_number"), row.get("row_number"))
                            for _, row in detected if row.get("row_number"))
    duplicate_numbers = {identity for identity, count in number_counts.items() if count > 1}
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
        reasons = _row_reasons(collection, row, extraction, parent_numbers, duplicate_numbers)
        disposition = ("excluded" if collection == "excluded" else
                       "unrecognized" if "table_header_unrecognized" in reasons or
                       "table_count_unrecognized" in reasons else
                       "qualified" if not reasons else "quarantined")
        fields = ("asset_name", "owner", "row_number", "value_low", "value_high",
                  "raw_value", "report_period_end") if part == "part6" else (
                  "asset_name", "owner", "row_number", "transaction_type",
                  "transaction_date", "amount_low", "amount_high", "raw_type",
                  "raw_date", "raw_amount")
        raw_fields = ({"value_low": "raw_value", "value_high": "raw_value"}
                      if part == "part6" else
                      {"transaction_type": "raw_type", "transaction_date": "raw_date",
                       "amount_low": "raw_amount", "amount_high": "raw_amount"})
        observations = []
        for field in fields:
            value = row.get(field)
            raw_value = row.get(raw_fields.get(field, field))
            observations.append({
                "schema_version": OBSERVATION_SCHEMA,
                "observation_id": hashlib.sha256(f"{candidate_id}|{field}".encode()).hexdigest()[:24],
                "field": field, "raw_value": raw_value, "normalized_value": value,
                "method": "embedded_text", "confidence": None, "selected": True,
                "required_for_projection": field in ({"asset_name", "owner", "value_low",
                    "value_high", "report_period_end"} if part == "part6" else
                    {"asset_name", "transaction_type", "transaction_date", "amount_low",
                     "amount_high"}),
                "locations": [location], "conditions": {"section": part,
                    "extraction_collection": collection},
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
                       "rules_version": RULES_VERSION, "layout_fingerprint": layout},
            "disposition": disposition, "reasons": reasons,
            "unresolved_conflicts": [] if disposition == "qualified" else reasons,
            "observations": observations, "evidence_locations": [location],
            "required_projection_fields": [observation["field"] for observation in observations
                                           if observation["required_for_projection"]],
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
            "rules_version": RULES_VERSION, "layout_fingerprint": layout,
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
                        for part in _PARTS},
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
        "part7_opt_in": "not_enabled",
        "publication_status": "shadow_only_not_promoted",
    }
