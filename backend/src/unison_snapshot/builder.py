"""Offline normalized-data producer. Live source admission is deliberately closed."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, date
import re
from urllib.parse import urlsplit

from .codec import bucket, digest, encode
from .legacy import PROCESSOR_SHA256, PROCESSOR_V2_SHA256, load
from .market_store import (MARKET_COVERAGE_SCHEMA, MIXED_MARKET_COVERAGE_SCHEMA,
                           SOURCE_ID as MARKET_SOURCE_ID, TWELVE_DATA_SOURCE_ID,
                           UNSUPPORTED_REASONS, MIXED_UNSUPPORTED_REASONS,
                           validate_market_rows)

SCHEMA = "politician-dashboard/v1"
LAYOUT = "hash-sharded-v2"
ARRAYS = ("people", "transactions", "reported_holdings", "security_market_data")
SOURCES = {"house_clerk", "senate_efd", "oge"}
HEALTH_SOURCES = SOURCES | {"house_ethics_guidance", "senate_ethics_guidance",
                            MARKET_SOURCE_ID, TWELVE_DATA_SOURCE_ID}
SOURCE_HOSTS = {
    "house_clerk": {"disclosures-clerk.house.gov", "clerk.house.gov"},
    "senate_efd": {"efdsearch.senate.gov"},
}
FIELDS = {
    "people": set("id display_name short_name role office_type chamber party state disclosure_authority priority priority_reason portrait_url".split()),
    "transactions": set("id filing_id person_id owner asset_name ticker ticker_mapping_basis instrument_type option_type strike_price expiration_date transaction_type transaction_date filed_at amount_low amount_high position_effect position_effect_basis source_id source source_url verification_status".split()),
    "reported_holdings": set("id filing_id person_id owner asset_name ticker ticker_mapping_basis instrument_type report_period_end filed_at value_low value_high change_from_prior source_id source source_url verification_status".split()),
}


@dataclass(frozen=True)
class Bundle:
    files: dict[str, bytes]
    manifest: dict


def timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Timestamp must be an ISO string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp requires timezone")
    return parsed


def required(row: dict, key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"Missing or invalid {key}")
    return value


def normalize(payload: dict, *, allow_production: bool = False,
              allow_empty_production: bool = False,
              allow_market: bool = False) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("meta"), dict):
        raise ValueError("Snapshot input must be an object with meta")
    data = deepcopy(payload)
    demo = data.get("meta", {}).get("is_demo")
    if type(demo) is not bool:
        raise ValueError("meta.is_demo must be an explicit boolean")
    if not demo and not allow_production:
        raise ValueError("Production admission requires the explicit production publication path")
    timestamp(data["meta"].get("data_cutoff_at"))
    for kind in ARRAYS:
        rows = data.get(kind)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"{kind} must be an array of objects")
        if kind == "security_market_data":
            if rows and not allow_market:
                raise ValueError("Market publishing disabled pending licensing and separate branch implementation")
            if rows:
                if demo:
                    raise ValueError("Licensed market publication cannot be demo data")
                data[kind] = validate_market_rows(rows, data_cutoff_at=data["meta"]["data_cutoff_at"])
            continue
        seen = set()
        for row in rows:
            if set(row) - FIELDS[kind]:
                raise ValueError(f"Unexpected fields in {kind}: {sorted(set(row) - FIELDS[kind])}")
            key = required(row, "id")
            if key in seen:
                raise ValueError(f"Duplicate {kind} id: {key}")
            seen.add(key)
        rows.sort(key=lambda row: row["id"])
    people = {row["id"] for row in data["people"]}
    for row in data["people"]:
        required(row, "display_name")
    for kind, prefix, day_field in (("transactions", "amount", "transaction_date"),
                                    ("reported_holdings", "value", "report_period_end")):
        for row in data[kind]:
            if required(row, "person_id") not in people:
                raise ValueError("Disclosure references an unknown person")
            required(row, "filing_id")
            source_id = row.get("source_id")
            expected_status = "simulated" if demo else "official_matched"
            if source_id not in SOURCES or row.get("verification_status") != expected_status:
                raise ValueError(f"Disclosure must use a known source and {expected_status} status")
            if not demo:
                source_url = urlsplit(required(row, "source_url"))
                host = source_url.hostname or ""
                # The White House hosts original OGE forms as PDFs, while the
                # frozen consumer contract still identifies their authority as OGE.
                white_house_oge_pdf = (source_id == "oge" and host == "www.whitehouse.gov"
                                       and source_url.path.startswith("/wp-content/uploads/")
                                       and source_url.path.lower().endswith(".pdf"))
                official_host = host in SOURCE_HOSTS.get(source_id, set()) \
                    or (source_id == "oge" and (host == "oge.gov" or host.endswith(".oge.gov"))) \
                    or white_house_oge_pdf
                if source_url.scheme != "https" or source_url.username or source_url.password or not official_host:
                    raise ValueError("Production disclosure source_url must use its allowlisted official host")
            event_date = date.fromisoformat(required(row, day_field))
            filed = timestamp(row.get("filed_at"))
            if filed.date() < event_date:
                raise ValueError("Filing precedes disclosed event")
            if filed > timestamp(data["meta"]["data_cutoff_at"]):
                raise ValueError("Filing exceeds coverage cutoff")
            lo, hi = row.get(prefix + "_low"), row.get(prefix + "_high")
            if type(lo) is not int or type(hi) is not int or not 0 <= lo <= hi:
                raise ValueError("Disclosure ranges must be explicit nonnegative integers")
            if kind == "transactions":
                option_fields = (row.get("option_type"), row.get("strike_price"),
                                 row.get("expiration_date"))
                if row.get("instrument_type") == "Option":
                    if option_fields[0] not in {"Call", "Put"} or \
                            type(option_fields[1]) not in {int, float} or option_fields[1] <= 0:
                        raise ValueError("Option transactions require a valid type and strike price")
                    if date.fromisoformat(required(row, "expiration_date")) < event_date:
                        raise ValueError("Option expiration precedes its transaction")
                elif any(value is not None for value in option_fields):
                    raise ValueError("Option details require an Option instrument")
            ticker = row.get("ticker")
            if ticker:
                ticker = ticker.upper() if isinstance(ticker, str) else ""
                if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-^/]{0,31}", ticker):
                    raise ValueError("Invalid ticker")
                row["ticker"] = ticker
    health = data.get("source_health")
    if not isinstance(health, list) or any(not isinstance(row, dict) for row in health):
        raise ValueError("source_health must be an array of objects")
    seen = set()
    allowed = set("source_id source source_type source_url status last_checked_at last_successful_sync_at data_cutoff_at detail".split())
    for row in health:
        if set(row) - allowed or row.get("source_id") not in HEALTH_SOURCES or row["source_id"] in seen:
            raise ValueError("Invalid or duplicated source health")
        if not demo and row.get("status") == "simulated":
            raise ValueError("Production source health cannot be simulated")
        seen.add(row["source_id"])
        timestamp(row.get("last_checked_at"))
    health.sort(key=lambda row: row["source_id"])
    if not demo:
        empty = not data["people"] and not data["transactions"] and not data["reported_holdings"]
        if empty and not allow_empty_production:
            raise ValueError("Empty production publication requires the explicit bootstrap gate")
        if not empty and (not data["people"] or not (data["transactions"] or data["reported_holdings"])):
            raise ValueError("Production publication requires people and at least one verified disclosure")
    encode(data)  # Reject non-finite numbers even in optional fields.
    return data


def build(payload: dict, *, generated_at: str, max_index_bytes: int = 8192,
          max_blob_bytes: int = 8 * 1024 * 1024, allow_production: bool = False,
          allow_empty_production: bool = False, allow_market: bool = False,
          market_commit: str | None = None,
          market_pages: tuple[str, ...] | list[str] | None = None) -> Bundle:
    data = normalize(payload, allow_production=allow_production,
                     allow_empty_production=allow_empty_production,
                     allow_market=allow_market)
    mixed_market = any(row["source_id"] == TWELVE_DATA_SOURCE_ID
                       for row in data["security_market_data"])
    processor_version = "v2" if mixed_market else "v1"
    processor_sha = PROCESSOR_V2_SHA256 if mixed_market else PROCESSOR_SHA256
    if allow_market:
        if not re.fullmatch(r"[0-9a-f]{40}", str(market_commit or "")):
            raise ValueError("Licensed market publication requires a frozen market commit")
        if not data["security_market_data"]:
            raise ValueError("Licensed market publication requires market rows")
        if not isinstance(market_pages, (tuple, list)) or not market_pages \
                or any(not re.fullmatch(r"[0-9a-f]{64}", str(item)) for item in market_pages) \
                or len(set(market_pages)) != len(market_pages):
            raise ValueError("Licensed market publication requires unique content-addressed market pages")
        disclosed_tickers = {row["ticker"] for kind in ("transactions", "reported_holdings")
                             for row in data[kind] if row.get("ticker")}
        market_tickers = {row["ticker"] for row in data["security_market_data"]}
        market_coverage = data["meta"].get("market_coverage")
        expected_schema = (MIXED_MARKET_COVERAGE_SCHEMA if mixed_market
                           else MARKET_COVERAGE_SCHEMA)
        if not isinstance(market_coverage, dict) \
                or market_coverage.get("schema_version") != expected_schema:
            raise ValueError("Licensed market publication requires deterministic market coverage")
        if mixed_market:
            actual_sources = {row["source_id"] for row in data["security_market_data"]}
            if market_coverage.get("source_ids") != sorted(actual_sources):
                raise ValueError("Mixed market coverage must identify each price source")
        elif market_coverage.get("source_id") != MARKET_SOURCE_ID:
            raise ValueError("Alpaca market coverage must identify its source")
        covered = market_coverage.get("covered_tickers")
        unsupported_rows = market_coverage.get("unsupported_tickers")
        if not isinstance(covered, list) or any(not isinstance(item, str) for item in covered) \
                or covered != sorted(set(covered)) or set(covered) != market_tickers:
            raise ValueError("Market coverage covered_tickers must exactly match market rows")
        if not isinstance(unsupported_rows, list) or any(not isinstance(item, dict)
                                                         for item in unsupported_rows):
            raise ValueError("Market coverage unsupported_tickers must be an array of objects")
        unsupported: set[str] = set()
        for item in unsupported_rows:
            if set(item) != {"ticker", "reason"} or not isinstance(item["ticker"], str) \
                or item["reason"] not in (MIXED_UNSUPPORTED_REASONS if mixed_market
                                          else UNSUPPORTED_REASONS) \
                or item["ticker"] in unsupported:
                raise ValueError("Market coverage contains an invalid unsupported ticker")
            unsupported.add(item["ticker"])
        if unsupported & market_tickers or disclosed_tickers != market_tickers | unsupported:
            raise ValueError("Market coverage must classify every disclosed ticker exactly once")
    elif market_commit is not None or market_pages is not None:
        raise ValueError("market commit and pages require licensed market publication")
    candidate = deepcopy(data)
    candidate["meta"].update(snapshot_id="prepublication-validation", generated_at=generated_at)
    load("process_snapshot", version=processor_version).build_snapshot(candidate)
    if timestamp(generated_at) < timestamp(data["meta"]["data_cutoff_at"]):
        raise ValueError("Generation precedes data cutoff")
    files: dict[str, bytes] = {}
    indexes: dict[str, dict] = {}

    def blob(prefix: str, value: dict) -> str:
        content = encode(value)
        if len(content) > max_blob_bytes:
            raise ValueError("Shard size budget exceeded; no truncation performed")
        sha = digest(content)
        files[f"{prefix}/{sha}.json"] = content
        return sha

    def entity(kind: str, key: str, value: dict) -> None:
        prefix = f"{kind}/{bucket(kind, key)}"
        sha = blob(prefix, value)
        indexes.setdefault(f"{prefix}/index.json", {})[key] = sha

    board = {kind: data[kind] for kind in ARRAYS}
    if allow_market:
        board["security_market_data"] = []
    board_sha = blob("board", board)
    market_tickers = {row["ticker"] for row in data["security_market_data"]}
    for person in data["people"]:
        rows = {kind: [row for row in data[kind] if row["person_id"] == person["id"]]
                for kind in ("transactions", "reported_holdings")}
        required_market = sorted({row["ticker"] for records in rows.values() for row in records
                                  if row.get("ticker") in market_tickers})
        entity("people", person["id"], {"person": person, **rows,
                                        "requires": [f"market:{ticker}" for ticker in required_market]})
    tickers = sorted({row["ticker"] for kind in ("transactions", "reported_holdings")
                      for row in data[kind] if row.get("ticker")})
    for ticker in tickers:
        rows = {kind: [row for row in data[kind] if row.get("ticker") == ticker]
                for kind in ("transactions", "reported_holdings")}
        ids = {row["person_id"] for records in rows.values() for row in records}
        entity("tickers", ticker, {**rows, "people": [p for p in data["people"] if p["id"] in ids],
                                   "requires": [f"market:{ticker}"] if ticker in market_tickers else []})
    for path, shards in sorted(indexes.items()):
        content = encode({"shards": shards})
        if len(content) > max_index_bytes:
            raise ValueError(f"Index size budget exceeded: {path}; no truncation performed")
        files[path] = content
    # Include business time and presentation configuration: both affect processor output.
    settings = {key: data["meta"][key] for key in ("title", "subtitle", "timezone", "default_window_days")
                if key in data["meta"]}
    coverage = {"scope": "all_input_records", "universe_complete": False,
                "people_count": len(data["people"]), "market_enabled": allow_market,
                "publication_state": "bootstrap_empty" if not data["people"] else "active"}
    if allow_market:
        coverage.update(market_ticker_count=len(market_tickers),
                        market_missing_ticker_count=0,
                        market_unsupported_ticker_count=len(unsupported),
                        market_supported_complete=True,
                        market_coverage_sha256=digest(encode(market_coverage)))
    identity = {"storage_layout": LAYOUT, "schema_version": SCHEMA, "processor_sha256": processor_sha, "board": board_sha,
                "indexes": {path: digest(files[path]) for path in sorted(indexes)},
                "data_cutoff_at": data["meta"]["data_cutoff_at"], "settings": settings,
                "coverage": coverage}
    if allow_market:
        identity.update(market_commit=market_commit, market_pages=list(market_pages or []))
    manifest = {"schema_version": SCHEMA, "storage_layout": LAYOUT,
                "snapshot_id": digest(encode(identity)), "generated_at": generated_at,
                "data_cutoff_at": data["meta"]["data_cutoff_at"], "board": board_sha,
                "source_health": data["source_health"], "status_revision": digest(encode(data["source_health"])),
                "is_demo": data["meta"]["is_demo"], "processor_sha256": processor_sha,
                "coverage": coverage, **settings}
    if market_commit is not None:
        manifest["market_commit"] = market_commit
        manifest["market_pages"] = list(market_pages or [])
    files["manifest.json"] = encode(manifest)
    if len(files["manifest.json"]) > 16 * 1024:
        raise ValueError("Manifest exceeds gateway size budget")
    return Bundle(files, manifest)
