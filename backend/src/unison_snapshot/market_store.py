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
MARKET_COVERAGE_SCHEMA = "alpaca-market-coverage/v1"
UNSUPPORTED_REASONS = frozenset({
    "non_equity_debt",
    "outside_sip_foreign_exchange",
    "outside_sip_fund",
    "outside_sip_otc",
    "private_entity",
})
TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-^/]{0,31}")
MUTABLE = re.compile(r"market/[0-9a-f]{2}/index\.json")
IMMUTABLE = re.compile(r"(?:market/[0-9a-f]{2}|market-pages)/[0-9a-f]{64}\.json")


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
        if row.get("source_id") != SOURCE_ID or row.get("feed") != "sip" \
                or row.get("timeframe") != "1Day" or row.get("adjustment") != "split":
            raise ValueError(f"Market row {ticker} is not licensed Alpaca SIP split-adjusted EOD")
        parsed_url = urlsplit(str(row.get("source_url") or ""))
        if parsed_url.scheme != "https" or parsed_url.username or parsed_url.password \
                or parsed_url.hostname not in {"alpaca.markets", "docs.alpaca.markets"}:
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


def build_market_bundle(rows: object, *, data_cutoff_at: str,
                        max_index_bytes: int = 8192,
                        max_blob_bytes: int = 8 * 1024 * 1024,
                        page_size: int = 50) -> MarketBundle:
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
    return MarketBundle(files, tuple(row["ticker"] for row in normalized),
                        tuple(page_shas), data_cutoff_at)


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
    if any(not (MUTABLE.fullmatch(path) or IMMUTABLE.fullmatch(path)) for path in bundle.files):
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
