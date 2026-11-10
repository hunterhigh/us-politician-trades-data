"""One candidate assembly engine for source qualification and annual overlays."""
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from functools import wraps


@dataclass
class CandidatePlan:
    """A source adapter supplies qualified rows and its explicit output policy."""
    base: dict
    arrays: dict = field(default_factory=dict)
    append: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    drop_metadata: tuple = ()
    sort: dict = field(default_factory=dict)
    remove_prefixes: dict = field(default_factory=dict)
    prune_people: bool = False
    health: tuple | None = None
    production: tuple | None = None
    normalization: tuple | None = None
    copy: bool = True


def referenced_people(candidate):
    referenced = {row["person_id"] for name in ("transactions", "reported_holdings")
                  for row in candidate[name]}
    return [person for person in candidate["people"] if person["id"] in referenced]


def _rows(rows):
    return [rows[key] for key in sorted(rows)] if isinstance(rows, Mapping) else list(rows)


def assemble(plan):
    candidate = deepcopy(plan.base) if plan.copy else plan.base
    if plan.production:
        error, message = plan.production
        if not isinstance(candidate.get("meta"), dict) or candidate["meta"].get("is_demo") is not False:
            raise error(message)
    for key in plan.drop_metadata:
        candidate["meta"].pop(key, None)
    if plan.metadata:
        candidate["meta"].update(plan.metadata)
    for name, prefix in plan.remove_prefixes.items():
        candidate[name] = [row for row in candidate[name]
                           if not str(row.get("id", "")).startswith(prefix)]
    if plan.prune_people:
        candidate["people"] = referenced_people(candidate)
    for name, rows in plan.arrays.items():
        candidate[name] = _rows(rows)
    for name, rows in plan.append.items():
        candidate[name].extend(_rows(rows))
    for name, key in plan.sort.items():
        candidate[name].sort(key=lambda row: row[key])
    if plan.health:
        record, error, message = plan.health
        health = candidate.get("source_health")
        if not isinstance(health, list):
            raise error(message)
        source = record["source_id"]
        candidate["source_health"] = [record if row.get("source_id") == source else row for row in health]
        if not any(row.get("source_id") == source for row in health):
            candidate["source_health"].append(record)
    if plan.normalization:
        normalize, error, message = plan.normalization
        if normalize(candidate, allow_production=True,
                     allow_market=bool(candidate["security_market_data"])) != candidate:
            raise error(message)
    return candidate


def candidate_source(adapter):
    """Run source-specific qualification between shared candidate assembly stages."""
    @wraps(adapter)
    def build(*args, **kwargs):
        stages = adapter(*args, **kwargs)
        result = None
        try:
            while True:
                try:
                    plan = stages.send(result)
                except StopIteration as finished:
                    return finished.value
                result = assemble(plan)
        finally:
            stages.close()
    return build


def person_record(identifier, display_name, short_name, role, office_type, source, **fields):
    return {"id": identifier, "display_name": display_name, "short_name": short_name,
            "role": role, "office_type": office_type,
            "chamber": None, "party": None, "state": None,
            "disclosure_authority": source, "priority": False,
            "priority_reason": None, "portrait_url": None, **fields}
