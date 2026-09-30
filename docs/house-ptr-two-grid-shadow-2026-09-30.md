# Two fixed House PTR grids: physical row shadow audit

This is an opt-in, offline audit of two **already parsed** reports whose stored
extractions omit visible transaction rows. It neither changes row qualification
nor enables a retry. The latest frontend baseline remains the user-provided
`politician-disclosures (3).html` (SHA-256
`d60282832dc0e38e47be900fdd37aa386db474df404989467ffb8b55367efaa4`).

## Fixed inputs and repeatability

- Evidence commit: `0bfde57073838652d58ed1fa814b42c49bf7bd5c`.
- Review commit: `edd5f8081c3c068dbfc1350179cd00a21d5441a7`.
- The original isolated audit was prepared on a House audit branch and later
  integrated after the three-source bundle. These reports are experimental and
  must not be labeled a same-code production bundle with the three sources.
- Evidence paths:
  `house_clerk/documents/2026/9116290/7cc36ca57a26ba0331fff01f6210d58d41719f074017442c14fc47983b27b42d.pdf`
  and
  `house_clerk/documents/2026/9115813/737955c7c26c497eda37f4378e1af51409b6231204a82d7ae2c3f25c10e0ae84.pdf`.
  The SHA-256 names match the PDF bytes observed in this run.
- The corresponding stored JSON inputs are at
  `house_clerk/extractions/2026/<id>/<sha>.json` and
  `house_clerk/qualifications/2026/<id>/<sha>.json` in the fixed review commit.
- Runtime used: Python 3.11.9, pdfplumber 0.11.9, Pillow 12.3.0, Poppler
  pdftoppm 26.07.0. Rasterization is 150 DPI.

For each report, retrieve the fixed PDF and two fixed review JSON files from
those Git objects into a scratch directory. Run from the repository root with
`PYTHONPATH=backend/src` (PowerShell: `$env:PYTHONPATH='backend/src'`):

```text
python -m unison_snapshot.house_ptr_grid_shadow --pdf <fixed.pdf> --extraction <fixed-extraction.json> --qualification <fixed-qualification.json> --spec docs/house-ptr-grid-9116290-spec.json --output <new-output.json>
python -m unison_snapshot.house_ptr_grid_shadow --pdf <fixed.pdf> --extraction <fixed-extraction.json> --qualification <fixed-qualification.json> --spec docs/house-ptr-grid-9115813-spec.json --output <new-output.json>
```

Use each report's own files on its command. The CLI refuses an existing output
path. Compare each output with the checked-in
`docs/house-ptr-grid-<id>-shadow.json`. The source SHA and document ID are
checked before rendering. The page count, inspected table boundary coordinates,
every grid row's visible asset ink, stored row disposition conservation, and
unique mapping of each stored evidence box to one physical row must all pass.
Any mismatch fails the entire sample audit.

## What the machine report proves

The page regions in the two `*-spec.json` files were selected by visual
inspection of these exact official scans. Within each region the code measures
long horizontal grid rules and dark pixels in the asset cell. It generates a
source-bound locator, page, row number, grid bounding box, and ink fraction for
each physical band. It maps archived extraction boxes to those bands by page
and vertical center. This grid census does **not** read transaction values from
new rows. A locator is evidence of a visible row, not a verified trade.

| Document | PDF page rows | Archived extracted rows | Archived qualified | New shadow rows, quarantined | Archived candidate IDs retained |
| --- | ---: | ---: | ---: | ---: | --- |
| 9116290 | 5 + 6 = 11 | 4, all on page 1 | 1 | 7 (page 1 row 2; page 2 rows 1–6) | `house-ptr:c2a121352254ef9a9983f263` |
| 9115813 | 4 + 5 = 9 | 1, page 2 row 5 | 0 | 8 (page 1 rows 1–4; page 2 rows 1–4) | none |

Every observed band has exactly one disposition. The 20 physical locators
reconcile to 5 archived rows and 15 **new unrecognized shadow rows**. The
archived qualified row in 9116290 remains qualified with its unchanged ID and
values; the four archived quarantined rows also retain their extraction IDs.
9115813's archived extraction succeeded but its single garbled, quarantined
row is insufficient evidence of complete report coverage. Both reports have
`coverage_status=known_incomplete`; unhandled row counts are lower bounds.

## Safe recovery boundary

The checked-in module is audit-only and has no production call site. It
provides a way to crop each missing locator for later per-cell OCR or visual
verification, while keeping all new rows in
`quarantined_shadow_unrecognized`. Advancement would require source-bound
asset, transaction date and type, amount, owner, and row geometry evidence;
ambiguous fields remain quarantined. The inspected page regions are not a
general layout detector, and these two examples cannot justify automatic
retry for the other House reports. No candidate is added, removed, or revised
by this audit.
