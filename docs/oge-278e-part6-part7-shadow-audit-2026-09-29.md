# OGE 278e Part 6/7 shadow audit — 2026-09-29

## Scope and baseline

This change is limited to annual 278e Part 6 asset rows, Part 6 owner and endnote relationships, duplicate summaries, Part 7 transaction rows, and report completeness. It does not change 278-T, other disclosure sources, workflows, `review-input/`, production candidates, or GitHub branches.

The front-end acceptance baseline is the user's latest HTML, SHA-256 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`. Its actual field and missing-value semantics take precedence over older handoff material. The shadow ledger preserves source-reported `Unknown` attribution and nulls; it never substitutes `Self`, zero, a valuation date, or another inferred value. Part 6/7 outputs remain review evidence and are not added to the five canonical front-end arrays.

## Completeness definitions

The extraction now records `recognized_source_row_counts` per section. These are parser-detected rows; they are not a claim that every printed row was detected.

The Part 6/7 audit distinguishes these states:

1. **Row accounting complete**: the section appears on at least one page, the parser's per-section detected count equals the rows assigned to holdings, transactions, exclusions, and quarantine, and a zero-row section has an explicit empty marker. A contradiction between an empty marker and rows fails this check.
2. **Source capture complete**: row accounting is complete, `source_row_census_complete=true` is accompanied by a `whitehouse-278e-source-row-census/v1` record bound to the same PDF SHA, a method of `independent_page_row_audit`, an evidence path and evidence SHA, and matching independently counted row totals for Parts 2/5/6/7; physical locators and scoped row numbers must also have no collisions. Parser conservation alone never sets this attestation.
3. **Complete report**: source capture is complete and the relevant rows have no unresolved row qualification, ownership, or cross-report reconciliation issue. A full HTML publication has additional identity, amendment, archive and snapshot gates.

The audit emits `part6_completeness` and `part7_completeness` with the counts and status. It does not alter existing per-row admission decisions. A review report can therefore contain individually source-qualified rows while its coverage status remains `partial` because the input lacks a complete source census.

For Part 6, the duplicate summary counts duplicate physical locators, duplicate row numbers within a disclosed account, and possible same-name repeats within an account. It does not treat the same row number or asset name across unknown/different accounts as a duplicate. Owner evidence separately counts explicit parent-account links and exact endnote links; conflicting evidence and unresolved endnote rows remain visible. A possible duplicate is a review signal; no rows are merged automatically.

For Part 7, row accounting does not mean cross-report uniqueness. Even a well-formed annual-report row remains pending 278-T reconciliation. The public HTML's displayed transactions must therefore continue to come from the canonical transaction set after the existing deduplication and publication checks.

## Part 7 opt-in boundary

The private parser helper `_extract_page_rows(..., enable_trump_part7_recovery=False)` keeps recovery disabled by default. The public annual extractor opts in only when both the fixed White House URL and fixed source SHA resolve to the Trump 2025 parser version. The entrypoint passes that exact source-bound version as the opt-in; another source cannot use its damaged header or row geometry. The offline test `test_recovery_remains_inert_without_source_bound_v8_opt_in` verifies the disabled path.

Review materialization has a second narrow guard: `_transaction_candidate_allowed` requires the exact Trump 2025 SHA and v8 parser, conserved detected rows, and individually qualified transactions; the output is still a review artifact, with 278-T dedup explicitly pending. This is not a repository-wide or all-report opt-in. The new ledger adapter marks its output `shadow_only_not_promoted` and carries a separate Part 7 pending-reconciliation status. Nothing in this change enables a production writer or turns a partial report into a complete one.

## Fixed inputs and evidence limitations

The latest HTML was present and its SHA-256 was recomputed in the assigned worktree; it matched the user-provided value above.

The local `origin/evidence` tree lists four annual OGE PDF/metadata pairs. In each path, the 64-character filename component is the archive's declared PDF content hash:

| Document ID | Declared PDF hash in evidence path | Local Git blob availability |
| --- | --- | --- |
| `1462ab8c5542e5e7852589b0007899fc` | `2d8f441b87c97812d2f7ab298657f834b636a14b78594b0cddffbefd8fa1de28` | PDF and metadata blobs absent |
| `40ce0f66f853096985258e27002ddfbb` | `bcad0b4e58789135b758b5a73fc9584ff4bf9bff9cf52b8e4ca9d4189dbe9e3d` | PDF and metadata blobs absent |
| `69aeaa9d7455acd585258e27002ddee1` | `84b5987e4c8a418188600bea1e1ba6b44e0d5735cdd757cee5aba551977ca402` | PDF and metadata blobs absent |
| `e0e994cfa156d7cf852589b000789259` | `8f7a606ddf0016b66b3e5a72bb4cc906b162c798c062ef163e0a2bf8b52974ea` | PDF and metadata blobs absent |

Blob availability was checked with Git lazy-fetch disabled. The names above come from the tree only; neither PDF bytes nor metadata contents could be read, so these hashes were **not recomputed** and no factual report result is attributed to these four files. An explicit fetch attempt could not reach GitHub and was not retried.

The code contains two source-bound White House scan references used by existing parser tests: Trump 2025 278e `1cc7951c6f72fab008e921903c9a1d03d41a9910239f954e208b501d608553a3` and JD Vance 2025 278e `d43f25659a26474faae4df8218ff352e3c01a2eaf17b6ae35ae07649bbe90c3d`. The corresponding real PDF bytes were not locally available for this turn either. Those hashes are code attestations, not this turn's replay inputs.

Consequently, validation below uses only committed synthetic page fixtures and fixed unit-test rows. It does not claim a fresh real-PDF replay or a new production coverage count.

## Shadow ledger

`whitehouse_annual_ledger.build_annual_shadow_ledger` maps only Part 6 and Part 7 dispositions into the frozen `pipeline-candidate-row/v1` and `pipeline-run-manifest/v1` schemas. It calls the shared validators, carries the source hash/parser/rules/layout provenance, and rejects a row whose evidence page is absent or invalid instead of inventing a location. A validated row disposition means its extracted fields passed source-row checks; it does not authorize publication. The manifest remains in-memory unless a later shadow runner explicitly persists it under review artifacts.

The audit does not infer the full HTML holdings projection from a Part 6 asset row. It preserves raw value bands and valuation dates for later mapping. Null or unknown fields remain null/unknown, in line with the latest HTML baseline.

## Offline validation performed

- `test_oge_278e*.py`: 31 tests passed.
- `test_trump_part*.py`: 25 tests passed.
- `test_whitehouse_annual_ledger.py`: 2 tests passed.
- `test_whitehouse_annual_review.py`: 8 tests passed.

These tests cover fixed synthetic PDF page text/geometry and constructed source-bound rows, including empty-marker conflicts, duplicates, explicit versus unresolved ownership evidence, Part 7 dedup status, disabled recovery, ledger schema validation, and refusal to fabricate page evidence. They do not constitute a replay of any of the four missing evidence blobs.
