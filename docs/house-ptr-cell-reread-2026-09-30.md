# Fixed House failed scans: four row-level cell rereads

This opt-in follow-up rereads four selected high-signal rows from the three
fixed failed scans. It uses the source-bound physical and cell locators in
[`house-ptr-checkbox-cell-shadow-2026-09-30.md`](house-ptr-checkbox-cell-shadow-2026-09-30.md).
The inputs remain `evidence=0bfde57073838652d58ed1fa814b42c49bf7bd5c`
and `review=edd5f8081c3c068dbfc1350179cd00a21d5441a7`; PDF SHA-256s
are checked by code against each observation file and cell ledger. The latest
frontend HTML SHA-256 remains
`d60282832dc0e38e47be900fdd37aa386db474df404989467ffb8b55367efaa4`.
Its seven pre-price-history return values are uncomputable and display `—`.

Each fixed observation JSON records what is visible in the official image,
separately from OCR. The code renders full pages at 200 DPI using the existing
parser's Tesseract configuration and rereads selected asset/date cell crops
at 300 DPI with single-line mode. Both raw word lists and confidence values
are preserved. Checkbox crops retain each option's inner dark-pixel fraction
and the separately recorded image observation; the code **does not** infer a
selected direction or amount from that fraction. This is evidence for further
verification, not a transaction extractor.

| Physical row | Asset, page OCR → crop OCR | Transaction date | Notification date | Image checkbox observation | Main unresolved issue |
| --- | --- | --- | --- | --- | --- |
| 9115704 p1:r1 | `Adobe Sys Inc Com (ADBE)` → same | `02/20/26` → same | `\| 03/02/26` → `03/02/26` | purchase; A | checkboxes not machine verified; report failed |
| 9115704 p2:r1 | `Untted Health Group Inc Com (UNH)` → `United Health Group Inc Gom (UNH)` | `02/20/26` → same | `\| 03/02/26` → `03/02/26` | purchase; B | asset print is degraded; OCR passes disagree |
| 9116260 p1:r1 | `ADOBE INC` → same | `6/9/26}` → `6/9/26` | `7/24/26` → `TI2A126` | purchase; C | crop OCR conflicts with image and page OCR |
| 9116326 p1:r2 | `SALESFORCE INC` → same | `08/14/26` → same | `\| 09/02/26` → `09/02/26` | purchase; B | checkboxes not machine verified; report failed |

The image observations are fixed to exact source hashes, rows, and printed
option indices in `docs/house-ptr-cell-observations-<id>.json`. In particular,
the 9115704 page 2 asset text is marked `ambiguous`; neither OCR spelling is
promoted to a fact. For 9116260, the original full-page OCR agrees with the
visible `7/24/26`, while the crop reread conflicts. The machine output records
both `notification_date_ocr_conflicts_with_image_observation` and
`notification_date_original_crop_ocr_conflict`.

All four output rows remain
`quarantined_shadow_field_evidence_incomplete`, with `candidate_id=null`,
`qualified_rows=0`, and `file_status=failed_open`. No existing qualified House
row is removed, and none of these OCR readings enters a formal candidate.
Even where text agrees, the amount and direction marks have not passed a
machine check and the full reports have other unclosed physical rows. A
malformed or conflicting date, multiple or unreadable marks, or uncertain
asset identity must remain isolated.

To reproduce with the fixed PDFs, run from the repository root with
`PYTHONPATH=backend/src` and Tesseract 5.4.0.20240606 available at the supplied path:

```text
python -m unison_snapshot.house_ptr_cell_reread_shadow --pdf <fixed.pdf> --cells docs/house-ptr-checkbox-cells-<id>-shadow.json --observations docs/house-ptr-cell-observations-<id>.json --tesseract <tesseract.exe> --output <new-output.json>
```

Use IDs 9115704, 9116260, and 9116326 separately. Compare each new result
with `docs/house-ptr-cell-reread-<id>-shadow.json`. The CLI refuses an
existing output file. Actual run environment: Python 3.11.9, pdfplumber
0.11.9, Pillow 12.3.0, Poppler 26.07.0, Tesseract 5.4.0.20240606. This audit does
not modify the production parser, retry workflow, review, evidence, main,
or any candidate.
