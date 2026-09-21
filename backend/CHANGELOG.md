# Changelog

All notable changes follow Semantic Versioning.

## Unreleased

- Added direct public-GitHub frozen-commit reading for dashboard, search, person and ticker selections.
- Added production-input validation, safe worktree materialization and an atomic manual publication workflow.
- Made the Cloudflare gateway optional for a later private-repository phase.
- Added House Clerk annual-index discovery with immutable SHA-256 source archiving; discovered filings remain explicitly unparsed.
- Added index-confirmed PTR archiving, page-coordinate extraction, official BioGuide identity matching, and deterministic automatic qualification with row-level quarantine.
- Expanded the House PTR parser against five official electronic filings covering options, wrapped amounts, cross-page rows and missing tickers.
- Added explicit human resolution for amended filings so revisions cannot silently overwrite an earlier record.
- Added recoverable House PTR checkpoints with failure retries, archive-state recovery, and fail-closed index-change anomalies.
- Added a scheduled production evidence branch that archives official House index ZIPs and bounded batches of original PTR PDFs with SHA-256 metadata.
- Added a GitHub production environment boundary for publication and source-archive workflows; external provider credentials remain environment secrets.
- Added a production qualification workflow that parses immutable House evidence into machine extractions, identity matches, qualified transactions, quarantined exceptions, and bounded failure records.
- Added a history-preserving production rollback workflow and upgraded official GitHub actions to Node 24-compatible v7 releases.
- Added a machine-readable review queue health summary for production monitoring.
- Completed the public GitHub production control plane with scoped environment deployment, protected production refs, scheduled House backfill, and resumable qualification state.
- Extended electronic PTR parsing for exact and open-ended amounts and final rows split across pages; image-only filings now report an explicit OCR requirement.
- Added bounded Tesseract OCR for image-only PTRs, including engine provenance, row confidence, conditional runner installation, and low-confidence quarantine.
- Added a validated House candidate assembler that combines qualified transactions, stable people and live source health, then runs the production builder and supplied frontend renderer.
- Added filing chronology quarantine and changed the remaining House backfill queue to newest filings first so 30/90-day frontend windows become useful earlier.
- Added a fail-closed parser for legacy House checkbox PTR forms using OCR coordinates for owner, transaction direction and disclosed amount bucket; ambiguous or low-confidence marks remain quarantined.
- Added deterministic support for four-digit dates, whitespace-damaged OCR titles, continuation-page table signatures and the wide-asset legacy checkbox layout; weak signatures and ambiguous marks still fail closed.
- Advanced the House legacy and retry parser versions so production replays actually revisit prior extractions and failures after those deterministic parsing changes.
- Completed the official House asset-type code set, made annual-holding retries parser-version aware with bounded diagnostics, and excluded identities from ineligible holding reports from source candidates.
- Added complete annual-report archive and parse backlog counts plus strict mutual-state checks to the House holding production status.
- Added licensed Alpaca market publication on a protected content-addressed `market` branch, frozen market commits in `main`, and a complete five-array production workflow.
- Defined OGE Form 201 request-only reports as outside the first-release completion boundary.

## 0.1.0 - 2026-09-18

- Added deterministic hash-sharded-v2 synthetic snapshot production and pinned supplied processor validation.
- Added local Git atomic demo publication, immutable version reads, rollback and restore verification.
- Added end-to-end synthetic reconstruction and supplied renderer demonstration; live ingestion remains disabled.
