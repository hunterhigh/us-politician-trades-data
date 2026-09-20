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
from datetime import datetime, time, timedelta, timezone

from .builder import timestamp
from .codec import digest, encode


READY_SOURCE_STATUSES = frozenset({"ok", "partial"})
DISCLOSURE_ARRAYS = ("people", "transactions", "reported_holdings")
SOURCE_LABELS = {"house_clerk": "House", "senate_efd": "Senate", "oge": "OGE"}
MAX_HARMONIZATION_LAG = timedelta(days=2)


class DisclosureCandidateError(ValueError):
    """A source projection is unsafe to include in a unified candidate."""


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
    if health.get("status") not in READY_SOURCE_STATUSES:
        raise DisclosureCandidateError(f"{source_id} source is not ready for candidate assembly")
    if health.get("data_cutoff_at") != cutoff:
        raise DisclosureCandidateError(f"{source_id} source health cutoff does not match its candidate")

    return {
        "source_id": source_id,
        "data_cutoff_at": cutoff,
        "people": deepcopy(selected_people),
        "transactions": deepcopy(selected_transactions),
        "reported_holdings": deepcopy(selected_holdings),
        "source_health": deepcopy(health),
    }


def _harmonize_projections(projections: list[dict], source_hashes: dict[str, str]) -> tuple[str, dict]:
    """Align date-precision disclosures to the last complete shared UTC day."""
    native_times = [timestamp(item["data_cutoff_at"]) for item in projections]
    if max(native_times) - min(native_times) > MAX_HARMONIZATION_LAG:
        raise DisclosureCandidateError("Source candidate cutoffs are too stale to harmonize")
    earliest = min(native_times).astimezone(timezone.utc)
    complete_day = earliest.date() - timedelta(days=1)
    cutoff_time = datetime.combine(complete_day, time(23, 59, 59), tzinfo=timezone.utc)
    cutoff = cutoff_time.isoformat().replace("+00:00", "Z")
    audit_sources = {}
    for projection in projections:
        before = {kind: len(projection[kind])
                  for kind in ("people", "transactions", "reported_holdings")}
        referenced_ids: set[str] = set()
        for kind in ("transactions", "reported_holdings"):
            retained = []
            for row in projection[kind]:
                try:
                    filed_at = timestamp(row.get("filed_at"))
                except (TypeError, ValueError):
                    raise DisclosureCandidateError(
                        f"{projection['source_id']} disclosure has an invalid filed_at"
                    ) from None
                if filed_at <= cutoff_time:
                    retained.append(row)
                    referenced_ids.add(_identifier(
                        row, "person_id", f"{projection['source_id']} disclosure"))
            projection[kind] = retained
        projection["people"] = [row for row in projection["people"]
                                if row.get("id") in referenced_ids]
        original_cutoff = projection["data_cutoff_at"]
        projection["data_cutoff_at"] = cutoff
        projection["source_health"]["data_cutoff_at"] = cutoff
        after = {kind: len(projection[kind])
                 for kind in ("people", "transactions", "reported_holdings")}
        projection["source_health"]["detail"] = (
            f"Unified candidate uses complete-day cutoff {cutoff}; retained "
            f"{after['transactions']} of {before['transactions']} transactions and "
            f"{after['reported_holdings']} of {before['reported_holdings']} holdings from "
            f"native source cutoff {original_cutoff}."
        )
        audit_sources[projection["source_id"]] = {
            "candidate_sha256": source_hashes[projection["source_id"]],
            "native_cutoff_at": original_cutoff,
            "input_counts": before,
            "retained_counts": after,
            "filtered_transaction_count": before["transactions"] - after["transactions"],
            "filtered_holding_count": before["reported_holdings"] - after["reported_holdings"],
        }
    return cutoff, {
        "schema_version": "disclosure-cutoff-audit/v1",
        "mode": "last_complete_shared_utc_day",
        "common_cutoff_at": cutoff,
        "sources": audit_sources,
    }


def build_disclosure_candidate(base: dict, source_candidates: Mapping[str, dict], *,
                               harmonize_cutoffs: bool = False,
                               harmonization_audit: dict | None = None) -> dict:
    """Merge complete source candidates into one production candidate.

    ``base`` owns presentation settings and market data.  Its disclosure arrays
    must be empty, making source ownership explicit.  Each mapping key names the
    sole disclosure source to extract from that candidate.  All projections must
    use the same business cutoff.
    """
    if not isinstance(base, dict) or not isinstance(base.get("meta"), dict):
        raise DisclosureCandidateError("Disclosure candidate base must be an object with meta")
    if base["meta"].get("is_demo") is not False:
        raise DisclosureCandidateError("Disclosure candidate base must be production data")
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
    if harmonize_cutoffs and len(source_candidates) > 1 and market_rows:
        raise DisclosureCandidateError(
            "Market data cannot be harmonized without an explicit market cutoff policy")
    base_health = _objects(base.get("source_health"), "Base source_health")

    source_hashes = {source_id: digest(encode(source_candidates[source_id]))
                     for source_id in sorted(source_candidates)}
    projections = [
        project_source_candidate(source_id, source_candidates[source_id])
        for source_id in sorted(source_candidates)
    ]
    cutoffs = {projection["data_cutoff_at"] for projection in projections}
    if len(cutoffs) != 1 and not harmonize_cutoffs:
        raise DisclosureCandidateError("Source candidate cutoffs do not match")
    cutoff_audit = None
    if harmonize_cutoffs and len(projections) > 1:
        cutoff, cutoff_audit = _harmonize_projections(projections, source_hashes)
    else:
        cutoff = next(iter(cutoffs))

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
    included = "、".join(SOURCE_LABELS.get(item["source_id"], item["source_id"])
                         for item in projections)
    result["meta"]["subtitle"] = f"{included}真实披露候选；其他披露来源和行情仍在回填"
    result["people"] = [deepcopy(people_by_id[key]) for key in sorted(people_by_id)]
    result["transactions"] = sorted(deepcopy(merged_transactions), key=lambda row: row["id"])
    result["reported_holdings"] = sorted(deepcopy(merged_holdings), key=lambda row: row["id"])
    result["source_health"] = [deepcopy(base_health_by_id[key])
                               for key in base_health_order + appended]
    if harmonization_audit is not None:
        harmonization_audit.clear()
        harmonization_audit.update(cutoff_audit or {
            "schema_version": "disclosure-cutoff-audit/v1",
            "mode": "strict_equal_cutoff",
            "common_cutoff_at": cutoff,
            "sources": {
                projection["source_id"]: {
                    "candidate_sha256": source_hashes[projection["source_id"]],
                    "native_cutoff_at": projection["data_cutoff_at"],
                    "input_counts": {
                        kind: len(projection[kind])
                        for kind in ("people", "transactions", "reported_holdings")
                    },
                }
                for projection in projections
            },
        })
    return result
