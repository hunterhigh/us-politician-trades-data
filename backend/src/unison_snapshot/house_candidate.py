"""Assemble automatically qualified House records into a frontend-compatible candidate input."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json

from .house import HouseIndexError
from .house_ptr import OWNER_CODES, QUALIFICATION_SCHEMA
from .house_ptr_paper_temporal import PARSER_VERSION as PAPER_PARSER_VERSION
from .house_holdings import QUALIFICATION_SCHEMA as HOLDING_QUALIFICATION_SCHEMA


PRIORITY_PEOPLE = {"house:P000197": "configured_priority_person"}
PERSON_FIELDS = ("official_name", "state", "party", "state_district", "evidence_url")


def _read_json(path: Path, description: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise HouseIndexError(f"{description} is invalid") from None
    if not isinstance(value, dict):
        raise HouseIndexError(f"{description} must be an object")
    return value


def _short_name(value: str) -> str:
    parts = value.replace(",", " ").split()
    if not parts:
        raise HouseIndexError("House candidate identity has no official name")
    suffixes = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
    return parts[-2] if len(parts) > 1 and parts[-1].lower() in suffixes else parts[-1]


def _person(identity: dict) -> dict:
    person_id = identity.get("person_id")
    name = identity.get("official_name")
    if not isinstance(person_id, str) or not person_id.startswith("house:") or not isinstance(name, str):
        raise HouseIndexError("House candidate identity is incomplete")
    priority_reason = PRIORITY_PEOPLE.get(person_id)
    return {
        "id": person_id,
        "display_name": name,
        "short_name": _short_name(name),
        "role": "U.S. Representative",
        "office_type": "Congress",
        "chamber": "House",
        "party": identity.get("party"),
        "state": identity.get("state"),
        "disclosure_authority": "house_clerk",
        "priority": priority_reason is not None,
        "priority_reason": priority_reason,
        "portrait_url": None,
    }


def _paper_pending_count(qualification: dict) -> int:
    projection = qualification.get("pending_transaction_projection")
    if projection is None:
        return 0  # Earlier paper qualification artifacts remain readable.
    quarantined = qualification["quarantined"]
    if (qualification.get("parser_version") != PAPER_PARSER_VERSION or
            qualification["transactions"] != [] or
            not isinstance(projection, dict) or
            projection.get("schema_version") != "house-paper-pending-transaction-projection/v1" or
            projection.get("status") != "unverified_pending" or
            projection.get("source_sha256") != qualification.get("source_sha256") or
            projection.get("document_id") != qualification.get("document_id") or
            not isinstance(projection.get("rows"), list) or
            type(projection.get("row_count")) is not int or
            projection["row_count"] != len(projection["rows"])):
        raise HouseIndexError("House paper pending projection is invalid")
    source_rows = {}
    for row in quarantined:
        if not isinstance(row, dict) or not isinstance(row.get("raw_fields"), dict):
            continue
        key = row.get("observation_key")
        if not isinstance(key, str) or key in source_rows:
            raise HouseIndexError("House paper source slots are duplicated")
        source_rows[key] = row
    projected = projection["rows"]
    if len(projected) != len(source_rows):
        raise HouseIndexError("House paper pending projection does not conserve source slots")
    seen = set()
    for row in projected:
        if not isinstance(row, dict) or "id" in row:
            raise HouseIndexError("House paper pending projection does not conserve source slots")
        key = row.get("observation_key")
        if not isinstance(key, str) or key not in source_rows or key in seen:
            raise HouseIndexError("House paper pending projection does not conserve source slots")
        seen.add(key)
        source = source_rows[key]
        raw = source["raw_fields"]
        reasons = source.get("reasons")
        amount = source.get("amount_range_observation")
        amount = amount if isinstance(amount, dict) else {}
        if (not isinstance(reasons, list) or
                row.get("blocking_reasons") != reasons or
                "paper_source_fields_unverified" not in reasons or
                row.get("evidence") != source.get("evidence") or
                row.get("person_id") != qualification["identity"].get("person_id") or
                row.get("owner") != OWNER_CODES.get(raw.get("owner_code")) or
                row.get("asset_name") != (raw.get("asset_raw") or None) or
                row.get("transaction_date") != raw.get("event_date") or
                row.get("transaction_type") != raw.get("direction_mark") or
                row.get("amount_letter") != raw.get("amount_letter") or
                row.get("amount_low") != amount.get("low") or
                row.get("amount_high") != amount.get("high") or
                row.get("amount_kind") != amount.get("kind") or
                row.get("source_quality_flags", []) != source.get(
                    "source_quality_flags", [])):
            raise HouseIndexError("House paper pending projection does not conserve source slots")
    return len(projected)


def build_house_candidate(review_root: Path, state_status: dict, base: dict, *,
                          evidence_root: Path | None = None,
                          annual_transaction_inputs: list[dict] | None = None,
                          annual_transaction_audit: dict | None = None) -> dict:
    """Build a candidate only from a complete, idle House review directory."""
    from .house_paper_commit import paper_review_access
    with paper_review_access(review_root,require_complete=True):
        return _build_house_candidate(review_root,state_status,base,
            evidence_root=evidence_root,
            annual_transaction_inputs=annual_transaction_inputs,annual_transaction_audit=annual_transaction_audit)


def _build_house_candidate(review_root: Path, state_status: dict, base: dict, *,
                           evidence_root: Path | None = None,
                           annual_transaction_inputs: list[dict] | None = None,
                           annual_transaction_audit: dict | None = None) -> dict:
    review_root = review_root.resolve()
    source_status = review_root / "status" / "house_clerk.json"
    summary = _read_json(source_status if source_status.is_file()
                         else review_root / "status" / "summary.json",
                         "House qualification summary")
    if (summary.get("parser_commit") is None or summary.get("pending_parse_count") != 0
            or summary.get("status_conflict_count", 0) != 0):
        raise HouseIndexError("House qualification queue is not ready for a candidate")
    paths = sorted((review_root / "house_clerk" / "qualifications").glob("*/*/*.json"))
    if len(paths) != summary.get("qualification_count"):
        raise HouseIndexError("House qualification count does not match its summary")

    transactions: list[dict] = []
    holdings: list[dict] = []
    identities: dict[str, dict] = {}
    seen_transaction_ids: set[str] = set()
    quarantined_count = 0
    paper_pending_count = 0
    paper_unknown_reports = paper_unknown_physical = 0
    native_account_pending = 0
    native_account_unassessed_reports = 0
    revision_inputs = []
    revision_sources = []
    contrast_sources = []
    for path in paths:
        qualification = _read_json(path, "House qualification artifact")
        if qualification.get("schema_version") != QUALIFICATION_SCHEMA:
            raise HouseIndexError("House qualification artifact has an unsupported schema")
        rows = qualification.get("transactions")
        quarantined = qualification.get("quarantined")
        identity = qualification.get("identity")
        if not isinstance(rows, list) or not isinstance(quarantined, list) or not isinstance(identity, dict):
            raise HouseIndexError("House qualification artifact is incomplete")
        quarantined_count += len(quarantined)
        from .house_ptr_paper_qualification import paper_materialization_counts
        paper_counts = paper_materialization_counts(qualification)
        if paper_counts is not None:
            paper_unknown_reports += paper_counts["whole_source_unique_quarantined_row_count"] is None
            paper_unknown_physical += paper_counts["unresolved_physical_slot_count"]
        paper_pending_count += _paper_pending_count(qualification)
        extraction_path = review_root / "house_clerk/extractions" / path.relative_to(review_root / "house_clerk/qualifications")
        if (qualification.get("native_account_context", {}).get("status") == "source_words_reproduced"
                and not extraction_path.is_file()):
            raise HouseIndexError("House native account context requires its source extraction")
        if (qualification.get("native_account_context", {}).get("status") == "source_pages_unavailable"
                and not extraction_path.is_file()):
            native_account_unassessed_reports += 1
        if extraction_path.is_file():
            extraction = _read_json(extraction_path, "House native account source extraction")
            if extraction.get("extraction_method") == "native_pdf_text" and extraction.get("source_word_pages") is None:
                if qualification.get("native_account_context", {}).get("status") == "source_words_reproduced":
                    raise HouseIndexError("House native account receipt has no saved source words")
                # Legacy formal facts are not requalified or rewritten merely
                # because this additive display proof was not saved previously.
                native_account_pending += sum(bool(r.get("subholding_of")) for r in extraction["transactions"])
            elif extraction.get("extraction_method") in {"native_pdf_text", "mixed_native_ocr"}:
                from .house_ptr import qualify_automatic
                source = extraction.get("source", {})
                if evidence_root is not None:
                    from .house_native_contrast import plan_house_native_contrast, PARSER_VERSIONS as CONTRAST_VERSIONS
                    relative = source.get('archive_path')
                    if not isinstance(relative, str):
                        raise HouseIndexError('House native source plan requires archived metadata')
                    plan = plan_house_native_contrast(evidence_root,
                        Path(evidence_root) / Path(relative).with_suffix('.json'))
                    if plan['parser_version'] in CONTRAST_VERSIONS and extraction.get('parser_version') != plan['parser_version']:
                        raise HouseIndexError('House cached native version differs from current original source plan')
                if (qualification.get("source_sha256") != extraction.get("source_sha256") or
                        qualification.get("document_id") != source.get("document_id") or
                        qualification.get("parser_version") != extraction.get("parser_version") or
                        extraction_path.stem != extraction.get("source_sha256") or
                        extraction_path.parent.name != str(source.get("document_id"))):
                    raise HouseIndexError("House native account document binding differs from qualification")
                from .house_native_contrast import PARSER_VERSIONS as CONTRAST_VERSIONS
                contrast = extraction.get('parser_version') in CONTRAST_VERSIONS
                expected = qualify_automatic(extraction, identity,
                    **({'evidence_root':evidence_root} if evidence_root is not None or contrast else {}))
                if contrast:
                    if qualification != expected:
                        raise HouseIndexError('House contrast qualification differs from original source replay')
                    contrast_sources.append((extraction, expected))
                proof = expected.get("native_account_context", {})
                if ("native_account_context" in qualification and
                        qualification["native_account_context"] != proof):
                    raise HouseIndexError("House native account receipt differs from source observations")
                expected_rows = {r["id"]: r for r in expected["transactions"]}
                raw_names = {r["extraction_id"]: r["asset_name"] for r in extraction["transactions"]}
                if len(expected_rows) != len(rows) or set(expected_rows) != {r.get("id") for r in rows}:
                    raise HouseIndexError("House native account qualification rows differ from source")
                derived = []
                for row in rows:
                    projected = expected_rows[row["id"]]
                    if (row.get("asset_name") not in {raw_names[row["id"]], projected["asset_name"]} or
                            {k:v for k,v in row.items() if k != "asset_name"} !=
                            {k:v for k,v in projected.items() if k != "asset_name"}):
                        raise HouseIndexError("House native account projection changed financial facts or identity")
                    derived.append(deepcopy(projected))
                rows = derived
        if qualification.get("revision_observations") and extraction_path.is_file():
            revision_inputs.append((qualification, extraction_path))
        if extraction_path.is_file():
            revision_sources.append((qualification, extraction))
        person_id = identity.get("person_id")
        if rows:
            if not isinstance(person_id, str):
                raise HouseIndexError("Qualified House rows require a stable identity")
            stable_identity = {field: identity.get(field) for field in PERSON_FIELDS}
            if person_id in identities and identities[person_id] != stable_identity:
                raise HouseIndexError("House identity changed across qualification artifacts")
            identities[person_id] = stable_identity
        for row in rows:
            if not isinstance(row, dict) or row.get("person_id") != person_id:
                raise HouseIndexError("Qualified House transaction has an invalid identity reference")
            transaction_id = row.get("id")
            if not isinstance(transaction_id, str) or transaction_id in seen_transaction_ids:
                raise HouseIndexError("Qualified House transaction ID is missing or duplicated")
            seen_transaction_ids.add(transaction_id)
            transactions.append(deepcopy(row))

    holding_summary_path = review_root / "status" / "house_holdings.json"
    holding_summary = None
    selected_holding_coverage = []
    if holding_summary_path.is_file():
        holding_summary = _read_json(holding_summary_path, "House holding qualification summary")
        holding_paths = sorted((review_root / "house_clerk" / "holding_qualifications").glob("*/*/*.json"))
        if len(holding_paths) != holding_summary.get("qualification_count"):
            raise HouseIndexError("House holding qualification count does not match its summary")
        holding_identities: dict[str, dict] = {}
        latest: dict[str, dict] = {}
        total_qualified = total_quarantined = 0
        for path in holding_paths:
            artifact = _read_json(path, "House holding qualification artifact")
            if artifact.get("schema_version") != HOLDING_QUALIFICATION_SCHEMA:
                raise HouseIndexError("House holding qualification artifact has an unsupported schema")
            identity = artifact.get("identity")
            source = artifact.get("source")
            rows = artifact.get("holdings")
            quarantined = artifact.get("quarantined")
            if not isinstance(identity, dict) or not isinstance(source, dict) \
                    or not isinstance(rows, list) or not isinstance(quarantined, list):
                raise HouseIndexError("House holding qualification artifact is incomplete")
            if artifact.get("production_eligible") is True and quarantined and "holding_coverage" not in artifact:
                raise HouseIndexError("Eligible partial House holding report requires source-bound coverage")
            if "holding_coverage" in artifact:
                # Partial reports require actual saved source observations, not
                # a free-standing eligibility/coverage label on an artifact.
                relative = path.relative_to(review_root / "house_clerk" / "holding_qualifications")
                extraction = _read_json(review_root / "house_clerk" / "holding_extractions" / relative,
                                        "House partial holding extraction")
                observation = _read_json(review_root / "house_clerk" / "holding_section_observations" / relative,
                                         "House partial holding observations")
                from .house_holdings import extract_schedule_a_pages, qualify_financial_report
                from .house_annual_sections import observe_annual_sections
                pages = observation.get("source_word_pages")
                if not isinstance(pages, list) or not pages:
                    raise HouseIndexError("House partial holding report has no saved source pages")
                parsed, _ = extract_schedule_a_pages(pages, source_sha256=extraction.get("source_sha256"))
                if (parsed != extraction.get("rows") or observe_annual_sections(pages, extraction) != observation or
                        observation.get("source_text_coverage") != extraction.get("source_text_coverage") or
                        qualify_financial_report(extraction, identity) != artifact):
                    raise HouseIndexError("House partial holding qualification differs from saved source observations")
            total_qualified += len(rows)
            total_quarantined += len(quarantined)
            person_id = identity.get("person_id")
            if not isinstance(person_id, str):
                continue
            stable_identity = {field: identity.get(field) for field in PERSON_FIELDS}
            if person_id in holding_identities and holding_identities[person_id] != stable_identity:
                raise HouseIndexError("House holding identity changed across qualification artifacts")
            holding_identities[person_id] = stable_identity
            rank = (str(artifact.get("report_period_end", "")), str(source.get("filed_date", "")),
                    str(source.get("document_id", "")))
            if person_id not in latest or rank > latest[person_id]["rank"]:
                latest[person_id] = {"rank": rank, "artifact": artifact}
        if total_qualified != holding_summary.get("qualified_holding_count") or \
                total_quarantined != holding_summary.get("quarantined_row_count"):
            raise HouseIndexError("House holding row counts do not match their summary")
        for person_id, selected in latest.items():
            artifact = selected["artifact"]
            # Never fall back to an older report when the latest known report is incomplete.
            if artifact.get("production_eligible") is True:
                if artifact.get("holding_coverage"):
                    selected_holding_coverage.append(deepcopy(artifact["holding_coverage"]))
                for row in artifact["holdings"]:
                    if not isinstance(row, dict) or row.get("person_id") != person_id:
                        raise HouseIndexError("Qualified House holding has an invalid identity reference")
                    holdings.append(deepcopy(row))
        holding_people = {row["person_id"] for row in holdings}
        for person_id in holding_people:
            identity = holding_identities[person_id]
            if person_id in identities and identities[person_id] != identity:
                raise HouseIndexError("House identity differs between transaction and holding reports")
            identities[person_id] = identity

    if len(transactions) != summary.get("qualified_transaction_count") or \
            quarantined_count != summary.get("quarantined_row_count"):
        raise HouseIndexError("House qualification row counts do not match their summary")
    from .house_ptr import resolve_automatic_revisions
    revision_resolution = resolve_automatic_revisions(revision_sources, transactions,
        prior_transactions=base.get("transactions", []),
        **({'evidence_root':evidence_root} if evidence_root is not None else {}))
    transactions = revision_resolution["transactions"]
    selected = {row['id']:row for row in transactions}
    for extraction, qualification in contrast_sources:
        source = extraction['source']
        raw_ids = {row['extraction_id'] for row in extraction['transactions']}
        for prior in base.get('transactions', []):
            if (prior.get('source_id') != 'house_clerk' or
                    str(prior.get('filing_id')) != str(source['document_id']) or
                    prior.get('verification_status') != 'official_matched'):
                continue
            identifier = prior.get('id')
            if (prior.get('source_url') != source['source_url'] or identifier not in raw_ids or
                    prior.get('person_id') != qualification['identity'].get('person_id')):
                raise HouseIndexError('Existing House contrast fact has no unique current physical source row')
            if identifier in selected and selected[identifier] != prior:
                raise HouseIndexError('Changed House contrast fact requires a separate source correction')
            selected[identifier] = deepcopy(prior)
    transactions = sorted(selected.values(), key=lambda row:row['id'])
    quarantined_count -= sum(len(q["quarantined"]) - len(revision_resolution["qualifications"].get(
        str(q.get("document_id")), q)["quarantined"]) for q, _ in revision_sources)
    for row in transactions:
        if row["person_id"] not in identities:
            identity = next((identity for identity in revision_resolution["identities"].values()
                             if identity.get("person_id") == row["person_id"]), None)
            if identity:
                identities[row["person_id"]] = {field: identity.get(field) for field in PERSON_FIELDS}
    if len(state_status.get("counts", {})) == 0 or state_status.get("status") != "ok":
        raise HouseIndexError("House source state is not healthy enough to build a candidate")
    if state_status.get("counts", {}).get("archived") != summary.get("evidence_count"):
        raise HouseIndexError("House source and qualification evidence counts disagree")
    cutoff = state_status.get("run_at")
    if not isinstance(cutoff, str):
        raise HouseIndexError("House source state has no data cutoff")

    candidate = deepcopy(base)
    meta = candidate.get("meta")
    if not isinstance(meta, dict) or meta.get("is_demo") is not False:
        raise HouseIndexError("House candidate base must be a production input")
    meta["data_cutoff_at"] = cutoff
    meta.pop("holding_report_coverage", None)
    if selected_holding_coverage:
        meta["holding_report_coverage"] = sorted(selected_holding_coverage, key=lambda r: r["person_id"])
    meta["subtitle"] = "House真实候选数据；其余披露来源和行情仍在回填"
    candidate["people"] = sorted((_person({"person_id": person_id, **identity})
                                  for person_id, identity in identities.items()), key=lambda row: row["id"])
    candidate["transactions"] = sorted(transactions, key=lambda row: row["id"])
    from .house_ptr import revision_reference_dispositions
    revision_dispositions = [item for qualification, path in revision_inputs for item in
        revision_resolution["dispositions"].get(str(qualification["document_id"]),
            revision_reference_dispositions(qualification, _read_json(path, "House revision source"), candidate["transactions"]))]
    if len({row.get("id") for row in holdings}) != len(holdings):
        raise HouseIndexError("Qualified House holding IDs are missing or duplicated")
    candidate["reported_holdings"] = sorted(holdings, key=lambda row: row["id"])
    candidate["security_market_data"] = []
    health = candidate.get("source_health")
    if not isinstance(health, list):
        raise HouseIndexError("House candidate base has no source health array")
    house_health = {
        "source_id": "house_clerk",
        "source": "U.S. House Clerk",
        "source_type": "official_disclosure",
        "source_url": "https://disclosures-clerk.house.gov/FinancialDisclosure/ViewSearch",
        "status": "partial",
        "last_checked_at": cutoff,
        "last_successful_sync_at": cutoff,
        "data_cutoff_at": cutoff,
        "detail": (f"{summary['evidence_count']} PTR PDFs archived; {summary['extracted_count']} extracted; "
                   f"{len(transactions)} transactions automatically qualified; "
                   f"{summary.get('zero_transaction_document_count', 0)} explicit zero-transaction filings; "
                   + (f"{quarantined_count} qualification entries quarantined including {paper_unknown_physical} unresolved paper slots; "
                      f"whole-source unique quarantined rows unknown for {paper_unknown_reports} paper reports; "
                      if paper_unknown_reports else f"{quarantined_count} rows quarantined; ")
                   + (f"{paper_pending_count} paper transaction projections pending; "
                      if paper_pending_count else "")
                   + f"{summary['failure_count']} parser failures; "
                   f"{state_status['counts']['pending']} filings pending archive; "
                   f"{len(holdings)} holdings from latest {'eligible' if selected_holding_coverage else 'complete'} annual reports"
                   + (f"; {holding_summary.get('quarantined_row_count', 0)} holding rows quarantined."
                      if holding_summary else ".")),
    }
    if revision_dispositions:
        applied = sum(r.get("action") == "replace_prior" for r in revision_dispositions)
        house_health["detail"] += (f"; {applied} source-bound amendments applied; "
                                   f"{len(revision_dispositions)-applied} official amendment references await source-bound predecessors")
    if native_account_pending:
        house_health["detail"] += f"; {native_account_pending} native account titles await saved source-word evidence; existing facts retained"
    if native_account_unassessed_reports:
        house_health["detail"] += f"; {native_account_unassessed_reports} native account display reports await missing source extraction; existing facts retained"
    candidate["source_health"] = [house_health if row.get("source_id") == "house_clerk" else row
                                  for row in health]
    if not any(row.get("source_id") == "house_clerk" for row in health):
        candidate["source_health"].append(house_health)
    # Consume saved annual B observations in the actual source candidate call.
    # These decisions cannot replace PTRs or alter the holding report selector.
    from .house_annual_sections import consume_schedule_b_inputs
    inputs = annual_transaction_inputs
    if inputs is None:
        inputs = []
        for path in sorted((review_root / "house_clerk/holding_section_observations").glob("*/*/*.json")):
            relative = path.relative_to(review_root / "house_clerk/holding_section_observations")
            observation = _read_json(path, "House annual section observation")
            extract_path = review_root / "house_clerk/holding_extractions" / relative
            identity_path = review_root / "house_clerk/holding_identities" / relative
            payload = {"observation": observation,
                           "extraction": _read_json(extract_path, "House annual extraction") if extract_path.is_file() else {},
                           "identity": _read_json(identity_path, "House annual identity") if identity_path.is_file() else {}}
            if "source_word_pages" in observation:
                import hashlib
                pages = observation["source_word_pages"]
                digest = hashlib.sha256(json.dumps(pages, ensure_ascii=False,
                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                if digest != observation.get("source_word_pages_sha256"):
                    raise HouseIndexError("House annual saved word pages digest differs")
                payload["pages"] = pages
            inputs.append(payload)
    existing_b = [r for r in base.get("transactions", [])
                  if r.get("source_id") == "house_clerk" and str(r.get("id", "")).startswith("house-annual-b:")]
    base_relations = deepcopy(base.get("meta", {}).get("transaction_disclosure_relations", []))
    redirects = {r["prior_transaction_id"]: r["transaction_id"] for r in revision_resolution["replacements"]}
    for relation in base_relations:
        values = relation.get("possible_ptr_transaction_ids", [])
        replaced = []
        for value in values:
            while value in redirects:
                value = redirects[value]
            replaced.append(value)
        relation["possible_ptr_transaction_ids"] = sorted(set(replaced))
    annual_audit = consume_schedule_b_inputs(inputs, transactions=candidate["transactions"],
        reported_holdings=candidate["reported_holdings"], existing_annual_transactions=existing_b,
        existing_relations=base_relations,data_cutoff_at=cutoff)
    candidate["transactions"] = sorted([*candidate["transactions"],*annual_audit["transactions"]],key=lambda r:r["id"])
    prior_people = {r["id"]:r for r in base.get("people", [])}
    people = {r["id"]:r for r in candidate["people"]}
    for row in annual_audit["transactions"]:
        person = row["person_id"]
        if person not in people:
            report = next((r for r in annual_audit["reports"] if r["person_id"] == person and
                           not r["report_blocking_reasons"]),None)
            if report is not None:
                people[person] = _person(report["identity"])
            elif person in prior_people:
                people[person] = deepcopy(prior_people[person])
            else:
                raise HouseIndexError("Retained annual House disclosure has no identity")
    candidate["people"] = [people[k] for k in sorted(people)]
    actual_ids = {r["id"] for r in candidate["transactions"]}
    relations = [deepcopy(r) for r in base_relations
                 if r.get("source_id") != "house_clerk" and r.get("transaction_id") in actual_ids]
    relations += annual_audit["transaction_disclosure_relations"]
    candidate["meta"].pop("transaction_disclosure_relations",None)
    if relations:
        candidate["meta"]["transaction_disclosure_relations"] = sorted(relations,key=lambda r:r["transaction_id"])
    if annual_transaction_audit is not None:
        annual_transaction_audit.clear()
        annual_transaction_audit.update(annual_audit)
    if annual_audit["reports"]:
        for row in candidate["source_health"]:
            if row.get("source_id") == "house_clerk":
                row["detail"] += (f"; annual B: {annual_audit['observed_row_count']} observations, "
                                  f"{len(annual_audit['transactions'])} independent disclosures included ({annual_audit['new_candidate_transaction_count']} new), "
                                  f"{sum(annual_audit['candidate_disposition_counts'].get(s,0) for s in ('blocked_pending_relationship','quarantined'))} blocked, "
                                  f"{annual_audit['candidate_disposition_counts'].get('excluded_exact_source_copy', 0)} exact-copy exclusions")
                row["detail"] += f"; {len(candidate['transactions'])} total candidate disclosure records; annual coverage remains bounded to observed rows"
                coverages = [report.get("source_text_coverage") for report in annual_audit["reports"]]
                covered = [item for item in coverages if isinstance(item, dict)]
                if covered:
                    row["detail"] += (f"; annual native text census: {sum(item['unresolved_financial_text_line_count'] for item in covered)} "
                                      "unresolved financial text lines (not unique disclosures)")
    elif annual_audit["transactions"]:
        for row in candidate["source_health"]:
            if row.get("source_id") == "house_clerk":
                row["detail"] += (f"; {len(annual_audit['transactions'])} existing annual B disclosures retained; "
                                  f"source observations unavailable; {len(candidate['transactions'])} total candidate disclosure records")
    return candidate


def load_house_candidate(review_root: Path, state_status_path: Path, base_path: Path, *,
                         evidence_root: Path | None = None,
                         annual_transaction_audit: dict | None = None) -> dict:
    return build_house_candidate(review_root, _read_json(state_status_path, "House source state"),
                                 _read_json(base_path, "House candidate base"),
                                 evidence_root=evidence_root,
                                 annual_transaction_audit=annual_transaction_audit)
