# Automatic column fingerprint within fixed failed House PTR tables

This opt-in shadow step uses the three fixed failed scans already counted in
[`house-ptr-failed-grid-samples-2026-09-30.md`](house-ptr-failed-grid-samples-2026-09-30.md).
Inputs remain `evidence=0bfde57073838652d58ed1fa814b42c49bf7bd5c`,
`review=edd5f8081c3c068dbfc1350179cd00a21d5441a7`, and the exact PDF
SHA-256 values in that record. The latest user HTML SHA-256 remains
`d60282832dc0e38e47be900fdd37aa386db474df404989467ffb8b55367efaa4`;
the seven pre-price-history return values are uncomputable and display `—`.

The table region and visual classification in each fixed sample spec were
previously selected by inspection. **Within that region**, the new code
automatically measures vertical lines through the transaction row band. It
accepts only a tail run of 10 or 11 amount cells with regular 2.7%–3.7% page
width spacing, the matching number of action cells, and the expected asset,
date, and notification boundaries. Each page is classified independently; its
detected family must agree with the inspected sample family. A missing or
shifted line, unexpected page count, source hash mismatch, nonmatching family,
or non-quarantined input row fails the entire sample.

| Document | Detected pages | Physical rows | Cell boxes per row | Total cell boxes | Status |
| --- | --- | ---: | ---: | ---: | --- |
| 9115704 | 10 amount / 3 action on both pages | 7 | 17 | 119 | failed, quarantined |
| 9116260 | 11 amount / 4 action on both pages | 7 | 19 | 133 | failed, quarantined |
| 9116326 | 11 amount / 4 action on both pages | 8 | 19 | 152 | failed, quarantined |

The 404 source-bound cell boxes cover owner, asset, transaction date,
notification date, every printed action option, and every printed amount
option for the 22 previously located physical rows. They are recorded in
`docs/house-ptr-checkbox-cells-<id>-shadow.json`. Every cell has
`status=unread_unverified` and `value=null`; every row has
`quarantined_shadow_unverified_cells`, `candidate_id=null`; every report keeps
`file_status=failed_open`. The existing production candidate set is untouched.

To reproduce, retrieve each PDF from the fixed evidence commit and use its
matching checked-in physical grid and spec:

```text
python -m unison_snapshot.house_ptr_checkbox_cells_shadow --pdf <fixed.pdf> --grid docs/house-ptr-failed-grid-<id>-shadow.json --spec docs/house-ptr-failed-grid-<id>-spec.json --output <new-output.json>
```

Run from the repository root with `PYTHONPATH=backend/src`; compare the new
JSON with the checked-in cell shadow. The CLI refuses an existing output.
Runtime used: Python 3.11.9, pdfplumber 0.11.9, Pillow 12.3.0, Poppler
pdftoppm 26.07.0. No OCR, parser retry, workflow, candidate, evidence, review,
or production ref was changed.

## Recovery limit

The geometry is a crop plan, not transaction facts. The next safe experiment
is per-cell OCR plus independent source-image checks for dates, action marks,
and amount marks. A missing or multiple amount check, a malformed or
out-of-period transaction date, or an unreadable action keeps the row
quarantined. File-level retry remains blocked until **all** visible rows on
every page have independently accounted dispositions. The current fingerprint
has only been shown on three scans and depends on inspected table regions; it
is not a general document-layout detector.
