"""Audit a review candidate rebuild for additive Trump White House facts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from unison_snapshot.codec import encode


SCHEMA = "whitehouse-trump-candidate-transition-audit/v1"
TRUMP_PERSON_ID = "oge:076544f8ba0638cf"
PUBLISHED_TRUMP_HOLDING_ID = "wh-annual:8b95ffd94c805396cc7aa1d0"
ARRAYS = ("people", "transactions", "reported_holdings",
          "security_market_data", "source_health")


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Candidate is not an object: {path}")
    return value


def _ids(value: dict, name: str, key: str) -> dict[str, dict]:
    rows = value.get(name)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Candidate {name} array is invalid")
    values = [row.get(key) for row in rows]
    if any(not isinstance(item, str) or not item for item in values) or len(
            set(values)) != len(values):
        raise ValueError(f"Candidate {name} identities are missing or duplicated")
    return dict(zip(values, rows, strict=True))


def _official_whitehouse_pdf(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (parsed.scheme == "https" and
            parsed.netloc.casefold() in {"whitehouse.gov", "www.whitehouse.gov"} and
            parsed.username is None and parsed.password is None and not parsed.fragment and
            not parsed.query and
            parsed.path.startswith("/wp-content/uploads/") and
            parsed.path.casefold().endswith(".pdf"))


def _require_unchanged_subset(before: dict[str, dict], after: dict[str, dict],
                              label: str) -> None:
    removed = sorted(before.keys() - after.keys())
    changed = sorted(key for key in before.keys() & after.keys()
                     if before[key] != after[key])
    if removed or changed:
        raise ValueError(
            f"Trump rebuild removed or changed existing {label}: {removed or changed}")


def audit_transition(before: dict, after: dict,
                     before_oge: dict, after_oge: dict) -> dict:
    for value in (before, after, before_oge, after_oge):
        if value.get("meta", {}).get("is_demo") is not False:
            raise ValueError("Candidate transition requires non-demo snapshots")
        for name in ARRAYS:
            if not isinstance(value.get(name), list):
                raise ValueError(f"Candidate is missing the {name} array")
        if value["security_market_data"]:
            raise ValueError("Review disclosure candidates cannot contain market data")

    before_people = _ids(before, "people", "id")
    after_people = _ids(after, "people", "id")
    before_transactions = _ids(before, "transactions", "id")
    after_transactions = _ids(after, "transactions", "id")
    before_holdings = _ids(before, "reported_holdings", "id")
    after_holdings = _ids(after, "reported_holdings", "id")
    before_oge_transactions = _ids(before_oge, "transactions", "id")
    after_oge_transactions = _ids(after_oge, "transactions", "id")
    before_oge_holdings = _ids(before_oge, "reported_holdings", "id")
    after_oge_holdings = _ids(after_oge, "reported_holdings", "id")

    if not before_people.keys() <= after_people.keys():
        raise ValueError("Candidate rebuild removed an existing person")
    _require_unchanged_subset(before_transactions, after_transactions, "transactions")
    _require_unchanged_subset(before_oge_transactions, after_oge_transactions,
                              "OGE transactions")
    removed = sorted(before_holdings.keys() - after_holdings.keys())
    removed_oge = sorted(before_oge_holdings.keys() - after_oge_holdings.keys())
    if removed or removed_oge:
        raise ValueError(f"Trump annual rebuild removed holdings: {removed or removed_oge}")
    if PUBLISHED_TRUMP_HOLDING_ID not in after_holdings:
        raise ValueError("Previously published Trump holding identity disappeared")

    added = sorted(after_holdings.keys() - before_holdings.keys())
    added_oge = sorted(after_oge_holdings.keys() - before_oge_holdings.keys())
    if added != added_oge:
        raise ValueError("Unified holding delta does not match the OGE source delta")
    if any(after_holdings[item].get("person_id") != TRUMP_PERSON_ID or
           after_holdings[item].get("source_id") != "oge" or
           after_holdings[item].get("verification_status") != "official_matched"
           for item in added):
        raise ValueError("Candidate rebuild added a non-Trump or non-official holding")
    added_transactions = sorted(after_transactions.keys() - before_transactions.keys())
    added_oge_transactions = sorted(
        after_oge_transactions.keys() - before_oge_transactions.keys())
    if added_transactions != added_oge_transactions:
        raise ValueError("Unified transaction delta does not match the OGE source delta")
    if any(after_transactions[item] != after_oge_transactions[item]
           for item in added_transactions):
        raise ValueError("Unified transaction rows differ from the OGE source rows")
    if any(after_transactions[item].get("person_id") != TRUMP_PERSON_ID or
           after_transactions[item].get("source_id") != "oge" or
           after_transactions[item].get("verification_status") != "official_matched" or
           not str(after_transactions[item].get("filing_id", "")).startswith("wh-url:") or
           not _official_whitehouse_pdf(after_transactions[item].get("source_url"))
           for item in added_transactions):
        raise ValueError("Candidate rebuild added a non-Trump or non-official transaction")
    cross_kind = set(after_transactions) & set(after_holdings)
    if cross_kind:
        raise ValueError("Candidate contains a cross-kind fact identity collision")

    return {
        "schema_version": SCHEMA,
        "before_candidate_sha256": hashlib.sha256(encode(before)).hexdigest(),
        "after_candidate_sha256": hashlib.sha256(encode(after)).hexdigest(),
        "before_counts": {name: len(before[name]) for name in ARRAYS},
        "after_counts": {name: len(after[name]) for name in ARRAYS},
        "added_holding_count": len(added),
        "added_holding_ids": added,
        "removed_holding_count": 0,
        "added_transaction_count": len(added_transactions),
        "added_transaction_ids": added_transactions,
        "removed_transaction_count": 0,
        "transaction_delta_count": len(added_transactions),
        "duplicate_person_id_count": 0,
        "duplicate_transaction_id_count": 0,
        "duplicate_holding_id_count": 0,
        "cross_kind_fact_id_collision_count": 0,
        "published_trump_holding_retained": True,
        "oge_holding_delta_matches_unified": True,
        "oge_transaction_delta_matches_unified": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--before-oge", type=Path, required=True)
    parser.add_argument("--after-oge", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_transition(_load(args.before), _load(args.after),
                              _load(args.before_oge), _load(args.after_oge))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encode(result))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
