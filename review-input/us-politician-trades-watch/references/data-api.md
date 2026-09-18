# Data API contract

The Skill is request-driven. The remote service owns ingestion, parsing, immutable raw documents, deduplication, locks, market-data credentials, and the PostgreSQL transaction. Read [data-sources.md](data-sources.md) for the production allowlist and ingestion rules.

## Environment

- `POLITICIAN_DATA_API_URL`: base HTTPS URL, without a trailing slash.
- `POLITICIAN_DATA_API_TOKEN`: optional bearer token managed by the cloud runtime.
- `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`: required only in the remote data service; never expose them to the Skill snapshot or generated HTML.

## Refresh and snapshot endpoint

`POST /v1/snapshots`

Request:

```json
{
  "schema_version": "politician-dashboard/v1",
  "mode": "dashboard",
  "scope": {"type": "dashboard", "value": null},
  "force_refresh": false,
  "locale": "zh-CN",
  "include": ["people", "transactions", "reported_holdings", "security_market_data", "source_health"]
}
```

Allowed scope types are `dashboard`, `person`, `ticker`, and `search`. `value` is required except for `dashboard`. The service should inspect its last-check watermark, perform only the required incremental source checks, commit new records atomically, and return one coherent snapshot.

Successful response:

```json
{
  "refresh": {
    "status": "fresh",
    "requested_at": "2026-09-11T08:41:20Z",
    "completed_at": "2026-09-11T08:41:24Z",
    "failed_sources": []
  },
  "snapshot": {
    "meta": {"schema_version": "politician-dashboard/v1"},
    "people": [],
    "transactions": [],
    "reported_holdings": [],
    "security_market_data": [],
    "source_health": [],
    "summary": {},
    "activity_by_day": [],
    "priority_people": [],
    "top_people": [],
    "top_tickers": [],
    "recent_transactions": [],
    "market_moves": {"30": [], "90": []}
  }
}
```

The response may return the snapshot fields directly at the top level. `people`, `transactions`, `reported_holdings`, `security_market_data`, and `source_health` are canonical. The remaining collections are deterministic projections. `fetch_snapshot.py` rebuilds and validates those projections with `process_snapshot.py` before writing the request-local file, so backend/frontend drift fails closed instead of producing a mixed Dashboard.

Each derived `market_moves["30"|"90"]` row includes `first_transaction_date`, `return_baseline_date`, `price_as_of_date`, and `return_since_first_trade`. The baseline is the first completed SIP EOD close on or after the ticker's earliest transaction date inside the selected transaction-date window; the return ends at the latest completed close on `price_as_of_date`. These dates must be passed through to the UI so the percentage is never presented as an unlabeled generic 30/90-day return.

### `people[]`

```json
{
  "id": "house:P000197",
  "display_name": "Nancy Pelosi",
  "short_name": "Pelosi",
  "role": "众议院议员",
  "office_type": "Congress",
  "chamber": "House",
  "party": "D",
  "state": "CA",
  "disclosure_authority": "house_clerk",
  "priority": true,
  "priority_reason": "configured_priority_person",
  "portrait_url": "https://official.example/portrait.jpg"
}
```

Portraits are presentation assets, not financial evidence. A production renderer should prefer an official portrait URL or a data URI supplied by the service and use an initials fallback if neither is available.

`demo_reference_snapshot` is reserved for a local `meta.is_demo=true` fixture and is forbidden in every production response. The remote service must never populate it from GuruTrack or another aggregator; production person statistics are derived from official canonical records only.

`demo_priority_rank`, `demo_priority_count`, and `demo_priority_group` are also local review-only fields. They exist only to reproduce a frozen 14-person design fixture. A production response must omit them; the production renderer orders configured `priority=true` identities normally, and every count it displays is recalculated from the canonical `transactions[]` window. No displayed count is ever read from `demo_priority_count`.

### `transactions[]`

```json
{
  "id": "house:filing-id:row-4",
  "filing_id": "official-filing-id",
  "person_id": "house:P000197",
  "owner": "Spouse",
  "asset_name": "Issuer or disclosed asset description",
  "ticker": "TICKER",
  "ticker_mapping_basis": "filing_explicit",
  "instrument_type": "Stock",
  "transaction_type": "purchase",
  "transaction_date": "2026-08-27",
  "filed_at": "2026-09-04T00:00:00-04:00",
  "amount_low": 50001,
  "amount_high": 100000,
  "position_effect": "unknown",
  "position_effect_basis": null,
  "source_id": "house_clerk",
  "source": "U.S. House Clerk",
  "source_url": "https://disclosures-clerk.house.gov/example.pdf",
  "verification_status": "official_matched"
}
```

The secondary processor calculates `disclosure_lag_days` and eligible security-price observations. The remote service must not supply a non-unknown position effect based only on purchase/sale direction.

### `priority_people[]`

```json
{
  "person": {"id": "stable-person-id", "display_name": "Nancy Pelosi", "chamber": "House"},
  "priority_reason": "User-pinned prominent official",
  "source_authority": "House Clerk",
  "data_status": "available",
  "latest_report_period_end": "2025-12-31",
  "reported_value_low": 0,
  "reported_value_high": 0,
  "reported_holding_count": 0,
  "top_holdings": [],
  "recent_changes": []
}
```

The initial priority configuration should include stable IDs for Donald Trump and Nancy Pelosi. It may also include the President, Vice President, congressional leadership, relevant committee leadership, and user-pinned people. `priority_reason` explains the editorial rule; it must not claim the person moved a stock price.

### `reported_holdings[]`

```json
{
  "id": "holding-row-id",
  "filing_id": "official-filing-id",
  "person": {"id": "stable-person-id", "display_name": "Display Name"},
  "owner": "Spouse",
  "asset_name": "Issuer or asset description",
  "ticker": "TICKER",
  "report_period_end": "2025-12-31",
  "filed_at": "2026-05-15T00:00:00Z",
  "value_low": 1001,
  "value_high": 15000,
  "change_from_prior": "newly_reported",
  "source_id": "house_clerk",
  "source": "House Clerk",
  "source_url": "https://official.example/filing",
  "verification_status": "official_matched"
}
```

`reported_holdings` contains the latest verified holdings snapshot, not a position reconstructed from transaction reports. The API may return prior snapshots separately for historical analysis, but the Dashboard should not mix them into the current reported-holdings table.

### `security_market_data[]`

Ticker-scoped responses should include the current close, prior quarter-end comparison point, and a 24-month daily-close series separately from filing evidence:

```json
{
  "ticker": "WFC",
  "company": "Wells Fargo & Co.",
  "source_id": "alpaca_sip_eod",
  "feed": "sip",
  "timeframe": "1Day",
  "adjustment": "split",
  "as_of_date": "2026-09-11",
  "current_price": 90.29,
  "previous_quarter_end": "2026-06-30",
  "previous_quarter_end_price": 82.64,
  "price_source": "Alpaca SIP EOD",
  "price_history": [
    {"date": "2024-09-16", "close": 53.82},
    {"date": "2024-09-17", "close": 54.11}
  ]
}
```

The processor recalculates `current_price`, `as_of_date`, the prior-quarter baseline, and `quarter_change_pct` from the series; conflicting summary values are overwritten. Transaction rows must return `unknown` when a purchase/sale disclosure does not prove whether the position was new, increased, reduced, or closed.

### `source_health[]`

Every relevant registry entry uses a stable ID:

```json
{
  "source_id": "house_clerk",
  "source": "U.S. House Clerk",
  "source_type": "official_disclosure",
  "source_url": "https://disclosures-clerk.house.gov/FinancialDisclosure/ViewSearch",
  "status": "ok",
  "last_checked_at": "2026-09-14T09:41:52-04:00",
  "last_successful_sync_at": "2026-09-14T09:41:51-04:00",
  "data_cutoff_at": "2026-09-14T09:40:00-04:00",
  "detail": null
}
```

Use `source_type=policy_guidance` for the two ethics committees and `source_type=market_eod` for Alpaca. Policy-guidance rows provide deadline context and never contribute transactions. Official-disclosure rows include an HTTPS `source_url`; the global Dashboard keeps only the newest row per disclosure source and links to that official entry point.

Fallback response uses HTTP 200 with `refresh.status = "fallback"`, includes `failed_sources`, and returns the last successful snapshot. If there is no usable snapshot, return HTTP 503 without fabricated data.

## Behavioral requirements

- Acquire a distributed lock per source and scope. Concurrent calls should share or reuse an in-flight refresh.
- Generate a new `snapshot_id` only after a complete database commit.
- Store raw official files outside the Capafy runtime in immutable object storage.
- Never delete prior official records when a source is temporarily unavailable.
- Use bounded retries with jitter for transient upstream failures.
- Return `last_checked_at`, `last_successful_sync_at`, and `status` for every relevant source.
- Build `priority_people` from configured stable person IDs. Never use loose display-name matching to attach holdings or transactions to a prominent official.
- Build `reported_holdings` only from House/Senate annual or termination reports and OGE 278e reports that actually disclose holdings. Do not reconstruct holdings by cumulatively adding PTR/278-T transactions.
- Reject disclosure rows whose `source_id` is not `house_clerk`, `senate_efd`, or `oge`; reject market rows whose `source_id` is not `alpaca_sip_eod`.
- Reject production records that are not `official_matched` from canonical transaction/holding arrays. Keep raw-unparsed, manual-request, and superseded states in an audit/coverage collection.
- Keep market-price observations and their cutoff/source separate from disclosure evidence. Do not silently substitute intraday, adjusted, or synthetic prices for an EOD-close series.
- Do not derive `new_position`, `increase`, `reduce`, or `close` from transaction direction alone. Only `filing_explicit` and `matched_holding_comparison` are valid production bases; exclude unknown rows from quarter-effect aggregates.
- Fetch market bars with `feed=sip`, `timeframe=1Day`, and `adjustment=split`. Use only the latest completed U.S. trading session and never allow a provider-default downgrade to IEX.
- Redact authentication headers and upstream cookies from logs and error responses.

## Recommended freshness gates

These are minimum recheck intervals, not scheduled jobs:

- House: 30 minutes.
- Senate: 60 minutes.
- OGE and executive disclosures: 6 hours.
- Ethics guidance, roster, offices, and identifier mappings: 24 hours.
- SIP daily bars: once per newly completed U.S. trading session, with a 30-minute publication buffer.

`force_refresh=true` bypasses the age gate but must still respect source rate limits and distributed locks.
