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
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .market_store import (MARKET_COVERAGE_SCHEMA, UNSUPPORTED_REASONS,
                           PublishedMarketCache)


SOURCE_ID = "alpaca_sip_eod"
SOURCE_NAME = "Alpaca SIP EOD"
SOURCE_URL = "https://docs.alpaca.markets/docs/market-data"
API_URL = "https://data.alpaca.markets/v2/stocks/bars"
ASSETS_URL = "https://paper-api.alpaca.markets/v2/assets"
FEED = "sip"
TIMEFRAME = "1Day"
ADJUSTMENT = "split"
MAX_ACTIVE_STALENESS_DAYS = 7
MAX_CACHE_AGE_DAYS = 7
CACHE_OVERLAP_DAYS = 45
FULL_REFRESH_INTERVAL_DAYS = 30
AUDIT_SCHEMA = "alpaca-market-validation/v2"
TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-^/]{0,31}")
EXPLICIT_NAME_TICKER = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,9})\)\s*$")
SIP_EXCHANGES = {"AMEX", "ARCA", "BATS", "NASDAQ", "NYSE", "NYSEARCA"}
# Official filings occasionally place a foreign-exchange code in the ticker field.
# Keep this exception explicit: symbol length alone is not enough to classify a
# security as foreign and must never turn an unresolved code into a silent skip.
KNOWN_OUTSIDE_SIP_SYMBOLS = {"COLPAL": "outside_sip_foreign_exchange"}
ANNUAL_TRANSACTION_PREFIX = "wh-annual-tx:"
ASSET_NAME_TOKEN_ALIASES = {
    "AMER": "AMERICA",
    "COMMUN": "COMMUNICATIONS",
    "COR": "CORPORATION",
    "COS": "COMPANIES",
    "ELEC": "ELECTRIC",
    "FINL": "FINANCIAL",
    "GRP": "GROUP",
    "HLDG": "HOLDING",
    "HLDGS": "HOLDINGS",
    "INSTRS": "INSTRUMENTS",
    "INTL": "INTERNATIONAL",
    "LABS": "LABORATORIES",
    "MTRLS": "MATERIALS",
    "NATL": "NATIONAL",
    "PPTYS": "PROPERTIES",
    "PWR": "POWER",
    "SVCS": "SERVICES",
    "SVS": "SERVICES",
    "SYS": "SYSTEMS",
    "TECH": "TECHNOLOGY",
    "TECHS": "TECHNOLOGIES",
    "TRANSN": "TRANSPORTATION",
    "WHSL": "WHOLESALE",
    "WKS": "WORKS",
    "WTR": "WATER",
}
ASSET_NAME_LEGAL_SUFFIXES = {
    "CO", "COMPANY", "CORP", "CORPORATION", "INC", "INCORPORATED",
    "LIMITED", "LTD", "PLC",
}
ASSET_NAME_SECURITY_SUFFIXES = (
    ("COMMON", "STOCK"),
    ("CAPITAL", "STOCK"),
    ("ORDINARY", "SHARES"),
    ("COMMON", "SHARES"),
)


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
                 sleeper: Callable[[float], None] = time_module.sleep,
                 assets_url: str = ASSETS_URL):
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
        parsed_assets_url = urlsplit(assets_url)
        if parsed_assets_url.scheme != "https" or parsed_assets_url.username \
                or parsed_assets_url.password or parsed_assets_url.hostname not in {
                    "api.alpaca.markets", "paper-api.alpaca.markets"} \
                or parsed_assets_url.path.rstrip("/") != "/v2/assets":
            raise AlpacaMarketError("assets_url must be an allowlisted Alpaca assets endpoint")
        self.assets_url = assets_url.rstrip("/")
        self.page_count = 0
        self.request_count = 0

    def _json(self, url: str) -> object:
        request = Request(url, headers=self._headers)
        if not 0 <= self.retries <= 8:
            raise AlpacaMarketError("retries must be between 0 and 8")
        for attempt in range(self.retries + 1):
            try:
                self.request_count += 1
                with self._opener(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
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
        return payload

    def _page(self, parameters: dict[str, str]) -> dict:
        payload = self._json(f"{API_URL}?{urlencode(parameters)}")
        if not isinstance(payload, dict) or not isinstance(payload.get("bars"), dict):
            raise AlpacaMarketError("Alpaca response requires an object-valued bars field")
        self.page_count += 1
        return payload

    def assets(self) -> list[dict]:
        """Return Alpaca's authoritative US-equity data universe."""
        payload = self._json(f"{self.assets_url}?{urlencode({'asset_class': 'us_equity'})}")
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise AlpacaMarketError("Alpaca assets response must be an array of objects")
        return payload

    def daily_bars(self, symbols: list[str], *, start: datetime, end: datetime,
                   max_pages: int = 500, asof: str | None = None) -> dict[str, list[dict]]:
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
        if asof is not None:
            if asof != "-" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", asof):
                raise AlpacaMarketError("Invalid historical symbol asof date")
            parameters["asof"] = asof
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


def _recover_explicit_name_tickers(snapshot: dict) -> tuple[dict, list[dict]]:
    """Recover a ticker only when the official asset label states it explicitly."""
    result = deepcopy(snapshot)
    recovered: list[dict] = []
    for kind in ("transactions", "reported_holdings"):
        rows = result.get(kind)
        if not isinstance(rows, list):
            raise AlpacaMarketError(f"{kind} must be an array")
        for row in rows:
            if not isinstance(row, dict):
                raise AlpacaMarketError(f"{kind} must contain objects")
            if str(row.get("ticker") or "").strip():
                continue
            if str(row.get("instrument_type") or "").strip().casefold() != "stock":
                continue
            match = EXPLICIT_NAME_TICKER.search(str(row.get("asset_name") or "").strip())
            if not match:
                continue
            ticker = match.group(1)
            row["ticker"] = ticker
            row["ticker_mapping_basis"] = "filing_explicit_asset_name"
            recovered.append({"record_id": row.get("id"), "ticker": ticker, "array": kind})
    return result, recovered


def _asset_name_key(value: str, *, drop_class: bool = False) -> str:
    """Normalize filing/provider labels without using fuzzy similarity."""
    tokens = re.findall(r"[A-Z0-9]+", value.upper().replace("&", " AND "))
    expanded = []
    for offset, token in enumerate(tokens):
        if token == "CL" and offset + 1 < len(tokens) \
                and re.fullmatch(r"[A-Z0-9]{1,3}", tokens[offset + 1]):
            expanded.append("CLASS")
        else:
            expanded.append(ASSET_NAME_TOKEN_ALIASES.get(token, token))
    if expanded and expanded[0] == "THE":
        expanded.pop(0)
    while expanded and expanded[-1] == "NEW":
        expanded.pop()
    for suffix in ASSET_NAME_SECURITY_SUFFIXES:
        if tuple(expanded[-len(suffix):]) == suffix:
            del expanded[-len(suffix):]
            break
    if drop_class:
        classless: list[str] = []
        offset = 0
        while offset < len(expanded):
            if expanded[offset] == "CLASS" and offset + 1 < len(expanded):
                offset += 2
                continue
            classless.append(expanded[offset])
            offset += 1
        expanded = classless
    while expanded and expanded[-1] in ASSET_NAME_LEGAL_SUFFIXES:
        expanded.pop()
    return "".join(expanded)


def _recover_unique_asset_name_tickers(
        snapshot: dict, assets: object) -> tuple[dict, list[dict], dict]:
    """Map White House annual labels only when Alpaca has one active SIP identity."""
    registry = _asset_registry(assets)
    exact: dict[str, set[str]] = defaultdict(set)
    classless: dict[str, set[str]] = defaultdict(set)
    provider_names: dict[str, str] = {}
    for symbol, asset in registry.items():
        if asset["status"] != "active" or asset["exchange"] not in SIP_EXCHANGES:
            continue
        name = str(asset.get("name") or "").strip()
        key = _asset_name_key(name)
        if not key:
            continue
        exact[key].add(symbol)
        base_key = _asset_name_key(name, drop_class=True)
        if base_key:
            classless[base_key].add(symbol)
        provider_names[symbol] = name

    result = deepcopy(snapshot)
    recovered: list[dict] = []
    ambiguous: list[dict] = []
    unmatched_names: set[str] = set()
    evaluated_names: set[str] = set()
    evaluated_count = 0
    for kind in ("transactions", "reported_holdings"):
        rows = result.get(kind)
        if not isinstance(rows, list):
            raise AlpacaMarketError(f"{kind} must be an array")
        for row in rows:
            if not isinstance(row, dict):
                raise AlpacaMarketError(f"{kind} must contain objects")
            record_id = str(row.get("id") or "")
            if not record_id.startswith(ANNUAL_TRANSACTION_PREFIX) or str(
                    row.get("ticker") or "").strip():
                continue
            asset_name = str(row.get("asset_name") or "").strip()
            key = _asset_name_key(asset_name)
            if not key:
                continue
            evaluated_count += 1
            evaluated_names.add(asset_name)
            alternatives = exact.get(key, set())
            basis = "alpaca_unique_asset_name"
            if len(alternatives) != 1:
                base_key = _asset_name_key(asset_name, drop_class=True)
                # Never discard a class explicitly stated by the filing.  The
                # classless fallback exists for labels such as ZOETIS INC where
                # Alpaca's sole active identity adds "Class A Common Stock".
                if base_key != key:
                    unmatched_names.add(asset_name)
                    continue
                alternatives = classless.get(base_key, set())
                basis = "alpaca_unique_classless_asset_name"
            if len(alternatives) != 1:
                if alternatives:
                    ambiguous.append({
                        "record_id": record_id,
                        "asset_name": asset_name,
                        "candidate_tickers": sorted(alternatives),
                    })
                else:
                    unmatched_names.add(asset_name)
                continue
            ticker = next(iter(alternatives))
            row["ticker"] = ticker
            row["ticker_mapping_basis"] = basis
            recovered.append({
                "record_id": record_id,
                "ticker": ticker,
                "array": kind,
                "asset_name": asset_name,
                "mapping_basis": basis,
                "provider_asset_name": provider_names[ticker],
            })
    audit = {
        "evaluated_record_count": evaluated_count,
        "evaluated_asset_name_count": len(evaluated_names),
        "recovered_record_count": len(recovered),
        "recovered_asset_name_count": len({row["asset_name"] for row in recovered}),
        "ambiguous_record_count": len(ambiguous),
        "ambiguous_asset_name_count": len({row["asset_name"] for row in ambiguous}),
        "ambiguous_records": ambiguous,
        "unmatched_record_count": evaluated_count - len(recovered) - len(ambiguous),
        "unmatched_asset_name_count": len(unmatched_names),
    }
    return result, recovered, audit


def _symbols_and_names(snapshot: dict) -> tuple[list[str], dict[str, str], dict[str, list[dict]]]:
    names: dict[str, Counter] = defaultdict(Counter)
    contexts: dict[str, list[dict]] = defaultdict(list)
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
            contexts[ticker].append(row)
            name = str(row.get("asset_name") or "").strip()
            if name:
                names[ticker][name] += 1
    symbols = sorted(names)
    display_names = {
        ticker: (sorted(counts, key=lambda name: (-counts[name], name))[0]
                 if counts else ticker)
        for ticker, counts in names.items()
    }
    return symbols, display_names, contexts


def _asset_registry(rows: object) -> dict[str, dict]:
    if not isinstance(rows, list):
        raise AlpacaMarketError("Alpaca assets response must be an array")
    candidates: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            raise AlpacaMarketError("Alpaca assets response must contain objects")
        symbol = str(row.get("symbol") or "").strip().upper()
        asset_class = str(row.get("class") or "").strip().casefold()
        exchange = str(row.get("exchange") or "").strip().upper()
        status = str(row.get("status") or "").strip().casefold()
        if not symbol or not TICKER.fullmatch(symbol) or asset_class != "us_equity" \
                or not exchange or status not in {"active", "inactive"}:
            # Alpaca's asset enum is broader than the US-equity universe and can
            # evolve independently of this consumer.  Ignore rows that cannot be
            # authoritative US-equity records.  A disclosed ticker that only has
            # an ignored row remains unresolved and is rejected by the production
            # coverage gate below, so this cannot silently create market coverage.
            continue
        candidates[symbol].append(
            dict(row, symbol=symbol, exchange=exchange, status=status))
    if not candidates:
        raise AlpacaMarketError("Alpaca assets response is empty")

    result: dict[str, dict] = {}
    for symbol, matches in candidates.items():
        # Alpaca may retain more than one asset identity for a reused symbol.
        # Current records supersede inactive identities.  Within the selected
        # status, exchange values are equivalent only when they lead to the same
        # SIP/OTC support decision; a conflict remains unsafe and fails closed.
        active = [row for row in matches if row["status"] == "active"]
        preferred = active or matches
        scopes = {
            "sip" if row["exchange"] in SIP_EXCHANGES
            else "otc" if row["exchange"] == "OTC"
            else "unknown"
            for row in preferred
        }
        if len(scopes) != 1:
            raise AlpacaMarketError(
                f"Alpaca assets response has conflicting market scope for {symbol}")
        result[symbol] = min(
            preferred,
            key=lambda row: (
                row["exchange"],
                str(row.get("id") or ""),
                json.dumps(row, sort_keys=True, ensure_ascii=True, separators=(",", ":")),
            ),
        )
    return result


def _compact_symbol(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _preferred_share_key(value: str) -> str | None:
    normalized = value.upper()
    provider = re.fullmatch(r"([A-Z]+)[.\-]?PR[.\-]?([A-Z])", normalized)
    if provider:
        return provider.group(1) + provider.group(2)
    disclosure = re.fullmatch(r"([A-Z]+)-([A-Z])", normalized)
    return disclosure.group(1) + disclosure.group(2) if disclosure else None


def _unsupported_reason(ticker: str, rows: list[dict]) -> str | None:
    instruments = {str(row.get("instrument_type") or "").strip().casefold() for row in rows}
    names = " ".join(str(row.get("asset_name") or "") for row in rows).casefold()
    if instruments and all("bond" in value or "debt" in value for value in instruments):
        return "non_equity_debt"
    if ticker.endswith("X") and (
            any("mutual fund" in value for value in instruments)
            or re.search(r"\b(fund|portfolio|money market|trust)\b", names)):
        return "outside_sip_fund"
    if "private equity" in names or ("private" in names and "llc" in names):
        return "private_entity"
    if ticker in KNOWN_OUTSIDE_SIP_SYMBOLS:
        return KNOWN_OUTSIDE_SIP_SYMBOLS[ticker]
    if (ticker.endswith(("Y", "F")) and len(ticker) == 5
            and any("adr" in str(row.get("asset_name") or "").casefold() for row in rows)):
        return "outside_sip_otc"
    return None


def _market_universe(symbols: list[str], contexts: dict[str, list[dict]],
                     assets: object) -> tuple[list[dict], list[dict], list[dict]]:
    registry = _asset_registry(assets)
    compact: dict[str, list[str]] = defaultdict(list)
    preferred: dict[str, list[str]] = defaultdict(list)
    for symbol in registry:
        compact[_compact_symbol(symbol)].append(symbol)
        preferred_key = _preferred_share_key(symbol)
        if preferred_key:
            preferred[preferred_key].append(symbol)
    supported: list[dict] = []
    unsupported: list[dict] = []
    unresolved: list[dict] = []
    for ticker in symbols:
        provider_symbol = ticker if ticker in registry else None
        mapping_basis = "exact_symbol"
        if provider_symbol is None:
            reason = _unsupported_reason(ticker, contexts[ticker])
            if reason in UNSUPPORTED_REASONS:
                unsupported.append({"ticker": ticker, "reason": reason})
                continue
        if provider_symbol is None and re.search(r"[.\-^/]", ticker):
            alternatives = compact.get(_compact_symbol(ticker), [])
            if len(alternatives) == 1 and re.search(r"[.\-^/]", alternatives[0]):
                provider_symbol = alternatives[0]
                mapping_basis = "unique_punctuation_variant"
        if provider_symbol is None and _preferred_share_key(ticker):
            alternatives = preferred.get(_preferred_share_key(ticker) or "", [])
            if len(alternatives) == 1:
                provider_symbol = alternatives[0]
                mapping_basis = "preferred_share_symbol_variant"
        if provider_symbol is not None:
            asset = registry[provider_symbol]
            if asset["exchange"] == "OTC":
                unsupported.append({"ticker": ticker, "reason": "outside_sip_otc",
                                    "provider_symbol": provider_symbol,
                                    "asset_status": asset["status"], "exchange": "OTC"})
            elif asset["exchange"] in SIP_EXCHANGES:
                supported.append({"ticker": ticker, "provider_symbol": provider_symbol,
                                  "mapping_basis": mapping_basis,
                                  "asset_status": asset["status"],
                                  "exchange": asset["exchange"]})
            else:
                unresolved.append({"ticker": ticker, "reason": "unknown_asset_exchange",
                                   "provider_symbol": provider_symbol,
                                   "asset_status": asset["status"],
                                   "exchange": asset["exchange"]})
            continue
        # The current asset master is not a complete historical symbol master.
        # Query historical SIP bars before declaring a former listing unavailable.
        supported.append({"ticker": ticker, "provider_symbol": ticker,
                          "mapping_basis": "historical_symbol_probe",
                          "asset_status": "not_listed", "exchange": "unknown"})
    return supported, unsupported, unresolved


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


def _historical_bar_matches_disclosure(history: list[dict], contexts: list[dict]) -> bool:
    """Avoid attaching a reused old symbol to an unrelated disclosed company."""
    bar_days = {date.fromisoformat(point["date"]) for point in history}
    for row in contexts:
        value = row.get("transaction_date") or row.get("report_period_end")
        if not value:
            continue
        try:
            disclosed_day = date.fromisoformat(str(value)[:10])
        except ValueError:
            raise AlpacaMarketError(f"Invalid disclosure date for {row.get('ticker')}") from None
        if any(abs((bar_day - disclosed_day).days) <= 10 for bar_day in bar_days):
            return True
    return False


def _historical_bars_with_rejected_symbols(
        client: AlpacaMarketClient, symbols: list[str], *, start: datetime,
        end: datetime) -> tuple[dict[str, list[dict]], list[str]]:
    """Isolate symbols rejected by Alpaca without discarding valid batch peers."""
    try:
        return client.daily_bars(symbols, start=start, end=end, asof="-"), []
    except AlpacaMarketError as error:
        if str(error) != "Alpaca HTTP 400":
            raise
        if len(symbols) == 1:
            return {}, symbols
        middle = len(symbols) // 2
        first, first_rejected = _historical_bars_with_rejected_symbols(
            client, symbols[:middle], start=start, end=end)
        second, second_rejected = _historical_bars_with_rejected_symbols(
            client, symbols[middle:], start=start, end=end)
        first.update(second)
        return first, first_rejected + second_rejected


def _merge_cached_history(cached: dict, fresh_bars: list[dict], *,
                          ticker: str, name: str, start_day: date,
                          tail_day: date, as_of: date,
                          previous_as_of: date) -> list[dict] | None:
    """Reuse old closes only when the overlapping split-adjusted tail agrees."""
    fresh = (_market_row(ticker, name, fresh_bars, tail_day, as_of)["price_history"]
             if fresh_bars else [])
    old = [point for point in cached["price_history"]
           if start_day <= date.fromisoformat(point["date"]) <= as_of]
    old_overlap = {point["date"]: point["close"] for point in old
                   if date.fromisoformat(point["date"]) >= tail_day}
    fresh_overlap = {point["date"]: point["close"] for point in fresh
                     if date.fromisoformat(point["date"]) <= previous_as_of}
    if old_overlap != fresh_overlap:
        return None
    merged = [point for point in old if date.fromisoformat(point["date"]) < tail_day]
    merged.extend(fresh)
    return [{"t": point["date"] + "T00:00:00Z", "c": point["close"]}
            for point in merged]


def build_market_validation(snapshot: dict, *, client: AlpacaMarketClient,
                            checked_at: str, as_of_date: str | None = None,
                            batch_size: int = 50,
                            distribution_authorized: bool = False,
                            previous_market: PublishedMarketCache | None = None) -> MarketValidation:
    """Fetch delayed SIP daily bars and attach them to a candidate."""
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

    normalized_snapshot, recovered = _recover_explicit_name_tickers(snapshot)
    symbols, names, contexts = _symbols_and_names(normalized_snapshot)
    if not symbols:
        raise AlpacaMarketError("Disclosure candidate has no ticker-qualified records")
    supported, unsupported, unresolved = _market_universe(
        symbols, contexts, client.assets())
    if distribution_authorized and unresolved:
        raise AlpacaMarketError(
            "Production market universe has unresolved disclosure tickers: "
            + ", ".join(row["ticker"] for row in unresolved))
    disclosure_days = []
    supported_tickers = {row["ticker"] for row in supported}
    for row in normalized_snapshot.get("transactions", []):
        if row.get("ticker") not in supported_tickers:
            continue
        for field in ("transaction_date", "filed_at"):
            try:
                disclosure_days.append(date.fromisoformat(str(row.get(field) or "")[:10]))
            except ValueError:
                raise AlpacaMarketError(f"Invalid {field}: {row.get(field)}") from None
    for row in normalized_snapshot.get("reported_holdings", []):
        if row.get("ticker") in supported_tickers:
            try:
                disclosure_days.append(date.fromisoformat(str(row.get("report_period_end") or "")[:10]))
            except ValueError:
                raise AlpacaMarketError(
                    f"Invalid report_period_end: {row.get('report_period_end')}") from None
    start_day = min([_lookback_start(requested_as_of), *disclosure_days]) - timedelta(days=7)
    start = datetime.combine(start_day, time(), timezone.utc)
    end = _request_end(requested_as_of, checked)
    rows: list[dict] = []
    missing: list[dict] = []
    current_entries = [entry for entry in supported if entry["asset_status"] != "not_listed"]
    historical_entries = [entry for entry in supported if entry["asset_status"] == "not_listed"]
    cache_usable = (previous_market is not None
                    and previous_market.requested_start_date <= start_day
                    and previous_market.requested_as_of_date <= requested_as_of
                    and (requested_as_of - previous_market.requested_as_of_date).days
                    <= MAX_CACHE_AGE_DAYS
                    and (requested_as_of - (previous_market.full_refresh_date
                                            or previous_market.requested_as_of_date)).days
                    < FULL_REFRESH_INTERVAL_DAYS)
    tail_day = (max(start_day, previous_market.requested_as_of_date
                    - timedelta(days=CACHE_OVERLAP_DAYS))
                if cache_usable else start_day)
    cached_entries = [entry for entry in current_entries
                      if cache_usable and entry["asset_status"] == "active"
                      and entry["ticker"] in previous_market.rows
                      and previous_market.provider_symbols.get(entry["ticker"])
                      == entry["provider_symbol"]]
    cached_tickers = {entry["ticker"] for entry in cached_entries}
    full_current_entries = [entry for entry in current_entries
                            if entry["ticker"] not in cached_tickers]
    split_refresh_count = 0
    rejected_historical_symbols: list[str] = []
    if historical_entries:
        # A 400 here means the query format itself is rejected, not a bad filing
        # symbol.  Do not misclassify every historical symbol as unsupported.
        client.daily_bars(
            ["AAPL"], start=end - timedelta(days=30), end=end, asof="-")
    for group, historical_probe, incremental in (
            (full_current_entries, False, False),
            (historical_entries, True, False),
            (cached_entries, False, True)):
      for offset in range(0, len(group), batch_size):
        batch_entries = group[offset:offset + batch_size]
        batch = [row["provider_symbol"] for row in batch_entries]
        if historical_probe:
            received, rejected = _historical_bars_with_rejected_symbols(
                client, batch, start=start, end=end)
            rejected_historical_symbols.extend(rejected)
        else:
            request_start = (datetime.combine(tail_day, time(), timezone.utc)
                             if incremental else start)
            received = client.daily_bars(batch, start=request_start, end=end)
        for entry in batch_entries:
            ticker = entry["ticker"]
            bars = received.get(entry["provider_symbol"], [])
            if incremental:
                combined = _merge_cached_history(
                    previous_market.rows[ticker], bars, ticker=ticker,
                    name=names[ticker], start_day=start_day, tail_day=tail_day,
                    as_of=requested_as_of,
                    previous_as_of=previous_market.requested_as_of_date)
                if combined is None:
                    split_refresh_count += 1
                    bars = client.daily_bars([entry["provider_symbol"]],
                                             start=start, end=end).get(
                                                 entry["provider_symbol"], [])
                else:
                    bars = combined
            if not bars:
                if entry["asset_status"] == "not_listed":
                    unsupported.append({"ticker": ticker, "reason": "outside_sip_not_listed"})
                    continue
                if entry["asset_status"] == "inactive":
                    unsupported.append({
                        "ticker": ticker,
                        "reason": "outside_sip_inactive",
                    })
                    continue
                missing.append({"ticker": ticker, "provider_symbol": entry["provider_symbol"],
                                "asset_status": entry["asset_status"],
                                "exchange": entry["exchange"], "reason": "no_bars_returned"})
                continue
            market_row = _market_row(ticker, names[ticker], bars, start_day, requested_as_of)
            if entry["asset_status"] == "not_listed" and not _historical_bar_matches_disclosure(
                    market_row["price_history"], contexts[ticker]):
                unsupported.append({"ticker": ticker, "reason": "outside_sip_not_listed"})
                continue
            last_day = date.fromisoformat(market_row["price_history"][-1]["date"])
            if entry["asset_status"] == "active" \
                    and last_day < requested_as_of - timedelta(days=MAX_ACTIVE_STALENESS_DAYS):
                missing.append({"ticker": ticker, "provider_symbol": entry["provider_symbol"],
                                "asset_status": entry["asset_status"],
                                "exchange": entry["exchange"],
                                "reason": "stale_active_market_series",
                                "last_bar_date": last_day.isoformat()})
                continue
            rows.append(market_row)

    if distribution_authorized and missing:
        raise AlpacaMarketError(
            "Production market coverage is incomplete for supported tickers: "
            + ", ".join(row["ticker"] for row in missing))
    if not rows:
        raise AlpacaMarketError("Alpaca returned no usable market rows")

    result = normalized_snapshot
    result["meta"]["subtitle"] = (
        "House、Senate与OGE真实披露；Alpaca SIP拆股调整日线"
        if distribution_authorized else
        "披露候选 + Alpaca Basic 延迟 SIP 日线；仅用于本地开发验证")
    result["security_market_data"] = sorted(rows, key=lambda row: row["ticker"])
    health = [deepcopy(row) for row in result.get("source_health", [])
              if row.get("source_id") != SOURCE_ID]
    health.append({
        "source_id": SOURCE_ID,
        "source": SOURCE_NAME,
        "source_type": "market_data",
        "source_url": SOURCE_URL,
        "status": "ok" if not missing and not unresolved else "partial",
        "last_checked_at": checked_at,
        "last_successful_sync_at": checked_at,
        "data_cutoff_at": max(
            (row["price_history"][-1]["date"] for row in rows)) + "T23:59:59Z",
        "detail": (("licensed_production" if distribution_authorized else "local_basic_validation")
                   + f":{len(rows)}/{len(supported)}_supported_tickers"
                   + f":{len(unsupported)}_outside_sip"),
    })
    result["source_health"] = sorted(health, key=lambda row: row["source_id"])
    result["meta"]["market_coverage"] = {
        "schema_version": MARKET_COVERAGE_SCHEMA,
        "source_id": SOURCE_ID,
        "covered_tickers": sorted(row["ticker"] for row in rows),
        "unsupported_tickers": sorted(
            ({"ticker": row["ticker"], "reason": row["reason"]} for row in unsupported),
            key=lambda row: row["ticker"]),
    }

    historical_tickers = {entry["ticker"] for entry in historical_entries}
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
        "supported_ticker_count": len(supported),
        "historical_recovered_count": sum(
            row["ticker"] in historical_tickers for row in rows),
        "historical_rejected_symbols": sorted(rejected_historical_symbols),
        "supported_tickers": supported,
        "unsupported_ticker_count": len(unsupported),
        "unsupported_tickers": unsupported,
        "unresolved_ticker_count": len(unresolved),
        "unresolved_tickers": unresolved,
        "recovered_ticker_count": len(recovered),
        "recovered_tickers": recovered,
        "market_row_count": len(rows),
        "covered_tickers": [row["ticker"] for row in sorted(rows, key=lambda row: row["ticker"])],
        "price_point_count": sum(len(row["price_history"]) for row in rows),
        "covered_date_min": min(row["price_history"][0]["date"] for row in rows),
        "covered_date_max": max(row["price_history"][-1]["date"] for row in rows),
        "cache_status": ("reused" if cached_entries else
                         "ineligible" if previous_market is not None else "unavailable"),
        "incremental_ticker_count": len(cached_entries) - split_refresh_count,
        "full_refresh_ticker_count": (len(full_current_entries)
                                      + len(historical_entries) + split_refresh_count),
        "split_refresh_ticker_count": split_refresh_count,
        "full_history_verified_at": (
            (previous_market.full_refresh_date
             or previous_market.requested_as_of_date).isoformat()
            if cached_entries and len(cached_entries) > split_refresh_count
            else requested_as_of.isoformat()),
        "missing_ticker_count": len(missing),
        "missing_tickers": missing,
        "distribution_authorized": distribution_authorized,
    }
    if isinstance(getattr(client, "page_count", None), int):
        audit["response_page_count"] = client.page_count
    if isinstance(getattr(client, "request_count", None), int):
        audit["request_attempt_count"] = client.request_count
    return MarketValidation(snapshot=result, audit=audit)
