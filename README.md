# House machine qualification queue

This branch contains machine extractions, deterministic identity matches, automatic qualification results,
quarantined exceptions, legacy review templates, and parse failures.
Qualification artifacts may feed a complete candidate snapshot; only the publication workflow writes `main`.
Every artifact is keyed to an immutable PDF SHA-256 from the `evidence` branch and the parser code commit.
