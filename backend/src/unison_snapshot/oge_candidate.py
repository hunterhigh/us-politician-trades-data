"""Conservative source candidate builder for archived OGE 278-T reports."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import re

from .builder import timestamp
from .oge_projection import (
    _identity_key, _person_id, _instrument, _transaction_projection,
    _legacy_default_owner_evidence,
)
from .oge import OgeCatalogError, SCHEMA as CATALOG_SCHEMA
from .oge_reports import (EXTRACTION_SCHEMA, GENERIC_PARSER_VERSION, PARSER_VERSION, collapse_direct_catalog_records)
from .reviewed_oge_extraction import (source_binding as _reviewed_source,person_for,parser_versions)


_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-^/]{0,31}")


def _short_name(value: str) -> str:
    if "," in value:
        surname = value.split(",", 1)[0].strip()
        if surname:
            return surname
    parts = value.split()
    if not parts:
        raise OgeCatalogError("OGE identity has no display name")
    return parts[-1]


def _inherit_generic_legacy(extraction, legacy, formal, record):
    """Reuse physical source-row IDs without inheriting old field values."""
    from .oge_transaction_adapter import _row_key
    result = deepcopy(extraction)
    ids = {row['id'] for row in formal}
    old_rows = {}
    for row in (legacy or {}).get('transactions', []):
        if row['extraction_id'] in ids:
            old_rows.setdefault(_row_key(row), []).append(row['extraction_id'])
    for kind in ('transactions', 'quarantined'):
        for row in result[kind]:
            matches = old_rows.get(_row_key(row), [])
            if len(matches) == 1:
                row['extraction_id'] = matches[0]
    return result, None


def build_oge_candidate(catalog: dict, extractions: list[dict], base: dict, *,
                        data_cutoff_at: str,
                        catalog_history: list[dict] | None = None,
                        saved_observations_by_document: dict | None = None,
                        legacy_extractions_by_document: dict | None = None,
                        saved_exhibit_observations_by_document: dict | None = None,
                        evidence_root=None,
                        source_extractions_by_document=None,
                        ) -> tuple[dict, dict]:
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
    exhibits = {} if saved_exhibit_observations_by_document is None else saved_exhibit_observations_by_document
    if not isinstance(exhibits,dict) or set(exhibits)-set(direct_by_id) or set(exhibits)&set(saved):
        raise OgeCatalogError("native exhibit observations are outside the catalog or overlap raster inputs")
    retained_from_history = len(set(direct_by_id) - set(current_direct_by_id))
    legacy_inputs = legacy_extractions_by_document or {}
    if not isinstance(legacy_inputs, dict) or set(legacy_inputs) - set(direct_by_id):
        raise OgeCatalogError("legacy extraction documents are outside the catalog")
    raw_inputs = source_extractions_by_document or {}
    if not isinstance(raw_inputs, dict) or set(raw_inputs) - set(direct_by_id):
        raise OgeCatalogError('selected raw extraction documents are outside the catalog')

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
    deferred_formal_ids: set[str] = set()
    from .oge_native_replacements import prepare_native_exhibit_applications
    native_views = prepare_native_exhibit_applications(extractions, direct_by_id, exhibits,
        base.get("transactions", []), data_cutoff_at)
    for extraction in sorted(extractions, key=lambda row: str(row.get("document_id"))):
        document_id = extraction.get("document_id")
        if not isinstance(document_id, str) or document_id in seen_document_ids:
            raise OgeCatalogError("OGE candidate extraction document ID is missing or duplicated")
        seen_document_ids.add(document_id)
        record = direct_by_id.get(document_id)
        source_extraction = raw_inputs.get(document_id, extraction)
        from .oge_native_owner_scope import source_interpretation_binding
        interpretation_binding = source_interpretation_binding(source_extraction)
        owner_scope = None
        from .oge_native_partition import VERSIONS as VISIBLE_VERSIONS, GENERIC_VERSIONS as VISIBLE_GENERICS, replay_partitioned_oge
        visible = extraction.get('parser_version') in VISIBLE_VERSIONS
        if evidence_root is not None and extraction.get('parser_version') in {PARSER_VERSION, GENERIC_PARSER_VERSION, *VISIBLE_VERSIONS}:
            from pathlib import Path
            from .oge_reports import _read_archived_source, selected_parser_version
            archive = Path(evidence_root)
            metadata, path, sha = _read_archived_source(archive,
                archive / f"oge/reports/{document_id}/{extraction.get('source_sha256')}.json")
            expected_version = selected_parser_version(metadata, path, sha,
                generic=extraction['parser_version'] in {GENERIC_PARSER_VERSION, *VISIBLE_GENERICS})
            from .oge_reports import parser_target
            if not parser_target(metadata, expected_version,
                    observed=document_id in saved or document_id in exhibits).accepts(source_extraction):
                raise OgeCatalogError('OGE cached native version differs from the current original source plan')
        if visible:
            extraction = replay_partitioned_oge(extraction, evidence_root)
        generic = extraction.get("parser_version") in {GENERIC_PARSER_VERSION, *VISIBLE_GENERICS}
        if generic and not visible:
            from .oge_reports import validate_generic_table_interpretation
            validate_generic_table_interpretation(extraction)
        observed = document_id in saved or document_id in exhibits
        amount_recompute = document_id in saved and saved[document_id].get('field_interpretation_mode') is not None
        retained = [row for row in base.get("transactions", []) if (visible or document_id in exhibits or
                    amount_recompute or
                    document_id in native_views and native_views[document_id].get("native_exhibit_preserved_target_ids") or
                    observed and "source_rows" not in extraction) and
                    row.get("source_id") == "oge" and row.get("filing_id") == document_id
                    and row.get("verification_status") == "official_matched"]
        inheritance = None
        owner_scope = None
        owner_mechanisms = set()
        if generic and not visible and not observed and record is not None:
            prior = [row for row in base.get('transactions', [])
                     if row.get('source_id') == 'oge' and row.get('filing_id') == document_id]
            extraction, inheritance = _inherit_generic_legacy(
                extraction, legacy_inputs.get(document_id), prior, record)
        if document_id in native_views:
            extraction = native_views[document_id]
        legacy_reference_rows = {}
        source_role_conflicted_formal_ids = set()
        if observed:
            from .oge_transaction_adapter import materialize_oge_278t_observations, _validated_legacy_reference
            if document_id in saved:
                source_formal = [row for row in base.get("transactions", [])
                    if row.get("source_id") == "oge"
                    and row.get("filing_id") == document_id
                    and row.get("source_url") == extraction.get("source_url")
                    and row.get("verification_status") == "official_matched"]
                prior_source_rows = extraction.get("transactions", [])
                parser_reference_ids = {row["extraction_id"] for row in
                    prior_source_rows + extraction.get("quarantined", [])}
                extraction = materialize_oge_278t_observations(extraction, record,
                    # Full physical inventories may have additional formal
                    # rows from a previous saved-observation run. Those rows
                    # are replayed below, not missing legacy parser references.
                    formal_reference_ids=[row["id"] for row in source_formal
                        if amount_recompute or "source_rows" not in extraction or row["id"] in parser_reference_ids],
                    **saved[document_id])
                # Only this call's source-replayed contradiction can select
                # these predecessors. Page roles cannot silently reproject an
                # existing formal fact under today's classification rules.
                receipt = extraction["observation_receipt"]
                conflict_pages = {proof["page_number"] for proof in receipt["page_role_conflicts"]}
                source_role_conflicted_formal_ids = {
                    row["extraction_id"] for row in prior_source_rows
                    if row.get("page_number") in conflict_pages} & {row["id"] for row in source_formal}
                retained_ids = {row["id"] for row in retained}
                retained.extend(row for row in source_formal
                    if row["id"] in source_role_conflicted_formal_ids and row["id"] not in retained_ids)
            else:
                from .oge_reports import materialize_saved_oge_278t_exhibits
                if "prior_formal_transactions" in exhibits[document_id]:
                    raise OgeCatalogError("native replacement formal facts must come from the current base")
                if document_id not in native_views:
                    exhibit_inputs = deepcopy(exhibits[document_id])
                    if exhibit_inputs.get("application_stage") == "post_whitehouse/v1":
                        # WH schema/identity belongs to its later consumer. Keep
                        # actual base facts while this stage records unresolved
                        # blocks; it cannot qualify or retire their WH targets.
                        exhibit_inputs.pop("application_stage")
                        exhibit_inputs.pop("source_extraction_canonical_sha256", None)
                        exhibit_inputs.pop("replacement_application_mode", None)
                        exhibit_inputs["replacement_targets"] = []
                    extraction = materialize_saved_oge_278t_exhibits(extraction,record,
                        formal_reference_ids=[row["id"] for row in retained],
                        prior_formal_transactions=base.get("transactions", []), **exhibit_inputs)
                if extraction.get("replacement_application_mode") is not None:
                    from .oge_transaction_adapter import adapt_oge_278t_extraction
                    adapt_oge_278t_extraction(extraction, record)
            reference = _validated_legacy_reference(extraction, record)
            if reference is not None:
                original = reference["source_extraction"]
                legacy_reference_rows = {row["extraction_id"]: row for row in original["transactions"]}
                referenced_ids = {row["extraction_id"] for row in original["transactions"] + original["quarantined"]}
                unbound = [row["id"] for row in retained if row["id"] not in referenced_ids]
                inheritance = {"basis": "existing_formal_base_preserved_without_requalifying_normalized_reference",
                    "retained_formal_ids": [row["id"] for row in retained], "unbound_formal_ids": unbound,
                    "legacy_normalized_transaction_count": len(legacy_reference_rows)}
                if unbound and not amount_recompute:
                    for row in extraction["transactions"]:
                        if row.get("extraction_id") not in referenced_ids:
                            row["reasons"] = sorted(set(row.get("reasons", []) + ["legacy_formal_reference_unbound"]))
        reasons = []
        reviewed_source = _reviewed_source(extraction, record)
        if reviewed_source:
            # The complete source-bound base has passed its existing assertions.
            # Interpret only its unresolved fixed-option cells; legacy caches
            # without that proof keep their current eligibility barriers.
            from .oge_278t_fixed_options import interpret_report
            extraction = interpret_report(extraction)
        supported_parser = (
            observed or visible or extraction.get("parser_version") in {PARSER_VERSION, GENERIC_PARSER_VERSION}
            or (extraction.get("parser_version") in parser_versions()
                and reviewed_source)
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
        if record is not None and record.get("amended_label") and extraction.get("replacement_application_mode") != "verified_amount_only/v1":
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
        amount_current_qualified_ids = set()
        current_qualified_ids = set()
        retained_by_id = {row["id"]: row for row in retained}
        visible_retained_positions = set()
        visible_unbound_formal = False
        if visible and retained:
            from .native_partition_rows import interpret_partition_rows
            raw_proof = {**extraction['native_text_partition'],
                'inventory': extraction['native_text_partition']['raw_inventory'],
                'scope_barriers_before': {}, 'row_reasons': {}}
            raw_good, raw_bad, _ = interpret_partition_rows(raw_proof,
                source_sha256=extraction['source_sha256'], generic=generic)
            for fact in retained:
                positions = [row['source_rows_index'] for row in raw_good + raw_bad
                             if row.get('extraction_id') == fact['id']]
                positions += [row['source_rows_index'] for row in source_rows
                              if row.get('extraction_id') == fact['id']]
                if len(set(positions)) == 1:
                    visible_retained_positions.update(positions)
                else:
                    visible_unbound_formal = True
        row_quarantines = list(source_quarantine)
        for row in source_rows:
            row_reasons = list(reasons)
            if not isinstance(row, dict):
                row_quarantines.append({"reasons": ["transaction_row_invalid"]})
                continue
            extraction_id = row.get("extraction_id")
            if visible and extraction_id in retained_by_id:
                if extraction_id in promoted_ids:
                    raise OgeCatalogError('Native visible retained formal identity is duplicated')
                transactions.append(deepcopy(retained_by_id.pop(extraction_id)))
                promoted_ids.add(extraction_id)
                report_promoted += 1
                continue
            if visible and (visible_unbound_formal or row.get('source_rows_index') in visible_retained_positions):
                row_quarantines.append({'extraction_id': extraction_id,
                    'reasons': ['native_visible_formal_correspondence_unresolved']})
                continue
            if extraction_id in source_role_conflicted_formal_ids and extraction_id in retained_by_id:
                if extraction_id in promoted_ids:
                    raise OgeCatalogError("source-conflicted formal identity is duplicated")
                transactions.append(deepcopy(retained_by_id.pop(extraction_id)))
                promoted_ids.add(extraction_id)
                report_promoted += 1
                continue
            if row.get("native_replacement_disposition") == "superseded":
                retained_by_id.pop(extraction_id, None)
                continue
            if legacy_reference_rows.get(extraction_id) == row:
                if extraction_id in retained_by_id:
                    transactions.append(deepcopy(retained_by_id.pop(extraction_id)))
                    if extraction_id in promoted_ids:
                        raise OgeCatalogError("retained legacy reference repeats a formal identity")
                    promoted_ids.add(extraction_id)
                    report_promoted += 1
                    continue
                row_reasons.append("legacy_normalized_reference_without_formal_binding")
            row_reasons.extend(row.get("reasons", []))
            if (extraction_id in retained_by_id and (row.get("legacy_inheritance") or
                    inheritance and extraction_id in inheritance.get("current_source_inherited_ids", []))):
                if extraction_id in promoted_ids:
                    raise OgeCatalogError("inherited formal identity is duplicated")
                retained_fact = deepcopy(retained_by_id.pop(extraction_id))
                for correction in row.get("legacy_inheritance", {}).get("field_corrections", []):
                    if (correction["rule"] != "approved_v2_default_self_to_undisclosed/v1" or
                            correction["field"] != "owner" or retained_fact["owner"] != correction["old_value"]):
                        raise OgeCatalogError("unsupported inherited formal field correction")
                    source_person = {'id': _person_id(key), 'display_name': record['filer_name'],
                        'role': record['position_title'], 'office_type': record['agency'],
                        'disclosure_authority': 'oge'} if key is not None and record is not None else None
                    actual_people = [person for person in base.get('people', [])
                                     if person.get('id') == retained_fact['person_id']]
                    identity_matches = (source_person is not None and len(actual_people) == 1
                        and all(actual_people[0].get(field) == value for field, value in source_person.items()))
                    if row_reasons or not identity_matches:
                        if inheritance is None:
                            inheritance = {'retained_formal_ids': [fact['id'] for fact in retained],
                                           'field_corrections': []}
                        if correction in inheritance.get('field_corrections', []):
                            inheritance['field_corrections'].remove(correction)
                        inheritance.setdefault('deferred_field_corrections', []).append(correction)
                        inheritance.setdefault('owner_scope_deferred_ids', []).append(extraction_id)
                        if not identity_matches:
                            inheritance.setdefault('owner_scope_deferred_reasons', {})[extraction_id] = (
                                'formal_person_identity_differs_from_source')
                    else:
                        retained_fact["owner"] = correction["new_value"]
                transactions.append(retained_fact)
                if not row_reasons and owner_scope is not None and extraction_id in owner_mechanisms:
                    if extraction_id in owner_scope.owner_decisions([retained_fact])['current_ids']:
                        current_qualified_ids.add(extraction_id)
                elif not row_reasons and inheritance and extraction_id in inheritance.get('current_source_inherited_ids', []):
                    current_qualified_ids.add(extraction_id)
                elif (not row_reasons and owner_scope is not None and not observed
                      and document_id not in native_views and owner_scope.ordinary_legacy_current(retained_fact)):
                    current_qualified_ids.add(extraction_id)
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
            if (owner_mechanisms and not observed and not visible and document_id not in native_views
                    and owner_scope.status != 'observed' and row.get('owner') == 'Undisclosed'):
                row_reasons.append('owner_scope_unresolved')
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
                                        "reasons": sorted(set(row_reasons)),
                                        **({"native_exhibit_reference":row["native_exhibit_reference"]} if row.get("native_exhibit_reference") else {})})
                continue
            person_id = person_for(extraction)['id'] if reviewed_source else _person_id(key)
            people.setdefault(person_id, {
                "id": person_id,
                "display_name": person_for(extraction)['display_name'] if reviewed_source else record["filer_name"],
                "short_name": person_for(extraction)['short_name'] if reviewed_source else _short_name(record["filer_name"]),
                "role": (person_for(extraction)['role'] if reviewed_source else
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
            fact = _transaction_projection(extraction, row, person_id)
            if amount_recompute and fact_id in retained_by_id:
                prior_fact = retained_by_id.pop(fact_id)
                amount_current_qualified_ids.add(fact_id)
            elif amount_recompute:
                amount_current_qualified_ids.add(fact_id)
            if row.get("native_exhibit_reference", {}).get("replacement_application"):
                retained_by_id.pop(fact_id, None)
            transactions.append(fact)
            if not amount_recompute or fact_id in amount_current_qualified_ids:
                current_qualified_ids.add(fact_id)
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
        deferred = (sorted(row["id"] for row in retained) if observed and
            exhibits.get(document_id,{}).get("application_stage")=="post_whitehouse/v1" else [])
        deferred_formal_ids.update(deferred)
        if visible:
            reasons = sorted(set(reasons + extraction.get('document_reasons', [])))
        from .native_partition_rows import partition_binding
        owner_audit = {}
        audit_reports.append({
            "document_id": document_id,
            'source_interpretation_binding': interpretation_binding,
            **owner_audit,
            **({'source_sha256': extraction['source_sha256'],
                'parser_version': extraction['parser_version'],
                'native_partition_published_ids': sorted(row['id'] for row in transactions
                    if row.get('filing_id') == document_id and row.get('source_url') == extraction['source_url']),
                'native_partition_retained_formal_ids': sorted(row['id'] for row in retained),
                'native_source_partition_binding': partition_binding(extraction['native_text_partition'], extraction['parser_version'])}
               if visible else {}),
            **({"legacy_inheritance": inheritance} if inheritance is not None else {}),
            **({"cover_date_evidence": extraction.get("cover_date_evidence"),
                "source_sha256": extraction["source_sha256"]} if generic else {}),
            **({"source_sha256": extraction["source_sha256"],
                "observation_receipt": extraction["observation_receipt"]}
               if observed else {}),
            **({'field_interpretation_mode':saved[document_id]['field_interpretation_mode'],
                'amount_recompute_current_qualified_ids':sorted(amount_current_qualified_ids),
                'amount_recompute_retained_formal_ids':sorted(row['id'] for row in retained),
                'amount_recompute_published_ids':sorted(row['id'] for row in transactions
                    if row.get('filing_id') == document_id and row.get('source_url') == extraction['source_url'])}
               if amount_recompute else {}),
            **({"native_exhibit_application_replay": extraction["native_exhibit_application_replay"]}
               if "native_exhibit_application_replay" in extraction else {}),
            **({field: extraction[field] for field in ("native_exhibit_application_context",
                 "native_exhibit_replacement_applications", "native_exhibit_superseded_transaction_ids") if field in extraction}
               if "native_exhibit_application_context" in extraction else {}),
            **({"source_sha256": extraction["source_sha256"]} if "native_exhibit_application_context" in extraction else {}),
            **({"legacy_normalized_reference_count": extraction["legacy_disposition_reference"]["normalized_transaction_count"],
                "legacy_quarantined_reference_count": extraction["legacy_disposition_reference"]["quarantined_reference_count"],
                "quarantine_count_unit": "observed_slots_not_whole_source_unique_rows",
                "unique_source_quarantined_row_count": None}
               if extraction.get("legacy_disposition_reference") else {}),
            **({"source_sha256": extraction["source_sha256"]}
               if "manual_filing_date_evidence" in extraction else {}),
            **({"manual_filing_date_evidence": extraction["manual_filing_date_evidence"]}
               if "manual_filing_date_evidence" in extraction else {}),
            **({"filing_date_evidence": extraction['filing_date_evidence']}
               if reviewed_source else {}),
            **({"fixed_option_interpretation": extraction['fixed_option_interpretation']}
               if 'fixed_option_interpretation' in extraction else {}),
            **({"deferred_cross_source_formal_ids":deferred} if deferred else {}),
            "promoted_count": report_promoted-len(deferred),
            "quarantined_count": len(row_quarantines),
            "document_reasons": sorted(set(reasons)),
            "quarantined": row_quarantines,
        })

    from .parser_replay import require_complete
    try:
        require_complete(direct_by_id, seen_document_ids, label='OGE direct report history')
    except ValueError as exc:
        raise OgeCatalogError(str(exc)) from exc
    replacement_redirects = _apply_native_exhibit_replacements(transactions, audit_reports, base)

    candidate = deepcopy(base)
    candidate["meta"]["data_cutoff_at"] = data_cutoff_at
    candidate["meta"]["subtitle"] = "OGE 真实 278-T 候选数据；申请型文件和异常记录保持隔离"
    candidate["people"] = [people[key] for key in sorted(people)]
    candidate["transactions"] = sorted(transactions, key=lambda row: row["id"])
    for relation in candidate["meta"].get("transaction_disclosure_relations", []):
        replaced = []
        for identifier in relation.get("possible_ptr_transaction_ids", []):
            seen = set()
            while identifier in replacement_redirects:
                if identifier in seen:
                    raise OgeCatalogError("native replacement reference cycle")
                seen.add(identifier)
                identifier = replacement_redirects[identifier]
            replaced.append(identifier)
        relation["possible_ptr_transaction_ids"] = sorted(set(replaced))
    candidate["reported_holdings"] = []
    candidate["meta"].pop("holding_report_coverage", None)
    output_ids = {row["id"] for row in candidate["transactions"]}
    relations = [row for row in candidate["meta"].get("transaction_disclosure_relations", [])
                 if row.get("transaction_id") in output_ids]
    candidate["meta"].pop("transaction_disclosure_relations", None)
    if relations:
        candidate["meta"]["transaction_disclosure_relations"] = relations
    candidate["security_market_data"] = []
    owner_current_count = len(transactions) - len(deferred_formal_ids)
    health = {
        "source_id": "oge",
        "source": "U.S. Office of Government Ethics",
        "source_type": "official_disclosure",
        "source_url": "https://www.oge.gov/web/OGE.nsf/Officials%20Individual%20Disclosures%20Search%20Collection?OpenForm",
        "status": "partial",
        "last_checked_at": data_cutoff_at,
        "last_successful_sync_at": data_cutoff_at,
        "data_cutoff_at": data_cutoff_at,
        "detail": (f"{len(direct_by_id)} direct 278-T documents; {len(extractions)} PDFs extracted; " +
                   f"{owner_current_count} transactions currently qualified; " +
                   f"{request_required} request-required "
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
        "promoted_transaction_count": len(transactions)-len(deferred_formal_ids),
        'current_qualified_transaction_count': owner_current_count,
        "quarantined_row_count": sum(row["quarantined_count"] for row in audit_reports),
        "reports": audit_reports,
    }
    return candidate, audit

def _apply_native_exhibit_replacements(transactions: list[dict], reports: list[dict], base: dict) -> dict:
    """Retire only proven formal predecessors; mappings never become extra facts."""
    facts={row['id']:row for row in transactions};redirects={}
    for report in reports:
        for application in report.get('native_exhibit_replacement_applications',[]):
            old=application['effective_prior_transaction_id'];new=application['transaction_id']
            if old in redirects and redirects[old]!=new:
                raise OgeCatalogError('native replacement predecessor has conflicting successors')
            redirects[old]=new
            prior=facts.get(old)
            if old==application['prior_transaction_id'] and prior is not None and any(
                    prior.get(key)!=application['prior_fact'].get(key) for key in set(prior)|set(application['prior_fact'])):
                raise OgeCatalogError('native replacement current predecessor differs from exact source facts')
    for old in redirects:
        node=old;seen=set()
        while node in redirects:
            if node in seen:raise OgeCatalogError('native replacement cycle')
            seen.add(node);node=redirects[node]
        if node not in facts:raise OgeCatalogError('native replacement successor did not pass current candidate qualification')
        facts.pop(old,None)
    transactions[:]=list(facts.values())
    return redirects
