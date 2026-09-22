"""Licensed Twelve Data split-adjusted EOD supplementation.

Exact symbol, US identity, filing-name overlap, and a current daily series are
required before a missing Alpaca ticker can enter the published market rows.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import math
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .market_store import MIXED_MARKET_COVERAGE_SCHEMA, TWELVE_DATA_SOURCE_ID


SOURCE_URL = "https://twelvedata.com/docs"
AUDIT_SCHEMA = "twelve-data-market-audit/v1"
TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-^/]{0,31}")
GENERIC_WORDS = {"stock", "stocks", "fund", "funds", "trust", "inc", "corp",
                 "corporation", "company", "class", "series", "shares", "the",
                 "and", "common", "etf", "plc", "limited", "ltd"}


class TwelveDataError(ValueError):
    """The request or data cannot support a qualified market row."""


class TwelveDataTransient(TwelveDataError):
    """Retry the whole publication later; do not classify a transient failure."""


class TwelveDataUnavailable(TwelveDataError):
    """Provider definitively has no usable data for this symbol."""


class TwelveDataInvalidSeries(TwelveDataError):
    """One symbol's series is unsuitable; isolate it and continue the audit."""


class TwelveDataClient:
    def __init__(self, key: str, *, timeout: float = 30, retries: int = 3,
                 opener=urlopen, sleeper=time.sleep, pace_seconds: float = 0.25,
                 monotonic=time.monotonic):
        if not key or key.lower() == "demo":
            raise TwelveDataError("A production TWELVE_DATA_API_KEY is required")
        self._key = key
        self.timeout = timeout
        self.retries = retries
        self._opener = opener
        self._sleeper = sleeper
        if pace_seconds < 0:
            raise TwelveDataError("pace_seconds must be nonnegative")
        self._pace_seconds = pace_seconds
        self._monotonic = monotonic
        self._last_request_at: float | None = None
        self.request_count = 0

    def _get(self, endpoint: str, params: dict[str, str]) -> dict:
        if endpoint not in {"symbol_search", "time_series"} or not 0 <= self.retries <= 8:
            raise TwelveDataError("Invalid Twelve Data request")
        query = urlencode({**params, "apikey": self._key, "format": "JSON"})
        request = Request(f"https://api.twelvedata.com/{endpoint}?{query}",
                          headers={"Accept": "application/json"})
        for attempt in range(self.retries + 1):
            try:
                now = self._monotonic()
                if self._last_request_at is not None:
                    pause = self._pace_seconds - (now - self._last_request_at)
                    if pause > 0:
                        self._sleeper(pause)
                self._last_request_at = self._monotonic()
                self.request_count += 1
                with self._opener(request, timeout=self.timeout) as response:
                    value = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as error:
                if error.code in {429, 500, 502, 503, 504}:
                    if attempt < self.retries:
                        self._sleeper(min(2 ** attempt, 8))
                        continue
                    raise TwelveDataTransient(f"Twelve Data HTTP {error.code}") from None
                if error.code in {400, 404}:
                    raise TwelveDataUnavailable(f"Twelve Data HTTP {error.code}") from None
                raise TwelveDataError(f"Twelve Data HTTP {error.code}") from None
            except (URLError, TimeoutError):
                if attempt < self.retries:
                    self._sleeper(min(2 ** attempt, 8))
                    continue
                raise TwelveDataTransient("Twelve Data network request failed") from None
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise TwelveDataTransient("Twelve Data returned invalid JSON") from None
        if not isinstance(value, dict):
            raise TwelveDataTransient("Twelve Data returned a non-object")
        if value.get("status") == "error":
            code = value.get("code")
            if code in {400, 404}:
                raise TwelveDataUnavailable(f"Twelve Data symbol unavailable ({code})")
            if code in {429, 500, 502, 503, 504}:
                raise TwelveDataTransient(f"Twelve Data service unavailable ({code})")
            raise TwelveDataError(f"Twelve Data API rejected request ({code})")
        if value.get("status") != "ok":
            raise TwelveDataTransient("Twelve Data returned an unknown response status")
        return value

    def search(self, ticker: str) -> list[dict]:
        if not TICKER.fullmatch(ticker):
            raise TwelveDataError("Invalid disclosure ticker")
        value = self._get("symbol_search", {"symbol": ticker, "outputsize": "120"})
        rows = value.get("data")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise TwelveDataTransient("Twelve Data symbol search is malformed")
        return rows

    def daily(self, ticker: str, *, start: date, end: date) -> dict:
        if not TICKER.fullmatch(ticker) or start > end:
            raise TwelveDataError("Invalid market symbol or date range")
        return self._get("time_series", {
            "symbol": ticker, "interval": "1day", "adjust": "splits",
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "order": "asc", "outputsize": "5000",
        })


def _name_words(value: object) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]{3,}", str(value or "").lower())
            if word not in GENERIC_WORDS}


def _identity_match(names: set[str], provider_name: object) -> bool:
    words = _name_words(provider_name)
    return bool(words and any(_name_words(name) & words for name in names))


def _points(value: dict, *, ticker: str, start: date, end: date) -> list[dict]:
    meta = value.get("meta")
    rows = value.get("values")
    if not isinstance(meta, dict) or meta.get("symbol") != ticker \
            or meta.get("interval") != "1day" or not isinstance(rows, list):
        raise TwelveDataTransient("Twelve Data daily response identity is malformed")
    points: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            raise TwelveDataInvalidSeries("non_object_bar")
        try:
            day = date.fromisoformat(row["datetime"])
            close = float(row["close"])
        except (KeyError, TypeError, ValueError):
            raise TwelveDataInvalidSeries("invalid_date_or_close") from None
        if day < start or day > end:
            raise TwelveDataInvalidSeries("bar_outside_requested_window")
        if not math.isfinite(close) or close <= 0:
            raise TwelveDataInvalidSeries("nonpositive_or_nonfinite_close")
        points.append({"date": day.isoformat(), "close": round(close, 4)})
    points.sort(key=lambda row: row["date"])
    if any(left["date"] == right["date"] for left, right in zip(points, points[1:])):
        raise TwelveDataInvalidSeries("duplicate_session_date")
    return points


def supplement(snapshot: dict, *, client: TwelveDataClient, checked_at: str,
               limit: int | None = None) -> tuple[dict, dict]:
    if snapshot.get("meta", {}).get("is_demo") is not False:
        raise TwelveDataError("Supplementation requires a real disclosure candidate")
    coverage = snapshot["meta"].get("market_coverage", {})
    if coverage.get("schema_version") != "alpaca-market-coverage/v1" \
            or coverage.get("source_id") != "alpaca_sip_eod":
        raise TwelveDataError("Supplementation requires Alpaca coverage first")
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise TwelveDataError("checked_at needs a timezone")
    cutoff = date.fromisoformat(snapshot["meta"]["data_cutoff_at"][:10])
    end = min(cutoff, checked.astimezone(timezone.utc).date() - timedelta(days=1))
    if end < cutoff - timedelta(days=7):
        raise TwelveDataError("Market audit is too far behind disclosure cutoff")
    names: dict[str, set[str]] = {}
    earliest = end - timedelta(days=770)
    for kind in ("transactions", "reported_holdings"):
        for row in snapshot.get(kind, []):
            ticker = row.get("ticker")
            if ticker:
                names.setdefault(ticker, set()).add(str(row.get("asset_name") or ""))
                if kind == "transactions":
                    earliest = min(earliest, date.fromisoformat(row["transaction_date"]) - timedelta(days=7))
    missing = coverage.get("unsupported_tickers")
    if not isinstance(missing, list):
        raise TwelveDataError("Alpaca coverage lacks unsupported tickers")
    if limit is not None and limit < 1:
        raise TwelveDataError("limit must be positive")
    result = deepcopy(snapshot)
    accepted: list[dict] = []
    audit: list[dict] = []
    remaining: list[dict] = []
    for index, item in enumerate(missing):
        ticker = item["ticker"]
        if limit is not None and index >= limit:
            remaining.append(item)
            continue
        if item["reason"] in {"non_equity_debt", "private_entity"}:
            remaining.append({"ticker": ticker, "reason": "not_market_security"})
            audit.append({"ticker": ticker, "status": "not_market_security"})
            continue
        try:
            matches = [row for row in client.search(ticker)
                       if row.get("symbol") == ticker
                       and row.get("country") == "United States"]
        except TwelveDataUnavailable:
            matches = []
        if len(matches) != 1 or not _identity_match(names.get(ticker, set()),
                                                     matches[0].get("instrument_name")):
            remaining.append({"ticker": ticker, "reason": "identity_unresolved"})
            audit.append({"ticker": ticker, "status": "identity_unresolved",
                          "match_count": len(matches)})
            continue
        identity = matches[0]
        try:
            points = _points(client.daily(ticker, start=earliest, end=end),
                             ticker=ticker, start=earliest, end=end)
        except TwelveDataUnavailable:
            points = []
        except TwelveDataInvalidSeries as error:
            remaining.append({"ticker": ticker, "reason": "twelve_data_unavailable"})
            audit.append({"ticker": ticker, "status": "invalid_series",
                          "reason": str(error)})
            continue
        if not points or date.fromisoformat(points[-1]["date"]) < end - timedelta(days=7):
            remaining.append({"ticker": ticker, "reason": "twelve_data_unavailable"})
            audit.append({"ticker": ticker, "status": "no_current_series",
                          "last_date": points[-1]["date"] if points else None})
            continue
        accepted.append({"ticker": ticker, "company_name": identity["instrument_name"],
                         "source_id": TWELVE_DATA_SOURCE_ID,
                         "price_source": "Twelve Data split-adjusted EOD",
                         "source_url": SOURCE_URL, "feed": "twelve_data",
                         "timeframe": "1Day", "adjustment": "split",
                         "price_history": points})
        audit.append({"ticker": ticker, "status": "accepted", "point_count": len(points),
                      "first_date": points[0]["date"], "last_date": points[-1]["date"]})
    if accepted:
        result["security_market_data"] = sorted(result["security_market_data"] + accepted,
                                                key=lambda row: row["ticker"])
        result["meta"]["market_coverage"] = {
            "schema_version": MIXED_MARKET_COVERAGE_SCHEMA,
            "source_ids": sorted({row["source_id"] for row in result["security_market_data"]}),
            "covered_tickers": [row["ticker"] for row in result["security_market_data"]],
            "unsupported_tickers": sorted(remaining, key=lambda row: row["ticker"]),
        }
        result["meta"]["subtitle"] = (
            "House、Senate与OGE真实披露；Alpaca SIP与Twelve Data拆股调整日线")
        result["source_health"] = sorted(result["source_health"] + [{
            "source_id": TWELVE_DATA_SOURCE_ID,
            "source": "Twelve Data split-adjusted EOD", "source_type": "market_data",
            "source_url": SOURCE_URL, "status": "ok", "last_checked_at": checked_at,
            "last_successful_sync_at": checked_at,
            "data_cutoff_at": max(row["price_history"][-1]["date"] for row in accepted)
                              + "T23:59:59Z",
            "detail": f"{len(accepted)}/{min(len(missing),limit or len(missing))}_attempted",
        }], key=lambda row: row["source_id"])
    return result, {"schema_version": AUDIT_SCHEMA,
                    "source_id": TWELVE_DATA_SOURCE_ID, "checked_at": checked_at,
                    "start_date": earliest.isoformat(), "end_date": end.isoformat(),
                    "attempted_count": min(len(missing), limit or len(missing)),
                    "accepted_count": len(accepted), "remaining_count": len(remaining),
                    "request_count": client.request_count, "results": audit}
