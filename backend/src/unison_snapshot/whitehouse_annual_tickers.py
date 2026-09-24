"""Persistent, fail-closed ticker enrichment for White House annual trades."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from .alpaca_market import (
    ANNUAL_TRANSACTION_PREFIX,
    TICKER,
    _recover_unique_asset_name_tickers,
)
from .codec import encode


SCHEMA = "whitehouse-annual-ticker-mapping/v2"
ALLOWED_BASES = {
    "alpaca_unique_asset_name",
    "alpaca_unique_classless_asset_name",
}


def is_allowed_ticker_upgrade(before: dict, after: dict) -> bool:
    """Return true only for a first, source-preserving annual ticker mapping."""
    if (not str(before.get("id") or "").startswith(ANNUAL_TRANSACTION_PREFIX) or
            before.get("ticker") is not None or
            before.get("ticker_mapping_basis") is not None or
            not isinstance(after.get("ticker"), str) or
            not TICKER.fullmatch(after["ticker"]) or
            after.get("ticker_mapping_basis") not in ALLOWED_BASES):
        return False
    keys = before.keys() | after.keys()
    return all(before.get(key) == after.get(key)
               for key in keys - {"ticker", "ticker_mapping_basis"})


def _previous_mappings(previous: dict | None) -> dict[str, dict]:
    if previous is None:
        return {}
    rows = previous.get("mappings")
    if previous.get("schema_version") != SCHEMA or not isinstance(rows, list):
        raise ValueError("Previous White House annual ticker mapping is invalid")
    result = {}
    for row in rows:
        if (not isinstance(row, dict) or
                not isinstance(row.get("record_id"), str) or
                not row["record_id"].startswith(ANNUAL_TRANSACTION_PREFIX) or
                not isinstance(row.get("asset_name"), str) or
                not isinstance(row.get("ticker"), str) or
                not TICKER.fullmatch(row["ticker"]) or
                row.get("mapping_basis") not in ALLOWED_BASES or
                row["record_id"] in result):
            raise ValueError("Previous White House annual ticker mapping row is invalid")
        result[row["record_id"]] = row
    return result


def enrich_whitehouse_annual_tickers(
        candidate: dict, assets: object, *, checked_at: str,
        previous: dict | None = None) -> tuple[dict, dict]:
    """Apply sticky unique-name mappings and return their persistent review audit."""
    if not isinstance(candidate, dict) or candidate.get("meta", {}).get("is_demo") is not False:
        raise ValueError("White House annual ticker enrichment requires a production candidate")
    if not isinstance(assets, list) or any(not isinstance(row, dict) for row in assets):
        raise ValueError("White House annual ticker enrichment requires an asset array")
    before = deepcopy(candidate)
    proposed, recovered, current_audit = _recover_unique_asset_name_tickers(candidate, assets)
    proposed_by_id = {row["record_id"]: row for row in recovered}
    prior_by_id = _previous_mappings(previous)
    ambiguous_ids = {row["record_id"] for row in current_audit["ambiguous_records"]}
    if prior_by_id.keys() & ambiguous_ids:
        raise ValueError(
            "A sticky White House annual ticker mapping is now class-ambiguous")
    transaction_by_id = {
        row.get("id"): row for row in proposed.get("transactions", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }

    mappings = []
    retained = 0
    for record_id, prior in prior_by_id.items():
        row = transaction_by_id.get(record_id)
        if row is None or row.get("asset_name") != prior["asset_name"]:
            raise ValueError("A sticky White House annual ticker mapping lost its source row")
        current = proposed_by_id.get(record_id)
        if current is not None and (current["ticker"] != prior["ticker"] or
                                    current["mapping_basis"] != prior["mapping_basis"]):
            raise ValueError("Alpaca asset identity conflicts with a sticky annual ticker mapping")
        row["ticker"] = prior["ticker"]
        row["ticker_mapping_basis"] = prior["mapping_basis"]
        mappings.append(dict(prior))
        retained += 1

    for record_id, row in proposed_by_id.items():
        if record_id in prior_by_id:
            continue
        mappings.append({
            "record_id": record_id,
            "asset_name": row["asset_name"],
            "ticker": row["ticker"],
            "mapping_basis": row["mapping_basis"],
            "provider_asset_name": row["provider_asset_name"],
        })

    mappings.sort(key=lambda row: row["record_id"])
    mapped_ids = {row["record_id"] for row in mappings}
    ambiguous = [row for row in current_audit["ambiguous_records"]
                 if row["record_id"] not in mapped_ids]
    ambiguous_ids = {row["record_id"] for row in ambiguous}
    annual_rows = [row for row in proposed.get("transactions", [])
                   if str(row.get("id") or "").startswith(ANNUAL_TRANSACTION_PREFIX)]
    unmatched = [row for row in annual_rows
                 if not str(row.get("ticker") or "").strip()
                 and row["id"] not in ambiguous_ids]
    for old, new in zip(before.get("transactions", []), proposed.get("transactions", []),
                        strict=True):
        if old.get("id") != new.get("id"):
            raise ValueError("Annual ticker enrichment reordered transactions")
        differences = {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
        if differences - {"ticker", "ticker_mapping_basis"}:
            raise ValueError("Annual ticker enrichment changed non-ticker transaction fields")

    ordered_assets = sorted(assets, key=lambda row: json.dumps(
        row, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    asset_bytes = json.dumps(ordered_assets, ensure_ascii=True, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
    audit = {
        "schema_version": SCHEMA,
        "checked_at": checked_at,
        "candidate_before_sha256": hashlib.sha256(encode(before)).hexdigest(),
        "asset_master_sha256": hashlib.sha256(asset_bytes).hexdigest(),
        "annual_transaction_count": len(annual_rows),
        "mapping_count": len(mappings),
        "retained_mapping_count": retained,
        "new_mapping_count": len(mappings) - retained,
        "mapped_asset_name_count": len({row["asset_name"] for row in mappings}),
        "ambiguous_record_count": len(ambiguous),
        "ambiguous_asset_name_count": len({row["asset_name"] for row in ambiguous}),
        "ambiguous_records": ambiguous,
        "unmatched_record_count": len(unmatched),
        "unmatched_asset_name_count": len({row["asset_name"] for row in unmatched}),
        "mappings": mappings,
    }
    if len(mappings) + len(ambiguous) + len(unmatched) != len(annual_rows):
        raise ValueError("Annual ticker mapping counts do not conserve transactions")
    return proposed, audit
