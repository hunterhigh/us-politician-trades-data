# Decision: shadow evidence ledger before adapter migration

Date: 2026-09-29
Status: accepted for the refactor branch; production adoption deferred

## Context

The pipeline has source-specific extraction and qualification artifacts, but no single record envelope that consistently ties observed field values to an immutable source hash, physical evidence location, parser/rules/layout version, disposition, and replay identity. The latest user-designated HTML is the highest-priority frontend behavior baseline. Existing `review-input/` materials are retained as compatibility evidence only, and remain unmodified.

The implementation plan calls for a shared candidate/observation schema and run ledger before source adapters migrate. Several source adapters and workflow writers already run in production, so directly replacing their artifact formats or writer paths would make unrelated source failures harder to isolate.

## Decision

Create `pipeline-field-observation/v1`, `pipeline-candidate-row/v1`, and `pipeline-run-manifest/v1` with a validator that has no added runtime dependency. Begin in isolated tests and future shadow output paths. Keep source-specific extraction and qualification logic in source adapters. Keep canonical five-array snapshot generation and all production writers unchanged until each adapter demonstrates row accounting and a fixed-input diff.

Idempotency is bound to source ID/hash, parser ID/version, eligibility rules version, layout fingerprint, and candidate schema version. Volatile run IDs and timestamps are excluded. Every row disposition is explicit; every candidate value carries a source location; row and document counts are accounted separately.

## Consequences

- Source-specific work can move at different rates while using a common evidence envelope.
- The schema can expose missing or conflicting fields without changing the current HTML projection or claiming that missing fields are zero.
- Existing candidate and run artifacts remain readable during migration.
- A second storage path and per-adapter mapper must be maintained temporarily; production integration waits until the row-diff and recovery checks are complete.
- The common validator proves envelope consistency, not factual correctness. Official source, identity, amendment, amount, and field semantics remain each adapter's responsibility.

## Revisit when

Revisit the schema version before enabling production use, if the latest HTML changes a required field or null meaning, if a source cannot provide stable physical evidence locations, or if the diff tooling shows the schema causes duplicated expensive OCR output rather than reusable observations.
