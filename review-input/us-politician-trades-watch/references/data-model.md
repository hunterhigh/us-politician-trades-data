# Disclosure data model and interpretation

## Core entities

### Person

Stable internal ID, legal/display name, office type, chamber, party, state, term dates, disclosure authority, and source identifiers. Name matching must not be the primary key.

### Filing

Official filing ID, form type, reporting period, filing date, stable `source_id`, source authority, official source URL, raw-document URI, content hash, amendment relationship, parser version, and verification status.

### Transaction

Filing ID, person ID, disclosed owner, asset name, ticker when confidently mapped, asset type, transaction type, transaction date, amount low/high, description, source location, and optional position-effect classification with its basis.

### Holding

Filing ID, person ID, disclosed owner, asset name, ticker when confidently mapped, value low/high, income type/range, and reporting-period end. A holding is a dated disclosure, not a real-time position.

### Priority person

A configured stable person ID, prominence reason, official source authority, latest holdings-filing period, aggregate reported range, top reported holdings, and recent verified transaction disclosures. Prominence is editorial routing, not a claim that the disclosure caused a market move.

### Security market snapshot

Ticker, company name, price as-of date, current EOD close, prior quarter-end date and close, `source_id=alpaca_sip_eod`, `feed=sip`, `timeframe=1Day`, `adjustment=split`, and a 24-month daily-close series of `{date, close}` observations. Market prices are a separate dataset from disclosure records. A missing or stale price series must produce an explicit unavailable or stale state rather than a fabricated chart.

## Holding snapshot changes

Compare two verified holdings filings for the same stable person, disclosed owner, and normalized asset identity. Allowed labels are:

- `newly_reported`: present in the newer filing and absent from the prior filing;
- `range_increased`: the statutory value range moved upward;
- `range_decreased`: the statutory value range moved downward;
- `unchanged_range`: the reported range did not change;
- `no_longer_reported`: absent from the newer filing;
- `incomparable`: identity, owner, or value bands cannot be compared reliably.

These labels describe filing-to-filing differences. They do not prove the exact transaction, timing, or whether a position was fully exited. A purchase or sale claim requires a corresponding transaction disclosure.

## Quarter-to-date position effects

The security page may group politician activity into `new_position`, `increase`, `reduce`, `close`, and `unknown`, but every non-unknown classification must include `position_effect_basis`. Production bases are limited to `filing_explicit` and `matched_holding_comparison`; `simulated` is allowed only for internal review fixtures.

A purchase does not by itself prove a new position or an increase, and a sale does not by itself prove a reduction or complete exit. If the available filing evidence cannot distinguish the effect, store `unknown` and exclude it from the new/increase/reduce-or-close counts. Display the number of unclassified records beside the aggregate. Quarter aggregates count unique people per effect group, not raw disclosure rows; one person may appear in more than one group if separately supported records show different effects.

## Verification states

- `official_matched`: normalized row is linked to an official filing.
- `official_raw_unparsed`: official filing exists but at least one field could not be reliably parsed.
- `manual_request_pending`: the official record requires a manual disclosure request.
- `superseded`: replaced by an amendment or corrected filing.
- `simulated`: demonstration data allowed only when `meta.is_demo=true`; never valid as production evidence.

Only `official_matched` rows belong in production transaction, holding, and ordinary aggregate arrays. `official_raw_unparsed`, `manual_request_pending`, and `superseded` are coverage/audit states, not substitute financial records. Demo arrays may use only `simulated` while `meta.is_demo=true`.

## Time fields

- `transaction_date`: date reported for the transaction.
- `filed_at`: date the disclosure was submitted or published.
- `last_checked_at`: latest attempt to inspect the source.
- `last_successful_sync_at`: latest completed source sync.
- `data_cutoff_at`: newest committed evidence included in the snapshot.

Never substitute one for another. “Recent trades” should normally filter by transaction date and also display filing date. “New disclosures” should filter by filing date.

## Post-disclosure performance

Treat performance as a derived security-price observation linked to a verified transaction, not as the official’s investment return. A derived record should retain `transaction_id`, `price_source_id=alpaca_sip_eod`, `as_of_date`, `horizon_days`, `security_return_pct`, `performance_basis`, and an eligibility flag. A fixed-horizon field such as `underlying_return_1y_after_filing` is usable only after the full horizon has elapsed and the required price observations exist.

Dashboard `market_moves[window]` rows retain `first_transaction_date`, `return_baseline_date`, and `price_as_of_date`. `return_baseline_date` is the first completed SIP EOD session on or after the earliest transaction date for that ticker inside the selected 30/90-day transaction-date window. `return_since_first_trade` compares that close with the latest completed close at `price_as_of_date`; switching the window can therefore change both the baseline date and the return.

For a person-level performance summary, return the eligible sample count, direction-alignment rate, and median security return for the stated horizon. Direction alignment means a purchase followed by a non-negative security return or a sale followed by a non-positive security return. The detail rows must still expose ticker, asset name, transaction direction, amount band, disclosed owner, instrument, transaction date, filing date, disclosure lag, observation horizon, and security return.

When a full-year field is unavailable, do not label a shorter observation as a one-year return. Use an explicit label such as “since filing” and show the observation date. Do not call security-price movement profit, portfolio performance, predictive skill, or realized return.

## Demo-only reference snapshots

`people[].demo_reference_snapshot` is an optional presentation fixture allowed only when `meta.is_demo=true`. It may hold a frozen third-party profile summary, annual activity, most-traded ticker counts, and visible example cards so the demo can exercise dense person-page states without fabricating thousands of canonical rows; the fixture may therefore carry fields the current UI does not render, as `demo_priority_count` also does. It must retain its reference name, HTTPS page URL, cutoff context, and caveats. It must not be joined into `transactions[]`, `reported_holdings[]`, market data, global 30/90-day aggregates, or question-answer results. Production processing rejects the field outright.

`people[].demo_priority_rank`, `people[].demo_priority_count`, and `people[].demo_priority_group` are a separate compact-directory presentation fixture. They are allowed only when `meta.is_demo=true`, require `priority=true`, and must have a unique positive integer rank, a non-negative integer count, and a non-empty group. They are never canonical activity statistics and cannot enter window totals, ticker aggregates, source evidence, or question-answer results. Production processing rejects any of these fields.

A reference snapshot does not prove owner, position effect, current holding, exact execution price, or portfolio return. Do not infer those fields. If the reference page uses ambiguous return labels, preserve them only as explicitly labeled display values and do not map them to canonical post-filing return fields.

## Aggregation

Sum all lower bounds to produce an aggregate minimum and all upper bounds to produce an aggregate maximum. Never use range midpoints unless the user explicitly asks for a labeled estimate. Do not calculate profit or current exposure without separately sourced market-price and position data.

## Source boundary

Official filing and amendment > official raw document awaiting parsing. Third-party disclosure and holdings vendors are outside the production data model. Alpaca SIP is used only for the separate market-price dataset and conservative U.S. equity reference matching; it never overrides a disclosed value or date.

The canonical frontend snapshot schema is `politician-dashboard/v1`. Its required arrays are `people`, `transactions`, `reported_holdings`, `security_market_data`, and `source_health`. `summary`, `activity_by_day`, `priority_people`, `top_people`, `top_tickers`, `recent_transactions`, and `market_moves` are deterministic projections generated from those arrays by `scripts/process_snapshot.py`; they are not independent sources of truth.
