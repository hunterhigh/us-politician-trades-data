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


def _inherit_generic_legacy(extraction: dict, legacy: dict | None,
                            formal: list[dict], record: dict, *, evidence_root=None,
                            source_extraction=None, _owner_scope=None, _classification_scope=None) -> tuple[dict, dict]:
    """Only an exact source row can carry an old formal identity into v7."""
    from .oge_transaction_adapter import _row_key, adapt_oge_278t_extraction
    result = deepcopy(extraction)
    receipt = {"retained_formal_ids": [row["id"] for row in formal], "field_corrections": [],
               "inherited_ids": [], "unbound_formal_ids": [], "blocked_new_count": 0}
    if len(set(receipt["retained_formal_ids"])) != len(formal):
        raise OgeCatalogError("existing formal report repeats an identity")
    if not formal:
        return result, receipt
    from .oge_native_owner_scope import prepare_native_owner_scope
    owner_scope = _owner_scope or prepare_native_owner_scope(extraction, legacy, record,
        evidence_root=None if _reviewed_source(extraction, record) else evidence_root,
        source_extraction=source_extraction)
    actual_formal = {fact['id']: fact for fact in formal}
    # A complete source-projected successor identifies the same physical row.
    # Normalize before the unbound scan, while keeping the actual old object
    # for publication and deciding qualification separately below.
    formal = [owner_scope.alias(fact) or fact for fact in formal]
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
    from .oge_reports import GENERIC_ROW_INTERPRETATION_MODE
    current_source = extraction.get("row_interpretation_mode") == GENERIC_ROW_INTERPRETATION_MODE
    current_rows = {row["extraction_id"]: row for row in extraction["transactions"]} if current_source else {}
    current_person = _person_id(_identity_key(record))
    def scope_key(row):
        key = (_row_key(row), row.get("account_title"))
        if not current_source:
            return key
        index = row.get("source_rows_index")
        if index is None:
            matching = [item for item in result["transactions"] + result["quarantined"]
                        if (_row_key(item), item.get("account_title")) == key]
            index = matching[0]["source_rows_index"] if len(matching) == 1 else None
        return (*key, index)

    current_ids = set()
    proven = {}
    compare = ("owner", "ticker", "transaction_type", "transaction_date",
               "amount_low", "amount_high", "amount_low_exclusive")
    for fact in formal:
        physical = (_classification_scope.legacy_binding(actual_formal[fact['id']])
                    if _classification_scope is not None else None)
        if physical is not None:
            proven.setdefault(scope_key(physical), []).append(physical)
            continue
        if _classification_scope is not None and fact['id'] in _classification_scope.target_ids:
            receipt.setdefault('classification_unbound_formal_ids', []).append(fact['id'])
            continue
        current = current_rows.get(fact["id"])
        projection = _transaction_projection(extraction, current, current_person) if current is not None else None
        # Public shards may omit optional null cells. Compare their values, not
        # the presence of an explicit null key, while retaining every field.
        if projection is not None and all(fact.get(field) == projection.get(field)
                                          for field in fact.keys() | projection.keys()):
            key = scope_key(current)
            proven.setdefault(key, []).append(current)
            current_ids.add(fact["id"])
            continue
        old = bound.get(fact["id"])
        if (old is not None and isinstance(old.get("cells"), list)
                and all(fact.get(field) == old.get(field) for field in compare)
                and fact.get("asset_name") == (f"{old['asset_name']} — {old['account_title']}"
                                               if old.get("account_title") else old.get("asset_name"))
                and fact.get("person_id") == (person_for(legacy)['id'] if _reviewed_source(legacy, record)
                                               else _person_id(_identity_key(record)))
                and fact.get("filed_at") == f"{legacy.get('filed_at')}T00:00:00Z"):
            key = scope_key(old)
            proven.setdefault(key, []).append(old)
        else:
            receipt["unbound_formal_ids"].append(fact["id"])
    all_new = result["transactions"] + result["quarantined"]
    counts = {}
    for row in all_new:
        key = scope_key(row)
        counts[key] = counts.get(key, 0) + 1
    for key, rows in proven.items():
        if len(rows) == 1 and counts.get(key) == 1:
            receipt["inherited_ids"].append(rows[0]["extraction_id"])
    inherited = set(receipt["inherited_ids"])
    if current_ids:
        receipt["current_source_inherited_ids"] = sorted(current_ids & inherited)
    unmatched_pages = {old.get("page_number") for rows in proven.values() for old in rows
                       if old["extraction_id"] not in inherited}
    qualified, quarantined = [], []
    for disposition, rows in (("qualified", result["transactions"]), ("quarantined", result["quarantined"])):
        for row in rows:
            key = scope_key(row)
            matches = proven.get(key, [])
            if len(matches) == 1 and matches[0]["extraction_id"] in inherited:
                retained = deepcopy(matches[0])
                if current_source:
                    retained["source_rows_index"] = row["source_rows_index"]
                if retained["extraction_id"] in current_ids:
                    qualified.append(retained)
                    continue
                retained["legacy_inheritance"] = {"basis": "same_source_exact_cells_and_formal_projection",
                    "source_sha256": extraction["source_sha256"], "new_parser_version": GENERIC_PARSER_VERSION,
                    "new_observation_reasons": row.get("reasons", [])}
                proof = (_legacy_default_owner_evidence(legacy, retained) if
                         retained["extraction_id"] not in current_ids and legacy is not None and
                         row.get("owner") == "Undisclosed" and row.get("owner_disclosure_basis") ==
                         "not_disclosed_in_bound_six_column_table" else None)
                if proof is not None:
                    correction = {"extraction_id": retained["extraction_id"], "field": "owner",
                        "old_value": "Self", "new_value": "Undisclosed",
                        "rule": "approved_v2_default_self_to_undisclosed/v1", "source_reference": proof}
                    decision = owner_scope.decision(actual_formal[retained['extraction_id']])
                    if decision['status'] == 'approved':
                        retained["owner"] = "Undisclosed"
                        retained["owner_disclosure_basis"] = row["owner_disclosure_basis"]
                        retained["legacy_inheritance"]["field_corrections"] = [correction]
                        receipt["field_corrections"].append(correction)
                    elif decision['status'] == 'alias':
                        retained['owner'] = actual_formal[retained['extraction_id']]['owner']
                        retained['owner_disclosure_basis'] = row['owner_disclosure_basis']
                    else:
                        receipt.setdefault('deferred_field_corrections', []).append(correction)
                    if decision['status'] != 'approved' and not (
                            decision['status'] == 'alias' and owner_scope.status == 'observed'):
                        receipt.setdefault('owner_scope_deferred_ids', []).append(retained['extraction_id'])
                qualified.append(retained)
            elif (receipt["unbound_formal_ids"] or legacy is None and not (
                    _classification_scope is not None and set(actual_formal) <= _classification_scope.target_ids)
                    or row.get("page_number") in unmatched_pages):
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
                        legacy_extractions_by_document: dict | None = None,
                        saved_exhibit_observations_by_document: dict | None = None,
                        evidence_root=None,
                        source_extractions_by_document=None,
                        incomplete_publication=None) -> tuple[dict, dict]:
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
        from .oge_native_owner_scope import source_interpretation_binding, prepare_native_owner_scope
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
            if expected_version != extraction['parser_version']:
                raise OgeCatalogError('OGE cached native version differs from the current original source plan')
        if visible:
            extraction = replay_partitioned_oge(extraction, evidence_root)
        generic = extraction.get("parser_version") in {GENERIC_PARSER_VERSION, *VISIBLE_GENERICS}
        if generic and not visible:
            from .oge_reports import validate_generic_table_interpretation
            validate_generic_table_interpretation(extraction)
        observed = document_id in saved or document_id in exhibits
        amount_recompute = document_id in saved and saved[document_id].get('field_interpretation_mode') is not None
        classification = None
        if (not visible and not observed and document_id not in native_views
                and extraction.get('parser_version') in {PARSER_VERSION, GENERIC_PARSER_VERSION}
                and record is not None and not _reviewed_source(extraction, record)):
            from .oge_native_classification import prepare_native_classification
            context = prepare_native_classification(extraction, base.get('transactions', []), record,
                evidence_root=evidence_root, source_extraction=source_extraction,
                legacy_extraction=legacy_inputs.get(document_id))
            if context.target_ids:
                classification = context
        default_conservation = None
        conservation_formal = base.get('transactions', [])
        if evidence_root is None and classification is not None:
            # The existing classification path already conserves these old
            # native identities without claiming current source qualification.
            conservation_formal = [fact for fact in conservation_formal
                if not (fact.get('owner') in {'Spouse', 'Dependent Child'}
                        and fact.get('id') in classification.target_ids)]
        if (not generic and not visible and not observed and document_id not in native_views
                and extraction.get('parser_version') == PARSER_VERSION and record is not None
                and not _reviewed_source(extraction, record)
                and any(fact.get('source_id') == 'oge' and fact.get('filing_id') == document_id
                    and fact.get('verification_status') == 'official_matched'
                    and fact.get('owner') in {'Undisclosed', 'Spouse', 'Dependent Child'}
                    for fact in conservation_formal)):
            from .oge_default_owner_conservation import prepare_default_owner_conservation
            context = prepare_default_owner_conservation(extraction, conservation_formal, record,
                evidence_root=evidence_root, source_extraction=source_extraction)
            if context.protected_ids:
                default_conservation = context
        retained = [row for row in base.get("transactions", []) if (visible or generic or document_id in exhibits or
                    amount_recompute or
                    document_id in native_views and native_views[document_id].get("native_exhibit_preserved_target_ids") or
                    observed and "source_rows" not in extraction) and
                    row.get("source_id") == "oge" and row.get("filing_id") == document_id
                    and row.get("verification_status") == "official_matched"]
        if default_conservation is not None:
            from .oge_transaction_adapter import _row_key
            default_blocked_keys = {_row_key(row) for row in
                source_extraction.get('transactions', []) + source_extraction.get('quarantined', [])
                if row.get('extraction_id') in default_conservation.blocked_selected_ids}
            retained.extend(deepcopy(fact) for fact in base['transactions']
                            if fact['id'] in default_conservation.protected_ids)
        if classification is not None:
            from .oge_transaction_adapter import _row_key
            classification_blocked_keys = {_row_key(row) for row in
                source_extraction.get('transactions', []) + source_extraction.get('quarantined', [])
                if row.get('extraction_id') in classification.blocked_selected_ids}
            retained_ids = {fact['id'] for fact in retained}
            retained.extend(deepcopy(fact) for fact in base['transactions']
                            if fact['id'] in classification.target_ids - retained_ids)
        if any(row.get("source_url") != extraction.get("source_url") for row in retained):
            raise OgeCatalogError("existing formal facts differ from generic report URL")
        inheritance = None
        if generic and not visible and record is not None:
            owner_scope = prepare_native_owner_scope(extraction, legacy_inputs.get(document_id), record,
                evidence_root=None if _reviewed_source(extraction, record) else evidence_root,
                source_extraction=source_extraction)
            extraction, inheritance = _inherit_generic_legacy(extraction, legacy_inputs.get(document_id), retained, record,
                evidence_root=evidence_root, source_extraction=source_extraction, _owner_scope=owner_scope,
                _classification_scope=classification)
        if classification is not None or default_conservation is not None:
            # This is an existing formal identity bound to one source row, not
            # a new identifier or an assertion about the row's owner/class.
            aliases = {}
            for context in (classification, default_conservation):
                if context is None:
                    continue
                for fact in retained:
                    selected = context.selected_binding(fact)
                    if selected is not None:
                        identifier = selected['extraction_id']
                        if identifier in aliases and aliases[identifier] != fact['id']:
                            raise OgeCatalogError('OGE source row has conflicting existing formal identities')
                        aliases[identifier] = fact['id']
            extraction = deepcopy(extraction)
            for disposition in ('transactions', 'quarantined'):
                if disposition == 'quarantined' and classification is None:
                    continue  # Keep the selected reader's original quarantine locator.
                for row in extraction[disposition]:
                    row['extraction_id'] = aliases.get(row['extraction_id'], row['extraction_id'])
        owner_mechanisms = set(owner_scope.owner_decisions(retained)['mechanism_ids']) if owner_scope else set()
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
        default_qualified_ids = set()
        classification_qualified_ids = set()
        classification_applications = []
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
            if (default_conservation is not None and (extraction_id in default_conservation.blocked_selected_ids
                    or _row_key(row) in default_blocked_keys)):
                row_quarantines.append({'extraction_id': extraction_id,
                    'reasons': ['default_owner_physical_correspondence_unresolved']})
                continue
            if (classification is not None and (extraction_id in classification.blocked_selected_ids
                    or _row_key(row) in classification_blocked_keys)):
                row_quarantines.append({'extraction_id': extraction_id,
                    'reasons': ['native_classification_physical_correspondence_unresolved']})
                continue
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
            if (extraction_id in retained_by_id and not (classification is not None
                    and extraction_id in classification.target_ids) and (row.get("legacy_inheritance") or
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
            if default_conservation is not None and fact_id in default_conservation.protected_ids:
                fact = deepcopy(retained_by_id.pop(fact_id))
                if fact_id in default_conservation.source_current_ids([fact]):
                    default_qualified_ids.add(fact_id)
            if classification is not None and fact_id in classification.target_ids:
                prior_fact = next(item for item in retained if item['id'] == fact_id)
                retained_by_id.pop(fact_id, None)
                decision = classification.decision(prior_fact)
                proposed = decision.get('fact')
                identity = {'id': _person_id(key), 'display_name': record['filer_name'],
                    'role': record['position_title'], 'office_type': record['agency'],
                    'disclosure_authority': 'oge'}
                actual_people = [person for person in base.get('people', []) if person.get('id') == prior_fact['person_id']]
                identity_matches = (len(actual_people) == 1 and all(
                    actual_people[0].get(field) == value for field, value in identity.items()))
                fact = deepcopy(prior_fact)
                if proposed is not None and (proposed == prior_fact or identity_matches):
                    fact = deepcopy(proposed)
                    classification_qualified_ids.add(fact_id)
                    if 'instrument_type' in decision['changed_fields']:
                        from .codec import digest, encode
                        classification_applications.append({'extraction_id': fact_id,
                            'changed_fields': decision['changed_fields'],
                            'old_fact_sha256': digest(encode(prior_fact)), 'new_fact_sha256': digest(encode(fact)),
                            'corrections': deepcopy(decision['corrections'])})
                elif fact_id in classification.unchanged_current_ids([fact]):
                    classification_qualified_ids.add(fact_id)
                if default_conservation is not None and fact_id in default_conservation.protected_ids:
                    default_qualified_ids.discard(fact_id)
                    if fact_id in default_conservation.source_current_ids([fact]):
                        default_qualified_ids.add(fact_id)
            if amount_recompute and fact_id in retained_by_id:
                prior_fact = retained_by_id.pop(fact_id)
                # Public shards can omit nullable cells. Their absence and an
                # explicit null have the existing generic consumer's same
                # meaning; retain the predecessor's exact key presence.
                if any(fact.get(field) != prior_fact.get(field)
                       for field in fact.keys() | prior_fact.keys()):
                    # This compatibility mode does not migrate any formal
                    # field, optional key, or identity. Preserve the entire
                    # predecessor on a separate current-qualification path.
                    row_quarantines.append({'extraction_id':fact_id,
                        'reasons':['legacy_amount_formal_projection_differs']})
                    fact = deepcopy(prior_fact)
                else:
                    amount_current_qualified_ids.add(fact_id)
                    fact = deepcopy(prior_fact)
            elif amount_recompute:
                amount_current_qualified_ids.add(fact_id)
            if row.get("native_exhibit_reference", {}).get("replacement_application") and fact_id in retained_by_id:
                prior_fact = retained_by_id.pop(fact_id)
                if any(prior_fact.get(key) != fact.get(key) for key in set(prior_fact) | set(fact)):
                    raise OgeCatalogError("existing native replacement differs from current source facts")
                fact = deepcopy(prior_fact)
            transactions.append(fact)
            if ((not amount_recompute or fact_id in amount_current_qualified_ids)
                    and (default_conservation is None or fact_id not in default_conservation.protected_ids
                         or fact_id in default_qualified_ids)):
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
        class_audit = None
        if classification is not None:
            from .codec import digest, encode
            published = [fact for fact in transactions if fact.get('filing_id') == document_id
                         and fact.get('source_url') == extraction['source_url']]
            mechanism = classification.target_ids & {fact['id'] for fact in published}
            rejected = {row.get('extraction_id') for row in row_quarantines}
            current = classification_qualified_ids - rejected if not reasons else set()
            current &= classification.source_current_ids(published) | classification.unchanged_current_ids(published)
            class_deferred = mechanism - current
            current_qualified_ids.difference_update(class_deferred)
            default_qualified_ids.difference_update(class_deferred)
            if inheritance is not None:
                actual_by_id = {fact['id']: fact for fact in published}
                for correction in list(inheritance.get('field_corrections', [])):
                    identifier = correction['extraction_id']
                    if identifier in mechanism and actual_by_id[identifier].get('owner') != correction['new_value']:
                        inheritance['field_corrections'].remove(correction)
                        deferred_corrections = inheritance.setdefault('deferred_field_corrections', [])
                        if correction not in deferred_corrections:
                            deferred_corrections.append(correction)
                        deferred_ids = inheritance.setdefault('owner_scope_deferred_ids', [])
                        if identifier not in deferred_ids:
                            deferred_ids.append(identifier)
            class_audit = {'binding': classification.binding, 'mechanism_ids': sorted(mechanism),
                'current_ids': sorted(current), 'deferred_ids': sorted(class_deferred),
                'applications': classification_applications,
                'status': classification.status, 'reason': classification.reason}
        owner_audit = {}
        if owner_mechanisms:
            published = [row for row in transactions if row.get('filing_id') == document_id
                         and row.get('source_url') == extraction['source_url']]
            decisions = owner_scope.owner_decisions(published)
            current_qualified_ids.difference_update(decisions['deferred_ids'])
            amount_current_qualified_ids.difference_update(decisions['deferred_ids'])
            owner_audit = {'owner_scope_binding': owner_scope.binding,
                'owner_scope_decisions': decisions,
                'owner_scope_current_qualified_ids': sorted(current_qualified_ids)}
            if not observed and not visible and document_id not in native_views:
                view = owner_scope.ordinary_native_view(published)
                view['current_ids'] = sorted(set(view['current_ids']) & current_qualified_ids)
                view['deferred_ids'] = sorted(set(view['published_ids']) - set(view['current_ids']))
                owner_audit['owner_scope_view'] = view
        if default_conservation is not None:
            published_ids = {fact['id'] for fact in transactions
                if fact.get('filing_id') == document_id and fact.get('source_url') == extraction['source_url']}
            protected = default_conservation.protected_ids & published_ids
            rejected = {row.get('extraction_id') for row in row_quarantines}
            current = (default_qualified_ids & protected) - rejected if not reasons else set()
            owner_audit['default_owner_conservation'] = {
                'binding': default_conservation.binding, 'retained_ids': sorted(protected),
                'current_ids': sorted(current), 'deferred_ids': sorted(protected - current),
                'status': default_conservation.status, 'reason': default_conservation.reason}
        if class_audit is not None:
            allowed = set(class_audit['current_ids'])
            if 'owner_scope_decisions' in owner_audit:
                decisions = owner_audit['owner_scope_decisions']
                allowed -= set(decisions['mechanism_ids']) - set(decisions['current_ids'])
            if 'default_owner_conservation' in owner_audit:
                receipt = owner_audit['default_owner_conservation']
                allowed -= set(receipt['retained_ids']) - set(receipt['current_ids'])
            class_audit['current_ids'] = sorted(allowed)
            classification_deferred = set(class_audit['mechanism_ids']) - allowed
            class_audit['deferred_ids'] = sorted(classification_deferred)
            current_qualified_ids.difference_update(classification_deferred)
            if 'owner_scope_current_qualified_ids' in owner_audit:
                owner_audit['owner_scope_current_qualified_ids'] = sorted(
                    set(owner_audit['owner_scope_current_qualified_ids']) - classification_deferred)
            if 'owner_scope_view' in owner_audit:
                view = owner_audit['owner_scope_view']
                view['current_ids'] = sorted(set(view['current_ids']) - classification_deferred)
                view['deferred_ids'] = sorted(set(view['published_ids']) - set(view['current_ids']))
            if 'default_owner_conservation' in owner_audit:
                receipt = owner_audit['default_owner_conservation']
                receipt['current_ids'] = sorted(set(receipt['current_ids']) - classification_deferred)
                receipt['deferred_ids'] = sorted(set(receipt['retained_ids']) - set(receipt['current_ids']))
            class_audit['qualified_ids'] = sorted(current_qualified_ids)
            owner_audit['native_classification'] = class_audit
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
            **({"deferred_cross_source_formal_ids":deferred} if deferred else {}),
            "promoted_count": report_promoted-len(deferred),
            "quarantined_count": len(row_quarantines),
            "document_reasons": sorted(set(reasons)),
            "quarantined": row_quarantines,
        })

    pending=set(direct_by_id)-seen_document_ids
    pending_reports=[];retained_published=[]
    if seen_document_ids-set(direct_by_id) or pending and incomplete_publication is None:
        raise OgeCatalogError("OGE candidate extractions do not close over direct report history")
    if pending:
        from .oge_pending import retain
        if callable(incomplete_publication):
            incomplete_publication=incomplete_publication()
        pending_reports,retained_published=retain(pending,direct_by_id,incomplete_publication,
            people,transactions,evidence_root)
    replacement_redirects = _apply_native_exhibit_replacements(transactions, audit_reports, base)
    if pending:
        effective={row['id'] for row in transactions}
        retained_published=sorted(set(retained_published)&effective)
        for report in pending_reports:
            report['retained_published_fact_ids']=sorted(set(report['retained_published_fact_ids'])&effective)

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
    owner_current_count = (sum(len(report['native_classification']['qualified_ids'])
        if 'native_classification' in report else len(report['owner_scope_current_qualified_ids'])
        if 'owner_scope_current_qualified_ids' in report else
        report['promoted_count'] - len(report['default_owner_conservation']['retained_ids'])
            + len(report['default_owner_conservation']['current_ids'])
        if 'default_owner_conservation' in report else report['promoted_count']
        for report in audit_reports) if any('owner_scope_current_qualified_ids' in report
                                            or 'default_owner_conservation' in report
                                            or 'native_classification' in report
                                            for report in audit_reports) else None)
    if pending and owner_current_count is None:
        owner_current_count=len(transactions)-len(deferred_formal_ids)-len(retained_published)
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
                   (f"{owner_current_count} transactions currently qualified; "
                    f"{len(transactions)-len(deferred_formal_ids)} published facts preserved; "
                    if owner_current_count is not None else
                    f"{len(transactions)-len(deferred_formal_ids)} transactions qualified; ") +
                   f"{request_required} request-required "
                   f"catalog entries were not automated; {retained_from_history} direct documents "
                   "were retained from archived catalog history."),
    }
    if pending:
        health['detail']+=f" {len(pending)} reports await extraction; {len(retained_published)} exact prior published facts retained."
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
        **({'pending_reports':pending_reports,'retained_published_fact_ids':retained_published,
            'retained_published_fact_count':len(retained_published),
            'prior_publication':{key:incomplete_publication[key] for key in ('main_commit','board_sha256','snapshot_id')}}
           if pending else {}),
        "promoted_transaction_count": len(transactions)-len(deferred_formal_ids),
        **({'current_qualified_transaction_count': owner_current_count}
           if owner_current_count is not None else {}),
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
