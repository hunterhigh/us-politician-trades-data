# OGE 278-T shadow adapter

This adapter is an offline bridge from a previously archived OGE Form 278-T PDF extraction to `pipeline-candidate-row/v1`. It does not discover, request, download, write review candidates, or publish. Collection remains behind the existing OGE terms gate, and request-required/Form 201 rows cannot enter this adapter.

## What it checks

- Binds each extraction to one direct OGE catalog record by document ID, URL, filer name, agency, and title. The catalog record is revalidated with the existing official-host/direct-PDF rules.
- Rechecks report completeness, amendment status, pending disposition, filing date, transaction type, enumerated amount range, transaction date against filing date, asset name, and owner.
- Emits one ledger candidate per extracted physical table row. Existing parser transactions become `qualified`; parser rejects and report-level failures become `quarantined`; recognized headers/account headings/blank notices/footers become `excluded`; rows the parser did not dispose become `unrecognized`.
- Requires a complete `source_rows` inventory. A legacy extraction without it fails closed and must be replayed from its archived PDF before ledger adoption.

## Mapping to pipeline-ledger-v1

Each row carries page and extracted-row locator evidence, source SHA-256, parser ID/version, deterministic rules version, layout fingerprint, and the common idempotency key. Raw six-cell values map to field observations alongside the existing normalized parser values. Candidate rows are validated by the shared ledger module. Document-level OGE identity remains the exact catalog binding; this adapter does not invent a second identity schema.

The parser still extracts table blocks using PDF layout detection. Completeness here means every row in each extracted transaction-table block gets exactly one disposition. If layout detection fails to identify a table block, the report remains failed/incomplete through the existing document evidence state; this adapter does not assert that arbitrary page text is a transaction row.

## Golden fixture

`backend/tests/fixtures/oge_278t/gold_rows_v1.json` covers a qualified purchase, an unsupported type, a header, an empty row, and a parser-unrecognized physical row. Expected disposition counts are 1 qualified, 1 quarantined, 2 excluded, and 1 unrecognized. The test uses fixed synthetic bytes metadata only; it performs no network request and uses no protected production input.
