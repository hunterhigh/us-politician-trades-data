"""Offline normalized-data producer. Live source admission is deliberately closed."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, date
import hashlib
import json
import math
import re
from urllib.parse import urlsplit

from .codec import bucket, digest, encode
from .legacy import PROCESSOR_V2_SHA256, PROCESSOR_V3_SHA256, load
from .disclosure_amounts import POLICY as AMOUNT_POLICY, is_open_amount, validate_amount
from .market_store import (MARKET_COVERAGE_SCHEMA, MIXED_MARKET_COVERAGE_SCHEMA,
                           SOURCE_ID as MARKET_SOURCE_ID, TWELVE_DATA_SOURCE_ID,
                           UNSUPPORTED_REASONS, MIXED_UNSUPPORTED_REASONS,
                           validate_market_rows)

SCHEMA = "politician-dashboard/v1"
LAYOUT = "hash-sharded-v2"
TRANSACTION_RELATION_POLICY = 'source_bound_independent_disclosures/v1'
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
OPTIONAL_FIELDS = {"transactions": {"amount_low_exclusive"},
                   "reported_holdings": {"value_low_exclusive"}}


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
            allowed = FIELDS[kind] | OPTIONAL_FIELDS.get(kind, set())
            if set(row) - allowed:
                raise ValueError(f"Unexpected fields in {kind}: {sorted(set(row) - allowed)}")
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
            validate_amount(row, prefix)
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
    validate_transaction_disclosure_relations(data)
    return data


def _transaction_relation_identity(relation: dict) -> tuple[str, str]:
    """Validate the original locator without inventing printed row numbers."""
    common = {'transaction_id','source_id','filing_id','source_url','source_sha256',
              'section','status','possible_ptr_transaction_ids'}
    sha = relation.get('source_sha256')
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha):
        raise ValueError('Transaction disclosure relation source hash differs')
    if relation.get('source_id') == 'senate_efd':
        number = relation.get('printed_row_number')
        if (set(relation) != common | {'printed_row_number'} or relation.get('section') != 'Part 4b' or
                not isinstance(number, str) or not re.fullmatch(r'[1-9]\d*', number)):
            raise ValueError('Senate annual disclosure relation locator differs')
        return 'senate-annual-part4b:' + hashlib.sha256(f'{sha}:part4b:{number}'.encode()).hexdigest()[:24], 'senate-ptr:'
    if relation.get('source_id') == 'house_clerk':
        page, top = relation.get('page_number'), relation.get('top_points')
        if (set(relation) != common | {'page_number','top_points'} or relation.get('section') != 'Schedule B' or
                type(page) is not int or not 1 <= page <= 1200 or
                type(top) not in (int, float) or not math.isfinite(top) or
                not 0 <= top <= 20000 or round(top, 2) != top):
            raise ValueError('House annual disclosure relation locator differs')
        return 'house-annual-b:' + hashlib.sha256(f'{sha}:{page}:{top:.2f}'.encode()).hexdigest()[:24], 'house-ptr:'
    if relation.get('source_id') == 'oge':
        page, number, digest = relation.get('page_number'), relation.get('printed_row_number'), relation.get('source_row_identity_sha256')
        if (set(relation) != common | {'page_number','printed_row_number','source_row_identity_sha256','report_period_end','source_binding_sha256'} or
                not re.fullmatch(r'(?:19|20)\d{2}-12-31', str(relation.get('report_period_end', ''))) or
                relation.get('section') != 'Part 7' or type(page) is not int or not 1 <= page <= 1200 or
                not isinstance(number, str) or not number.strip() or
                not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest) or
                not re.fullmatch(r'[0-9a-f]{32}', str(relation.get('filing_id', '')))):
            raise ValueError('OGE annual disclosure relation locator differs')
        fields = ('source_id','filing_id','source_url','source_sha256','section','page_number',
                  'printed_row_number','report_period_end','source_row_identity_sha256')
        binding = hashlib.sha256(json.dumps({key: relation[key] for key in fields}, ensure_ascii=False,
                                           sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if relation.get('source_binding_sha256') != binding:
            raise ValueError('OGE annual disclosure relation source binding differs')
        return 'oge-annual-row:' + digest[:24], 'oge-278t:'
    raise ValueError('Unsupported annual disclosure relation source')


def validate_transaction_disclosure_relations(data: dict, *, allow_unloaded_ptr: bool = False) -> None:
    """Full-input relation validation; no additional facts or source fetches."""
    relations=data['meta'].get('transaction_disclosure_relations')
    annual_ids={r['id'] for r in data['transactions'] if str(r['id']).startswith(('senate-annual-part4b:','house-annual-b:','oge-annual-row:'))}
    if relations is None:
        if annual_ids:raise ValueError('Annual disclosure facts require complete relationship metadata')
        return
    if not isinstance(relations,list):raise ValueError('Transaction disclosure relations must be a list')
    facts={r['id']:r for r in data['transactions']};seen=set()
    for relation in relations:
        if not isinstance(relation,dict):raise ValueError('Transaction disclosure relation must be an object')
        expected, ptr_prefix = _transaction_relation_identity(relation)
        key=relation['transaction_id']
        if key != expected or key in seen:
            raise ValueError('Transaction disclosure relation source identity differs')
        seen.add(key);fact=facts.get(key)
        if fact is None or any(fact.get(k)!=relation[k] for k in ('source_id','filing_id','source_url')):
            raise ValueError('Transaction disclosure relation references another or missing fact')
        possible=relation['possible_ptr_transaction_ids']
        if (not isinstance(possible,list) or any(not isinstance(i,str) for i in possible) or len(possible)!=len(set(possible)) or
                relation['status']!=('possible_ptr_overlap' if possible else 'cross_report_relationship_unconfirmed')):
            raise ValueError('Transaction disclosure relation status differs')
        for ptr in possible:
            other=facts.get(ptr)
            if other is None and allow_unloaded_ptr and ptr.startswith(ptr_prefix):continue
            if (other is None or not ptr.startswith(ptr_prefix) or other.get('source_id')!=relation['source_id'] or
                    other.get('person_id')!=fact['person_id']):
                raise ValueError('Transaction disclosure relation possible PTR is not a same-person fact')
    if seen!=annual_ids:raise ValueError('Annual disclosure relationship metadata does not cover every published annual fact')


def restore_shard_disclosure_relations(meta: dict, shard: dict, *, subset: bool = False) -> None:
    """Restore only optional metadata already present in the loaded shard."""
    policy=meta.get('transaction_disclosure_relation_policy')
    present='transaction_disclosure_relations' in shard
    if policy is None and not present:
        validate_transaction_disclosure_relations({'meta':{},'transactions':shard['transactions']})
        return
    if policy!=TRANSACTION_RELATION_POLICY:raise ValueError('Unsupported transaction disclosure relation policy')
    relations=shard.pop('transaction_disclosure_relations',[])
    validate_transaction_disclosure_relations({'meta':{'transaction_disclosure_relations':relations},
        'transactions':shard['transactions']},allow_unloaded_ptr=subset)
    meta['transaction_disclosure_relations']=relations


def build(payload: dict, *, generated_at: str, max_index_bytes: int = 8192,
          max_blob_bytes: int = 16 * 1024 * 1024,
          max_board_bytes: int = 24 * 1024 * 1024, allow_production: bool = False,
          allow_empty_production: bool = False, allow_market: bool = False,
          market_commit: str | None = None,
          market_pages: tuple[str, ...] | list[str] | None = None) -> Bundle:
    data = normalize(payload, allow_production=allow_production,
                     allow_empty_production=allow_empty_production,
                     allow_market=allow_market)
    market_sources = {row["source_id"] for row in data["security_market_data"]}
    mixed_market = TWELVE_DATA_SOURCE_ID in market_sources
    # The v2 consumer carries current price-history semantics for every
    # snapshot, including Alpaca-only production data. Keep processor routing
    # independent from coverage validation, which still reflects market rows.
    processor_version = "v2"
    processor_sha = PROCESSOR_V2_SHA256
    has_open = any(is_open_amount(row, prefix)
                   for kind, prefix in (("transactions", "amount"), ("reported_holdings", "value"))
                   for row in data[kind])
    if has_open or "holding_report_coverage" in data["meta"] or 'transaction_disclosure_relations' in data['meta']:
        processor_version, processor_sha = "v3", PROCESSOR_V3_SHA256
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
            classified = market_tickers | unsupported
            raise ValueError(
                "Market coverage must classify every disclosed ticker exactly once: "
                f"missing={sorted(disclosed_tickers - classified)[:10]}, "
                f"extra={sorted(classified - disclosed_tickers)[:10]}, "
                f"overlap={sorted(unsupported & market_tickers)[:10]}"
            )
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
        limit = max_board_bytes if prefix == "board" else max_blob_bytes
        if len(content) > limit:
            kind = "Board" if prefix == "board" else "Shard"
            raise ValueError(f"{kind} size budget exceeded; no truncation performed")
        sha = digest(content)
        files[f"{prefix}/{sha}.json"] = content
        return sha

    def entity(kind: str, key: str, value: dict) -> None:
        prefix = f"{kind}/{bucket(kind, key)}"
        sha = blob(prefix, value)
        indexes.setdefault(f"{prefix}/index.json", {})[key] = sha

    board = {kind: data[kind] for kind in ARRAYS}
    relations=data['meta'].get('transaction_disclosure_relations')
    def relation_fields(rows):
        ids={r['id'] for r in rows};selected=[r for r in relations or [] if r['transaction_id'] in ids]
        return {'transaction_disclosure_relations':selected} if selected else {}
    board.update(relation_fields(data['transactions']))
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
                                        **relation_fields(rows['transactions']),
                                        "requires": [f"market:{ticker}" for ticker in required_market]})
    tickers = sorted({row["ticker"] for kind in ("transactions", "reported_holdings")
                      for row in data[kind] if row.get("ticker")})
    for ticker in tickers:
        rows = {kind: [row for row in data[kind] if row.get("ticker") == ticker]
                for kind in ("transactions", "reported_holdings")}
        ids = {row["person_id"] for records in rows.values() for row in records}
        entity("tickers", ticker, {**rows, "people": [p for p in data["people"] if p["id"] in ids],
                                   **relation_fields(rows['transactions']),
                                   "requires": [f"market:{ticker}"] if ticker in market_tickers else []})
    for path, shards in sorted(indexes.items()):
        content = encode({"shards": shards})
        if len(content) > max_index_bytes:
            raise ValueError(f"Index size budget exceeded: {path}; no truncation performed")
        files[path] = content
    # Include business time and presentation configuration: both affect processor output.
    settings = {key: data["meta"][key] for key in ("title", "subtitle", "timezone", "default_window_days", "holding_report_coverage")
                if key in data["meta"]}
    if relations is not None:settings['transaction_disclosure_relation_policy']=TRANSACTION_RELATION_POLICY
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
    if has_open:
        manifest["amount_policy"] = AMOUNT_POLICY
    if market_commit is not None:
        manifest["market_commit"] = market_commit
        manifest["market_pages"] = list(market_pages or [])
    files["manifest.json"] = encode(manifest)
    if len(files["manifest.json"]) > 16 * 1024:
        raise ValueError("Manifest exceeds gateway size budget")
    return Bundle(files, manifest)
