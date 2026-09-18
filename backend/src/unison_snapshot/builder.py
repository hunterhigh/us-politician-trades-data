"""Offline normalized-data producer. Live source admission is deliberately closed."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, date
import re
from urllib.parse import urlsplit

from .codec import bucket, digest, encode
from .legacy import PROCESSOR_SHA256, load

SCHEMA = "politician-dashboard/v1"
LAYOUT = "hash-sharded-v2"
ARRAYS = ("people", "transactions", "reported_holdings", "security_market_data")
SOURCES = {"house_clerk", "senate_efd", "oge"}
HEALTH_SOURCES = SOURCES | {"house_ethics_guidance", "senate_ethics_guidance", "alpaca_sip_eod"}
SOURCE_HOSTS = {
    "house_clerk": {"disclosures-clerk.house.gov", "clerk.house.gov"},
    "senate_efd": {"efdsearch.senate.gov"},
}
FIELDS = {
    "people": set("id display_name short_name role office_type chamber party state disclosure_authority priority priority_reason portrait_url".split()),
    "transactions": set("id filing_id person_id owner asset_name ticker ticker_mapping_basis instrument_type transaction_type transaction_date filed_at amount_low amount_high position_effect position_effect_basis source_id source source_url verification_status".split()),
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


def normalize(payload: dict, *, allow_production: bool = False) -> dict:
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
            if rows:
                raise ValueError("Market publishing disabled pending licensing and separate branch implementation")
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
                official_host = host in SOURCE_HOSTS.get(source_id, set()) \
                    or (source_id == "oge" and (host == "oge.gov" or host.endswith(".oge.gov")))
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
    if not demo and (not data["people"] or not (data["transactions"] or data["reported_holdings"])):
        raise ValueError("Production publication requires people and at least one verified disclosure")
    encode(data)  # Reject non-finite numbers even in optional fields.
    return data


def build(payload: dict, *, generated_at: str, max_index_bytes: int = 8192,
          max_blob_bytes: int = 8 * 1024 * 1024, allow_production: bool = False) -> Bundle:
    data = normalize(payload, allow_production=allow_production)
    candidate = deepcopy(data)
    candidate["meta"].update(snapshot_id="prepublication-validation", generated_at=generated_at)
    load("process_snapshot").build_snapshot(candidate)
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
    board_sha = blob("board", board)
    for person in data["people"]:
        rows = {kind: [row for row in data[kind] if row["person_id"] == person["id"]]
                for kind in ("transactions", "reported_holdings")}
        entity("people", person["id"], {"person": person, **rows, "requires": []})
    tickers = sorted({row["ticker"] for kind in ("transactions", "reported_holdings")
                      for row in data[kind] if row.get("ticker")})
    for ticker in tickers:
        rows = {kind: [row for row in data[kind] if row.get("ticker") == ticker]
                for kind in ("transactions", "reported_holdings")}
        ids = {row["person_id"] for records in rows.values() for row in records}
        entity("tickers", ticker, {**rows, "people": [p for p in data["people"] if p["id"] in ids], "requires": []})
    for path, shards in sorted(indexes.items()):
        content = encode({"shards": shards})
        if len(content) > max_index_bytes:
            raise ValueError(f"Index size budget exceeded: {path}; no truncation performed")
        files[path] = content
    # Include business time and presentation configuration: both affect processor output.
    settings = {key: data["meta"][key] for key in ("title", "subtitle", "timezone", "default_window_days")
                if key in data["meta"]}
    coverage = {"scope": "all_input_records", "universe_complete": False,
                "people_count": len(data["people"]), "market_enabled": False}
    identity = {"storage_layout": LAYOUT, "schema_version": SCHEMA, "processor_sha256": PROCESSOR_SHA256, "board": board_sha,
                "indexes": {path: digest(files[path]) for path in sorted(indexes)},
                "data_cutoff_at": data["meta"]["data_cutoff_at"], "settings": settings, "coverage": coverage}
    manifest = {"schema_version": SCHEMA, "storage_layout": LAYOUT,
                "snapshot_id": digest(encode(identity)), "generated_at": generated_at,
                "data_cutoff_at": data["meta"]["data_cutoff_at"], "board": board_sha,
                "source_health": data["source_health"], "status_revision": digest(encode(data["source_health"])),
                "is_demo": data["meta"]["is_demo"], "processor_sha256": PROCESSOR_SHA256,
                "coverage": coverage, **settings}
    files["manifest.json"] = encode(manifest)
    if len(files["manifest.json"]) > 16 * 1024:
        raise ValueError("Manifest exceeds gateway size budget")
    return Bundle(files, manifest)
