# Senate eFD electronic PTR shadow entrypoint

This local, read-only replay emits `pipeline-candidate-row/v1` rows and one
`pipeline-run-manifest/v1` for selected electronic PTR report versions. The run
directory contains `manifest.json` and `candidate_rows.json`; the manifest
declares exactly one `candidate_rows` output and its SHA-256, so the directory
can be passed directly to `pipeline_qa.bundle_cli`. It does
not read paper viewer rows, modify `review-input/`, or write any production ref.
The latest HTML file `C:\Users\admin\Downloads\politician-disclosures (3).html`
(SHA-256 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`)
remains the consumer baseline.

The command requires exact code, evidence and review commits. The code commit
must equal a clean checkout's HEAD. The review identity batch binds an archived
roster XML by SHA-256. Each selected extraction is read from the fixed review
commit, and its fixed evidence metadata and HTML are re-parsed; any mismatch
stops the run before output. The existing amendment resolver and transaction
qualification rules decide each row. Verified superseded rows are `excluded`
with reason `superseded_by_verified_amendment`; unresolved revision groups are
`quarantined`. A detected row has exactly one disposition and is counted once.
The manifest includes fixed refs, per-document keys, counts and a hash of the
candidate row output. Output directory must not already exist.

`.github/workflows/senate-efd-shadow.yml` is a separate manual workflow. It
only runs when dispatched on `code`, grants `contents: read`, checks out that
exact code SHA without persisted credentials, fetches the two archived source
refs for local object access, then invokes the same entrypoint with the fixed
evidence/review commits below. It uploads the resulting two JSON files as a
seven-day Actions artifact. It has no production environment, source request,
review/evidence write, publication step, or scheduled trigger. Workflow wiring
was checked locally; no GitHub Actions run has been invoked for this change.

Example for the two locally available official amendments, after using the
actual clean checkout commit for `--code-commit`:

```powershell
$env:PYTHONPATH='backend/src'
python -m unison_snapshot.senate_shadow --repo . `
  --code-commit (git rev-parse HEAD) `
  --evidence-commit 80f086267677b8fde34314f24024bdc505d97cb0 `
  --review-commit 9749718e5ae837057db7c031d431714fc72988d1 `
  --identity-path senate_efd/identities/8d8c5a7abbec9e3d956a9cceae78505f374b2237e156a4a9fcca7463d43e4619/984865a4a6e00af68c9617ea45f52b8939f49143780c832cb68c747fa4bfef5e.json `
  --roster-source-path senate_efd/members/984865a4a6e00af68c9617ea45f52b8939f49143780c832cb68c747fa4bfef5e.xml `
  --document-id cce52b36-d00c-4710-a8ee-e84893fb4be1 `
  --document-id 2b076d77-6bc1-4b67-8be9-8f45a787479f `
  --run-id senate-two-amendments --output-root .local/senate-two-amendments
```

In the fixed two-report sample, Amendment 2 contributes 12 qualified rows and
the verified Amendment 1 predecessor contributes 12 excluded rows: 24 input
rows = 24 candidate ledger rows. Running only Amendment 2 leaves all 12 rows
quarantined because its predecessor is absent from that selected replay.
This is a deliberately selected subset, not a complete catalog run or a
production candidate. The current review catalog, paper PTRs, source failures,
and any other electronic reports are outside this sample. A complete run must
select and validate its full source scope before its totals can describe source
coverage. The latest HTML's page behavior remains a separate end-to-end gate.

After the bundle layout fix, the fixed two-report run directory was passed to
the existing `pipeline_qa.bundle_cli` in the integration checkout. It accepted
the source manifest and row artifact, verified their binding and hash, and
reported two documents and 24 rows. This was a local offline acceptance run;
the Actions workflow has still not been triggered.
