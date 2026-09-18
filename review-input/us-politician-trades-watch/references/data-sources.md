# Authoritative source allowlist and ingestion rules

Production disclosure records are allowed to originate only from the sources in this file. Competitor sites, newsletters, social posts, SEC 13F filings, paid holdings feeds, and search-engine snippets are not ingestion sources.

## Source registry

| `source_id` | Authority and entry point | Allowed use in this Skill | Access mode |
|---|---|---|---|
| `house_clerk` | U.S. House Clerk, [Financial Disclosure Reports](https://disclosures-clerk.house.gov/FinancialDisclosure) | House PTRs, annual reports, termination reports, amendments, filing metadata, and original documents | Public portal / year index; retain the original document and official URL |
| `senate_efd` | Secretary of the Senate, [eFD public search](https://efdsearch.senate.gov/search/home/) | Senate PTRs, annual reports, termination reports, amendments, filing metadata, and original documents | Public search; retain the original document and official URL |
| `oge` | U.S. Office of Government Ethics, [Public Financial Disclosure Guide](https://www.oge.gov/web/OGE.nsf/publicresources_disclosure-quickstart) and Officials' Individual Disclosures | OGE Form 278-T, 278e annual/termination reports, amendments, filing metadata, and original documents for the President, Vice President, PAS officials, DAEOs, and other covered executive officials | Direct public collection where available; otherwise record `manual_request_pending` and the responsible agency |
| `house_ethics_guidance` | House Committee on Ethics, [Financial Disclosure](https://ethics.house.gov/financial-disclosure/) | Reporting thresholds, form definitions, and due-date rules only | Policy metadata; never creates a transaction or holding row |
| `senate_ethics_guidance` | Senate Select Committee on Ethics, [Financial Disclosure](https://www.ethics.senate.gov/public/index.cfm/financialdisclosure) | Reporting thresholds, form definitions, and due-date rules only | Policy metadata; never creates a transaction or holding row |
| `alpaca_sip_eod` | Alpaca Market Data, [`GET /v2/stocks/bars`](https://docs.alpaca.markets/us/reference/stockbars) with the SIP feed | U.S. equity security master lookup and completed-session daily bars used only for background price observations | Authenticated API; request `feed=sip`, `timeframe=1Day`, and `adjustment=split` explicitly |

## Explicit exclusions

- Do not ingest SEC EDGAR Form 13F. It describes institutional investment managers and does not belong in this politician-only product.
- Do not ingest GuruTrack, Quiver Quantitative, Capitol Trades, Unusual Whales, or any other aggregator. They may be UI references only.
- Do not ingest paid/private holdings feeds, social media, news reports, copied spreadsheets, or search-result snippets.
- Do not silently fall back from Alpaca SIP to IEX, Yahoo pages, Stooq, Alpha Vantage, browser scraping, or synthetic prices.

## Disclosure ingestion sequence

1. **Discover official filings.** Check the official year index or public search for the scoped person, chamber, filing year, or latest watermark.
2. **Capture provenance before parsing.** Store `source_id`, official filing ID, official URL, discovered time, HTTP metadata, and the immutable original document in remote object storage. Compute SHA-256 before any OCR or normalization.
3. **Classify the filing.** Distinguish PTR/278-T transactions from annual, termination, candidate, new-entrant, and amendment reports. PTR/278-T rows never become a holdings snapshot.
4. **Parse without overwriting the source.** Preserve disclosed owner, asset description, transaction type, dates, amount/value bands, option terms, page/line location, and parser version. Store uncertain fields as null and retain the raw text region.
5. **Resolve identity by authority identifiers.** Map the filing to one stable person ID using official filing identifiers, office, chamber/agency, and term dates. Never attach records to Donald Trump, Nancy Pelosi, or another priority person through loose display-name matching.
6. **Resolve security conservatively.** Prefer a ticker printed in the filing. If it is absent, allow only a unique exact issuer match against the Alpaca U.S. equity asset reference and record `ticker_mapping_basis=alpaca_exact_issuer`; otherwise keep `ticker=null`.
7. **Apply amendments and deduplication.** The latest official amendment supersedes affected normalized rows but never deletes the prior raw document or audit history.
8. **Commit atomically.** Only after validation succeeds should the service advance its source watermark and issue a new `snapshot_id`.

## Position and performance derivation

- A purchase/sale direction does not establish `new_position`, `increase`, `reduce`, or `close`.
- Production position effects are allowed only when the official filing states the effect (`filing_explicit`) or when two comparable official holdings snapshots support the change (`matched_holding_comparison`). Otherwise use `unknown`.
- Do not use third-party classifications. Unknown rows stay visible in transaction detail but are excluded from new/increase/reduce-or-close aggregates.
- Price observations use split-adjusted SIP daily closes. They describe the security, not the official's execution price, cost basis, profit, or portfolio return.
- For “since transaction/filing” observations, use the first completed daily close on or after the event date and the latest completed-session close. Record the basis and price cutoff.

## Deadline metadata shown in the product

- House and Senate PTRs: the earlier of 30 days after written notification and 45 days after the transaction. Store the observed `transaction_date` and `filed_at`; do not assume when the filer received notice.
- OGE 278-T: generally due no later than 45 days after the transaction absent an approved extension. Public availability can occur later and can require an access request.
- `disclosure_lag_days` is the observed calendar-day difference between the reported transaction date and filing date. It is not automatically a lateness finding.
- `data_cutoff_at` is the newest committed evidence in the snapshot, not a claim that every legally reportable transaction by then is already public.

## Price request policy

Use environment-managed `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY`. The remote service should request the multi-symbol historical bars endpoint with:

```text
feed=sip
timeframe=1Day
adjustment=split
start=<24 months plus a 10-trading-day baseline buffer>
end=<after the latest completed U.S. trading session, never an unfinished session>
```

Follow `next_page_token` until all symbols are complete. The last returned bar becomes `current_price` and `as_of_date`. The prior-quarter comparison uses the last available bar on or before the prior calendar-quarter end. Keep `feed`, `timeframe`, `adjustment`, and the provider response cutoff in the stored market snapshot.

The request must set `feed=sip` explicitly because provider defaults depend on account entitlement. An authorization or entitlement error makes the price module unavailable; it must not trigger a lower-quality feed substitution.

## On-demand refresh gates

These are cache-age gates evaluated only when a user queries; they are not scheduled jobs:

| Source | Minimum age before a normal request rechecks it |
|---|---:|
| House Clerk | 30 minutes |
| Senate eFD | 60 minutes |
| OGE / agency disclosure availability | 6 hours |
| House and Senate ethics guidance | 24 hours |
| People/office mappings | 24 hours |
| Alpaca SIP daily bars | Cache until the next completed U.S. market session, then allow recheck after a 30-minute publication buffer |

`force_refresh=true` bypasses these age gates but not upstream rate limits, access terms, or distributed locks.

## Access and compliance boundary

The House public search page displays statutory restrictions, including restrictions on commercial use other than dissemination by news and communications media. Senate and OGE access can also require acknowledgements or a Form 201 request. Before a Capafy production launch, the operator must review the current portal terms and the product's intended use. The Skill must not automate an acknowledgement, impersonate the user, submit Form 201, or bypass access controls; it records such items as `manual_request_pending`.
