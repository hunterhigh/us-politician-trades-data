"""Licensed Twelve Data split-adjusted EOD supplementation.

Exact symbol, US identity, filing-name overlap, and a current daily series are
required before a missing Alpaca ticker can enter the published market rows.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import math
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .codec import digest, encode
from .market_store import (MIXED_MARKET_COVERAGE_SCHEMA, PublishedTwelveDataCache,
                           TWELVE_DATA_SOURCE_ID, TWELVE_STATE_SCHEMA)


SOURCE_URL = "https://twelvedata.com/docs"
AUDIT_SCHEMA = "twelve-data-market-audit/v1"
CACHE_OVERLAP_DAYS = 45
FULL_REFRESH_DAYS = 30
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
                 opener=urlopen, sleeper=time.sleep, pace_seconds: float | None = None,
                 monotonic=time.monotonic):
        if not key or key.lower() == "demo":
            raise TwelveDataError("A production TWELVE_DATA_API_KEY is required")
        self._key = key
        self.timeout = timeout
        self.retries = retries
        self._opener = opener
        self._sleeper = sleeper
        if pace_seconds is not None and (not math.isfinite(pace_seconds) or pace_seconds < 0):
            raise TwelveDataError("pace_seconds must be nonnegative")
        self._pace_seconds = 8.5 if pace_seconds is None else pace_seconds
        self._quota_checked = pace_seconds is not None
        self.quota_status = "explicit_pace" if self._quota_checked else "not_requested"
        self.plan_limit: int | None = None
        self._quota_remaining = 0
        self._quota_reset_at = 0.0
        self._monotonic = monotonic
        self._last_request_at: float | None = None
        self.request_count = 0
        self.wait_seconds = 0.0
        self.response_bytes = 0

    def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            self.wait_seconds += seconds
            self._sleeper(seconds)

    def _configure_quota(self) -> None:
        # One credit only when actual work is requested; cache-only runs stay offline.
        # https://support.twelvedata.com/en/articles/5713553-control-over-api-usage
        self._quota_checked = True
        try:
            usage = self._get("api_usage", {})
            limit, used = usage.get("plan_limit"), usage.get("current_usage")
            if type(limit) is not int or limit < 1 or type(used) is not int or used < 0:
                raise TwelveDataTransient("Invalid quota response")
        except TwelveDataError:
            self.quota_status = "unavailable_default_pace"
            return
        self.plan_limit = limit
        self._quota_remaining = max(0, limit - max(1, used))
        self._quota_reset_at = self._monotonic() + 60
        self._pace_seconds = max(0.05, 68 / limit)
        self.quota_status = "verified"

    def metrics(self) -> dict:
        return {"quota_status": self.quota_status, "plan_limit_per_minute": self.plan_limit,
                "pace_seconds": self._pace_seconds, "wait_seconds": round(self.wait_seconds, 3),
                "response_bytes": self.response_bytes}

    def _get(self, endpoint: str, params: dict[str, str]) -> dict:
        if endpoint not in {"symbol_search", "time_series", "api_usage"} or not 0 <= self.retries <= 8:
            raise TwelveDataError("Invalid Twelve Data request")
        if not self._quota_checked:
            self._configure_quota()
        query = urlencode({**params, "apikey": self._key, "format": "JSON"})
        request = Request(f"https://api.twelvedata.com/{endpoint}?{query}",
                          headers={"Accept": "application/json"})
        for attempt in range(self.retries + 1):
            try:
                now = self._monotonic()
                if self.plan_limit is not None:
                    if self._quota_remaining <= 0:
                        self._sleep(self._quota_reset_at - now)
                        now = self._monotonic()
                    if now >= self._quota_reset_at:
                        self._quota_remaining = self.plan_limit
                        self._quota_reset_at = now + 60
                    self._quota_remaining -= 1
                if self._last_request_at is not None:
                    pause = self._pace_seconds - (now - self._last_request_at)
                    self._sleep(pause)
                self._last_request_at = self._monotonic()
                self.request_count += 1
                with self._opener(request, timeout=self.timeout) as response:
                    body = response.read()
                    self.response_bytes += len(body)
                    value = json.loads(body.decode("utf-8"))
                if isinstance(value, dict) and value.get("status") == "error" \
                        and value.get("code") == 429 and attempt < self.retries:
                    self._sleep(60)
                    continue
                break
            except HTTPError as error:
                if error.code in {429, 500, 502, 503, 504}:
                    if attempt < self.retries:
                        self._sleep(60 if error.code == 429 else min(2 ** attempt, 8))
                        continue
                    raise TwelveDataTransient(f"Twelve Data HTTP {error.code}") from None
                if error.code in {400, 404}:
                    raise TwelveDataUnavailable(f"Twelve Data HTTP {error.code}") from None
                raise TwelveDataError(f"Twelve Data HTTP {error.code}") from None
            except (URLError, TimeoutError):
                if attempt < self.retries:
                    self._sleep(min(2 ** attempt, 8))
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
        if endpoint != "api_usage" and value.get("status") != "ok":
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


def _points(value: dict, *, ticker: str, start: date, end: date) -> tuple[list[dict], int]:
    meta = value.get("meta")
    rows = value.get("values")
    if not isinstance(meta, dict) or meta.get("symbol") != ticker \
            or meta.get("interval") != "1day" or not isinstance(rows, list):
        raise TwelveDataTransient("Twelve Data daily response identity is malformed")
    points_by_date: dict[str, float] = {}
    duplicate_count = 0
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
        key = day.isoformat()
        close = round(close, 4)
        if key in points_by_date:
            if points_by_date[key] != close:
                raise TwelveDataInvalidSeries("conflicting_duplicate_session_date")
            duplicate_count += 1
        points_by_date[key] = close
    return ([{"date": day, "close": points_by_date[day]}
             for day in sorted(points_by_date)], duplicate_count)


def _fingerprint(names: set[str]) -> str:
    return digest(encode(sorted(names)))


def _checked_day(entry: dict) -> date | None:
    try:
        return datetime.fromisoformat(str(entry["checked_at"]).replace("Z", "+00:00")).date()
    except (KeyError, TypeError, ValueError):
        return None


def _history(client, ticker: str, *, start: date, end: date, prior: dict | None,
             cached: dict | None, checked_at: str) -> tuple[list[dict], int, dict]:
    """Fetch a tail when its overlap agrees; refresh history on changes or periodically."""
    request_start = start
    mode = "full"
    try:
        old_start = date.fromisoformat(prior["history_start_date"])
        old_end = date.fromisoformat(prior["history_end_date"])
        full_day = _checked_day({"checked_at": prior["full_refreshed_at"]})
        today = datetime.fromisoformat(checked_at.replace("Z", "+00:00")).date()
        if cached and old_start <= start <= old_end <= end and full_day is not None \
                and 0 <= (today - full_day).days < FULL_REFRESH_DAYS:
            request_start = max(start, old_end - timedelta(days=CACHE_OVERLAP_DAYS))
            mode = "incremental"
    except (KeyError, TypeError, ValueError):
        pass  # Legacy state has no requested-window metadata: establish it with one full read.
    points, duplicates = _points(client.daily(ticker, start=request_start, end=end),
                                 ticker=ticker, start=request_start, end=end)
    received = len(points)
    if mode == "incremental":
        old_overlap = {p["date"]: p["close"] for p in cached["price_history"]
                       if request_start.isoformat() <= p["date"] <= old_end.isoformat()}
        new_overlap = {p["date"]: p["close"] for p in points if p["date"] <= old_end.isoformat()}
        if not old_overlap or old_overlap != new_overlap:
            mode = "overlap_repair"
            points, full_duplicates = _points(client.daily(ticker, start=start, end=end),
                                              ticker=ticker, start=start, end=end)
            duplicates += full_duplicates
            received += len(points)
        else:
            points = [p for p in cached["price_history"]
                      if start.isoformat() <= p["date"] < request_start.isoformat()] + points
    return points, duplicates, {"history_mode": mode, "request_start_date": request_start.isoformat(),
                                "received_point_count": received,
                                "history_start_date": start.isoformat(), "history_end_date": end.isoformat(),
                                "full_refreshed_at": prior["full_refreshed_at"]
                                if mode == "incremental" else checked_at}


def _schedule(new_work: list, refresh_work: list, limit: int | None, next_kind: str):
    """Alternate exploration and refresh; unused capacity goes to the nonempty queue."""
    def age(work):
        prior = work[2] or {}
        try:
            attempted = datetime.fromisoformat(prior.get("last_attempt_at", prior.get("checked_at", ""))
                                               .replace("Z", "+00:00")).timestamp()
        except (AttributeError, TypeError, ValueError):
            attempted = float("-inf")
        return attempted, work[0]["ticker"]
    queues = {"new": deque(sorted(new_work, key=age)),
              "refresh": deque(sorted(refresh_work, key=age))}
    kind = next_kind if next_kind in queues else "refresh"
    selected = []
    while any(queues.values()) and (limit is None or len(selected) < limit):
        if not queues[kind]:
            kind = "new" if kind == "refresh" else "refresh"
        selected.append(queues[kind].popleft())
        kind = "new" if kind == "refresh" else "refresh"
    return selected, kind


def supplement(snapshot: dict, *, client: TwelveDataClient, checked_at: str,
               limit: int | None = None,
               previous_market: PublishedTwelveDataCache | None = None,
               refresh_days: int = 7, retry_days: int = 30) -> tuple[dict, dict]:
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
    if limit is not None and limit < 0:
        raise TwelveDataError("limit must be nonnegative")
    if refresh_days < 1 or retry_days < 1:
        raise TwelveDataError("refresh and retry intervals must be positive")
    result = deepcopy(snapshot)
    accepted: dict[str, dict] = {}
    audit: list[dict] = []
    remaining: list[dict] = []
    previous_entries = (previous_market.state.get("entries", {})
                        if previous_market else {})
    previous_rows = previous_market.rows if previous_market else {}
    state_entries: dict[str, dict] = {}
    new_work: list[tuple[dict, str, dict | None]] = []
    refresh_work: list[tuple[dict, str, dict | None]] = []
    deferred: list[dict] = []
    for item in missing:
        ticker = item["ticker"]
        fingerprint = _fingerprint(names.get(ticker, set()))
        prior = previous_entries.get(ticker)
        if not isinstance(prior, dict) or prior.get("fingerprint") != fingerprint:
            prior = None
        if item["reason"] in {"non_equity_debt", "private_entity"}:
            remaining.append({"ticker": ticker, "reason": "not_market_security"})
            state_entries[ticker] = {"status": "not_market_security",
                                     "checked_at": checked_at,
                                     "fingerprint": fingerprint}
            continue
        if prior and prior.get("status") == "accepted" and ticker in previous_rows:
            accepted[ticker] = previous_rows[ticker]
            state_entries[ticker] = prior
            last = _checked_day(prior)
            if last is None or last <= end - timedelta(days=refresh_days):
                refresh_work.append((item, fingerprint, prior))
            continue
        if prior and prior.get("status") in {"identity_unresolved", "twelve_data_unavailable",
                                              "no_current_series"}:
            last = _checked_day(prior)
            if last is not None and last > end - timedelta(days=retry_days):
                reason = ("identity_unresolved" if prior["status"] == "identity_unresolved"
                          else "twelve_data_unavailable")
                remaining.append({"ticker": ticker, "reason": reason})
                state_entries[ticker] = prior
                continue
        new_work.append((item, fingerprint, prior))

    work = new_work + refresh_work
    selected, next_kind = _schedule(new_work, refresh_work, limit,
                                    previous_market.state.get("next_work_kind", "refresh")
                                    if previous_market else "refresh")
    selected_tickers = {item[0]["ticker"] for item in selected}
    refresh_tickers = {item[0]["ticker"] for item in refresh_work}
    refresh_selected = len(selected_tickers & refresh_tickers)
    for item, fingerprint, prior in work:
        if item["ticker"] not in selected_tickers:
            deferred.append(item)
    new_accepted = 0
    duplicate_rows = 0
    for item, fingerprint, prior in selected:
        ticker = item["ticker"]
        refreshing = prior is not None and prior.get("status") == "accepted" \
            and ticker in previous_rows
        if refreshing:
            # Failed refreshes keep the last success date, but move behind unattempted work.
            prior = dict(prior, last_attempt_at=checked_at)
        try:
            matches = ([{"symbol": ticker,
                         "instrument_name": previous_rows[ticker]["company_name"],
                         "country": "United States"}]
                       if refreshing else
                       [row for row in client.search(ticker)
                        if row.get("symbol") == ticker
                        and row.get("country") == "United States"])
        except TwelveDataUnavailable:
            matches = []
        if len(matches) != 1 or not _identity_match(names.get(ticker, set()),
                                                     matches[0].get("instrument_name")):
            if refreshing:
                state_entries[ticker] = prior
                audit.append({"ticker": ticker, "status": "refresh_identity_kept"})
                continue
            remaining.append({"ticker": ticker, "reason": "identity_unresolved"})
            state_entries[ticker] = {"status": "identity_unresolved",
                                     "checked_at": checked_at,
                                     "fingerprint": fingerprint}
            audit.append({"ticker": ticker, "status": "identity_unresolved",
                          "match_count": len(matches)})
            continue
        identity = matches[0]
        try:
            points, duplicates, history = _history(client, ticker, start=earliest, end=end,
                                                  prior=prior if refreshing else None,
                                                  cached=previous_rows.get(ticker) if refreshing else None,
                                                  checked_at=checked_at)
        except TwelveDataUnavailable:
            points, duplicates = [], 0
        except TwelveDataInvalidSeries as error:
            if refreshing:
                state_entries[ticker] = prior
                audit.append({"ticker": ticker, "status": "refresh_invalid_kept",
                              "reason": str(error)})
                continue
            remaining.append({"ticker": ticker, "reason": "twelve_data_unavailable"})
            state_entries[ticker] = {"status": "twelve_data_unavailable",
                                     "checked_at": checked_at,
                                     "fingerprint": fingerprint,
                                     "reason": str(error)}
            audit.append({"ticker": ticker, "status": "invalid_series",
                          "reason": str(error)})
            continue
        if not points or date.fromisoformat(points[-1]["date"]) < end - timedelta(days=7):
            if refreshing:
                state_entries[ticker] = prior
                audit.append({"ticker": ticker, "status": "refresh_stale_kept",
                              "last_date": points[-1]["date"] if points else None})
                continue
            remaining.append({"ticker": ticker, "reason": "twelve_data_unavailable"})
            state_entries[ticker] = {"status": "no_current_series",
                                     "checked_at": checked_at,
                                     "fingerprint": fingerprint}
            audit.append({"ticker": ticker, "status": "no_current_series",
                          "last_date": points[-1]["date"] if points else None})
            continue
        accepted[ticker] = {"ticker": ticker, "company_name": identity["instrument_name"],
                            "source_id": TWELVE_DATA_SOURCE_ID,
                            "price_source": "Twelve Data split-adjusted EOD",
                            "source_url": SOURCE_URL, "feed": "twelve_data",
                            "timeframe": "1Day", "adjustment": "split",
                            "price_history": points}
        state_entries[ticker] = {"status": "accepted", "checked_at": checked_at,
                                 "last_attempt_at": checked_at, "fingerprint": fingerprint,
                                 **{key: history[key] for key in ("history_start_date", "history_end_date",
                                                                  "full_refreshed_at")}}
        new_accepted += 0 if refreshing else 1
        duplicate_rows += duplicates
        audit.append({"ticker": ticker, "status": "accepted", "point_count": len(points),
                      "first_date": points[0]["date"], "last_date": points[-1]["date"],
                      "deduplicated_rows": duplicates, "refresh": refreshing,
                      **{key: history[key] for key in ("history_mode", "request_start_date",
                                                       "received_point_count")}})
    # A deferred refresh still has a valid published price row in ``accepted``.
    # Keep it in the refresh backlog, but never classify it as unsupported.
    remaining.extend(item for item in deferred if item["ticker"] not in accepted)
    if accepted:
        result["security_market_data"] = sorted(result["security_market_data"]
                                                + list(accepted.values()),
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
            "last_successful_sync_at": max((state_entries[ticker]["checked_at"] for ticker in accepted
                                             if isinstance(state_entries[ticker].get("checked_at"), str)),
                                            default=None),
            "data_cutoff_at": max(row["price_history"][-1]["date"]
                                  for row in accepted.values())
                              + "T23:59:59Z",
            "detail": f"{len(accepted)}_covered;{len(selected)}_checked",
        }], key=lambda row: row["source_id"])
    state = {"schema_version": TWELVE_STATE_SCHEMA, "updated_at": checked_at,
             "next_work_kind": next_kind,
             "entries": dict(sorted(state_entries.items()))}
    return result, {"schema_version": AUDIT_SCHEMA,
                    "source_id": TWELVE_DATA_SOURCE_ID, "checked_at": checked_at,
                    "start_date": earliest.isoformat(), "end_date": end.isoformat(),
                    "attempted_count": len(selected), "accepted_count": len(accepted),
                    "new_accepted_count": new_accepted,
                    "cached_accepted_count": len(accepted) - new_accepted,
                    "remaining_count": len(missing) - len(accepted),
                    "backlog_count": len(deferred),
                    "new_due_count": len(new_work), "refresh_due_count": len(refresh_work),
                    "new_selected_count": len(selected) - refresh_selected,
                    "refresh_selected_count": refresh_selected,
                    "new_deferred_count": len(new_work) - len(selected) + refresh_selected,
                    "refresh_deferred_count": len(refresh_work) - refresh_selected,
                    "history_counts": {mode: sum(row.get("history_mode") == mode for row in audit)
                                       for mode in ("full", "incremental", "overlap_repair")},
                    "deduplicated_row_count": duplicate_rows,
                    "request_count": client.request_count, "transport": client.metrics(), "results": audit,
                    "state": state}
