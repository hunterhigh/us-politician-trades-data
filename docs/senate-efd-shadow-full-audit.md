# Senate eFD electronic PTR full shadow replay

The fixed official catalog has 131 reports: 122 electronic PTR links and 9 paper
viewer links. The electronic shadow reads only `/search/view/ptr/` URLs. The
per-report [coverage audit](senate-efd-shadow-coverage-2026-09.json) records
all 131 catalog entries, their official URL and archived source hash, the
legacy extraction state, electronic shadow row dispositions, and qualified
candidate set differences. It is a local audit artifact, not a production
candidate or an update to `review-input/`.

Fixed inputs for the completed replay:

| Input | Exact value |
| --- | --- |
| Code | `4f3e8e608ad15745a8ff708e4a26de205f25dcfc` |
| Evidence | `80f086267677b8fde34314f24024bdc505d97cb0` |
| Review | `9749718e5ae837057db7c031d431714fc72988d1` |
| Catalog | `senate_efd/catalog/0cf0622f563dbd276ed05b96424765f9a596515fd494e03bbb2c1b25ed611a63.json` |
| Identity batch | `senate_efd/identities/0cf0622f563dbd276ed05b96424765f9a596515fd494e03bbb2c1b25ed611a63/d26c545c27ea0fc05a55b6317b521e6993f73ff107f4f5166b0a0308998bc7b6.json` |
| Roster | `senate_efd/members/7ed5a29def7139ac58a30b1e10e68f3c6059cf7938a7c5256c5374c94a5d1d8e.xml` |
| Historical amendment supplement | `ebe495dde334280f83b169304e039b6b92b9cd604db6c128fd17024d6e6118f5` |
| Legacy qualification Git blob | `3d183e31872c638a4523dd821a379f1418d0acfe` |

The actual local output is in
`C:\Users\admin\.codex\worktrees\pipeline-senate-paper\whitehouse\.local\senate-efd-122-electronic-supplement`.
Its `manifest.json` SHA-256 is
`fcbca4673b407ae0a23e436372545774b3b14fd42c56e30b2b69238069a72e6c`;
its `candidate_rows.json` SHA-256 is
`6089b56d5fbd486894db5f23956ed32dd175b1281ca15e815a9bd3a934b9f769`.
The output is reproducible from the fixed inputs in a clean checkout of the
recorded code commit. The latest frontend HTML remains the consumer baseline.

The replay accounted for all 1,647 archived electronic rows: 1,435 qualified,
171 quarantined, and 41 excluded as verified superseded rows. All 122 current
electronic reports had an extraction and at least one row, so there were zero
current electronic file failures and zero `no_rows` reports. Four paper viewer
reports have 9 legacy extracted rows, of which one entered the formal candidate.
Five other paper viewer reports have no legacy extraction; their row counts
remain unknown. Historical `report_failures/` artifacts coexist with successful
current electronic extractions and are not counted as current failures.

The legacy qualification audit has 1,656 input rows and 1,436 qualified
candidate rows. Removing the 9 parsed paper rows leaves the same 1,647
electronic input rows; removing the one qualified paper row leaves the same
1,435 qualified electronic IDs. The electronic qualified ID sets match exactly.
The paper reports remain outside the electronic shadow, including the four
with a legacy extraction. Their unresolved work is tracked separately in the
paper audit; an unparsed paper viewer does not mean a zero-transaction report.

Use `--catalog-path` with the fixed catalog and omit `--document-id` to replay
all electronic reports. The entrypoint verifies catalog page hashes, official
URLs, the identity batch and roster binding, each archived HTML and fixed
extraction, and historical amendment predecessors. A catalog electronic
report with an archived source but no review extraction becomes one `failed`
document with `row_count: null`, reason
`fixed_review_extraction_missing`, and unknown aggregate source row count.
An absent or ambiguous archived source fails the replay instead of inventing
a source digest. Existing two-report `--document-id` selection remains
available for diagnostic replays.

The seven `backend.tests.test_senate_shadow` tests passed locally after the
five missing objects in the partial Git clone were fetched by their exact
Git object IDs and verified before insertion into the local object store.
The full 122-report CLI replay also completed locally. No GitHub Actions
workflow or production publish was run for this shadow output.
