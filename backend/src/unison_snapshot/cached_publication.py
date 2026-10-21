"""Join current disclosures to a fixed price publication without vendor requests."""
from __future__ import annotations

from copy import deepcopy
import gzip
import json
from pathlib import Path

from .codec import digest
from .alpaca_market import _recover_explicit_name_tickers
from .market_store import (MIXED_MARKET_COVERAGE_SCHEMA, MARKET_COVERAGE_SCHEMA,
                           SOURCE_ID, TWELVE_DATA_SOURCE_ID, TWELVE_STATE)


def prepared_prices(root: Path, market_commit: str) -> dict:
    if (root / "market-base.txt").read_text().strip() != market_commit:
        raise ValueError("Market changed during refresh; discard this stale price candidate and rerun refresh")
    return json.loads(gzip.decompress((root / "prices.json.gz").read_bytes()))


def published_prices(main_root: Path, market_root: Path, market_commit: str) -> dict:
    manifest = json.loads((main_root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("market_commit") != market_commit or manifest.get("is_demo") is not False:
        raise ValueError("Cached prices require a matching published main/market pair")
    rows = []
    for sha in manifest["market_pages"]:
        raw = (market_root / "market-pages" / f"{sha}.json").read_bytes()
        if digest(raw) != sha:
            raise ValueError("Cached market page hash mismatch")
        rows.extend(json.loads(raw)["security_market_data"])
    if len(rows) != manifest["coverage"]["market_ticker_count"]:
        raise ValueError("Cached price count differs from the published manifest")
    state_path = market_root / TWELVE_STATE
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    unsupported = []
    for ticker, entry in state.get("entries", {}).items():
        status = entry.get("status")
        if status in {"not_market_security", "identity_unresolved", "twelve_data_unavailable",
                      "no_current_series"}:
            unsupported.append({"ticker": ticker, "reason": (
                "twelve_data_unavailable" if status == "no_current_series" else status)})
    return {"security_market_data": rows, "source_health": manifest["source_health"],
            "meta": {"market_coverage": {"unsupported_tickers": unsupported}}}


def join_disclosures(disclosures: dict, prices: dict, *, generated_at: str) -> tuple[dict, dict]:
    """Disclosure facts always come from the current projection, never a price job."""
    # This existing disclosure-only normalization also runs before live pricing.
    # Keep explicit filing tickers independent from the availability of a vendor.
    disclosures, recovered = _recover_explicit_name_tickers(disclosures)
    result = dict(disclosures, meta=dict(disclosures["meta"]))
    rows = prices["security_market_data"]
    covered = {row["ticker"] for row in rows}
    disclosed = {row["ticker"] for kind in ("transactions", "reported_holdings")
                 for row in disclosures[kind] if row.get("ticker")}
    known = prices["meta"]["market_coverage"].get("unsupported_tickers", [])
    unsupported = sorted((row for row in known if row["ticker"] in disclosed - covered),
                         key=lambda row: row["ticker"])
    pending = sorted(disclosed - covered - {row["ticker"] for row in unsupported})
    sources = sorted({row["source_id"] for row in rows})
    mixed = TWELVE_DATA_SOURCE_ID in sources
    coverage = {"schema_version": MIXED_MARKET_COVERAGE_SCHEMA if mixed else MARKET_COVERAGE_SCHEMA,
                "covered_tickers": sorted(covered), "unsupported_tickers": unsupported,
                "pending_tickers": pending, "price_scope": "published_cache"}
    coverage.update({"source_ids": sources} if mixed else {"source_id": SOURCE_ID})
    result["meta"].update(market_coverage=coverage, generated_at=generated_at,
                          snapshot_id="prepublication-validation")
    result["meta"]["subtitle"] = ("House、Senate与OGE真实披露；Alpaca SIP与Twelve Data拆股调整日线"
                                   if mixed else "House、Senate与OGE真实披露；Alpaca SIP拆股调整日线")
    result["security_market_data"] = rows
    result["source_health"] = sorted(
        [row for row in disclosures["source_health"] if row["source_id"] not in sources]
        + [deepcopy(row) for row in prices["source_health"] if row["source_id"] in sources],
        key=lambda row: row["source_id"])
    return result, {"market_row_count": len(rows), "pending_tickers": pending,
                    "explicit_tickers_recovered": len(recovered),
                    "unsupported_ticker_count": len(unsupported),
                    "price_point_count": sum(len(row["price_history"]) for row in rows),
                    "market_source_health": [row for row in result["source_health"]
                                             if row["source_id"] in sources]}
