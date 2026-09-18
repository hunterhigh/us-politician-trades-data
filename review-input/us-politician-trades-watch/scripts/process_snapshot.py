#!/usr/bin/env python3
"""Validate official-source records and build the canonical dashboard snapshot."""

from __future__ import annotations

import argparse
import json
import math
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "politician-dashboard/v1"

DISCLOSURE_SOURCE_IDS = {"house_clerk", "senate_efd", "oge"}
GUIDANCE_SOURCE_IDS = {"house_ethics_guidance", "senate_ethics_guidance"}
MARKET_SOURCE_ID = "alpaca_sip_eod"
SOURCE_HEALTH_IDS = DISCLOSURE_SOURCE_IDS | GUIDANCE_SOURCE_IDS | {MARKET_SOURCE_ID}
DISCLOSURE_SOURCE_PORTALS = {
    "house_clerk": "https://disclosures-clerk.house.gov/FinancialDisclosure/ViewSearch",
    "senate_efd": "https://efdsearch.senate.gov/search/home/",
    "oge": "https://www.oge.gov/web/OGE.nsf/Officials%20Individual%20Disclosures%20Search%20Collection?OpenForm",
}
PRODUCTION_EFFECT_BASES = {"filing_explicit", "matched_holding_comparison"}
POSITION_EFFECTS = {"new_position", "increase", "reduce", "close", "unknown"}

SOURCE_ALIASES = {
    "house clerk": "house_clerk",
    "house clerk · 模拟": "house_clerk",
    "senate efd": "senate_efd",
    "senate efd · 模拟": "senate_efd",
    "oge": "oge",
    "oge · 模拟": "oge",
    "house ethics": "house_ethics_guidance",
    "senate ethics": "senate_ethics_guidance",
    "alpaca sip eod": MARKET_SOURCE_ID,
    "alpaca sip eod · 模拟": MARKET_SOURCE_ID,
    "模拟 eod 行情": MARKET_SOURCE_ID,
}


class ProcessingError(ValueError):
    """Snapshot violates the source or frontend data contract."""


def normalized(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def parse_date(value: Any, field: str) -> date:
    text = str(value or "").strip()
    if not text:
        raise ProcessingError(f"Missing required date field: {field}")
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        raise ProcessingError(f"Invalid {field}: {text}") from None


def parse_datetime(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ProcessingError(f"Missing required datetime field: {field}")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ProcessingError(f"Invalid {field}: {text}") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def number(value: Any, field: str, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProcessingError(f"{field} must be a finite number")
    return float(value)


def is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def stable_source_id(row: dict[str, Any], *, demo: bool) -> str:
    source_id = normalized(row.get("source_id"))
    if source_id:
        return source_id.replace(" ", "_")
    alias = SOURCE_ALIASES.get(normalized(row.get("source") or row.get("price_source")))
    if alias:
        return alias
    if demo:
        raise ProcessingError(f"Demo row has an unknown source label: {row.get('source') or row.get('price_source')}")
    raise ProcessingError("Production rows require source_id")


def copy_rows(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ProcessingError(f"{field} must be an array")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            raise ProcessingError(f"{field}[{index}] must be an object")
        rows.append(dict(row))
    return rows


def require_https(row: dict[str, Any], field: str) -> None:
    value = str(row.get(field) or "")
    if not value.startswith("https://"):
        raise ProcessingError(f"Production official row {row.get('id', '<unknown>')} requires HTTPS {field}")


def price_point_at_or_after(series: list[dict[str, Any]], target: date) -> tuple[date, float] | None:
    for point in series:
        point_date = parse_date(point.get("date"), "price_history.date")
        if point_date >= target:
            return point_date, float(point["close"])
    return None


def price_at_or_after(series: list[dict[str, Any]], target: date) -> float | None:
    point = price_point_at_or_after(series, target)
    return point[1] if point else None


def price_at_or_before(series: list[dict[str, Any]], target: date) -> tuple[date, float] | None:
    result: tuple[date, float] | None = None
    for point in series:
        point_date = parse_date(point.get("date"), "price_history.date")
        if point_date > target:
            break
        result = point_date, float(point["close"])
    return result


def return_pct(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline is None or baseline <= 0:
        return None
    return round((current / baseline - 1) * 100, 2)


def prior_quarter_end(as_of: date) -> date:
    quarter_start_month = ((as_of.month - 1) // 3) * 3 + 1
    return date(as_of.year, quarter_start_month, 1) - timedelta(days=1)


def prepare_people(rows: list[dict[str, Any]], *, demo: bool) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    demo_priority_ranks: set[int] = set()
    for row in rows:
        person_id = str(row.get("id") or "").strip()
        if not person_id or not row.get("display_name"):
            raise ProcessingError("Every person requires id and display_name")
        if person_id in by_id:
            raise ProcessingError(f"Duplicate person id: {person_id}")
        row["id"] = person_id
        row.setdefault("short_name", str(row["display_name"]).split()[-1])
        row.setdefault("role", row.get("chamber") or row.get("office_type") or "公职人员")
        row.setdefault("party", "—")
        row.setdefault("state", "—")
        priority_fixture_fields = ("demo_priority_rank", "demo_priority_count", "demo_priority_group")
        if any(field in row for field in priority_fixture_fields):
            if not demo:
                raise ProcessingError("demo_priority_* fields are forbidden in production snapshots")
            if not row.get("priority"):
                raise ProcessingError(f"Person {person_id} demo priority fixture requires priority=true")
            rank_value = number(row.get("demo_priority_rank"), "demo_priority_rank")
            count_value = number(row.get("demo_priority_count"), "demo_priority_count")
            rank = int(rank_value)
            count = int(count_value)
            group = str(row.get("demo_priority_group") or "").strip()
            if rank_value != rank or rank <= 0 or count_value != count or count < 0 or not group:
                raise ProcessingError(f"Person {person_id} has an invalid demo priority fixture")
            if rank in demo_priority_ranks:
                raise ProcessingError(f"Duplicate demo priority rank: {rank}")
            demo_priority_ranks.add(rank)
            row["demo_priority_rank"] = rank
            row["demo_priority_count"] = count
            row["demo_priority_group"] = group
        reference = row.get("demo_reference_snapshot")
        if reference is not None:
            if not demo:
                raise ProcessingError("demo_reference_snapshot is forbidden in production snapshots")
            if not isinstance(reference, dict):
                raise ProcessingError(f"Person {person_id} demo_reference_snapshot must be an object")
            counts = {
                field: int(number(reference.get(field), f"demo_reference_snapshot.{field}"))
                for field in ("disclosed_transactions", "buy_count", "sell_count")
            }
            if min(counts.values()) < 0 or counts["buy_count"] + counts["sell_count"] != counts["disclosed_transactions"]:
                raise ProcessingError(f"Person {person_id} demo reference buy/sell counts must equal disclosed_transactions")
            if not str(reference.get("reference_url") or "").startswith("https://"):
                raise ProcessingError(f"Person {person_id} demo reference requires HTTPS reference_url")
            if not str(reference.get("reference_source") or "").strip():
                raise ProcessingError(f"Person {person_id} demo reference requires reference_source")
            parse_date(reference.get("reference_captured_at"), "demo_reference_snapshot.reference_captured_at")
            number(reference.get("estimated_total_usd"), "demo_reference_snapshot.estimated_total_usd")
            number(reference.get("median_disclosure_lag_days"), "demo_reference_snapshot.median_disclosure_lag_days")
            performance_sample = number(reference.get("performance_sample_1y"), "demo_reference_snapshot.performance_sample_1y")
            direction_match = number(reference.get("direction_match_pct"), "demo_reference_snapshot.direction_match_pct")
            number(reference.get("median_return_1y_pct"), "demo_reference_snapshot.median_return_1y_pct")
            if performance_sample < 0 or not 0 <= direction_match <= 100:
                raise ProcessingError(f"Person {person_id} demo reference performance values are invalid")
            parse_date(reference.get("latest_filing_date"), "demo_reference_snapshot.latest_filing_date")
            top_tickers = reference.get("top_tickers")
            if not isinstance(top_tickers, list) or not top_tickers:
                raise ProcessingError(f"Person {person_id} demo reference requires top_tickers")
            for item in top_tickers:
                if not isinstance(item, dict) or not str(item.get("ticker") or "").strip():
                    raise ProcessingError(f"Person {person_id} demo reference contains an invalid top ticker")
                ticker_counts = [
                    int(number(item.get(field), f"demo_reference_snapshot.top_tickers.{field}"))
                    for field in ("transaction_count", "buy_count", "sell_count")
                ]
                if min(ticker_counts) < 0 or ticker_counts[1] + ticker_counts[2] != ticker_counts[0]:
                    raise ProcessingError(f"Person {person_id} demo reference ticker counts do not reconcile")
            annual_activity = reference.get("annual_activity")
            if not isinstance(annual_activity, list) or not annual_activity:
                raise ProcessingError(f"Person {person_id} demo reference requires annual_activity")
            annual_buys = annual_sells = 0
            for item in annual_activity:
                if not isinstance(item, dict):
                    raise ProcessingError(f"Person {person_id} demo reference contains invalid annual activity")
                number(item.get("year"), "demo_reference_snapshot.annual_activity.year")
                annual_buys += int(number(item.get("buy_count"), "demo_reference_snapshot.annual_activity.buy_count"))
                annual_sells += int(number(item.get("sell_count"), "demo_reference_snapshot.annual_activity.sell_count"))
            if annual_buys != counts["buy_count"] or annual_sells != counts["sell_count"]:
                raise ProcessingError(f"Person {person_id} demo reference annual activity does not reconcile")
            notable_transactions = reference.get("notable_transactions")
            if not isinstance(notable_transactions, list):
                raise ProcessingError(f"Person {person_id} demo reference requires notable_transactions")
            for item in notable_transactions:
                if not isinstance(item, dict) or item.get("transaction_type") not in {"purchase", "sale"}:
                    raise ProcessingError(f"Person {person_id} demo reference contains an invalid notable transaction")
                if not str(item.get("ticker") or "").strip() or not str(item.get("company") or "").strip():
                    raise ProcessingError(f"Person {person_id} demo reference notable transaction requires ticker and company")
                low = number(item.get("amount_low"), "demo_reference_snapshot.notable_transactions.amount_low")
                high = number(item.get("amount_high"), "demo_reference_snapshot.notable_transactions.amount_high")
                if low < 0 or high < low:
                    raise ProcessingError(f"Person {person_id} demo reference notable transaction has an invalid amount range")
                parse_date(item.get("transaction_date"), "demo_reference_snapshot.notable_transactions.transaction_date")
                parse_date(item.get("filed_date"), "demo_reference_snapshot.notable_transactions.filed_date")
                number(item.get("lag_days"), "demo_reference_snapshot.notable_transactions.lag_days")
                number(item.get("display_return_pct"), "demo_reference_snapshot.notable_transactions.display_return_pct")
        by_id[person_id] = row
    return rows, by_id


def prepare_disclosure_rows(
    rows: list[dict[str, Any]],
    people: dict[str, dict[str, Any]],
    *,
    kind: str,
    demo: bool,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    prepared: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        row_id = str(row.get("id") or (f"SIM-{kind.upper()}-{index:04d}" if demo else "")).strip()
        if not row_id or row_id in seen:
            raise ProcessingError(f"{kind} rows require unique id values")
        seen.add(row_id)
        row["id"] = row_id
        person_id = str(row.get("person_id") or ((row.get("person") or {}).get("id") if isinstance(row.get("person"), dict) else ""))
        if person_id not in people:
            raise ProcessingError(f"{kind} {row_id} references unknown person_id {person_id}")
        row["person_id"] = person_id
        row["person"] = {
            "id": person_id,
            "display_name": people[person_id]["display_name"],
            "chamber": people[person_id].get("chamber") or people[person_id].get("role"),
            "party": people[person_id].get("party"),
            "state": people[person_id].get("state"),
        }
        source_id = stable_source_id(row, demo=demo)
        if source_id not in DISCLOSURE_SOURCE_IDS:
            raise ProcessingError(f"Forbidden {kind} source_id: {source_id}")
        row["source_id"] = source_id

        status = str(row.get("verification_status") or "")
        if demo:
            if not status:
                status = "simulated"
                row["verification_status"] = status
            if status != "simulated":
                raise ProcessingError(f"Demo {kind} {row_id} must use verification_status=simulated")
        else:
            if status != "official_matched":
                raise ProcessingError(f"Production {kind} {row_id} must be official_matched")
            require_https(row, "source_url")

        if kind == "transaction":
            row.setdefault("instrument_type", row.get("asset_type") or "Stock")
            transaction_date = parse_date(row.get("transaction_date"), "transaction_date")
            filed_date = parse_date(row.get("filed_at"), "filed_at")
            row["disclosure_lag_days"] = (filed_date - transaction_date).days
            if row["disclosure_lag_days"] < 0:
                raise ProcessingError(f"Transaction {row_id} was filed before its transaction date")
            effect = str(row.get("position_effect") or "unknown")
            basis = str(row.get("position_effect_basis") or "")
            if effect not in POSITION_EFFECTS:
                raise ProcessingError(f"Invalid position_effect on {row_id}: {effect}")
            allowed_bases = PRODUCTION_EFFECT_BASES | ({"simulated"} if demo else set())
            if effect != "unknown" and basis not in allowed_bases:
                effect, basis = "unknown", ""
            row["position_effect"] = effect
            row["position_effect_basis"] = basis or None
            for key in ("amount_low", "amount_high"):
                row[key] = int(number(row.get(key, 0), f"{kind}.{key}"))
            if row["amount_low"] > row["amount_high"]:
                raise ProcessingError(f"Transaction {row_id} has an inverted amount range")
        else:
            row.setdefault("instrument_type", row.get("asset_type") or "Stock")
            parse_date(row.get("report_period_end"), "report_period_end")
            if demo and not row.get("filed_at"):
                row["filed_at"] = row["report_period_end"]
            parse_date(row.get("filed_at"), "filed_at")
            for key in ("value_low", "value_high"):
                row[key] = int(number(row.get(key, 0), f"{kind}.{key}"))
            if row["value_low"] > row["value_high"]:
                raise ProcessingError(f"Holding {row_id} has an inverted value range")
        prepared.append(row)
    return prepared


def prepare_market(rows: list[dict[str, Any]], *, demo: bool) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    prepared: list[dict[str, Any]] = []
    by_ticker: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").upper().strip()
        if not ticker or ticker in by_ticker:
            raise ProcessingError("security_market_data requires unique ticker values")
        row["ticker"] = ticker
        row["source_id"] = stable_source_id(row, demo=demo)
        if row["source_id"] != MARKET_SOURCE_ID:
            raise ProcessingError(f"Forbidden market source_id: {row['source_id']}")
        if demo:
            row["price_source"] = "Alpaca SIP EOD · 模拟"
        if not demo and row.get("adjustment") != "split":
            raise ProcessingError(f"Market row {ticker} must declare adjustment=split")
        raw_series = row.get("price_history")
        series = copy_rows(raw_series, f"security_market_data[{ticker}].price_history") if raw_series is not None else []
        if not series and not demo:
            raise ProcessingError(f"Market row {ticker} has no price history")
        if not series:
            parse_date(row.get("as_of_date"), "as_of_date")
            row["current_price"] = round(float(number(row.get("current_price"), "current_price")), 4)
            row["previous_quarter_end_price"] = round(float(number(row.get("previous_quarter_end_price"), "previous_quarter_end_price")), 4)
            row["price_history"] = []
            row["quarter_change_pct"] = return_pct(row["current_price"], row["previous_quarter_end_price"])
            row["adjustment"] = "split"
            row["timeframe"] = "1Day"
            row["feed"] = "sip"
            prepared.append(row)
            by_ticker[ticker] = row
            continue
        dates: list[date] = []
        for point in series:
            dates.append(parse_date(point.get("date"), "price_history.date"))
            point["close"] = round(float(number(point.get("close"), "price_history.close")), 4)
            if point["close"] <= 0:
                raise ProcessingError(f"Market row {ticker} contains a non-positive close")
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise ProcessingError(f"Market row {ticker} price history must be unique and ascending")
        row["price_history"] = series
        row["as_of_date"] = dates[-1].isoformat()
        row["current_price"] = series[-1]["close"]
        quarter_target = prior_quarter_end(dates[-1])
        quarter_point = price_at_or_before(series, quarter_target)
        row["previous_quarter_end"] = quarter_target.isoformat()
        row["previous_quarter_end_price"] = quarter_point[1] if quarter_point else None
        row["quarter_change_pct"] = return_pct(row["current_price"], row["previous_quarter_end_price"])
        row["adjustment"] = "split"
        row["timeframe"] = "1Day"
        row["feed"] = "sip"
        prepared.append(row)
        by_ticker[ticker] = row
    return prepared, by_ticker


def enrich_transaction_prices(rows: list[dict[str, Any]], market: dict[str, dict[str, Any]], *, demo: bool) -> None:
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        snapshot = market.get(ticker)
        if not snapshot or not snapshot.get("price_history"):
            if demo and snapshot:
                row.setdefault("performance_eligible", is_finite_number(row.get("underlying_return_since_filing")))
                row.setdefault("performance_basis", "simulated")
                row.setdefault("performance_as_of_date", snapshot.get("as_of_date"))
                row.setdefault("price_source_id", MARKET_SOURCE_ID)
                continue
            row["underlying_return_since_trade"] = None
            row["underlying_return_since_filing"] = None
            row["performance_eligible"] = False
            row["performance_ineligible_reason"] = "missing_market_series"
            continue
        series = snapshot["price_history"]
        current = float(snapshot["current_price"])
        trade_close = price_at_or_after(series, parse_date(row["transaction_date"], "transaction_date"))
        filing_close = price_at_or_after(series, parse_date(row["filed_at"], "filed_at"))
        row["underlying_return_since_trade"] = return_pct(current, trade_close)
        row["underlying_return_since_filing"] = return_pct(current, filing_close)
        row["performance_eligible"] = filing_close is not None
        row["performance_ineligible_reason"] = None if filing_close is not None else "filing_after_price_cutoff"
        row["performance_basis"] = "split_adjusted_sip_eod_close_on_or_after_event_date"
        row["performance_as_of_date"] = snapshot["as_of_date"]
        row["price_source_id"] = MARKET_SOURCE_ID


def cutoff_date(meta: dict[str, Any], transactions: list[dict[str, Any]], market: list[dict[str, Any]]) -> date:
    if meta.get("data_cutoff_at"):
        return parse_date(meta["data_cutoff_at"], "meta.data_cutoff_at")
    if market:
        return max(parse_date(row["as_of_date"], "as_of_date") for row in market)
    if transactions:
        return max(parse_date(row["filed_at"], "filed_at") for row in transactions)
    raise ProcessingError("Snapshot needs data_cutoff_at or at least one dated record")


def rows_in_window(rows: Iterable[dict[str, Any]], cutoff: date, days: int, field: str) -> list[dict[str, Any]]:
    start = cutoff - timedelta(days=days - 1)
    return [row for row in rows if start <= parse_date(row.get(field), field) <= cutoff]


def rows_in_hours(rows: Iterable[dict[str, Any]], cutoff: datetime, hours: int, field: str) -> list[dict[str, Any]]:
    start = cutoff - timedelta(hours=hours)
    return [row for row in rows if start <= parse_datetime(row.get(field), field).astimezone(cutoff.tzinfo) <= cutoff]


def build_market_moves(rows: list[dict[str, Any]], market: dict[str, dict[str, Any]], cutoff: date, days: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows_in_window(rows, cutoff, days, "transaction_date"):
        if row.get("ticker"):
            grouped[str(row["ticker"]).upper()].append(row)
    result: list[dict[str, Any]] = []
    for ticker, items in grouped.items():
        snapshot = market.get(ticker)
        first_date = min(parse_date(item["transaction_date"], "transaction_date") for item in items)
        baseline_point = price_point_at_or_after(snapshot["price_history"], first_date) if snapshot and snapshot.get("price_history") else None
        baseline = baseline_point[1] if baseline_point else None
        current = float(snapshot["current_price"]) if snapshot else None
        fallback_return = next((item.get("underlying_return_since_trade") for item in sorted(items, key=lambda item: item["transaction_date"]) if is_finite_number(item.get("underlying_return_since_trade"))), None)
        result.append({
            "ticker": ticker,
            "transaction_count": len(items),
            "people_count": len({item["person_id"] for item in items}),
            "buy_count": sum(item.get("transaction_type") == "purchase" for item in items),
            "sell_count": sum(item.get("transaction_type") == "sale" for item in items),
            "amount_low": sum(int(item.get("amount_low") or 0) for item in items),
            "amount_high": sum(int(item.get("amount_high") or 0) for item in items),
            "return_since_first_trade": return_pct(current, baseline) if baseline is not None else fallback_return,
            "first_transaction_date": first_date.isoformat(),
            "return_baseline_date": (baseline_point[0] if baseline_point else first_date).isoformat() if baseline is not None or fallback_return is not None else None,
            "price_as_of_date": snapshot.get("as_of_date") if snapshot else None,
            "price_source_id": MARKET_SOURCE_ID if snapshot else None,
        })
    return sorted(result, key=lambda row: (-row["transaction_count"], row["ticker"]))


def build_priority_people(
    people: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    holdings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for person in people:
        if not person.get("priority"):
            continue
        person_holdings = [row for row in holdings if row["person_id"] == person["id"]]
        person_transactions = [row for row in transactions if row["person_id"] == person["id"]]
        periods = [row["report_period_end"] for row in person_holdings]
        latest_period = max(periods) if periods else None
        latest_holdings = [row for row in person_holdings if row["report_period_end"] == latest_period]
        result.append({
            "person": {
                "id": person["id"],
                "display_name": person["display_name"],
                "office_type": person.get("office_type") or person.get("role"),
                "chamber": person.get("chamber"),
                "party": person.get("party"),
                "state": person.get("state"),
            },
            "priority_reason": person.get("priority_reason") or "configured_priority_person",
            "source_authority": latest_holdings[0].get("source") if latest_holdings else None,
            "data_status": "available" if latest_holdings or person_transactions else "no_records",
            "latest_report_period_end": latest_period,
            "reported_value_low": sum(int(row.get("value_low") or 0) for row in latest_holdings),
            "reported_value_high": sum(int(row.get("value_high") or 0) for row in latest_holdings),
            "reported_holding_count": len(latest_holdings),
            "top_holdings": sorted(latest_holdings, key=lambda row: int(row.get("value_high") or 0), reverse=True)[:5],
            "recent_changes": sorted(person_transactions, key=lambda row: (row["filed_at"], row["transaction_date"]), reverse=True)[:5],
        })
    return result


def build_top_people(people: list[dict[str, Any]], transactions: list[dict[str, Any]], cutoff: date) -> list[dict[str, Any]]:
    last_30 = rows_in_window(transactions, cutoff, 30, "transaction_date")
    last_7_filings = rows_in_window(transactions, cutoff, 7, "filed_at")
    counts = Counter(row["person_id"] for row in last_30)
    new_counts = Counter(row["person_id"] for row in last_7_filings)
    by_id = {row["id"]: row for row in people}
    result = []
    for person_id, count in counts.most_common():
        person = by_id[person_id]
        result.append({
            "id": person_id,
            "display_name": person["display_name"],
            "chamber": person.get("chamber") or person.get("role"),
            "party": person.get("party"),
            "state": person.get("state"),
            "transactions": count,
            "new_filings": new_counts[person_id],
        })
    return result


def build_top_tickers(transactions: list[dict[str, Any]], cutoff: date) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows_in_window(transactions, cutoff, 30, "transaction_date"):
        if row.get("ticker"):
            groups[str(row["ticker"]).upper()].append(row)
    result = []
    for ticker, items in groups.items():
        result.append({
            "ticker": ticker,
            "asset_name": next((item.get("asset_name") for item in items if item.get("asset_name")), ticker),
            "transaction_count": len(items),
            "people_count": len({item["person_id"] for item in items}),
            "amount_low": sum(int(item.get("amount_low") or 0) for item in items),
            "amount_high": sum(int(item.get("amount_high") or 0) for item in items),
        })
    return sorted(result, key=lambda row: (-row["transaction_count"], row["ticker"]))


def build_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("meta"), dict):
        raise ProcessingError("Snapshot must be an object with meta")
    result = dict(payload)
    meta = dict(payload["meta"])
    demo = meta.get("is_demo") is True
    if not meta.get("snapshot_id"):
        raise ProcessingError("Snapshot meta requires snapshot_id")
    meta["schema_version"] = SCHEMA_VERSION
    meta["source_policy"] = "official_disclosures_only"
    meta["market_price_policy"] = "alpaca_sip_split_adjusted_completed_eod"

    people, people_by_id = prepare_people(copy_rows(payload.get("people"), "people"), demo=demo)
    transactions = prepare_disclosure_rows(
        copy_rows(payload.get("transactions"), "transactions"), people_by_id, kind="transaction", demo=demo
    )
    holdings = prepare_disclosure_rows(
        copy_rows(payload.get("reported_holdings"), "reported_holdings"), people_by_id, kind="holding", demo=demo
    )
    market_rows, market_by_ticker = prepare_market(
        copy_rows(payload.get("security_market_data"), "security_market_data"), demo=demo
    )
    source_health = copy_rows(payload.get("source_health"), "source_health")
    for row in source_health:
        row["source_id"] = stable_source_id(row, demo=demo)
        if row["source_id"] not in SOURCE_HEALTH_IDS:
            raise ProcessingError(f"Forbidden source_health source_id: {row['source_id']}")
        if row["source_id"] in DISCLOSURE_SOURCE_IDS:
            row["source_type"] = "official_disclosure"
            row["source_url"] = row.get("source_url") or DISCLOSURE_SOURCE_PORTALS[row["source_id"]]
    if demo:
        default_names = {
            "house_ethics_guidance": "House Ethics 规则 · 模拟状态",
            "senate_ethics_guidance": "Senate Ethics 规则 · 模拟状态",
            MARKET_SOURCE_ID: "Alpaca SIP EOD · 模拟",
        }
        existing_ids = {row["source_id"] for row in source_health}
        for source_id, name in default_names.items():
            if source_id not in existing_ids:
                source_health.append({
                    "source_id": source_id,
                    "source": name,
                    "status": "ok",
                    "last_checked_at": meta.get("data_cutoff_at") or meta.get("generated_at"),
                })

    transactions.sort(key=lambda row: (row["filed_at"], row["transaction_date"], row["id"]), reverse=True)
    holdings.sort(key=lambda row: (row["report_period_end"], row["filed_at"], row["id"]), reverse=True)
    enrich_transaction_prices(transactions, market_by_ticker, demo=demo)
    cutoff = cutoff_date(meta, transactions, market_rows)
    meta["data_cutoff_at"] = payload["meta"].get("data_cutoff_at") or f"{cutoff.isoformat()}T23:59:59Z"

    cutoff_dt = parse_datetime(meta["data_cutoff_at"], "meta.data_cutoff_at")
    filings_24h = rows_in_hours(transactions, cutoff_dt, 24, "filed_at")
    filings_7d = rows_in_window(transactions, cutoff, 7, "filed_at")
    tx_30 = rows_in_window(transactions, cutoff, 30, "transaction_date")
    official_count = sum(row.get("verification_status") == "official_matched" for row in transactions + holdings)
    total_records = len(transactions) + len(holdings)
    activity_counts = Counter(parse_date(row["filed_at"], "filed_at").isoformat() for row in transactions)

    result.update({
        "meta": meta,
        "people": people,
        "transactions": transactions,
        "reported_holdings": holdings,
        "security_market_data": market_rows,
        "source_health": source_health,
        "summary": {
            "new_filings_24h": len(filings_24h),
            "new_filings_7d": len(filings_7d),
            "transactions_30d": len(tx_30),
            "amount_low_30d": sum(int(row.get("amount_low") or 0) for row in tx_30),
            "amount_high_30d": sum(int(row.get("amount_high") or 0) for row in tx_30),
            "tracked_people": len(people),
            "official_match_rate": round(official_count / total_records, 4) if total_records else None,
        },
        "activity_by_day": [{"date": day, "filing_count": count} for day, count in sorted(activity_counts.items())],
        "priority_people": build_priority_people(people, transactions, holdings),
        "top_people": build_top_people(people, transactions, cutoff),
        "top_tickers": build_top_tickers(transactions, cutoff),
        "recent_transactions": transactions,
        "market_moves": {
            "30": build_market_moves(transactions, market_by_ticker, cutoff, 30),
            "90": build_market_moves(transactions, market_by_ticker, cutoff, 90),
        },
        "processing": {
            "schema_version": SCHEMA_VERSION,
            "ordinary_aggregate_verification_statuses": ["simulated"] if demo else ["official_matched"],
            "disclosure_source_ids": sorted(DISCLOSURE_SOURCE_IDS),
            "market_source_id": MARKET_SOURCE_ID,
            "position_effect_bases": sorted(PRODUCTION_EFFECT_BASES | ({"simulated"} if demo else set())),
        },
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = build_snapshot(payload)
    except (OSError, json.JSONDecodeError, ProcessingError) as exc:
        parser.exit(2, f"processing error: {exc}\n")
    text = json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":")) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(json.dumps({"snapshot_id": result["meta"]["snapshot_id"], "output": str(args.output.resolve())}, ensure_ascii=False))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
