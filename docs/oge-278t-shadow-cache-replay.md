# OGE 278-T legacy-cache shadow replay

The manual `build-shadow-ledger` job can repair an old extraction artifact that
has no `source_rows`, but only by reparsing the matching official PDF and JSON
metadata from the exact `evidence_commit` emitted by the collection job. The
evidence worktree is checked out detached, its `HEAD` and clean status are
verified, and the parser verifies the PDF byte length and SHA-256 against the
metadata. No source URL is fetched by the shadow code.

Before replacing the cached extraction in memory, the replay compares its
complete transaction and quarantine structures with the reparse. The only
permitted additions are the new top-level `source_rows` inventory and parser
`cells` absent from transaction rows in the old cache. Changed values, row
order/count changes, new fields, missing archive bytes, or hash mismatches fail
the run. Physical rows are never synthesized from transaction or quarantine
arrays. The ledger is built only from the verified reparse. The job has
`contents: read`; it does not publish review data or alter evidence.
An empty physical-row inventory is valid when the fixed reparse and legacy
cache both contain no transaction or quarantine rows; the document remains a
`no_rows` result. The first remote replay exposed this case in document
`174165f6e1e120b185258db000347f54`, whose cached reason was
`transaction_table_not_found`.

## Offline evidence checked

The fixed collection run was `36556719856`; its evidence commit is
`353a443b4a76b15463e3e490d61b7d6162d350c8`. The 329 extraction JSONs supplied
with that run are legacy cache entries without `source_rows`. Their local
artifact hashes were:

| Input | SHA-256 |
| --- | --- |
| `oge-catalog.json` | `e9776720a67732d5d7eb90cc86c0e45cc77955a1e381ce3c3ca00ce47a4568e4` |
| `oge-report-batch.json` | `5b2d1fa6d5e44d0b1f13eba74eb618cee35346186270dfa8ac08d5bdb2caf31f` |
| `oge-extraction-batch.json` | `8ef1793ffc26bd89efa515a254991c3c9051d3ca71ffd43f6839ee5027c8f38e` |
| Sorted extraction-name/content-hash manifest (329 JSON files) | `db71fbfa5b7b7b1b07fcd1a33f2c9a0dee7de4d7cf0b95b9282bbf89f99368be` |

The locally available Mullin report `038f222e7b084f8c85258e3f002db296` was
read only from evidence commit `80f086267677b8fde34314f24024bdc505d97cb0`
(lazy fetch disabled). Its PDF SHA-256 is
`2232e039f5fc9c98f1a187b60fe275675215a91bcd779120b0d0184ccdb8d3ec`.
Reparse recovered 68 source rows; the old and new quarantines were identical,
and all 68 transactions matched after disregarding only the newly present
`cells` field. Evidence objects for commit `353a443...` were unavailable in
the local object store, so no 329-document replay is claimed here.

The artifact's 329 metadata records describe 108,241,184 PDF bytes in total
(median 5,762 bytes, maximum 26,880,519 bytes). The manual shadow job timeout
is 90 minutes to cover sequential PDF parsing. That is a timeout ceiling, not
a measured runtime or billing quote; actual CPU time depends on PDF page counts
and parser behavior. Native PDF text extraction is used; this replay does not
add OCR. Each report is hash-checked and compared independently, and any
unavailable/mismatching PDF blocks the ledger instead of yielding guessed
physical rows.
