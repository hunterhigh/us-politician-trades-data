"""Content-addressed publication for licensed market rows."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import json
import math
import re
import tempfile
from urllib.parse import urlsplit

from .codec import bucket, digest, encode


SOURCE_ID = "alpaca_sip_eod"
TWELVE_DATA_SOURCE_ID = "twelve_data_split_adjusted_eod"
MARKET_COVERAGE_SCHEMA = "alpaca-market-coverage/v1"
MIXED_MARKET_COVERAGE_SCHEMA = "mixed-market-coverage/v2"
UNSUPPORTED_REASONS = frozenset({
    "non_equity_debt",
    "outside_sip_foreign_exchange",
    "outside_sip_fund",
    "outside_sip_inactive",
    "outside_sip_not_listed",
    "outside_sip_otc",
    "private_entity",
})
MIXED_UNSUPPORTED_REASONS = UNSUPPORTED_REASONS | {
    "twelve_data_unavailable", "identity_unresolved", "not_market_security",
}
TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-^/]{0,31}")
MUTABLE = re.compile(r"market/[0-9a-f]{2}/index\.json")
IMMUTABLE = re.compile(r"(?:market/[0-9a-f]{2}|market-pages)/[0-9a-f]{64}\.json")
CACHE_WINDOW = "market/cache-window.json"
CACHE_WINDOW_SCHEMA = "market-cache-window/v1"


@dataclass(frozen=True)
class MarketBundle:
    files: dict[str, bytes]
    tickers: tuple[str, ...]
    page_shas: tuple[str, ...]
    data_cutoff_at: str


@dataclass(frozen=True)
class MarketMaterializeResult:
    changed: bool
    written: tuple[str, ...]
    removed: tuple[str, ...]


@dataclass(frozen=True)
class PublishedMarketCache:
    rows: dict[str, dict]
    requested_start_date: date
    requested_as_of_date: date
    provider_symbols: dict[str, str]
    full_refresh_date: date | None = None


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Market timestamp must be an ISO string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Market timestamp requires a timezone")
    return parsed


def validate_market_rows(rows: object, *, data_cutoff_at: str) -> list[dict]:
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("security_market_data must be an array of objects")
    cutoff = _timestamp(data_cutoff_at).date()
    result: list[dict] = []
    seen: set[str] = set()
    for source in rows:
        row = dict(source)
        ticker = str(row.get("ticker") or "").strip().upper()
        if not TICKER.fullmatch(ticker) or ticker in seen:
            raise ValueError("Market tickers must be unique normalized symbols")
        seen.add(ticker)
        source_id = row.get("source_id")
        expected_feed = {SOURCE_ID: "sip", TWELVE_DATA_SOURCE_ID: "twelve_data"}.get(source_id)
        if expected_feed is None or row.get("feed") != expected_feed \
                or row.get("timeframe") != "1Day" or row.get("adjustment") != "split":
            raise ValueError(f"Market row {ticker} has an invalid source or price basis")
        parsed_url = urlsplit(str(row.get("source_url") or ""))
        allowed_hosts = ({"alpaca.markets", "docs.alpaca.markets"}
                         if source_id == SOURCE_ID else {"twelvedata.com"})
        if parsed_url.scheme != "https" or parsed_url.username or parsed_url.password \
                or parsed_url.hostname not in allowed_hosts:
            raise ValueError(f"Market row {ticker} requires an allowlisted HTTPS source URL")
        history = row.get("price_history")
        if not isinstance(history, list) or not history:
            raise ValueError(f"Market row {ticker} requires price history")
        previous: date | None = None
        normalized_history: list[dict] = []
        for point in history:
            if not isinstance(point, dict) or set(point) != {"date", "close"}:
                raise ValueError(f"Market row {ticker} contains an invalid price point")
            day = date.fromisoformat(str(point["date"]))
            close = point["close"]
            if previous is not None and day <= previous:
                raise ValueError(f"Market row {ticker} history must be unique and ascending")
            if day > cutoff or isinstance(close, bool) or not isinstance(close, (int, float)) \
                    or not math.isfinite(close) or close <= 0:
                raise ValueError(f"Market row {ticker} contains an invalid completed close")
            previous = day
            normalized_history.append({"date": day.isoformat(), "close": round(float(close), 4)})
        row["ticker"] = ticker
        row["price_history"] = normalized_history
        result.append(row)
    return sorted(result, key=lambda item: item["ticker"])


def _cache_window(audit: dict, rows: list[dict]) -> bytes:
    if audit.get("schema_version") != "alpaca-market-validation/v2":
        raise ValueError("Market cache requires a validated Alpaca audit")
    try:
        start = date.fromisoformat(audit["start_date"])
        as_of = date.fromisoformat(audit["requested_as_of_date"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Market cache audit has an invalid request window") from None
    if start > as_of or audit.get("feed") != "sip" \
            or audit.get("timeframe") != "1Day" or audit.get("adjustment") != "split":
        raise ValueError("Market cache audit does not match the published feed")
    tickers = {row["ticker"] for row in rows}
    if set(audit.get("covered_tickers", [])) != tickers \
            or audit.get("market_row_count") != len(rows):
        raise ValueError("Market cache audit does not match published rows")
    supported = audit.get("supported_tickers")
    if not isinstance(supported, list):
        raise ValueError("Market cache audit lacks provider mappings")
    mappings = {row.get("ticker"): row.get("provider_symbol") for row in supported
                if isinstance(row, dict) and row.get("ticker") in tickers}
    if set(mappings) != tickers or any(not TICKER.fullmatch(str(value or ""))
                                       for value in mappings.values()):
        raise ValueError("Market cache audit has incomplete provider mappings")
    try:
        full_refresh = date.fromisoformat(audit["full_history_verified_at"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Market cache audit lacks a full refresh date") from None
    if full_refresh > as_of:
        raise ValueError("Market cache full refresh date exceeds market cutoff")
    return encode({
        "schema_version": CACHE_WINDOW_SCHEMA,
        "source_id": SOURCE_ID,
        "feed": "sip", "timeframe": "1Day", "adjustment": "split",
        "requested_start_date": start.isoformat(),
        "requested_as_of_date": as_of.isoformat(),
        "full_history_verified_at": full_refresh.isoformat(),
        "provider_symbols": dict(sorted(mappings.items())),
    })


def build_market_bundle(rows: object, *, data_cutoff_at: str,
                        max_index_bytes: int = 8192,
                        max_blob_bytes: int = 8 * 1024 * 1024,
                        page_size: int = 50,
                        audit: dict | None = None) -> MarketBundle:
    normalized = validate_market_rows(rows, data_cutoff_at=data_cutoff_at)
    if not normalized:
        raise ValueError("Licensed market publication requires at least one market row")
    if not 1 <= page_size <= 200:
        raise ValueError("Market page_size must be between 1 and 200")
    files: dict[str, bytes] = {}
    indexes: dict[str, dict[str, str]] = {}
    for row in normalized:
        ticker = row["ticker"]
        prefix = f"market/{bucket('market', ticker)}"
        content = encode({"security_market_data": [row]})
        if len(content) > max_blob_bytes:
            raise ValueError(f"Market shard size budget exceeded: {ticker}")
        sha = digest(content)
        files[f"{prefix}/{sha}.json"] = content
        indexes.setdefault(f"{prefix}/index.json", {})[ticker] = sha
    for path, shards in sorted(indexes.items()):
        content = encode({"shards": shards})
        if len(content) > max_index_bytes:
            raise ValueError(f"Market index size budget exceeded: {path}")
        files[path] = content
    page_shas: list[str] = []
    for offset in range(0, len(normalized), page_size):
        content = encode({"security_market_data": normalized[offset:offset + page_size]})
        if len(content) > max_blob_bytes:
            raise ValueError("Market page shard size budget exceeded")
        sha = digest(content)
        files[f"market-pages/{sha}.json"] = content
        page_shas.append(sha)
    if audit is not None:
        # The incremental cache belongs only to Alpaca. Twelve Data rows share
        # the published market branch but never inherit Alpaca's feed metadata.
        alpaca_rows = [row for row in normalized if row["source_id"] == SOURCE_ID]
        files[CACHE_WINDOW] = _cache_window(audit, alpaca_rows)
    return MarketBundle(files, tuple(row["ticker"] for row in normalized),
                        tuple(page_shas), data_cutoff_at)


def load_published_market_cache(main_root: Path, market_root: Path,
                                *, market_commit: str) -> PublishedMarketCache | None:
    """Read only a market version already referenced by the published main tree.

    The first publication predates cache metadata and legitimately returns None.
    Once metadata exists, malformed indexes or blobs are integrity failures.
    """
    manifest_path = main_root / "manifest.json"
    window_path = market_root / CACHE_WINDOW
    if not manifest_path.exists() or not window_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("market_commit") != market_commit:
        return None
    if manifest.get("is_demo") is not False:
        raise ValueError("Market cache requires a published production manifest")
    window = json.loads(window_path.read_text(encoding="utf-8"))
    if window.get("schema_version") != CACHE_WINDOW_SCHEMA \
            or window.get("source_id") != SOURCE_ID \
            or (window.get("feed"), window.get("timeframe"),
                window.get("adjustment")) != ("sip", "1Day", "split"):
        raise ValueError("Published market cache metadata is invalid")
    try:
        start = date.fromisoformat(window["requested_start_date"])
        as_of = date.fromisoformat(window["requested_as_of_date"])
        full_refresh = date.fromisoformat(window["full_history_verified_at"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Published market cache window is invalid") from None
    mappings = window.get("provider_symbols")
    if start > as_of or full_refresh > as_of or not isinstance(mappings, dict) or not mappings \
            or any(not TICKER.fullmatch(str(ticker))
                   or not TICKER.fullmatch(str(provider))
                   for ticker, provider in mappings.items()):
        raise ValueError("Published market cache mappings are invalid")
    rows: dict[str, dict] = {}
    for ticker in sorted(mappings):
        index_path = market_root / "market" / bucket("market", ticker) / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        sha = index.get("shards", {}).get(ticker)
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError(f"Published market cache index is invalid for {ticker}")
        blob = index_path.parent / f"{sha}.json"
        content = blob.read_bytes()
        if digest(content) != sha:
            raise ValueError(f"Published market cache hash mismatch for {ticker}")
        value = json.loads(content)
        listed = value.get("security_market_data")
        normalized = validate_market_rows(listed, data_cutoff_at=as_of.isoformat() + "T23:59:59Z")
        if len(normalized) != 1 or normalized[0]["ticker"] != ticker:
            raise ValueError(f"Published market cache row is invalid for {ticker}")
        rows[ticker] = normalized[0]
    total = manifest.get("coverage", {}).get("market_ticker_count")
    if type(total) is not int or total < len(rows):
        raise ValueError("Published market cache count does not match main")
    if total > len(rows) and not any(
            item.get("source_id") == TWELVE_DATA_SOURCE_ID
            for item in manifest.get("source_health", [])):
        raise ValueError("Published market cache has unexplained non-Alpaca rows")
    return PublishedMarketCache(rows, start, as_of, mappings, full_refresh)


def _atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                     suffix=".tmp", delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def materialize_market(root: Path, bundle: MarketBundle) -> MarketMaterializeResult:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(not (MUTABLE.fullmatch(path) or IMMUTABLE.fullmatch(path)
                or path == CACHE_WINDOW) for path in bundle.files):
        raise ValueError("Market bundle contains a path outside the public contract")
    written: list[str] = []
    for relative, content in sorted(bundle.files.items()):
        target = root / relative
        if IMMUTABLE.fullmatch(relative) and target.exists() and target.read_bytes() != content:
            raise ValueError(f"Immutable market object changed in place: {relative}")
        if not target.exists() or target.read_bytes() != content:
            _atomic(target, content)
            written.append(relative)
    expected = {path for path in bundle.files if MUTABLE.fullmatch(path)}
    removed: list[str] = []
    directory = root / "market"
    if directory.exists():
        for existing in directory.glob("[0-9a-f][0-9a-f]/index.json"):
            relative = existing.relative_to(root).as_posix()
            if relative not in expected:
                existing.unlink()
                removed.append(relative)
    return MarketMaterializeResult(bool(written or removed), tuple(written), tuple(removed))
