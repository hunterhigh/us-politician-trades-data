"""Local Alpaca Basic market-data validation for the frozen dashboard contract.

This module deliberately produces a candidate snapshot only.  It does not publish
market data or relax the production publisher's market-data gate.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
import math
import re
import time as time_module
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


SOURCE_ID = "alpaca_sip_eod"
SOURCE_NAME = "Alpaca SIP EOD"
SOURCE_URL = "https://docs.alpaca.markets/docs/market-data"
API_URL = "https://data.alpaca.markets/v2/stocks/bars"
FEED = "sip"
TIMEFRAME = "1Day"
ADJUSTMENT = "split"
AUDIT_SCHEMA = "alpaca-market-validation/v1"
TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-^/]{0,31}")


class AlpacaMarketError(ValueError):
    """The request or response cannot produce a trustworthy market candidate."""


@dataclass(frozen=True)
class MarketValidation:
    snapshot: dict
    audit: dict


def _timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise AlpacaMarketError(f"Invalid {field}: {value}") from None
    if parsed.tzinfo is None:
        raise AlpacaMarketError(f"{field} requires a timezone")
    return parsed.astimezone(timezone.utc)


def latest_safe_session(checked_at: datetime) -> date:
    """Return a conservative completed U.S. session for delayed Basic data.

    Alpaca Basic with SIP history requires the requested data to be at least
    15 minutes old.  We use 16:30 New York time and move weekends backward.
    Exchange holidays naturally yield no bar and are handled by the response.
    """
    eastern = checked_at.astimezone(ZoneInfo("America/New_York"))
    candidate = eastern.date()
    if eastern.time() < time(16, 30):
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _lookback_start(as_of: date) -> date:
    try:
        two_years_ago = as_of.replace(year=as_of.year - 2)
    except ValueError:  # February 29
        two_years_ago = as_of.replace(year=as_of.year - 2, day=28)
    # More than ten trading days, even across ordinary weekends and holidays.
    return two_years_ago - timedelta(days=21)


def _request_end(as_of: date, checked_at: datetime) -> datetime:
    next_midnight = datetime.combine(as_of + timedelta(days=1), time(), timezone.utc)
    delayed_cutoff = checked_at.astimezone(timezone.utc) - timedelta(minutes=16)
    return min(next_midnight, delayed_cutoff)


class AlpacaMarketClient:
    """Small standard-library client with pagination and credential isolation."""

    def __init__(self, key_id: str, secret_key: str, *, timeout: float = 30.0,
                 retries: int = 3, opener: Callable = urlopen,
                 sleeper: Callable[[float], None] = time_module.sleep):
        if not key_id or not secret_key:
            raise AlpacaMarketError(
                "ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY are required")
        self._headers = {
            "Accept": "application/json",
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret_key,
        }
        self.timeout = timeout
        self.retries = retries
        self._opener = opener
        self._sleeper = sleeper
        self.page_count = 0
        self.request_count = 0

    def _page(self, parameters: dict[str, str]) -> dict:
        request = Request(f"{API_URL}?{urlencode(parameters)}", headers=self._headers)
        if not 0 <= self.retries <= 8:
            raise AlpacaMarketError("retries must be between 0 and 8")
        for attempt in range(self.retries + 1):
            try:
                self.request_count += 1
                with self._opener(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.page_count += 1
                break
            except HTTPError as error:
                if error.code in {429, 500, 502, 503, 504} and attempt < self.retries:
                    self._sleeper(min(2 ** attempt, 8))
                    continue
                # Never echo response bodies, headers, or credential-bearing requests.
                raise AlpacaMarketError(f"Alpaca HTTP {error.code}") from None
            except (URLError, TimeoutError) as error:
                if attempt < self.retries:
                    self._sleeper(min(2 ** attempt, 8))
                    continue
                raise AlpacaMarketError(f"Alpaca request failed: {error}") from None
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise AlpacaMarketError(f"Alpaca response decoding failed: {error}") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("bars"), dict):
            raise AlpacaMarketError("Alpaca response requires an object-valued bars field")
        return payload

    def daily_bars(self, symbols: list[str], *, start: datetime, end: datetime,
                   max_pages: int = 500) -> dict[str, list[dict]]:
        if not symbols:
            return {}
        parameters = {
            "symbols": ",".join(symbols),
            "timeframe": TIMEFRAME,
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "adjustment": ADJUSTMENT,
            "feed": FEED,
            "sort": "asc",
            "limit": "10000",
        }
        result: dict[str, list[dict]] = defaultdict(list)
        seen_tokens: set[str] = set()
        for _ in range(max_pages):
            page = self._page(parameters)
            for symbol, bars in page["bars"].items():
                normalized_symbol = str(symbol).upper()
                if normalized_symbol not in symbols or not isinstance(bars, list):
                    raise AlpacaMarketError("Alpaca returned an unexpected symbol or bars value")
                result[normalized_symbol].extend(bars)
            token = page.get("next_page_token")
            if not token:
                return dict(result)
            if not isinstance(token, str) or token in seen_tokens:
                raise AlpacaMarketError("Alpaca returned an invalid or repeated page token")
            seen_tokens.add(token)
            parameters["page_token"] = token
        raise AlpacaMarketError("Alpaca pagination exceeded the safety limit")


def _symbols_and_names(snapshot: dict) -> tuple[list[str], dict[str, str]]:
    names: dict[str, Counter] = defaultdict(Counter)
    for kind in ("transactions", "reported_holdings"):
        rows = snapshot.get(kind)
        if not isinstance(rows, list):
            raise AlpacaMarketError(f"{kind} must be an array")
        for row in rows:
            if not isinstance(row, dict):
                raise AlpacaMarketError(f"{kind} must contain objects")
            ticker = str(row.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            if not TICKER.fullmatch(ticker):
                raise AlpacaMarketError(f"Invalid ticker in disclosure candidate: {ticker}")
            names[ticker]
            name = str(row.get("asset_name") or "").strip()
            if name:
                names[ticker][name] += 1
    symbols = sorted(names)
    display_names = {
        ticker: (sorted(counts, key=lambda name: (-counts[name], name))[0]
                 if counts else ticker)
        for ticker, counts in names.items()
    }
    return symbols, display_names


def _market_row(ticker: str, name: str, bars: list[dict], start_day: date,
                as_of: date) -> dict:
    points: dict[date, float] = {}
    for bar in bars:
        if not isinstance(bar, dict):
            raise AlpacaMarketError(f"Alpaca bars for {ticker} must contain objects")
        try:
            bar_time = _timestamp(str(bar.get("t") or ""), f"{ticker}.bar.t")
        except AlpacaMarketError:
            raise
        close = bar.get("c")
        if isinstance(close, bool) or not isinstance(close, (int, float)) \
                or not math.isfinite(close) or close <= 0:
            raise AlpacaMarketError(f"Alpaca bar for {ticker} has an invalid close")
        day = bar_time.date()
        if day < start_day or day > as_of:
            continue
        rounded = round(float(close), 4)
        if day in points:
            raise AlpacaMarketError(f"Alpaca returned duplicate daily bars for {ticker} on {day}")
        points[day] = rounded
    history = [{"date": day.isoformat(), "close": close}
               for day, close in sorted(points.items())]
    if not history:
        raise AlpacaMarketError(f"Alpaca returned no usable completed bars for {ticker}")
    return {
        "ticker": ticker,
        "company_name": name,
        "source_id": SOURCE_ID,
        "price_source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "feed": FEED,
        "timeframe": TIMEFRAME,
        "adjustment": ADJUSTMENT,
        "price_history": history,
    }


def build_market_validation(snapshot: dict, *, client: AlpacaMarketClient,
                            checked_at: str, as_of_date: str | None = None,
                            batch_size: int = 50) -> MarketValidation:
    """Fetch delayed SIP daily bars and attach them to a local candidate."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("meta"), dict):
        raise AlpacaMarketError("Snapshot requires a meta object")
    if snapshot["meta"].get("is_demo") is not False:
        raise AlpacaMarketError("Market validation requires an is_demo=false disclosure candidate")
    checked = _timestamp(checked_at, "checked_at")
    safe_session = latest_safe_session(checked)
    meta_cutoff = _timestamp(snapshot["meta"].get("data_cutoff_at"),
                             "meta.data_cutoff_at").date()
    if as_of_date:
        try:
            requested_as_of = date.fromisoformat(as_of_date)
        except ValueError:
            raise AlpacaMarketError(f"Invalid as_of_date: {as_of_date}") from None
        if requested_as_of > safe_session:
            raise AlpacaMarketError(
                f"as_of_date {requested_as_of} exceeds safe completed session {safe_session}")
    else:
        requested_as_of = min(safe_session, meta_cutoff)
    if requested_as_of > meta_cutoff:
        raise AlpacaMarketError(
            f"market as_of_date {requested_as_of} exceeds snapshot cutoff {meta_cutoff}")
    if not 1 <= batch_size <= 200:
        raise AlpacaMarketError("batch_size must be between 1 and 200")

    symbols, names = _symbols_and_names(snapshot)
    if not symbols:
        raise AlpacaMarketError("Disclosure candidate has no ticker-qualified records")
    start_day = _lookback_start(requested_as_of)
    start = datetime.combine(start_day, time(), timezone.utc)
    end = _request_end(requested_as_of, checked)
    rows: list[dict] = []
    missing: list[dict] = []
    for offset in range(0, len(symbols), batch_size):
        batch = symbols[offset:offset + batch_size]
        received = client.daily_bars(batch, start=start, end=end)
        for ticker in batch:
            bars = received.get(ticker, [])
            if not bars:
                missing.append({"ticker": ticker, "reason": "no_bars_returned"})
                continue
            rows.append(_market_row(
                ticker, names[ticker], bars, start_day, requested_as_of))

    if not rows:
        raise AlpacaMarketError("Alpaca returned no usable market rows")

    result = deepcopy(snapshot)
    result["meta"]["subtitle"] = (
        "披露候选 + Alpaca Basic 延迟 SIP 日线；仅用于本地开发验证")
    result["security_market_data"] = sorted(rows, key=lambda row: row["ticker"])
    health = [deepcopy(row) for row in result.get("source_health", [])
              if row.get("source_id") != SOURCE_ID]
    health.append({
        "source_id": SOURCE_ID,
        "source": SOURCE_NAME,
        "source_type": "market_data",
        "source_url": SOURCE_URL,
        "status": "ok" if not missing else "partial",
        "last_checked_at": checked_at,
        "last_successful_sync_at": checked_at,
        "data_cutoff_at": max(
            (row["price_history"][-1]["date"] for row in rows)) + "T23:59:59Z",
        "detail": f"local_basic_validation:{len(rows)}/{len(symbols)}_tickers",
    })
    result["source_health"] = sorted(health, key=lambda row: row["source_id"])

    audit = {
        "schema_version": AUDIT_SCHEMA,
        "source_id": SOURCE_ID,
        "checked_at": checked_at,
        "requested_as_of_date": requested_as_of.isoformat(),
        "start_date": start_day.isoformat(),
        "request_end_at": end.isoformat().replace("+00:00", "Z"),
        "feed": FEED,
        "timeframe": TIMEFRAME,
        "adjustment": ADJUSTMENT,
        "symbol_count": len(symbols),
        "requested_tickers": symbols,
        "market_row_count": len(rows),
        "covered_tickers": [row["ticker"] for row in sorted(rows, key=lambda row: row["ticker"])],
        "price_point_count": sum(len(row["price_history"]) for row in rows),
        "covered_date_min": min(row["price_history"][0]["date"] for row in rows),
        "covered_date_max": max(row["price_history"][-1]["date"] for row in rows),
        "missing_ticker_count": len(missing),
        "missing_tickers": missing,
    }
    if isinstance(getattr(client, "page_count", None), int):
        audit["response_page_count"] = client.page_count
    if isinstance(getattr(client, "request_count", None), int):
        audit["request_attempt_count"] = client.request_count
    return MarketValidation(snapshot=result, audit=audit)
