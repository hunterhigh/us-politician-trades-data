# Senate eFD electronic PTR audit (work package E)

Audit date: 2026-09-29
Code base: `origin/code` at `0d33e949a503770767d35fa64bff6709aaa395b1`, plus shadow ledger C v1 (`0a7225d60`).
Scope: electronic eFD catalog identity, report content identity, amendment relationships, and candidate row disposition. Paper/PDF scan parsing is outside this audit.

## Baselines and fixed input coverage

The current frontend baseline was checked at `C:\Users\admin\Downloads\politician-disclosures (3).html`. Its SHA-256 is `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`, matching the user-designated value. This audit makes no frontend contract changes.

Correction to the initial checkout-only inventory: although no evidence files are checked out, the local read-only Git ref `origin/evidence` contains Senate records. The evidence ref points to `80f086267677b8fde34314f24024bdc505d97cb0`. Its tree has 646 Senate paths: 149 PTR report HTML/metadata pairs, 96 annual report HTML/metadata pairs, 20 catalog objects, 6 roster files, and 113 paper-page files. Reading was restricted to locally available objects with Git lazy fetch disabled.

Of the 149 PTR metadata paths, 137 metadata/content pairs were locally available, passed the metadata SHA-256 and byte-length checks, and parsed successfully as electronic PTRs. Nine were paper reports, outside this work package. Three metadata blobs were not present locally; they were not fetched. No hash mismatch or electronic parser rejection occurred among the 137 available electronic pairs. This is an inventory of locally available Git objects, not a claim that the evidence ref is a complete production backup.

The added adversarial fixture is explicitly synthetic, contains two catalog rows with one UUID but different filer, office, and listed date, and has SHA-256 `A9E668B6FDC445A0F2E887FE873C6C70D26202B086E0E568C6C7DA959EAE5256`. It verifies that a duplicate official document key is rejected while assembling complete pagination. It is not an official eFD sample.

## Findings

1. `build_discovery` previously verified page continuity and total row count but did not ensure report UUID uniqueness across pages. Conflicting catalog rows could therefore be emitted as a complete discovery batch, with the duplicate only rejected later by the report-entrypoint archiver. The discovery boundary now rejects repeated UUIDs immediately. No downstream schema or persisted production data changed.
2. Existing electronic report parsing binds the report URL kind and UUID, archived byte length and SHA-256, catalog amendment label, in-document amendment title, printed transaction count, row numbering, and parser version. Duplicate printed row numbers and table-count mismatches fail closed.
3. Existing candidate logic groups revisions by matched person and report title date, accepts only numbered amendment tails with a unique strictly later official filing timestamp and allowed row-by-row comparisons, and leaves ambiguous or unsupported report groups unresolved. The synthetic suite exercises single and three-version chains, a missing bounded-window predecessor, an unnumbered amendment, time-order reversal, duplicate predecessor ambiguity, allowed narrow correction, and rejected unanchored rewrite.
4. Candidate accounting asserts that input extracted rows equal qualified plus quarantined plus superseded rows. Tests include a three-input-row amendment/ambiguity case and assert the row ledger closes.
5. Two locally available official PTR versions were replayed from raw HTML: document `cce52b36-d00c-4710-a8ee-e84893fb4be1` (SHA-256 `bf3b724d45d556e4c36f31a0b09ff1e881c2a9fa6409e91c9ac65565eb2f39e7`, Amendment 1, 26,191 bytes) and document `2b076d77-6bc1-4b67-8be9-8f45a787479f` (SHA-256 `ffdafce4919c76aeccdd4943610aee5110321bc5e442e88cd195bc8a14a91a07`, Amendment 2, 26,194 bytes). Both report title dates are 2024-11-15 and both contain 12 printed rows. Parser SHA and byte-length checks passed.
6. Both report rows were found in locally available archived catalog pages `abc180f4efc76ee3cd13975ab8174f3b957b31a8ed52d638dd7c6259d666d1ac` and `f260309b4a14f6992fd4d6aeebd546f02944d5d7539075427b87dff308ddf474`; each page's content SHA-256 equals its content-addressed filename. Current parser output matched the catalog-derived metadata for document UUID, filer name, office, portal date, report title date, amendment number, and official URL.
7. Identity matching against the locally available official roster `senate_efd/members/984865a4a6e00af68c9617ea45f52b8939f49143780c832cb68c747fa4bfef5e.roster.json` (roster SHA-256 `984865a4a6e00af68c9617ea45f52b8939f49143780c832cb68c747fa4bfef5e`) uniquely returned `senate:T000278`, class `alias`, for both. The latest `origin/state` status points to roster SHA `7ed5a29def7139ac58a30b1e10e68f3c6059cf7938a7c5256c5374c94a5d1d8e`; that roster blob is listed in the evidence tree but is not locally available. Thus the replay verifies identity against an archived official roster, but not against the production status's latest roster binding.
8. The original `build_senate_candidate` function was run over these two real parsed reports in a disposable temporary directory. Its review status/catalog count and identity batch were a deliberately cropped shadow envelope derived from the two selected reports; no production review tree was changed. Candidate audit returned 1 resolved chain, 1 superseded predecessor report/12 rows, 12 candidate transactions, 0 quarantined rows, and closed row accounting `24 = 12 candidate + 12 superseded`. The change was confined to raw `transaction_type_raw` on row 2. The C v1 ledger validator independently accepted the same 24 row dispositions as 12 qualified and 12 excluded for verified supersession.

## Disposition ledger and limits

| Input class | Documents | Rows | Disposition |
|---|---:|---:|---|
| Added duplicate-UUID catalog fixture | 2 | N/A | Discovery fails closed; no discovery batch emitted |
| Existing amendment chain unit fixture | 3 | 3 synthetic rows | 1 amendment report retained; 1 verified predecessor superseded; 1 ambiguous predecessor report quarantined; accounting closes 3/3 |
| Official PTR Amendment 1 and 2 sample | 2 | 24 | 12 latest-version rows qualified; 12 predecessor rows superseded; 0 quarantined; accounting closes 24/24 |
| Official PTR evidence objects unavailable locally | 3 metadata blobs | N/A | Not fetched; excluded from replay |
| Official paper PTR samples | 9 | N/A | Excluded from E; paper/OCR work package F |

The two-report candidate replay proves the behavior of the current parser, identity matcher, amendment resolver, candidate builder, and ledger validator for these fixed archived bytes. It is a cropped shadow replay, not a full current production catalog rebuild: the active `origin/state` catalog contains 133 records with SHA-256 `a723c551017786772e3ab7b58adb567c8109381173e8980c6d9908e6cd9f84e2`, and the corresponding complete current review blobs were not locally available. No overall real-source disposition totals are claimed. The E code change remains limited to early duplicate document identity rejection; archived official reports were not copied into the code branch.

## Verification

`python -m unittest discover -s tests -p 'test_senate*.py' -v` passed all 86 Senate tests. `python -m unittest discover -s tests -p 'test_pipeline_ledger.py' -v` passed all 16 shadow-ledger tests. `git diff --check` passed. The real-report replay used only existing local Git objects with lazy fetch disabled and wrote only to a disposable temporary directory. The current HTML hash was verified independently as stated above. No network collection, evidence-branch write, production review/main write, workflow run, or production publication was performed.
