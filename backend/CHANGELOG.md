# Changelog

All notable changes follow Semantic Versioning.

## Unreleased

- Added direct public-GitHub frozen-commit reading for dashboard, search, person and ticker selections.
- Added production-input validation, safe worktree materialization and an atomic manual publication workflow.
- Made the Cloudflare gateway optional for a later private-repository phase.
- Added House Clerk annual-index discovery with immutable SHA-256 source archiving; discovered filings remain explicitly unparsed.
- Added index-confirmed PTR archiving, page-coordinate extraction into a review queue, official BioGuide identity suggestions, and a fail-closed human promotion gate.
- Expanded the House PTR parser against five official electronic filings covering options, wrapped amounts, cross-page rows and missing tickers.
- Added explicit human resolution for amended filings so revisions cannot silently overwrite an earlier record.
- Added recoverable House PTR checkpoints with failure retries, archive-state recovery, and fail-closed index-change anomalies.

## 0.1.0 - 2026-09-18

- Added deterministic hash-sharded-v2 synthetic snapshot production and pinned supplied processor validation.
- Added local Git atomic demo publication, immutable version reads, rollback and restore verification.
- Added end-to-end synthetic reconstruction and supplied renderer demonstration; live ingestion remains disabled.
