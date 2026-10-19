# Approved open amount semantics

This optional consumer implements the user's 2026-10-19 decisions for both
transactions and reported holdings. The designated latest HTML and its frozen
extracted template remain unchanged. `render_dashboard.py` verifies that template
and applies only explicit approved amount changes.

- A bounded record retains its original two integer bounds and no extra marker.
- A disclosed open threshold has a nonnegative integer `amount_low` or `value_low`,
  a null upper bound, and an explicit boolean `*_low_exclusive`.
  `true` means “Over”; `false` means “At least”.
- A null upper bound without that marker is unknown and rejected.
- Counts include all qualifying disclosure rows. Finite totals exclude open
  values and carry separate open-record metadata. Open records remain visible
  with their threshold and official source. Upper-bound rankings use bounded
  records only, and identify excluded open records separately.
- Holdings use one selected report period, without merging older periods. This
  change does not authorize publishing incomplete reports.

The v3 processor loads its own hash-verified v2 module to retain established
identity, date, source, market, and performance handling. Validation envelopes
are internal only; output always restores the null upper bound. Existing v1/v2
consumers and old snapshot readers remain available. The builder selects v3 only
when an explicit open record exists; finite-only snapshots retain v2.

Checks: `backend/tests/test_disclosure_amounts.py` and
`frontend-e2e/tests/open-amounts.e2e.mjs`. The browser check accepts
`OPEN_AMOUNTS_HTML` generated from that test's `fixture()`, with optional
`PLAYWRIGHT_MODULE` and `PLAYWRIGHT_CHROMIUM_EXECUTABLE` for local runtimes.
