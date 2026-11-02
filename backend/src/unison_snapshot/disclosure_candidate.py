"""Deterministically merge source-scoped disclosure candidates.

The source collectors own qualification.  This module only combines their
frontend-compatible projections and fails closed when the projections cannot
describe one coherent snapshot.  It deliberately leaves every person and fact
row unchanged so the existing House candidate can be used as an input without
being reinterpreted.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from .builder import timestamp, validate_transaction_disclosure_relations
from .codec import digest, encode


READY_SOURCE_STATUSES = frozenset({"ok", "partial"})
DISCLOSURE_ARRAYS = ("people", "transactions", "reported_holdings")
SOURCE_LABELS = {"house_clerk": "House", "senate_efd": "Senate", "oge": "OGE"}


class DisclosureCandidateError(ValueError):
    """A source projection is unsafe to include in a unified candidate."""


class SourceCutoffsStaleError(DisclosureCandidateError):
    """Valid source waterlines cannot form a current coherent candidate."""

    def __init__(self, projections: list[dict], message: str):
        self.source_cutoffs = {item['source_id']: item['data_cutoff_at']
                               for item in projections}
        super().__init__(message)


def _objects(value: object, description: str) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise DisclosureCandidateError(f"{description} must be an array of objects")
    return value


def _identifier(row: dict, field: str, description: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise DisclosureCandidateError(f"{description} has no valid {field}")
    return value


def project_source_candidate(source_id: str, candidate: dict) -> dict:
    """Extract one source projection from a frontend-compatible candidate.

    Existing source builders, including ``build_house_candidate``, may keep
    producing full candidates.  This adapter selects only facts owned by
    ``source_id`` and the people they reference; it does not normalize, enrich,
    or otherwise alter those rows.
    """
    if not isinstance(source_id, str) or not source_id.strip() or source_id != source_id.strip():
        raise DisclosureCandidateError("Source projection has no valid source_id")
    if not isinstance(candidate, dict) or not isinstance(candidate.get("meta"), dict):
        raise DisclosureCandidateError(f"{source_id} candidate must be an object with meta")
    if candidate["meta"].get("is_demo") is not False:
        raise DisclosureCandidateError(f"{source_id} candidate must be production data")
    cutoff = candidate["meta"].get("data_cutoff_at")
    try:
        timestamp(cutoff)
    except (TypeError, ValueError):
        raise DisclosureCandidateError(f"{source_id} candidate has an invalid cutoff") from None

    people = _objects(candidate.get("people"), f"{source_id} people")
    transactions = _objects(candidate.get("transactions"), f"{source_id} transactions")
    holdings = _objects(candidate.get("reported_holdings"), f"{source_id} reported_holdings")
    selected_transactions = [row for row in transactions if row.get("source_id") == source_id]
    selected_holdings = [row for row in holdings if row.get("source_id") == source_id]
    cutoff_time = timestamp(cutoff)
    for row in selected_transactions + selected_holdings:
        try:
            filed_at = timestamp(row.get("filed_at"))
        except (TypeError, ValueError):
            raise DisclosureCandidateError(
                f"{source_id} disclosure has an invalid filed_at") from None
        if filed_at > cutoff_time:
            raise DisclosureCandidateError(
                f"{source_id} disclosure exceeds its native cutoff")
    referenced_ids = {
        _identifier(row, "person_id", f"{source_id} disclosure")
        for row in selected_transactions + selected_holdings
    }
    people_by_id: dict[str, dict] = {}
    for row in people:
        person_id = _identifier(row, "id", f"{source_id} person")
        if person_id in people_by_id:
            raise DisclosureCandidateError(f"{source_id} candidate duplicates person ID {person_id}")
        people_by_id[person_id] = row
    missing = referenced_ids - people_by_id.keys()
    if missing:
        raise DisclosureCandidateError(
            f"{source_id} disclosure references unknown person {sorted(missing)[0]}"
        )
    selected_people = [people_by_id[person_id] for person_id in sorted(referenced_ids)]
    for person in selected_people:
        if person.get("disclosure_authority") != source_id:
            raise DisclosureCandidateError(
                f"{source_id} person {_identifier(person, 'id', 'person')} has another authority"
            )

    health_rows = _objects(candidate.get("source_health"), f"{source_id} source_health")
    source_health = [row for row in health_rows if row.get("source_id") == source_id]
    if len(source_health) != 1:
        raise DisclosureCandidateError(f"{source_id} candidate requires exactly one source health row")
    health = source_health[0]
    if health.get("status") not in READY_SOURCE_STATUSES and not (
            source_id == 'senate_efd' and health.get('status') == 'stale' and health.get('stale') is True):
        raise DisclosureCandidateError(f"{source_id} source is not ready for candidate assembly")
    if health.get("data_cutoff_at") != cutoff:
        raise DisclosureCandidateError(f"{source_id} source health cutoff does not match its candidate")

    coverage = _objects(candidate["meta"].get("holding_report_coverage", []),
                        f"{source_id} holding report coverage")
    selected_coverage = [r for r in coverage if r.get("source_id") == source_id]
    try:validate_transaction_disclosure_relations(candidate)
    except ValueError as exc:raise DisclosureCandidateError(str(exc)) from exc
    relations=[r for r in candidate['meta'].get('transaction_disclosure_relations',[]) if r['source_id']==source_id]
    for report in selected_coverage:
        rows = [r for r in selected_holdings if r.get("person_id") == report.get("person_id")]
        if report.get("schema_version") == "oge-approved-partial-holdings/v1":
            rows = [r for r in rows if r.get("report_period_end") == report.get("report_period_end")]
        if (not rows or sorted(r["id"] for r in rows) != sorted(report.get("published_holding_ids", [])) or
                any(r.get("filing_id") != report.get("filing_id") or
                    r.get("report_period_end") != report.get("report_period_end") for r in rows)):
            raise DisclosureCandidateError("Holding coverage differs from source projection")
    return {
        "source_id": source_id,
        "data_cutoff_at": cutoff,
        "people": deepcopy(selected_people),
        "transactions": deepcopy(selected_transactions),
        "reported_holdings": deepcopy(selected_holdings),
        "source_health": deepcopy(health),
        **({"holding_report_coverage": deepcopy(selected_coverage)} if selected_coverage else {}),
        **({'transaction_disclosure_relations':deepcopy(relations)} if relations else {}),
    }


def _source_cutoff_audit(projections, source_hashes):
    """Retain all source facts at their native cutoffs; use newest for windows."""
    cutoff = max((item['data_cutoff_at'] for item in projections), key=timestamp)
    sources = {}
    for item in projections:
        counts = {kind:len(item[kind]) for kind in DISCLOSURE_ARRAYS}
        sources[item['source_id']] = {
            'candidate_sha256':source_hashes[item['source_id']],
            'native_cutoff_at':item['data_cutoff_at'],
            'input_counts':counts, 'retained_counts':deepcopy(counts),
            'filtered_transaction_count':0, 'filtered_holding_count':0}
    return cutoff, {'schema_version':'disclosure-cutoff-audit/v2',
                    'mode':'per_source_cutoff', 'reference_cutoff_at':cutoff,
                    'sources':sources}


def build_disclosure_candidate(base: dict, source_candidates: Mapping[str, dict], *,
                               harmonize_cutoffs: bool = False,
                               harmonization_audit: dict | None = None,
                               source_statuses: Mapping[str, dict] | None = None,
                               now=None, senate_days=None) -> dict:
    """Merge complete source candidates into one production candidate.

    ``base`` owns presentation settings and market data.  Its disclosure arrays
    must be empty, making source ownership explicit.  Each mapping key names the
    sole disclosure source to extract from that candidate. Each projection keeps
    its native cutoff. The newest cutoff anchors the report's statistical windows.
    ``harmonize_cutoffs`` remains accepted for older command invocations; it never
    truncates facts or rewrites a source cutoff.
    """
    if not isinstance(base, dict) or not isinstance(base.get("meta"), dict):
        raise DisclosureCandidateError("Disclosure candidate base must be an object with meta")
    if base["meta"].get("is_demo") is not False:
        raise DisclosureCandidateError("Disclosure candidate base must be production data")
    if 'data_cutoff_at' in base['meta']:
        try: timestamp(base['meta']['data_cutoff_at'])
        except (TypeError, ValueError):
            raise DisclosureCandidateError('Disclosure candidate base cutoff is invalid') from None
    if not isinstance(source_candidates, Mapping) or not source_candidates:
        raise DisclosureCandidateError("At least one source candidate is required")
    if any(not isinstance(source_id, str) or not source_id.strip()
           or source_id != source_id.strip() for source_id in source_candidates):
        raise DisclosureCandidateError("Source candidate keys must be valid source IDs")
    for name in DISCLOSURE_ARRAYS:
        rows = _objects(base.get(name), f"Base {name}")
        if rows:
            raise DisclosureCandidateError(f"Base {name} must be empty")
    market_rows = _objects(base.get("security_market_data"), "Base security_market_data")
    base_health = _objects(base.get("source_health"), "Base source_health")

    source_hashes = {source_id: digest(encode(source_candidates[source_id]))
                     for source_id in sorted(source_candidates)}
    projections = [
        project_source_candidate(source_id, source_candidates[source_id])
        for source_id in sorted(source_candidates)
    ]
    cutoff, cutoff_audit = _source_cutoff_audit(projections, source_hashes)

    people_by_id: dict[str, dict] = {}
    facts_by_id: dict[str, str] = {}
    merged_transactions: list[dict] = []
    merged_holdings: list[dict] = []
    for projection in projections:
        source_id = projection["source_id"]
        for person in projection["people"]:
            person_id = _identifier(person, "id", f"{source_id} person")
            previous = people_by_id.get(person_id)
            if previous is not None and previous != person:
                raise DisclosureCandidateError(f"Identity fields conflict for person {person_id}")
            people_by_id.setdefault(person_id, person)
        for kind, target in (("transactions", merged_transactions),
                             ("reported_holdings", merged_holdings)):
            for row in projection[kind]:
                fact_id = _identifier(row, "id", f"{source_id} {kind}")
                if fact_id in facts_by_id:
                    raise DisclosureCandidateError(
                        f"Duplicate disclosure fact ID {fact_id} in {facts_by_id[fact_id]} and {kind}"
                    )
                if row.get("source_id") != source_id:
                    raise DisclosureCandidateError(f"{fact_id} is assigned to the wrong source")
                _identifier(row, "person_id", f"Disclosure fact {fact_id}")
                facts_by_id[fact_id] = kind
                target.append(row)

    for row in merged_transactions + merged_holdings:
        if row["person_id"] not in people_by_id:
            raise DisclosureCandidateError(
                f"Disclosure fact {row['id']} references unknown person {row['person_id']}"
            )

    base_health_by_id: dict[str, dict] = {}
    base_health_order: list[str] = []
    for row in base_health:
        source_id = _identifier(row, "source_id", "Base source health")
        if source_id in base_health_by_id:
            raise DisclosureCandidateError(f"Base duplicates source health for {source_id}")
        base_health_by_id[source_id] = deepcopy(row)
        base_health_order.append(source_id)
    projection_health = {projection["source_id"]: projection["source_health"]
                         for projection in projections}
    for source_id, row in projection_health.items():
        base_health_by_id[source_id] = row
    appended = sorted(set(projection_health) - set(base_health_order))

    result = deepcopy(base)
    result["meta"]["data_cutoff_at"] = cutoff
    result['meta']['source_cutoff_policy'] = 'per_source/v1'
    included = "、".join(SOURCE_LABELS.get(item["source_id"], item["source_id"])
                         for item in projections)
    result["meta"]["subtitle"] = f"{included}真实披露候选；其他披露来源和行情仍在回填"
    result["people"] = [deepcopy(people_by_id[key]) for key in sorted(people_by_id)]
    result["transactions"] = sorted(deepcopy(merged_transactions), key=lambda row: row["id"])
    result["reported_holdings"] = sorted(deepcopy(merged_holdings), key=lambda row: row["id"])
    result["meta"].pop("holding_report_coverage", None)
    coverage = []
    merged_ids = {r["id"] for r in merged_holdings}
    for projection in projections:
        for report in projection.get("holding_report_coverage", []):
            ids = set(report["published_holding_ids"])
            if not ids & merged_ids:
                # Cutoff harmonization removed this whole filing.
                continue
            if not ids <= merged_ids:
                raise DisclosureCandidateError("Partial report selection lost some published holdings")
            coverage.append(deepcopy(report))
    if coverage:
        result["meta"]["holding_report_coverage"] = sorted(coverage,key=lambda r:(r["source_id"],r["person_id"]))
    result['meta'].pop('transaction_disclosure_relations',None)
    transaction_ids={r['id'] for r in merged_transactions}
    relations=[deepcopy(r) for projection in projections for r in projection.get('transaction_disclosure_relations',[]) if r['transaction_id'] in transaction_ids]
    if relations:
        # Cutoff harmonization may legitimately omit an older possible PTR;
        # do not leave a full-input dangling reference in the merged artifact.
        for relation in relations:
            relation['possible_ptr_transaction_ids']=[i for i in relation['possible_ptr_transaction_ids'] if i in transaction_ids]
            relation['status']='possible_ptr_overlap' if relation['possible_ptr_transaction_ids'] else 'cross_report_relationship_unconfirmed'
        result['meta']['transaction_disclosure_relations']=sorted(relations,key=lambda r:r['transaction_id'])
    validate_transaction_disclosure_relations(result)
    from .source_freshness import health_rows
    result["source_health"] = health_rows(
        [base_health_by_id[key] for key in base_health_order + appended],
        as_of=now or cutoff, statuses=source_statuses, senate_days=senate_days,
        included_sources=set(source_candidates))
    if harmonization_audit is not None:
        harmonization_audit.clear()
        harmonization_audit.update(cutoff_audit)
    return result
