"""Assemble automatically qualified House records into a frontend-compatible candidate input."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json

from .house import HouseIndexError
from .house_ptr import QUALIFICATION_SCHEMA


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


def build_house_candidate(review_root: Path, state_status: dict, base: dict) -> dict:
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
    identities: dict[str, dict] = {}
    seen_transaction_ids: set[str] = set()
    quarantined_count = 0
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

    if len(transactions) != summary.get("qualified_transaction_count") or \
            quarantined_count != summary.get("quarantined_row_count"):
        raise HouseIndexError("House qualification row counts do not match their summary")
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
    meta["subtitle"] = "House真实候选数据；其余披露来源和行情仍在回填"
    candidate["people"] = sorted((_person({"person_id": person_id, **identity})
                                  for person_id, identity in identities.items()), key=lambda row: row["id"])
    candidate["transactions"] = sorted(transactions, key=lambda row: row["id"])
    candidate["reported_holdings"] = []
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
                   f"{quarantined_count} rows quarantined; {summary['failure_count']} parser failures; "
                   f"{state_status['counts']['pending']} filings pending archive."),
    }
    candidate["source_health"] = [house_health if row.get("source_id") == "house_clerk" else row
                                  for row in health]
    if not any(row.get("source_id") == "house_clerk" for row in health):
        candidate["source_health"].append(house_health)
    return candidate


def load_house_candidate(review_root: Path, state_status_path: Path, base_path: Path) -> dict:
    return build_house_candidate(review_root, _read_json(state_status_path, "House source state"),
                                 _read_json(base_path, "House candidate base"))
