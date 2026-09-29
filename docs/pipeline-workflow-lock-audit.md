# Pipeline workflow lock and handoff audit

Audit date: 2026-09-29
Read-only base: `origin/code` at `0d33e949a503770767d35fa64bff6709aaa395b1`.
Scope: current Actions triggers, branch writers, concurrency groups, and artifact/ref handoffs. No workflow files or remote refs were changed.

The current production workflow baseline was frozen in [`pipeline-refactor-baseline-2026-09-29.md`](pipeline-refactor-baseline-2026-09-29.md). The latest HTML designated by the user remains the highest-priority consumer target; this workflow audit does not override its field or missing-value semantics.

## Observed workflow shape

| Workflow | Trigger and role | Current writer protection / handoff |
|---|---|---|
| `house-state.yml` | Every six hours and manual; discovers/archives House source files and updates state | `disclosure-source-writer`; `cancel-in-progress: false` |
| `house-review.yml` | Successful `workflow_run` from House source, plus manual replay | Also uses `disclosure-source-writer`; checks out current `code` and fetches current evidence/review/state refs rather than receiving an immutable source-run artifact |
| `senate-efd.yml` | Every six hours and manual; electronic eFD catalog/review stages | Job-level source-writer locks; later stages pass run artifacts; artifact retention is one day |
| `oge.yml` | Every six hours and manual under explicit gates; catalog/archive/review stages | Job-level source-writer locks; review stages use run artifacts; artifact retention is one day |
| `senate-roster.yml` | Daily and manual roster refresh | `disclosure-source-writer` |
| `house-holdings.yml` | Manual annual batch | Workflow- and job-level `disclosure-source-writer` |
| `senate-paper-pages.yml` | Manual paper-page archive | Job-level `disclosure-source-writer` |
| `publish-complete.yml` | Weekdays on a switch and manual; builds market and complete snapshot, checks browser contract, publishes fixed refs | `publish-snapshot`; validates expected prior branch SHAs and reads back prepared commits |
| `publish.yml` | Manual legacy/bootstrap publication path | Shares `publish-snapshot`; not part of the normal complete publication path |
| `rollback.yml` | Manual fixed-commit rollback | Shares `publish-snapshot` |

The common source-writer group serializes House, Senate, OGE, roster, holdings, and paper archive jobs that use it. In the audited YAML, House review currently shares that general group; it is not represented as a separately named review-only lock. The source archive and review stages are therefore mutually exclusive within that group, but the name does not expose which branch mutation a given job is protecting.

## Handoff and recovery risks to address before J

1. **House event handoff is ref-based.** A successful House source workflow starts `house-review`, but the review job checks out `code` and fetches the latest branch refs. It does not bind its input to the exact evidence/state commits produced by the triggering run. A later writer could advance those refs before review begins.
2. **Concurrency is a mutex, not a durable queue.** The workflows use `cancel-in-progress: false`, which preserves a running writer. They omit the newer `queue` option, so GitHub's default permits one pending run and cancels/replaces an existing pending run when another arrives. GitHub now offers `queue: max` for up to 100 waiting runs, but that finite queue does not replace reconciliation from source state. Any future run manifest/checkpoint must make missed or coalesced triggers discoverable. See [GitHub Actions concurrency documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).
3. **Artifact retention is short.** Senate eFD and OGE source/review artifacts use one-day retention. This is enough for immediate jobs but is not a durable replay ledger; long-term recovery must bind to immutable evidence/state commits or persisted shadow artifacts.
4. **Publish reads one review snapshot.** Complete publication starts from the current review ref and checks expected prior `market` and `main` SHAs before compare-and-swap writes. It does not turn the review tip into a durable run manifest shared with discovery runs; a new shadow manifest should record the exact review commit and generated output hashes.
5. **Market and main are sequential refs.** The complete publisher advances `market` before `main`. If the second write fails after the first succeeds, recovery must explicitly pair the advanced market commit with the prior published main or retry the exact prepared main commit. Do not assume both branch updates are atomic together.
6. **Every writer needs job-level review.** Senate and OGE workflows contain multiple jobs; the lock audit must continue to verify the actual job that writes each of `evidence`, `state`, and `review`, not infer protection solely from a top-level workflow declaration. Senate paper/amendment plan and resolve jobs also need classification by target branch before any scheduling change.

## Integration boundary

This audit records existing behavior; it does not change triggers, locks, retention, or production branch writers. C through I establish offline record shapes and source-specific adapters first. J can then pass exact run IDs, commits, row manifests, and output hashes through shadow artifacts, while the current production publisher remains the sole writer until fixed-input comparisons, row conservation, HTML acceptance, and recovery checks pass.

## Offline bundle implementation update

The refactor worktree now includes `python -m unison_snapshot.pipeline_qa.bundle_cli`, which combines source run directories only after validating each `pipeline-run-manifest/v1`, matching the exact same `code_commit`, verifying the declared candidate-row artifact SHA-256, checking row dispositions against manifest counts, and rejecting duplicate source-document identities. Multiple runs from the same source are allowed when their document identities differ. It requires every row to bind to a parsed document and its source run, then writes deterministic combined rows, a manifest and byte-for-byte copies of source manifests with hashes under a caller-selected new or empty local output directory; nonempty run directories are never overwritten. It has no network or Git-ref write path.

This closes the offline bundle/conservation step for source runs that already emit the v1 manifest and candidate-row artifact. The bundler now also checks that each v1 row belongs to a parsed document and the exact source run named by its manifest.

## OGE manual shadow handoff update

The OGE workflow now records the exact collection code, post-archive evidence, and extraction-cache review commits, then on **manual** OGE workflow runs launches a separate read-only shadow job. That job downloads the same run's existing OGE artifact, checks out the recorded code commit, builds a `pipeline-run-manifest/v1` and `candidate_rows.json`, and uploads them as `pipeline-ledger-oge-<run id>` for seven days. The scheduled OGE path does not start this new job. The original review publication job continues to depend only on collection; the shadow job has no production branch write or secret access.

The producer checks direct catalog identity, archive metadata, extraction source hashes, parser row dispositions and failures before writing an immutable local artifact. A locally available official OGE PDF and catalog record were replayed through this producer and the shared bundle command: one archived document, 68 qualified rows, 0 other row dispositions, and 68 rows retained in the bundle with a passing output hash check. This was an offline replay with a deliberately cropped one-document archive batch, not a GitHub Actions run or a full 333-document production replay.

House and Senate now emit the shared manifest/row artifact shape from fixed evidence/review commits. Separate House and Senate manual read-only workflows upload them; a fourth manual read-only workflow downloads all three source artifacts from specified run IDs and verifies a combined bundle against one code commit. In the local integration checkout, fixed official OGE, House, and Senate samples combined as 5 documents and 100 observed rows (88 qualified, 12 excluded; one House failed scan retains unknown physical row count), with output hashes verified. No new workflow has yet run on GitHub; this is an offline shadow acceptance, not full source coverage or production publication.

The repository's existing evidence/review/main branch separation remains in force. No Codex task, local scheduler, or shadow adapter writes production branches.
