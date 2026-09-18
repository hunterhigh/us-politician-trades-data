#!/usr/bin/env python3
"""Project a disclosure snapshot into bounded, evidence-linked query results."""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path
from typing import Any


def normalize(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def load_snapshot(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read snapshot: {exc}") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("meta"), dict):
        raise ValueError("Snapshot must be an object with meta")
    if not payload["meta"].get("snapshot_id"):
        raise ValueError("Snapshot has no snapshot_id")
    return payload


def person_text(row: dict[str, Any]) -> str:
    person = row.get("person") if isinstance(row.get("person"), dict) else {}
    return normalize(" ".join(str(x or "") for x in (
        person.get("id"), person.get("display_name"), person.get("legal_name"), row.get("person_name")
    )))


def ticker_text(row: dict[str, Any]) -> str:
    return normalize(" ".join(str(x or "") for x in (
        row.get("ticker"), row.get("asset_name"), row.get("issuer_name"), row.get("company")
    )))


def compact_transaction(row: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "id", "filing_id", "person", "owner", "asset_name", "ticker", "asset_type",
        "transaction_type", "transaction_date", "filed_at", "amount_low", "amount_high",
        "position_effect", "position_effect_basis", "source_id", "source", "source_url",
        "disclosure_lag_days", "underlying_return_since_trade", "underlying_return_since_filing",
        "performance_basis", "performance_as_of_date", "price_source_id",
        "verification_status", "is_amendment", "supersedes_filing_id",
    )
    return {key: row.get(key) for key in fields if row.get(key) is not None}


def compact_holding(row: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "id", "filing_id", "person", "owner", "asset_name", "ticker", "asset_type",
        "report_period_end", "filed_at", "value_low", "value_high", "income_type",
        "income_low", "income_high", "change_from_prior", "prior_filing_id", "source",
        "source_id", "source_url", "verification_status",
    )
    return {key: row.get(key) for key in fields if row.get(key) is not None}


def relevant_sources(snapshot: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_ids = {
        str(row.get("source_id") or row.get("price_source_id") or "")
        for row in records
        if row.get("source_id") or row.get("price_source_id")
    }
    all_sources = snapshot.get("source_health") or []
    if not source_ids:
        return all_sources
    return [row for row in all_sources if str(row.get("source_id") or "") in source_ids]


def project(snapshot: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    transactions = [row for row in (snapshot.get("transactions") or snapshot.get("recent_transactions") or []) if isinstance(row, dict)]
    holdings = [row for row in (snapshot.get("reported_holdings") or []) if isinstance(row, dict)]
    priority_people = [row for row in (snapshot.get("priority_people") or []) if isinstance(row, dict)]
    security_market_data = [row for row in (snapshot.get("security_market_data") or []) if isinstance(row, dict)]
    mode = "summary"
    query = None
    if args.person:
        mode, query = "person", normalize(args.person)
        transactions = [row for row in transactions if query in person_text(row)]
        holdings = [row for row in holdings if query in person_text(row)]
        priority_people = [row for row in priority_people if query in person_text(row)]
        matched_tickers = {normalize(row.get("ticker")) for row in transactions + holdings if row.get("ticker")}
        security_market_data = [row for row in security_market_data if normalize(row.get("ticker")) in matched_tickers]
    elif args.ticker:
        mode, query = "ticker", normalize(args.ticker)
        transactions = [row for row in transactions if query in ticker_text(row)]
        holdings = [row for row in holdings if query in ticker_text(row)]
        security_market_data = [row for row in security_market_data if query in ticker_text(row)]
        priority_people = [
            row for row in priority_people
            if any(query in ticker_text(item) for item in (row.get("top_holdings") or []))
            or any(query in ticker_text(item) for item in (row.get("recent_changes") or []))
        ]
    elif args.latest is not None:
        mode, query = "latest", str(args.latest)
        holdings = []
        priority_people = []
        security_market_data = []
    else:
        security_market_data = []

    transactions.sort(key=lambda row: (str(row.get("filed_at") or ""), str(row.get("transaction_date") or "")), reverse=True)
    holdings.sort(key=lambda row: (str(row.get("report_period_end") or ""), str(row.get("filed_at") or "")), reverse=True)
    limit = args.latest if args.latest is not None else args.limit
    limit = min(max(int(limit), 1), 50)
    selected_transactions = transactions[:limit]
    selected_holdings = holdings[:limit]

    transaction_low = sum(int(row.get("amount_low") or 0) for row in transactions)
    transaction_high = sum(int(row.get("amount_high") or 0) for row in transactions)
    holding_low = sum(int(row.get("value_low") or 0) for row in holdings)
    holding_high = sum(int(row.get("value_high") or 0) for row in holdings)
    people = sorted({
        str((row.get("person") or {}).get("display_name") or row.get("person_name") or "")
        for row in transactions + holdings
        if isinstance(row.get("person"), dict) or row.get("person_name")
    })
    tickers = sorted({str(row.get("ticker")) for row in transactions + holdings if row.get("ticker")})
    meta = snapshot.get("meta") or {}
    return {
        "status": "ok" if transactions or holdings or priority_people else "no_matches",
        "mode": mode,
        "query": query,
        "snapshot": {
            "snapshot_id": meta.get("snapshot_id"),
            "data_cutoff_at": meta.get("data_cutoff_at"),
            "timezone": meta.get("timezone"),
            "refresh": meta.get("refresh"),
            "is_demo": bool(meta.get("is_demo")),
        },
        "match_summary": {
            "transaction_record_count": len(transactions),
            "holding_record_count": len(holdings),
            "returned_transaction_count": len(selected_transactions),
            "returned_holding_count": len(selected_holdings),
            "person_count": len(people),
            "tickers": tickers,
            "transaction_amount_low": transaction_low,
            "transaction_amount_high": transaction_high,
            "reported_holding_value_low": holding_low,
            "reported_holding_value_high": holding_high,
            "market_snapshot_count": len(security_market_data),
        },
        "priority_people": priority_people,
        "reported_holdings": [compact_holding(row) for row in selected_holdings],
        "transactions": [compact_transaction(row) for row in selected_transactions],
        "security_market_data": security_market_data,
        "records": [compact_transaction(row) for row in selected_transactions],
        "source_health": relevant_sources(snapshot, selected_transactions + selected_holdings + security_market_data),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--person")
    group.add_argument("--ticker")
    group.add_argument("--latest", type=int)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        result = project(load_snapshot(args.input), args)
    except ValueError as exc:
        parser.exit(2, f"query error: {exc}\n")
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
