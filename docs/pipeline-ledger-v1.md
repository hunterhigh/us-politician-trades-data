# Pipeline evidence ledger v1

Status: new shadow schema; not yet connected to production writers or publication.

## Purpose and boundary

The ledger records what a source adapter observed before it projects facts into the five arrays consumed by the latest HTML baseline. It is for isolated `review`/offline artifacts. It does not replace official source files in `evidence`, source-specific parsers, automatic qualification, or the frontend snapshot. A ledger result cannot itself promote data to `main`.

This is an additive shared record shape: source adapters keep their own parsing and validation rules while emitting the same provenance envelope. The newest HTML remains the UI acceptance target; field names in these records describe extraction observations and do not change HTML field semantics.

## Versioned records

### Field observation — `pipeline-field-observation/v1`

One observation is one parser's claim about one source field. It contains:

- stable `observation_id` and source `field` name;
- `raw_value` as observed and `normalized_value` as interpreted, each scalar or `null`;
- `method`: `embedded_text`, `ocr`, `geometry`, or `derived`;
- optional `confidence` in `[0,1]`, plus a boolean `selected` to identify the currently chosen observation;
- `required_for_projection`, set from the latest HTML field matrix for the relevant output entity;
- one or more `locations`, each with a 1-based page, a location `kind`, optional row locator, and optional bounding box;
- `conditions`, a JSON object for OCR/render/geometry settings such as engine, engine version, DPI, PSM, and crop rule.

Multiple observations for the same field preserve disagreements. Observation IDs are unique within a candidate row, and at most one value per field may be selected. Unresolved disagreements are listed on the candidate row and block `qualified` disposition. A missing optional value can remain `null`; an unknown value must never be encoded as zero or an empty factual default.

Bounding boxes use `x0,y0,x1,y1` with top-left origin. Units are explicit: `pdf_points`, `pixels`, or `normalized`; pixel/point coordinates include page width and height. Normalized values lie in `[0,1]`. Locations are tied to the document SHA on the enclosing candidate row, so coordinates cannot be reused against a different file version.

### Candidate row — `pipeline-candidate-row/v1`

One detected physical/logical row contains:

- stable `candidate_id` and `run_id`;
- `source`: source ID, document ID, official URL and immutable source SHA-256;
- `parser`: parser ID/version, qualification `rules_version`, and `layout_fingerprint`;
- the derived `idempotency_key`;
- a `disposition`: `qualified`, `quarantined`, `excluded`, or `unrecognized`;
- machine-readable `reasons` and `unresolved_conflicts`;
- `required_projection_fields`, populated according to the relevant latest-HTML projection;
- field observations and row-level evidence locations.

`qualified` requires one selected, non-null normalized observation for every named required projection field, no blocking reason, and no unresolved conflicts. Source-specific validators still decide whether a value is actually valid (date chronology, amount meaning, identity, amendment relation, and evidence closure); this common validator only enforces envelope integrity.

Non-qualified rows require at least one reason. A ticker/market field can remain nullable where the HTML permits an unmapped ticker; that does not prevent an otherwise valid transaction row from being represented.

### Run manifest — `pipeline-run-manifest/v1`

One workflow or local replay includes:

- `run_id`, workflow name/ID, trigger, exact 40-character `code_commit`, start/completion timestamps with timezone, and `source_scope`;
- per-document source hash, parser/rules/layout versions, idempotency key, final document disposition, and reason when failed/excluded;
- counts for discovered/archived documents and parsed/failed/no-row/excluded documents;
- row disposition counts for qualified/quarantined/excluded/unrecognized rows, plus `accounted_rows`;
- output artifact paths and SHA-256 values, plus downstream run IDs when known.

Shadow source runs may additionally pin `evidence_commit` and `review_commit` (full Git SHAs) and list hashed `input_artifacts`. Combined bundles preserve byte-for-byte copies of their source manifests as hashed outputs; each `source_runs` entry binds its source run ID to one of those output paths and hashes. The validator rejects malformed optional commits, malformed input hashes, duplicate output paths, and source-run paths absent from the outputs list.

Each listed document has exactly one terminal disposition and its `source_id` must appear in the manifest's `source_scope`. Listed document totals must agree with the corresponding counts. `accounted_rows` must equal the sum of all four row dispositions. A document-level failure does not fabricate row results; row counts and file counts remain separate. Output paths must be relative and remain within the run artifact root.

## Idempotency and version changes

`idempotency_key` is `pipeline:v1:` followed by SHA-256 of canonical UTF-8 JSON containing `source_id`, `source_sha256`, `parser_id`, `parser_version`, `rules_version`, `layout_fingerprint`, and the candidate schema version. JSON keys are sorted; separators are compact; Unicode is preserved. Replaying the same immutable input and versions yields the same key. Changing the source bytes, parser, rules, layout fingerprint, or candidate schema produces a new key and a separately auditable result.

Do not include volatile `run_id`, timestamps, workflow attempt number, or current branch refs in this key. Those identify execution, not the interpretation of an immutable source file.

## Example

```json
{
  "schema_version": "pipeline-candidate-row/v1",
  "candidate_id": "house-ptr:document-hash-row-17",
  "run_id": "house-review-12345",
  "source": {
    "source_id": "house_clerk",
    "document_id": "20035420",
    "source_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/20035420.pdf",
    "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "parser": {
    "parser_id": "house-ptr",
    "parser_version": "house-ptr-2026-04",
    "rules_version": "eligibility-v1",
    "layout_fingerprint": "electronic-ptr-v2"
  },
  "idempotency_key": "pipeline:v1:fdfe9086fad5f8c612321ffd6201e85db5de8560dde45589494b2f95ad6b2dd7",
  "disposition": "quarantined",
  "reasons": ["notification_date_unreadable"],
  "unresolved_conflicts": [],
  "required_projection_fields": ["transaction_date", "transaction_type", "asset_name", "amount_low", "amount_high"],
  "evidence_locations": [
    {"page": 2, "row_locator": "row-17", "kind": "table_row"}
  ],
  "observations": [
    {
      "schema_version": "pipeline-field-observation/v1",
      "observation_id": "obs:transaction_date:row-17",
      "field": "transaction_date",
      "raw_value": "08/14/2026",
      "normalized_value": "2026-08-14",
      "method": "embedded_text",
      "confidence": 1.0,
      "selected": true,
      "required_for_projection": true,
      "locations": [
        {
          "page": 2,
          "row_locator": "row-17",
          "kind": "table_cell",
          "bbox": {"x0": 24.0, "y0": 320.0, "x1": 82.0, "y1": 333.0,
                   "unit": "pdf_points", "page_width": 612.0, "page_height": 792.0}
        }
      ],
      "conditions": {"extractor": "pdf_text_layer"}
    }
  ]
}
```

The example uses a synthetic SHA-256 and is quarantined because a required notification date is not readable. Its key was calculated by `pipeline_ledger.idempotency_key`. Full run manifests have independent examples in the offline tests.

## Validation and adoption

`backend/src/unison_snapshot/pipeline_ledger.py` validates envelopes without a third-party dependency. The validators reject malformed source hashes, missing locations, invalid coordinate units, conflicting selected values, mismatched idempotency keys, invalid timestamps, unsafe artifact paths, unsupported dispositions, and incomplete disposition accounting. They do not contact official sources, write branches, parse PDFs, or publish data.

The first implementation stage only creates/validates isolated records. Source adapters may adopt it one at a time after golden fixture review. Production workflow integration requires a separate shadow-run diff showing complete row accounting and no unexplained changes; a new schema version is required if field semantics or validation rules change incompatibly.
