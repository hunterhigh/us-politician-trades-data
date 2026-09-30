# Fixed failed House PTR samples: row locators without promotion

The fixed 403-document House shadow ledger has 21 archived `failed` reports.
This opt-in audit selects three of those 21 for which the official PDF and
visual row counts can be verified. The sample spans two inspected checkbox
table layouts. It does not reclassify any of the 21 archived statuses.

Inputs are `evidence=0bfde57073838652d58ed1fa814b42c49bf7bd5c` and
`review=edd5f8081c3c068dbfc1350179cd00a21d5441a7`; the experimental
code branch builds on `code=58eb265e35b291bbe72111501ee84ee65ab8d892`.
The latest frontend remains the user-provided HTML with SHA-256
`d60282832dc0e38e47be900fdd37aa386db474df404989467ffb8b55367efaa4`.
The user's seven pre-price-history return values are uncomputable and display
as `—`; these source-row audits do not change that rule.

| Fixed report | PDF SHA-256 | Inspected layout | Visible page rows | Lower bound | Archived state |
| --- | --- | --- | --- | ---: | --- |
| 9115704 | `c830ee046083e8894f0a360f011186e755f3bbf0d6b83c7084611e80e1776d81` | 10 amount columns, 2026 older checkbox form | 5 + 2 | 7 | failed |
| 9116260 | `40abd1de969ff344fab5f3c8950a17ebb8ab5df993ded910f00e48d0d47e385d` | 11 amount columns, Tony Wied scan | 4 + 3 | 7 | failed |
| 9116326 | `be114957d03486636e639fe9d6c124c71ba8634e2f38f5dfb672eee92502ad2d` | 11 amount columns, Tony Wied scan | 4 + 4 | 8 | failed |

The three machine reports have 22 unique, source-bound page/row locators.
**All 22 have `quarantined_shadow_failed_report`, `candidate_id=null`, and
`file_status=failed_open`.** These are physical row locations only. The tool
does not OCR a new transaction, infer missing fields, alter the stored parser
failure, or write any formal candidate. The archived error for each sample is
preserved in the report. Earlier isolated parsing recovered seven rows for
9115704 and two rows for each Tony Wied scan, but the latter two each miss
visible rows; a successful local parse cannot close their whole files.

The inspected page regions in each `*-spec.json` were chosen after viewing
both PDF pages. For each band the tool re-renders the fixed PDF at 150 DPI,
checks horizontal grid coverage above 55% of the page width sampled from 7%
to 90%, requires visible asset-cell ink, and records the bounding box in PDF
points. It excludes the printed example and trailing blank table slots through
the inspected regions. The 55% threshold avoids one short spurious line in
9116260 page 2 without removing the fainter real lines in 9116326. Layout
family labels are **manual classification** of the fixed images. The census
proves the stated lower bounds for these files; it does not establish a
general layout detector or full field evidence.

To reproduce, retrieve the three PDFs from
`house_clerk/documents/2026/<id>/<sha>.pdf` at the fixed evidence commit,
and the corresponding `house_clerk/failures/2026/<id>/<sha>.json` at the fixed
review commit. Check each PDF SHA-256. Run from the repository root with
`PYTHONPATH=backend/src` (PowerShell `$env:PYTHONPATH='backend/src'`):

```text
python -m unison_snapshot.house_ptr_failed_grid_shadow --pdf <fixed.pdf> --failure <fixed-failure.json> --spec docs/house-ptr-failed-grid-<id>-spec.json --output <new-output.json>
```

Run once for each ID; the output must match its checked-in
`docs/house-ptr-failed-grid-<id>-shadow.json`. The CLI refuses an existing
output file and fails on a source hash, failure identity, page count, table
boundary, or row ink mismatch. Runtime used: Python 3.11.9, pdfplumber 0.11.9,
Pillow 12.3.0, Poppler pdftoppm 26.07.0.

Next recovery work should bind each locator to source-cell OCR or inspected
field evidence for asset, dates, direction, amount, and owner, then require
complete page coverage before any file-level retry decision. These 22 rows
remain quarantined while that evidence is absent.
