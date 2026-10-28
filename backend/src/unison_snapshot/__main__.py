import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile

from .alpaca_market import AlpacaMarketClient, AlpacaMarketError, build_market_validation
from .twelve_data_market import TwelveDataClient, TwelveDataError, supplement as supplement_twelve_data
from .builder import build, normalize
from .codec import digest, encode
from .materialize import materialize
from .market_store import (build_market_bundle, load_published_market_cache,
                           load_published_twelve_data_cache, materialize_market)
from .house import HouseDocumentClient, HouseIndexClient, HouseIndexError, archive_indexed_ptr, discover
from .house_ptr import make_review_template, parse_archived_pdf, promote_review, qualify_automatic
from .house_ptr_paper_qualification import qualify_paper_observation
from .house_ptr_amount_header import observe_amount_header
from .house_paper_pipeline import bridge_paper_batch
from .house_sync import plan_checkpoint, record_result
from .house_members import HouseMemberClient, discover_members, suggest_identity
from .house_candidate import load_house_candidate
from .house_holdings import (HouseFinancialDocumentClient, archive_indexed_financial_report,
                             parse_archived_financial_report, qualify_financial_report)
from .disclosure_candidate import (DisclosureCandidateError, SourceCutoffsStaleError,
                                  build_disclosure_candidate, project_source_candidate)
from .congress_members import (
    CongressMemberClient, CongressMemberError, DEFAULT_CONGRESS,
    discover_congress_senate_members,
)
from .legacy import load, processor_version_for, PROCESSOR_V2_SHA256, PROCESSOR_V3_SHA256
from .senate import SenateEfdError, SenateHttpError, SenateTransportError, collection_gate_status, discover_ptrs, parse_search_page, \
    source_config_from_environment
from .senate_members import SenateMemberClient, SenateRosterError, build_roster, \
    discover_members as discover_senate_members
from .senate_identity import SenateIdentityError, build_catalog_identities
from .senate_reports import (
    archive_catalog_report_entrypoints, archive_review_paper_pages,
    extract_archived_report_batch,
)
from .senate_paper import extract_archived_paper_reports
from .senate_candidate import load_senate_candidate
from .senate_history import (
    SenateHistoryError, activate_amendment_supplement,
    load_amendment_predecessor_plan, load_amendment_supplement,
)
from .oge import (
    MAX_CATALOG_PAGE_SIZE as OGE_MAX_CATALOG_PAGE_SIZE,
    OgeCatalogError, collection_gate_status as oge_collection_gate_status,
    discover_catalog as discover_oge_catalog,
    source_config_from_environment as oge_source_config_from_environment,
)
from .oge_reports import (
    PARSER_VERSION as OGE_REPORT_PARSER_VERSION,
    GENERIC_PARSER_VERSION as OGE_GENERIC_PARSER_VERSION,
    TRUMP_SEPT_2026_DOCUMENT_ID, TRUMP_SEPT_2026_PARSER_VERSION,
    TRUMP_SEPT_2026_SECOND_PASS_VERSION,
    TRUMP_SEPT_2026_STRUCTURAL_PASS_VERSION,
    TRUMP_SEPT_2026_PAGE7_PASS_VERSION,
    TRUMP_SEPT_2026_SOURCE_SHA256, TRUMP_SEPT_2026_SOURCE_URL,
    archive_direct_batch as archive_oge_direct_batch,
    load_reusable_extraction as load_reusable_oge_extraction,
    parse_archived_pdf as parse_oge_archived_pdf,
)
from .oge_candidate import build_oge_candidate
from .public_repo import HTTPTransport, PublicSnapshotRepository
from .release_readiness import ReleaseReadinessError, validate_first_launch
from .store import GitStore, assemble


def _write_atomic(path: Path, payload: dict) -> None:
    encoded = encode(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
                                     delete=False) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot production, public reading and official-source discovery")
    sub = parser.add_subparsers(dest="command", required=True)
    publish = sub.add_parser("publish-demo")
    publish.add_argument("--input", type=Path, required=True)
    publish.add_argument("--store", type=Path, required=True)
    publish.add_argument("--generated-at", required=True)
    export = sub.add_parser("assemble")
    export.add_argument("--store", type=Path, required=True)
    export.add_argument("--commit", required=True)
    export.add_argument("--mode", choices=["dashboard", "people", "tickers"], default="dashboard")
    export.add_argument("--key")
    export.add_argument("--output", type=Path, required=True)
    rollback = sub.add_parser("rollback-demo")
    rollback.add_argument("--store", type=Path, required=True)
    rollback.add_argument("--target", required=True)
    rollback.add_argument("--expected-head", required=True)
    prepare = sub.add_parser("prepare-publication")
    prepare.add_argument("--input", type=Path, required=True)
    prepare.add_argument("--root", type=Path, required=True)
    prepare.add_argument("--generated-at", required=True)
    prepare.add_argument("--allow-empty-production", action="store_true")
    prepare.add_argument("--allow-market", action="store_true")
    prepare.add_argument("--market-commit")
    prepare.add_argument("--market-pages-file", type=Path)
    prepare.add_argument("--disclosure-status-input", type=Path)
    prepare.add_argument("--consumer-validation-file", type=Path,
                         help="Reuse successful rendering of these exact bytes by the pinned processor")
    prepare_market = sub.add_parser("prepare-market-publication")
    prepare_market.add_argument("--input", type=Path, required=True)
    prepare_market.add_argument("--root", type=Path, required=True)
    prepare_market.add_argument("--metadata-output", type=Path, required=True)
    prepare_market.add_argument("--audit", type=Path)
    prepare_market.add_argument("--twelve-audit", type=Path)
    fetch = sub.add_parser("fetch-public")
    fetch.add_argument("--owner", required=True)
    fetch.add_argument("--repo", required=True)
    fetch.add_argument("--ref", choices=["main", "demo"], default="main")
    fetch.add_argument("--mode", choices=["dashboard", "search", "person", "ticker"], default="dashboard")
    fetch.add_argument("--key")
    fetch.add_argument("--token-env", default="GITHUB_TOKEN")
    fetch.add_argument("--output", type=Path, required=True)
    fetch_status = sub.add_parser("fetch-disclosure-status")
    fetch_status.add_argument("--owner", required=True)
    fetch_status.add_argument("--repo", required=True)
    fetch_status.add_argument("--commit", required=True)
    fetch_status.add_argument("--include-records", action="store_true")
    fetch_status.add_argument("--token-env", default="GITHUB_TOKEN")
    fetch_status.add_argument("--output", type=Path, required=True)
    readiness = sub.add_parser(
        "verify-first-launch",
        help="Fail closed unless every in-scope disclosure source is fully accounted for")
    readiness.add_argument("--review-root", type=Path, required=True)
    readiness.add_argument("--input", type=Path, required=True)
    readiness.add_argument("--output", type=Path)
    house = sub.add_parser("discover-house-index")
    house.add_argument("--year", type=int, required=True)
    house.add_argument("--archive", type=Path, required=True)
    house.add_argument("--output", type=Path, required=True)
    house.add_argument("--timeout", type=float, default=30.0)
    ptr = sub.add_parser("archive-house-ptr")
    ptr.add_argument("--year", type=int, required=True)
    ptr.add_argument("--index-sha", required=True)
    ptr.add_argument("--document-id", required=True)
    ptr.add_argument("--archive", type=Path, required=True)
    ptr.add_argument("--output", type=Path, required=True)
    ptr.add_argument("--timeout", type=float, default=30.0)
    holding_archive = sub.add_parser("archive-house-holding-report")
    holding_archive.add_argument("--year", type=int, required=True)
    holding_archive.add_argument("--index-sha", required=True)
    holding_archive.add_argument("--document-id", required=True)
    holding_archive.add_argument("--archive", type=Path, required=True)
    holding_archive.add_argument("--output", type=Path, required=True)
    holding_archive.add_argument("--timeout", type=float, default=30.0)
    holding_parse = sub.add_parser("parse-house-holding-report")
    holding_parse.add_argument("--archive", type=Path, required=True)
    holding_parse.add_argument("--metadata", type=Path, required=True)
    holding_parse.add_argument("--output", type=Path, required=True)
    holding_qualify = sub.add_parser("qualify-house-holding-report")
    holding_qualify.add_argument("--extraction", type=Path, required=True)
    holding_qualify.add_argument("--identity", type=Path, required=True)
    holding_qualify.add_argument("--output", type=Path, required=True)
    parse_ptr = sub.add_parser("parse-house-ptr")
    parse_ptr.add_argument("--archive", type=Path, required=True)
    parse_ptr.add_argument("--metadata", type=Path, required=True)
    parse_ptr.add_argument("--output", type=Path, required=True)
    review_ptr = sub.add_parser("create-house-ptr-review")
    review_ptr.add_argument("--extraction", type=Path, required=True)
    review_ptr.add_argument("--output", type=Path, required=True)
    promote_ptr = sub.add_parser("promote-house-ptr-review")
    promote_ptr.add_argument("--extraction", type=Path, required=True)
    promote_ptr.add_argument("--review", type=Path, required=True)
    promote_ptr.add_argument("--output", type=Path, required=True)
    qualify_ptr = sub.add_parser("qualify-house-ptr")
    qualify_ptr.add_argument("--extraction", type=Path, required=True)
    qualify_ptr.add_argument("--identity", type=Path, required=True)
    qualify_ptr.add_argument("--evidence-root", type=Path)
    qualify_ptr.add_argument("--output", type=Path, required=True)
    qualify_paper = sub.add_parser("qualify-house-paper-observation")
    qualify_paper.add_argument("--observation", type=Path, required=True)
    qualify_paper.add_argument("--metadata", type=Path, required=True)
    qualify_paper.add_argument("--identity", type=Path, required=True)
    qualify_paper.add_argument("--pdf", type=Path)
    qualify_paper.add_argument("--tesseract")
    qualify_paper.add_argument("--peer-metadata", type=Path, nargs="+")
    qualify_paper.add_argument("--output", type=Path, required=True)
    reviewed_paper = sub.add_parser("correct-reviewed-house-paper")
    reviewed_paper.add_argument("--evidence-root", type=Path, required=True)
    reviewed_paper.add_argument("--review-root", type=Path, required=True)
    reviewed_paper.add_argument("--correction", type=Path, required=True)
    bridge_paper = sub.add_parser("bridge-house-paper-batch")
    bridge_paper.add_argument("--evidence-root", type=Path, required=True)
    bridge_paper.add_argument("--review-root", type=Path, required=True)
    bridge_paper.add_argument("--parse-batch", type=Path, required=True)
    bridge_paper.add_argument("--tesseract", required=True)
    bridge_paper.add_argument("--document-id")
    bridge_paper.add_argument("--materialize-saved", action="store_true",
                              help="Interpret saved paper evidence without starting OCR")
    candidate = sub.add_parser("build-house-candidate")
    candidate.add_argument("--review-root", type=Path, required=True)
    candidate.add_argument("--evidence-root", type=Path)
    candidate.add_argument("--state-status", type=Path, required=True)
    candidate.add_argument("--base", type=Path, required=True)
    candidate.add_argument("--output", type=Path, required=True)
    candidate.add_argument("--html-output", type=Path)
    candidate.add_argument("--annual-transaction-audit-out", type=Path,
                           help="Save source-bound annual transaction relationship decisions")
    senate_candidate = sub.add_parser("build-senate-candidate")
    senate_candidate.add_argument("--review-root", type=Path, required=True)
    senate_candidate.add_argument("--evidence-root", type=Path)
    senate_candidate.add_argument("--state-status", type=Path, required=True)
    senate_candidate.add_argument("--base", type=Path, required=True)
    senate_candidate.add_argument("--output", type=Path, required=True)
    senate_candidate.add_argument("--audit-output", type=Path, required=True)
    senate_candidate.add_argument("--html-output", type=Path)
    disclosure_candidate = sub.add_parser("build-disclosure-candidate")
    disclosure_candidate.add_argument("--base", type=Path, required=True)
    disclosure_candidate.add_argument(
        "--source", action="append", required=True, metavar="SOURCE_ID=PATH",
        help="Repeat once per source candidate, for example house_clerk=house-current.json")
    disclosure_candidate.add_argument("--output", type=Path, required=True)
    disclosure_candidate.add_argument("--audit-output", type=Path)
    disclosure_candidate.add_argument("--html-output", type=Path)
    disclosure_candidate.add_argument("--assembly-status-output", type=Path)
    disclosure_candidate.add_argument("--defer-stale-sources", action="store_true",
                                      help="Record stale-source assembly as deferred; never replace the candidate")
    disclosure_candidate.add_argument(
        "--harmonize-cutoffs", action="store_true",
        help="Align source candidates to their earliest cutoff and exclude later-filed facts")
    market_validation = sub.add_parser(
        "build-alpaca-market-validation",
        help="Build a local Alpaca Basic candidate without enabling production publication")
    market_validation.add_argument("--input", type=Path, required=True)
    market_validation.add_argument("--output", type=Path, required=True)
    market_validation.add_argument("--audit-output", type=Path, required=True)
    market_validation.add_argument("--processed-output", type=Path)
    market_validation.add_argument("--html-output", type=Path)
    market_validation.add_argument("--checked-at")
    market_validation.add_argument("--as-of-date")
    market_validation.add_argument("--batch-size", type=int, default=50)
    market_validation.add_argument("--timeout", type=float, default=30.0)
    market_validation.add_argument("--key-id-env", default="ALPACA_API_KEY_ID")
    market_validation.add_argument("--secret-key-env", default="ALPACA_API_SECRET_KEY")
    market_validation.add_argument(
        "--assets-url", default="https://paper-api.alpaca.markets/v2/assets",
        help="Allowlisted Alpaca paper or live Assets API endpoint")
    market_validation.add_argument("--distribution-authorized", action="store_true")
    market_validation.add_argument("--previous-main-root", type=Path)
    market_validation.add_argument("--previous-market-root", type=Path)
    market_validation.add_argument("--previous-market-commit")
    twelve_market = sub.add_parser("build-twelve-data-supplement")
    twelve_market.add_argument("--input", type=Path, required=True)
    twelve_market.add_argument("--output", type=Path, required=True)
    twelve_market.add_argument("--audit-output", type=Path, required=True)
    twelve_market.add_argument("--processed-output", type=Path)
    twelve_market.add_argument("--html-output", type=Path)
    twelve_market.add_argument("--checked-at")
    twelve_market.add_argument("--limit", type=int)
    twelve_market.add_argument("--key-env", default="TWELVE_DATA_API_KEY")
    twelve_market.add_argument("--distribution-authorized", action="store_true")
    twelve_market.add_argument("--previous-main-root", type=Path)
    twelve_market.add_argument("--previous-market-root", type=Path)
    twelve_market.add_argument("--previous-market-commit")
    senate_roster = sub.add_parser("parse-senate-members")
    senate_roster.add_argument("--input", type=Path, required=True)
    senate_roster.add_argument("--output", type=Path, required=True)
    senate_roster_live = sub.add_parser("discover-senate-members")
    senate_roster_live.add_argument("--archive", type=Path, required=True)
    senate_roster_live.add_argument("--output", type=Path, required=True)
    senate_roster_live.add_argument("--timeout", type=float, default=30.0)
    congress_roster_live = sub.add_parser("discover-congress-senate-members")
    congress_roster_live.add_argument("--archive", type=Path, required=True)
    congress_roster_live.add_argument("--output", type=Path, required=True)
    congress_roster_live.add_argument("--congress", type=int, default=DEFAULT_CONGRESS)
    congress_roster_live.add_argument("--api-key-env", default="CONGRESS_GOV_API_KEY")
    congress_roster_live.add_argument("--timeout", type=float, default=30.0)
    senate_search = sub.add_parser("parse-senate-search-page")
    senate_search.add_argument("--input", type=Path, required=True)
    senate_search.add_argument("--start", type=int, required=True)
    senate_search.add_argument("--length", type=int, required=True)
    senate_search.add_argument("--output", type=Path, required=True)
    senate_gate = sub.add_parser("senate-efd-gate")
    senate_gate.add_argument("--output", type=Path, required=True)
    senate_gate.add_argument("--enabled-env", default="SENATE_EFD_COLLECTION_ENABLED")
    senate_gate.add_argument("--terms-env", default="SENATE_EFD_TERMS_ACKNOWLEDGED")
    senate_discovery = sub.add_parser("discover-senate-efd")
    senate_discovery.add_argument("--archive", type=Path, required=True)
    senate_discovery.add_argument("--output", type=Path, required=True)
    senate_discovery.add_argument("--failure-output", type=Path)
    senate_discovery.add_argument("--attempt-output", type=Path)
    senate_discovery.add_argument("--isolate-source-unavailable", action="store_true")
    senate_discovery.add_argument("--submitted-start-date", required=True)
    senate_discovery.add_argument("--page-size", type=int, default=100)
    senate_discovery.add_argument("--enabled-env", default="SENATE_EFD_COLLECTION_ENABLED")
    senate_discovery.add_argument("--terms-env", default="SENATE_EFD_TERMS_ACKNOWLEDGED")
    senate_identities = sub.add_parser("match-senate-catalog")
    senate_identities.add_argument("--discovery", type=Path, required=True)
    senate_identities.add_argument("--roster", type=Path, required=True)
    senate_identities.add_argument("--congress-roster", type=Path)
    senate_identities.add_argument("--output", type=Path, required=True)
    senate_reports = sub.add_parser("archive-senate-report-entrypoints")
    senate_reports.add_argument("--discovery", type=Path, required=True)
    senate_reports.add_argument("--archive", type=Path, required=True)
    senate_reports.add_argument("--output", type=Path, required=True)
    senate_reports.add_argument("--limit", type=int, default=2)
    senate_reports.add_argument("--document-ids", type=Path)
    senate_reports.add_argument("--enabled-env", default="SENATE_EFD_COLLECTION_ENABLED")
    senate_reports.add_argument("--terms-env", default="SENATE_EFD_TERMS_ACKNOWLEDGED")
    senate_extract = sub.add_parser("extract-senate-report-entrypoints")
    senate_extract.add_argument("--batch", type=Path, required=True)
    senate_extract.add_argument("--archive", type=Path, required=True)
    senate_extract.add_argument("--output", type=Path, required=True)
    senate_paper = sub.add_parser("archive-senate-paper-pages")
    senate_paper.add_argument("--evidence-root", type=Path, required=True)
    senate_paper.add_argument("--review-root", type=Path, required=True)
    senate_paper.add_argument("--expected-documents", type=int, default=9)
    senate_paper.add_argument("--expected-pages", type=int, default=52)
    senate_paper.add_argument("--enabled-env", default="SENATE_EFD_COLLECTION_ENABLED")
    senate_paper.add_argument("--terms-env", default="SENATE_EFD_TERMS_ACKNOWLEDGED")
    senate_paper.add_argument("--output", type=Path, required=True)
    senate_paper_extract = sub.add_parser("extract-senate-paper-pages")
    senate_paper_extract.add_argument("--evidence-root", type=Path, required=True)
    senate_paper_extract.add_argument("--review-root", type=Path, required=True)
    senate_paper_extract.add_argument("--tesseract", default="tesseract")
    senate_paper_extract.add_argument("--saved-grid-observations", type=Path)
    senate_paper_extract.add_argument("--saved-word-pages", type=Path)
    senate_paper_extract.add_argument("--output", type=Path, required=True)
    senate_history = sub.add_parser("plan-senate-amendment-backfill")
    senate_history.add_argument("--review-root", type=Path, required=True)
    senate_history.add_argument("--historical-discovery", type=Path, required=True)
    senate_history.add_argument("--historical-identities", type=Path, required=True)
    senate_history.add_argument("--expected-target-count", type=int, required=True)
    senate_history.add_argument("--output", type=Path, required=True)
    senate_history_resolve = sub.add_parser("resolve-senate-amendment-backfill")
    senate_history_resolve.add_argument("--review-root", type=Path, required=True)
    senate_history_resolve.add_argument("--plan", type=Path, required=True)
    senate_history_resolve.add_argument("--historical-extractions", type=Path, required=True)
    senate_history_resolve.add_argument("--output", type=Path, required=True)
    senate_history_activate = sub.add_parser("activate-senate-amendment-supplement")
    senate_history_activate.add_argument("--review-root", type=Path, required=True)
    senate_history_activate.add_argument("--supplement", type=Path, required=True)
    senate_history_activate.add_argument("--historical-extractions", type=Path, required=True)
    senate_history_activate.add_argument("--output", type=Path, required=True)
    oge_gate = sub.add_parser("oge-gate")
    oge_gate.add_argument("--output", type=Path, required=True)
    oge_gate.add_argument("--enabled-env", default="OGE_COLLECTION_ENABLED")
    oge_gate.add_argument("--terms-env", default="OGE_TERMS_ACKNOWLEDGED")
    oge_discovery = sub.add_parser("discover-oge")
    oge_discovery.add_argument("--archive", type=Path, required=True)
    oge_discovery.add_argument("--output", type=Path, required=True)
    oge_discovery.add_argument("--page-size", type=int, default=OGE_MAX_CATALOG_PAGE_SIZE)
    oge_discovery.add_argument("--enabled-env", default="OGE_COLLECTION_ENABLED")
    oge_discovery.add_argument("--terms-env", default="OGE_TERMS_ACKNOWLEDGED")
    oge_reports = sub.add_parser("archive-oge-direct-pdfs")
    oge_reports.add_argument("--catalog", type=Path, required=True)
    oge_reports.add_argument("--archive", type=Path, required=True)
    oge_reports.add_argument("--output", type=Path, required=True)
    oge_reports.add_argument("--limit", type=int, default=2)
    oge_reports.add_argument("--enabled-env", default="OGE_COLLECTION_ENABLED")
    oge_reports.add_argument("--terms-env", default="OGE_TERMS_ACKNOWLEDGED")
    oge_extract = sub.add_parser("extract-oge-direct-pdfs")
    oge_extract.add_argument("--batch", type=Path, required=True)
    oge_extract.add_argument("--archive", type=Path, required=True)
    oge_extract.add_argument("--reuse-root", type=Path)
    oge_extract.add_argument("--generic", action="store_true")
    oge_extract.add_argument("--output-dir", type=Path, required=True)
    oge_extract.add_argument("--output", type=Path, required=True)
    oge_candidate = sub.add_parser("build-oge-candidate")
    oge_candidate.add_argument("--catalog", type=Path, required=True)
    oge_candidate.add_argument("--catalog-history-dir", type=Path)
    oge_candidate.add_argument("--extractions-dir", type=Path, required=True)
    oge_candidate.add_argument("--evidence-root", type=Path)
    oge_candidate.add_argument("--manual-cover-dates-dir", type=Path)
    oge_candidate_mode = oge_candidate.add_mutually_exclusive_group()
    oge_candidate_mode.add_argument("--generic", action="store_true")
    oge_candidate_mode.add_argument("--generic-document-id", action="append", default=[],
        help="Select this document's existing source-planned generic cache only; repeat up to 32 times (native v7 requires its v2 pair)")
    oge_candidate.add_argument("--document-source", action="append", default=[], metavar="DOC:SHA",
        help="Select one archived ordinary native input by document and SHA; repeat up to 32 times, without asserting recency or replacement")
    oge_candidate.add_argument("--legacy-extractions-dir", type=Path)
    oge_candidate.add_argument("--saved-exhibit-observations", type=Path)
    oge_candidate.add_argument("--saved-observations", type=Path,
                               help="Explicit portable saved 278-T readings; requires --evidence-root")
    oge_candidate.add_argument("--base", type=Path, required=True)
    oge_candidate.add_argument("--data-cutoff-at", required=True)
    oge_candidate.add_argument("--output", type=Path, required=True)
    oge_candidate.add_argument("--audit-output", type=Path, required=True)
    members = sub.add_parser("discover-house-members")
    members.add_argument("--archive", type=Path, required=True)
    members.add_argument("--output", type=Path, required=True)
    members.add_argument("--timeout", type=float, default=30.0)
    identity = sub.add_parser("suggest-house-identity")
    identity.add_argument("--extraction", type=Path, required=True)
    identity.add_argument("--members", type=Path, required=True)
    identity.add_argument("--output", type=Path, required=True)
    sync = sub.add_parser("plan-house-ptr-sync")
    sync.add_argument("--discovery", type=Path, required=True)
    sync.add_argument("--archive", type=Path, required=True)
    sync.add_argument("--checkpoint", type=Path)
    sync.add_argument("--planned-at")
    sync.add_argument("--output", type=Path, required=True)
    sync_result = sub.add_parser("record-house-ptr-result")
    sync_result.add_argument("--checkpoint", type=Path, required=True)
    sync_result.add_argument("--document-id", required=True)
    sync_result.add_argument("--status", choices=["archived", "failed"], required=True)
    sync_result.add_argument("--metadata", type=Path)
    sync_result.add_argument("--error")
    sync_result.add_argument("--result-at")
    sync_result.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "publish-demo":
            store = GitStore(args.store)
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            bundle = build(payload, generated_at=args.generated_at)
            store.initialize()
            previous = store.head()
            commit = store.publish(bundle, expected_head=previous)
            print(json.dumps({"commit": commit, "snapshot_id": bundle.manifest["snapshot_id"],
                              "changed": commit != previous, "ref": "demo", "is_demo": True}))
        elif args.command == "assemble":
            store = GitStore(args.store)
            result = assemble(store, args.commit, mode=args.mode, key=args.key)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(encode(result))
            print(str(args.output.resolve()))
        elif args.command == "rollback-demo":
            store = GitStore(args.store)
            store.rollback(args.target, expected_head=args.expected_head)
            print(json.dumps({"ref": "demo", "commit": args.target}))
        elif args.command == "prepare-publication":
            input_bytes = args.input.read_bytes()
            payload = json.loads(input_bytes)
            if args.consumer_validation_file:
                receipt = json.loads(args.consumer_validation_file.read_bytes())
                if receipt != {"input_sha256": digest(input_bytes),
                               "processor_sha256": PROCESSOR_V3_SHA256 if processor_version_for(payload) == "v3" else PROCESSOR_V2_SHA256}:
                    raise ValueError("Consumer validation receipt does not match the input and processor")
            if payload.get("meta", {}).get("is_demo") is not False:
                raise ValueError("Public production publication requires is_demo=false")
            bundle = build(payload, generated_at=args.generated_at, allow_production=True,
                           validate_consumer=not args.consumer_validation_file,
                           allow_empty_production=args.allow_empty_production,
                           allow_market=args.allow_market, market_commit=args.market_commit,
                           market_pages=(json.loads(args.market_pages_file.read_text(encoding="utf-8"))
                                         ["market_pages"] if args.market_pages_file else None))
            if args.disclosure_status_input:
                from .disclosure_status import attach_disclosure_status
                status_input = json.loads(args.disclosure_status_input.read_text(encoding="utf-8"))
                if (status_input.get("schema_version") != "disclosure-status-input/v1" or
                        status_input.get("transactions_sha256") != digest(encode(payload["transactions"]))):
                    raise ValueError("Disclosure status input does not match publication transactions")
                bundle = attach_disclosure_status(
                    bundle, status_input["scopes"], transactions=payload["transactions"],
                    provenance=status_input["provenance"])
            result = materialize(args.root, bundle)
            print(json.dumps({"changed": result.changed, "business_changed": result.business_changed,
                              "snapshot_id": bundle.manifest["snapshot_id"],
                              "written": result.written, "removed": result.removed}))
        elif args.command == "prepare-market-publication":
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            if payload.get("meta", {}).get("is_demo") is not False:
                raise ValueError("Public market publication requires is_demo=false")
            bundle = build_market_bundle(
                payload.get("security_market_data"),
                data_cutoff_at=payload.get("meta", {}).get("data_cutoff_at"),
                audit=(json.loads(args.audit.read_text(encoding="utf-8"))
                       if args.audit else None),
                twelve_audit=(json.loads(args.twelve_audit.read_text(encoding="utf-8"))
                              if args.twelve_audit else None))
            result = materialize_market(args.root, bundle)
            _write_atomic(args.metadata_output, {
                "schema_version": "market-publication-plan/v1",
                "data_cutoff_at": bundle.data_cutoff_at,
                "ticker_count": len(bundle.tickers),
                "market_pages": list(bundle.page_shas),
            })
            print(json.dumps({"changed": result.changed, "tickers": len(bundle.tickers),
                              "pages": len(bundle.page_shas),
                              "written": result.written, "removed": result.removed}))
        elif args.command == "fetch-public":
            token = os.environ.get(args.token_env) if args.token_env else None
            selection = PublicSnapshotRepository(args.owner, args.repo, ref=args.ref,
                transport=HTTPTransport(token=token)).fetch(args.mode, args.key)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(encode(selection.snapshot))
            print(json.dumps({"commit": selection.commit, "output": str(args.output.resolve())}))
        elif args.command == "fetch-disclosure-status":
            token = os.environ.get(args.token_env) if args.token_env else None
            status = PublicSnapshotRepository(args.owner, args.repo,
                transport=HTTPTransport(token=token)).disclosure_status(
                    args.commit, include_records=args.include_records)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(encode({"snapshot_commit": args.commit,
                                           "disclosure_status": status}))
            print(json.dumps({"commit": args.commit, "provided": status is not None,
                              "output": str(args.output.resolve())}))
        elif args.command == "verify-first-launch":
            result = validate_first_launch(args.review_root, args.input)
            if args.output:
                _write_atomic(args.output, result)
            print(json.dumps({**result, "status": "ready",
                              "output": str(args.output.resolve()) if args.output else None}))
        elif args.command == "discover-house-index":
            result = discover(args.year, args.archive, client=HouseIndexClient(args.timeout))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(encode(result))
            print(json.dumps({"year": args.year, "records": len(result["filings"]),
                              "sha256": result["metadata"]["sha256"],
                              "output": str(args.output.resolve())}))
        elif args.command == "archive-house-ptr":
            result = archive_indexed_ptr(args.archive, args.year, args.index_sha, args.document_id,
                                         client=HouseDocumentClient(args.timeout))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(encode(result))
            print(json.dumps({"year": args.year, "document_id": args.document_id,
                              "sha256": result["sha256"], "bytes": result["byte_length"],
                              "output": str(args.output.resolve())}))
        elif args.command == "archive-house-holding-report":
            result = archive_indexed_financial_report(
                args.archive, args.year, args.index_sha, args.document_id,
                client=HouseFinancialDocumentClient(args.timeout))
            _write_atomic(args.output, result)
            print(json.dumps({"year": args.year, "document_id": args.document_id,
                              "sha256": result["sha256"], "bytes": result["byte_length"],
                              "output": str(args.output.resolve())}))
        elif args.command == "parse-house-holding-report":
            metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
            result = parse_archived_financial_report(args.archive, metadata)
            _write_atomic(args.output, result)
            print(json.dumps({"document_id": result["source"]["document_id"],
                              "rows": len(result["rows"]),
                              "report_period_end": result["report_period_end"],
                              "output": str(args.output.resolve())}))
        elif args.command == "qualify-house-holding-report":
            extraction = json.loads(args.extraction.read_text(encoding="utf-8"))
            identity = json.loads(args.identity.read_text(encoding="utf-8"))
            result = qualify_financial_report(extraction, identity)
            _write_atomic(args.output, result)
            print(json.dumps({"document_id": result["source"]["document_id"],
                              "eligible": result["production_eligible"],
                              "holdings": len(result["holdings"]),
                              "quarantined": len(result["quarantined"]),
                              "output": str(args.output.resolve())}))
        elif args.command == "parse-house-ptr":
            result = parse_archived_pdf(args.archive, args.metadata)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(encode(result))
            print(json.dumps({"document_id": result["source"]["document_id"],
                              "transactions": len(result["transactions"]),
                              "status": result["review"]["status"],
                              "production_eligible": result["review"]["production_eligible"],
                              "output": str(args.output.resolve())}))
        elif args.command == "create-house-ptr-review":
            extraction = json.loads(args.extraction.read_text(encoding="utf-8"))
            result = make_review_template(extraction)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(encode(result))
            print(json.dumps({"document_id": result["document_id"], "rows": len(result["rows"]),
                              "decision": result["review"]["decision"],
                              "output": str(args.output.resolve())}))
        elif args.command == "promote-house-ptr-review":
            extraction = json.loads(args.extraction.read_text(encoding="utf-8"))
            review = json.loads(args.review.read_text(encoding="utf-8"))
            result = promote_review(extraction, review)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(encode(result))
            print(json.dumps({"document_id": result["audit"]["document_id"],
                              "transactions": len(result["transactions"]),
                              "output": str(args.output.resolve())}))
        elif args.command == "qualify-house-ptr":
            extraction = json.loads(args.extraction.read_text(encoding="utf-8"))
            identity = json.loads(args.identity.read_text(encoding="utf-8"))
            result = qualify_automatic(extraction, identity,
                **({'evidence_root':args.evidence_root} if args.evidence_root is not None else {}))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(encode(result))
            print(json.dumps({"document_id": result["document_id"],
                              "qualified": result["qualification"]["qualified_count"],
                              "quarantined": result["qualification"]["quarantined_count"],
                              "output": str(args.output.resolve())}))
        elif args.command == "qualify-house-paper-observation":
            observation = json.loads(args.observation.read_text(encoding="utf-8"))
            metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
            identity = json.loads(args.identity.read_text(encoding="utf-8"))
            if bool(args.pdf) != bool(args.tesseract):
                raise ValueError("House paper amount header requires both PDF and Tesseract path")
            amount_header = (observe_amount_header(
                args.pdf.read_bytes(), source_sha256=metadata["sha256"],
                executable=args.tesseract) if args.pdf else None)
            result = qualify_paper_observation(observation, metadata, identity,
                                               amount_header=amount_header,
                                               peer_metadata=[json.loads(path.read_text(
                                                   encoding="utf-8"))
                                                   for path in args.peer_metadata]
                                               if args.peer_metadata is not None else None)
            _write_atomic(args.output, result)
            print(json.dumps({"document_id": result["document_id"],
                              "qualified": 0,
                              "quarantined": result["qualification"]["quarantined_count"],
                              "output": str(args.output.resolve())}))
        elif args.command == "correct-reviewed-house-paper":
            from .reviewed_house_paper import qualify_reviewed_paper_paths
            result = qualify_reviewed_paper_paths(args.evidence_root, args.review_root, args.correction)
            print(json.dumps(result, sort_keys=True))
        elif args.command == "bridge-house-paper-batch":
            result = bridge_paper_batch(
                evidence_root=args.evidence_root, review_root=args.review_root,
                parse_batch=args.parse_batch, tesseract=args.tesseract,
                document_id=args.document_id,
                **({"materialize_saved": True} if args.materialize_saved else {}))
            print(json.dumps(result, sort_keys=True))
        elif args.command == "build-house-candidate":
            annual_audit = None
            if args.annual_transaction_audit_out:
                if args.annual_transaction_audit_out.resolve() in {
                        path.resolve() for path in (args.output, args.html_output, args.base, args.state_status)
                        if path is not None}:
                    raise ValueError("Annual transaction audit must have its own output path")
                annual_audit = {}
                result = load_house_candidate(args.review_root, args.state_status, args.base,
                    annual_transaction_audit=annual_audit,
                    **({'evidence_root':args.evidence_root} if args.evidence_root is not None else {}))
            else:
                result = load_house_candidate(args.review_root, args.state_status, args.base,
                    **({'evidence_root':args.evidence_root} if args.evidence_root is not None else {}))
            generated_at = result["meta"]["data_cutoff_at"]
            bundle = build(result, generated_at=generated_at, allow_production=True)
            result["meta"].update(snapshot_id=bundle.manifest["snapshot_id"],
                                  generated_at=generated_at)
            _write_atomic(args.output, result)
            if annual_audit is not None:
                _write_atomic(args.annual_transaction_audit_out, annual_audit)
            if args.html_output:
                renderer = load("render_dashboard", version="v3" if processor_version_for(result) == "v3" else "v1")
                html = renderer.render_html(renderer.load_dashboard_data(args.output))
                args.html_output.parent.mkdir(parents=True, exist_ok=True)
                args.html_output.write_text(html, encoding="utf-8")
            print(json.dumps({"people": len(result["people"]),
                              "transactions": len(result["transactions"]),
                              "reported_holdings": len(result["reported_holdings"]),
                              "output": str(args.output.resolve()),
                              "html": str(args.html_output.resolve()) if args.html_output else None}))
        elif args.command == "build-senate-candidate":
            result, audit = load_senate_candidate(
                args.review_root, args.state_status, args.base,
                evidence_root=args.evidence_root)
            generated_at = result["meta"]["data_cutoff_at"]
            bundle = build(result, generated_at=generated_at, allow_production=True)
            result["meta"].update(snapshot_id=bundle.manifest["snapshot_id"],
                                  generated_at=generated_at)
            audit["candidate_snapshot_id"] = bundle.manifest["snapshot_id"]
            audit["candidate_sha256"] = digest(encode(result))
            _write_atomic(args.output, result)
            _write_atomic(args.audit_output, audit)
            if args.html_output:
                renderer = load("render_dashboard", version="v3" if processor_version_for(result) == "v3" else "v1")
                html = renderer.render_html(renderer.load_dashboard_data(args.output))
                args.html_output.parent.mkdir(parents=True, exist_ok=True)
                args.html_output.write_text(html, encoding="utf-8")
            print(json.dumps({"people": len(result["people"]),
                              "transactions": len(result["transactions"]),
                              "quarantined_reports": audit["quarantined_report_count"],
                              "quarantined_rows": audit["quarantined_row_count"],
                              "output": str(args.output.resolve()),
                              "audit": str(args.audit_output.resolve()),
                              "html": str(args.html_output.resolve()) if args.html_output else None}))
        elif args.command == "build-disclosure-candidate":
            if args.defer_stale_sources and not args.assembly_status_output:
                raise DisclosureCandidateError("Deferring stale sources requires --assembly-status-output")
            if args.assembly_status_output and args.assembly_status_output.resolve() in {
                    value.resolve() for value in (args.base, args.output, args.audit_output,
                                                   args.html_output) if value}:
                raise DisclosureCandidateError("Assembly status must have its own output path")
            sources: dict[str, dict] = {}
            for item in args.source:
                source_id, separator, source_path = item.partition("=")
                if not separator or not source_id or not source_path or source_id in sources:
                    raise DisclosureCandidateError(
                        "Each --source must be a unique SOURCE_ID=PATH value")
                if (args.assembly_status_output and
                        args.assembly_status_output.resolve() == Path(source_path).resolve()):
                    raise DisclosureCandidateError("Assembly status cannot replace a source candidate")
                sources[source_id] = json.loads(Path(source_path).read_text(encoding="utf-8"))
            base = json.loads(args.base.read_text(encoding="utf-8"))
            cutoff_audit = {} if args.audit_output else None
            try:
                result = build_disclosure_candidate(
                    base, sources, harmonize_cutoffs=args.harmonize_cutoffs,
                    harmonization_audit=cutoff_audit)
            except SourceCutoffsStaleError as exc:
                if not args.defer_stale_sources:
                    raise
                # Deferral must not turn malformed facts or colliding IDs into
                # an expected availability result. It authorizes no publication.
                normalize(base, allow_production=True, allow_empty_production=True)
                people_ids, fact_ids = set(), set()
                for source_id, source in sources.items():
                    build(source, generated_at=source['meta']['data_cutoff_at'],
                          allow_production=True, allow_empty_production=True)
                    projection = project_source_candidate(source_id, source)
                    for person in projection['people']:
                        if person['id'] in people_ids:
                            raise DisclosureCandidateError("Deferred sources duplicate a person ID")
                        people_ids.add(person['id'])
                    for fact in projection['transactions'] + projection['reported_holdings']:
                        if fact['id'] in fact_ids:
                            raise DisclosureCandidateError("Deferred sources duplicate a fact ID")
                        fact_ids.add(fact['id'])
                status = {'schema_version': 'disclosure-assembly-status/v1',
                          'status': 'deferred', 'reason': 'source_cutoffs_too_stale',
                          'attempted_at': datetime.now(timezone.utc).isoformat(),
                          'source_cutoffs': exc.source_cutoffs,
                          'source_candidate_sha256': {
                              source_id: digest(encode(source))
                              for source_id, source in sorted(sources.items())}}
                _write_atomic(args.assembly_status_output, status)
                print(json.dumps(status))
                return
            generated_at = result["meta"]["data_cutoff_at"]
            bundle = build(result, generated_at=generated_at, allow_production=True)
            result["meta"].update(snapshot_id=bundle.manifest["snapshot_id"],
                                  generated_at=generated_at)
            _write_atomic(args.output, result)
            if args.audit_output:
                cutoff_audit["candidate_snapshot_id"] = bundle.manifest["snapshot_id"]
                cutoff_audit["candidate_sha256"] = digest(encode(result))
                _write_atomic(args.audit_output, cutoff_audit)
            if args.html_output:
                renderer = load("render_dashboard", version="v3" if processor_version_for(result) == "v3" else "v1")
                html = renderer.render_html(renderer.load_dashboard_data(args.output))
                args.html_output.parent.mkdir(parents=True, exist_ok=True)
                args.html_output.write_text(html, encoding="utf-8")
            if args.assembly_status_output:
                _write_atomic(args.assembly_status_output, {
                    'schema_version': 'disclosure-assembly-status/v1', 'status': 'ready',
                    'attempted_at': datetime.now(timezone.utc).isoformat(),
                    'data_cutoff_at': generated_at,
                    'candidate_sha256': digest(encode(result)),
                })
            print(json.dumps({"sources": sorted(sources), "people": len(result["people"]),
                              "transactions": len(result["transactions"]),
                              "reported_holdings": len(result["reported_holdings"]),
                              "output": str(args.output.resolve()),
                              "html": str(args.html_output.resolve()) if args.html_output else None}))
        elif args.command == "build-twelve-data-supplement":
            if not args.distribution_authorized:
                raise TwelveDataError("Twelve Data distribution authorization is required")
            checked_at = args.checked_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            # A zero limit is a cache-only publication: it carries forward
            # already licensed rows and deliberately cannot issue a request.
            key = "cache-only" if args.limit == 0 else os.environ.get(args.key_env, "")
            client = TwelveDataClient(key)
            cache_options = (args.previous_main_root, args.previous_market_root,
                             args.previous_market_commit)
            if any(value is not None for value in cache_options) \
                    and not all(value is not None for value in cache_options):
                raise TwelveDataError(
                    "Previous main, market and commit must be provided together")
            previous_market = (load_published_twelve_data_cache(
                args.previous_main_root, args.previous_market_root,
                market_commit=args.previous_market_commit)
                if all(value is not None for value in cache_options) else None)
            result, audit = supplement_twelve_data(payload, client=client,
                                                   checked_at=checked_at, limit=args.limit,
                                                   previous_market=previous_market)
            result["meta"].update(generated_at=checked_at,
                                  snapshot_id="prepublication-validation")
            version = ("v3" if processor_version_for(result) == "v3" else "v2" if audit["accepted_count"] else "v1")
            processor = load("process_snapshot", version=version)
            identity = dict(result, meta={key: value for key, value in result["meta"].items()
                                          if key != "snapshot_id"})
            result["meta"]["snapshot_id"] = digest(encode(identity))
            processed = processor.build_snapshot(result)
            audit = dict(audit, candidate_snapshot_id=result["meta"]["snapshot_id"],
                         candidate_sha256=digest(encode(result)))
            _write_atomic(args.output, result)
            _write_atomic(args.audit_output, audit)
            if args.processed_output:
                _write_atomic(args.processed_output, processed)
            if args.html_output:
                renderer = load("render_dashboard", version=version)
                html = renderer.render_html(renderer.load_dashboard_data(args.output))
                args.html_output.parent.mkdir(parents=True, exist_ok=True)
                args.html_output.write_text(html, encoding="utf-8")
            print(json.dumps({"attempted": audit["attempted_count"],
                              "accepted": audit["accepted_count"],
                              "remaining": audit["remaining_count"],
                              "requests": audit["request_count"],
                              "output": str(args.output.resolve())}))
        elif args.command == "build-alpaca-market-validation":
            checked_at = args.checked_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            client = AlpacaMarketClient(
                os.environ.get(args.key_id_env, ""),
                os.environ.get(args.secret_key_env, ""),
                timeout=args.timeout,
                assets_url=args.assets_url,
            )
            cache_options = (args.previous_main_root, args.previous_market_root,
                             args.previous_market_commit)
            if any(value is not None for value in cache_options) \
                    and not all(value is not None for value in cache_options):
                raise ValueError("Previous main, market and commit must be provided together")
            cache = (load_published_market_cache(
                args.previous_main_root, args.previous_market_root,
                market_commit=args.previous_market_commit)
                if all(value is not None for value in cache_options) else None)
            validation = build_market_validation(
                payload, client=client, checked_at=checked_at,
                as_of_date=args.as_of_date, batch_size=args.batch_size,
                distribution_authorized=args.distribution_authorized,
                previous_market=cache)
            result = validation.snapshot
            result["meta"]["generated_at"] = checked_at
            result["meta"]["snapshot_id"] = "prepublication-validation"
            processor = load("process_snapshot", version="v3" if processor_version_for(result) == "v3" else "v1")
            identity = dict(result, meta={key: value for key, value in result["meta"].items()
                                          if key != "snapshot_id"})
            result["meta"]["snapshot_id"] = digest(encode(identity))
            processed = processor.build_snapshot(result)
            audit = dict(validation.audit, candidate_snapshot_id=result["meta"]["snapshot_id"],
                         candidate_sha256=digest(encode(result)))
            _write_atomic(args.output, result)
            _write_atomic(args.audit_output, audit)
            if args.processed_output:
                _write_atomic(args.processed_output, processed)
            if args.html_output:
                renderer = load("render_dashboard", version="v3" if processor_version_for(result) == "v3" else "v1")
                html = renderer.render_html(renderer.load_dashboard_data(args.output))
                args.html_output.parent.mkdir(parents=True, exist_ok=True)
                args.html_output.write_text(html, encoding="utf-8")
            print(json.dumps({"symbols": audit["symbol_count"],
                              "market_rows": audit["market_row_count"],
                              "cache_status": audit["cache_status"],
                              "incremental_tickers": audit["incremental_ticker_count"],
                              "full_refresh_tickers": audit["full_refresh_ticker_count"],
                              "split_refresh_tickers": audit["split_refresh_ticker_count"],
                              "response_pages": audit.get("response_page_count"),
                              "request_attempts": audit.get("request_attempt_count"),
                              "historical_recovered": audit["historical_recovered_count"],
                              "historical_rejected": len(audit["historical_rejected_symbols"]),
                              "missing_tickers": audit["missing_ticker_count"],
                              "unsupported_tickers": audit["unsupported_ticker_count"],
                              "unresolved_tickers": audit["unresolved_ticker_count"],
                              "recovered_tickers": audit["recovered_ticker_count"],
                              "output": str(args.output.resolve()),
                              "audit": str(args.audit_output.resolve()),
                              "processed": (str(args.processed_output.resolve())
                                            if args.processed_output else None),
                              "html": str(args.html_output.resolve()) if args.html_output else None}))
        elif args.command == "parse-senate-members":
            result = build_roster(args.input.read_bytes())
            _write_atomic(args.output, result)
            print(json.dumps({"members": len(result["members"]),
                              "sha256": result["metadata"]["sha256"],
                              "output": str(args.output.resolve())}))
        elif args.command == "discover-senate-members":
            result = discover_senate_members(
                args.archive, client=SenateMemberClient(args.timeout))
            _write_atomic(args.output, result)
            print(json.dumps({"members": len(result["members"]),
                              "sha256": result["metadata"]["sha256"],
                              "output": str(args.output.resolve())}))
        elif args.command == "discover-congress-senate-members":
            api_key = os.environ.get(args.api_key_env)
            if not api_key:
                raise CongressMemberError(
                    f"Congress.gov API key environment variable {args.api_key_env} is required")
            result = discover_congress_senate_members(
                args.archive, api_key, congress=args.congress,
                client=CongressMemberClient(api_key, args.timeout))
            _write_atomic(args.output, result)
            print(json.dumps({"members": len(result["members"]),
                              "congress": result["metadata"]["congress"],
                              "sha256": result["metadata"]["sha256"],
                              "credential_mode": "configured",
                              "output": str(args.output.resolve())}))
        elif args.command == "parse-senate-search-page":
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            result = asdict(parse_search_page(payload, start=args.start, length=args.length))
            _write_atomic(args.output, result)
            print(json.dumps({"start": result["start"], "rows": result["row_count"],
                              "records_total": result["records_total"],
                              "output": str(args.output.resolve())}))
        elif args.command == "senate-efd-gate":
            config = source_config_from_environment(
                enabled_name=args.enabled_env, terms_name=args.terms_env)
            result = collection_gate_status(config)
            _write_atomic(args.output, result)
            print(json.dumps({"status": result["status"],
                              "collection_enabled": result["collection_enabled"],
                              "terms_acknowledged": result["terms_acknowledged"],
                              "output": str(args.output.resolve())}))
        elif args.command == "discover-senate-efd":
            if args.isolate_source_unavailable and not args.attempt_output:
                raise SenateEfdError("Source failure isolation requires --attempt-output")
            if args.attempt_output and args.attempt_output.resolve() in {
                    value.resolve() for value in (args.output, args.failure_output) if value}:
                raise SenateEfdError("Source attempt must have its own output path")
            if args.failure_output and args.failure_output.resolve() == args.output.resolve():
                raise SenateEfdError("Discovery and failure outputs must be different paths")
            config = source_config_from_environment(
                enabled_name=args.enabled_env, terms_name=args.terms_env)
            try:
                result = discover_ptrs(
                    args.archive, config, submitted_start_date=args.submitted_start_date,
                    page_size=args.page_size)
            except (SenateHttpError, SenateTransportError) as exc:
                recoverable = (isinstance(exc, SenateTransportError) or
                               exc.diagnostic['http_status'] in {403, 408, 429, 500, 502, 503, 504})
                if not args.isolate_source_unavailable or not recoverable:
                    raise
                status = {'schema_version': 'senate-source-attempt/v1',
                          'source_id': 'senate_efd',
                          'status': ('blocked' if exc.diagnostic.get('http_status') == 403
                                     else 'unavailable'),
                          'available': False,
                          'attempted_at': datetime.now(timezone.utc).isoformat(),
                          **exc.diagnostic}
                _write_atomic(args.attempt_output, status)
                if args.failure_output:
                    _write_atomic(args.failure_output, {
                        'schema_version': 'senate-collection-failure/v1',
                        'source_id': 'senate_efd', 'status': 'failed',
                        'recorded_at': status['attempted_at'], **exc.diagnostic})
                print(json.dumps(status))
                return
            _write_atomic(args.output, result)
            if args.attempt_output:
                _write_atomic(args.attempt_output, {
                    'schema_version': 'senate-source-attempt/v1', 'source_id': 'senate_efd',
                    'status': 'ready', 'available': True,
                    'attempted_at': datetime.now(timezone.utc).isoformat(),
                    'retrieved_at': result['metadata']['retrieved_at']})
            print(json.dumps({"records": result["records_total"],
                              "pages": result["metadata"]["page_count"],
                              "sha256": result["metadata"]["sha256"],
                              "output": str(args.output.resolve())}))
        elif args.command == "match-senate-catalog":
            discovery = json.loads(args.discovery.read_text(encoding="utf-8"))
            roster = json.loads(args.roster.read_text(encoding="utf-8"))
            congress_roster = (json.loads(args.congress_roster.read_text(encoding="utf-8"))
                               if args.congress_roster else None)
            result = build_catalog_identities(discovery, roster, congress_roster)
            _write_atomic(args.output, result)
            print(json.dumps({"reports": result["report_count"],
                              "identity_counts": result["identity_counts"],
                              "output": str(args.output.resolve())}))
        elif args.command == "archive-senate-report-entrypoints":
            discovery = json.loads(args.discovery.read_text(encoding="utf-8"))
            selected_document_ids = (json.loads(args.document_ids.read_text(encoding="utf-8"))
                                     if args.document_ids else None)
            config = source_config_from_environment(
                enabled_name=args.enabled_env, terms_name=args.terms_env)
            result = archive_catalog_report_entrypoints(
                args.archive, discovery, limit=args.limit, config=config,
                selected_document_ids=selected_document_ids)
            _write_atomic(args.output, result)
            print(json.dumps({"attempted": result["attempted_count"],
                              "archived": result["archived_count"],
                              "failures": result["failure_count"],
                              "archived_total": result["archived_total"],
                              "pending": result["pending_count"],
                              "output": str(args.output.resolve())}))
        elif args.command == "plan-senate-amendment-backfill":
            result = load_amendment_predecessor_plan(
                args.review_root, args.historical_discovery, args.historical_identities,
                expected_target_count=args.expected_target_count)
            _write_atomic(args.output, result)
            print(json.dumps({"status": result["status"],
                              "targets": result["target_count"],
                              "unique_predecessors": result["unique_predecessor_count"],
                              "output": str(args.output.resolve())}))
        elif args.command == "resolve-senate-amendment-backfill":
            result = load_amendment_supplement(
                args.review_root, args.plan, args.historical_extractions)
            _write_atomic(args.output, result)
            print(json.dumps({"status": result["status"],
                              "targets": result["target_count"],
                              "selected_predecessors": result["selected_predecessor_count"],
                              "output": str(args.output.resolve())}))
        elif args.command == "activate-senate-amendment-supplement":
            supplement = json.loads(args.supplement.read_text(encoding="utf-8"))
            historical_extractions = json.loads(
                args.historical_extractions.read_text(encoding="utf-8"))
            result = activate_amendment_supplement(
                args.review_root, supplement, historical_extractions)
            _write_atomic(args.output, result)
            print(json.dumps({"status": result["status"],
                              "selected_predecessors": result["selected_predecessor_count"],
                              "output": str(args.output.resolve())}))
        elif args.command == "extract-senate-report-entrypoints":
            batch = json.loads(args.batch.read_text(encoding="utf-8"))
            result = extract_archived_report_batch(args.archive, batch)
            _write_atomic(args.output, result)
            print(json.dumps({"entrypoints": result["entrypoint_count"],
                              "extractions": result["extraction_count"],
                              "inspections": result["inspection_count"],
                              "failures": result["failure_count"],
                              "transactions": result["transaction_count"],
                              "output": str(args.output.resolve())}))
        elif args.command == "archive-senate-paper-pages":
            config = source_config_from_environment(
                enabled_name=args.enabled_env, terms_name=args.terms_env)
            result = archive_review_paper_pages(
                args.evidence_root, args.review_root,
                expected_documents=args.expected_documents,
                expected_pages=args.expected_pages, config=config)
            _write_atomic(args.output, result)
            print(json.dumps({"documents": result["document_count"],
                              "pages": result["page_count"],
                              "evidence_complete": result["evidence_complete"],
                              "output": str(args.output.resolve())}))
        elif args.command == "extract-senate-paper-pages":
            if bool(args.saved_grid_observations) != bool(args.saved_word_pages):
                raise SenateEfdError("Saved grid observations and word pages must be provided together")
            saved = {}
            if args.saved_grid_observations:
                grids = json.loads(args.saved_grid_observations.read_text(encoding="utf-8"))
                words = json.loads(args.saved_word_pages.read_text(encoding="utf-8"))
                if not isinstance(grids, dict) or not isinstance(words, dict) or not grids or set(grids) != set(words):
                    raise SenateEfdError("Saved Senate inputs require matching nonempty document maps")
                saved = {"grid_observations": grids, "cached_word_pages": words,
                         "selected_document_ids": sorted(grids)}
            result = extract_archived_paper_reports(
                args.evidence_root, args.review_root, executable=args.tesseract, **saved)
            _write_atomic(args.output, result)
            print(json.dumps({"reports": result["report_count"],
                              "extractions": result["extraction_count"],
                              "failures": result["failure_count"],
                              "transactions": result["transaction_count"],
                              "eligible": result["eligible_transaction_count"],
                              "quarantined": result["quarantined_transaction_count"],
                              "output": str(args.output.resolve())}))
        elif args.command == "oge-gate":
            config = oge_source_config_from_environment(
                enabled_name=args.enabled_env, terms_name=args.terms_env)
            result = oge_collection_gate_status(config)
            _write_atomic(args.output, result)
            print(json.dumps({"status": result["status"],
                              "collection_enabled": result["collection_enabled"],
                              "terms_acknowledged": result["terms_acknowledged"],
                              "output": str(args.output.resolve())}))
        elif args.command == "discover-oge":
            config = oge_source_config_from_environment(
                enabled_name=args.enabled_env, terms_name=args.terms_env)
            result = discover_oge_catalog(
                args.archive, config, page_size=args.page_size)
            _write_atomic(args.output, result)
            print(json.dumps({"records": result["records_total"],
                              "transaction_reports": len(result["transactions"]),
                              "direct_pdfs": result["metadata"]["direct_pdf_count"],
                              "request_required": result["metadata"]["request_required_count"],
                              "output": str(args.output.resolve())}))
        elif args.command == "archive-oge-direct-pdfs":
            catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
            config = oge_source_config_from_environment(
                enabled_name=args.enabled_env, terms_name=args.terms_env)
            result = archive_oge_direct_batch(
                args.archive, catalog, config, limit=args.limit)
            _write_atomic(args.output, result)
            print(json.dumps({"attempted": result["attempted_count"],
                              "archived": result["archived_count"],
                              "failures": result["failure_count"],
                              "request_required": result["request_required_count"],
                              "pending": result["pending_count"],
                              "output": str(args.output.resolve())}))
        elif args.command == "extract-oge-direct-pdfs":
            batch = json.loads(args.batch.read_text(encoding="utf-8"))
            reports = batch.get("reports") if isinstance(batch, dict) else None
            if not isinstance(reports, list):
                raise OgeCatalogError("OGE archive batch has no report metadata")
            args.output_dir.mkdir(parents=True, exist_ok=True)
            extracted = []
            failures = []
            reused_count = parsed_count = 0
            for metadata in reports:
                if not isinstance(metadata, dict) or not isinstance(metadata.get("document_id"), str):
                    raise OgeCatalogError("OGE archive batch report is invalid")
                metadata_path = args.archive / "oge" / "reports" / metadata["document_id"] / \
                    f"{metadata.get('sha256')}.json"
                try:
                    result = (load_reusable_oge_extraction(
                        args.archive, metadata_path, args.reuse_root,
                        **({"generic": True} if args.generic else {}))
                        if args.reuse_root else None)
                    if result is None:
                        result = parse_oge_archived_pdf(args.archive, metadata_path,
                                                       **({"generic": True} if args.generic else {}))
                        parsed_count += 1
                    else:
                        reused_count += 1
                    _write_atomic(args.output_dir / f"{metadata['document_id']}.json", result)
                    extracted.append(result)
                except (OgeCatalogError, OSError, json.JSONDecodeError) as exc:
                    failures.append({"document_id": metadata["document_id"], "error": str(exc)})
            summary = {
                "schema_version": "oge-278t-extraction-batch/v1", "source_id": "oge",
                "report_count": len(reports), "extraction_count": len(extracted),
                "reused_extraction_count": reused_count,
                "parsed_extraction_count": parsed_count,
                "failure_count": len(failures),
                "transaction_count": sum(len(row["transactions"]) for row in extracted),
                "quarantined_row_count": sum(len(row["quarantined"]) for row in extracted),
                "failures": failures,
            }
            _write_atomic(args.output, summary)
            print(json.dumps({**{key: summary[key] for key in (
                "report_count", "extraction_count", "reused_extraction_count",
                "parsed_extraction_count", "failure_count", "transaction_count",
                "quarantined_row_count")}, "output": str(args.output.resolve())}))
        elif args.command == "build-oge-candidate":
            from .oge_source_selection import parse_document_sources, verify_selected_native_cache
            document_sources = parse_document_sources(args.document_source)
            if document_sources and args.evidence_root is None:
                raise OgeCatalogError('Explicit OGE document sources require --evidence-root')
            pinned_source_inputs = [args.catalog, args.base]
            if document_sources:
                from .oge_saved_observations import guard_saved_oge_outputs
                guard_saved_oge_outputs([args.output, args.audit_output],
                    inputs=pinned_source_inputs,
                    protected_roots=[args.evidence_root, args.extractions_dir,
                                     args.legacy_extractions_dir, args.catalog_history_dir,
                                     args.manual_cover_dates_dir])
            from .oge_native_partition import DEFAULT_VERSIONS as VISIBLE_DEFAULTS, GENERIC_VERSIONS as VISIBLE_GENERICS
            from .oge_reports import selected_parser_version, _read_archived_source
            scoped_generic_ids = args.generic_document_id
            if (len(scoped_generic_ids) > 32 or len(set(scoped_generic_ids)) != len(scoped_generic_ids)
                    or any(re.fullmatch(r'[0-9a-f]{32}', value) is None for value in scoped_generic_ids)):
                raise OgeCatalogError('Scoped generic document IDs must be unique canonical IDs, at most 32')
            scoped_generic_ids = set(scoped_generic_ids)
            if scoped_generic_ids and args.evidence_root is None:
                raise OgeCatalogError('Scoped generic selection requires --evidence-root')
            if args.saved_observations is not None:
                from .oge_saved_observations import guard_saved_oge_outputs
                if args.evidence_root is None:
                    raise OgeCatalogError("Saved OGE observations require --evidence-root")
                guard_saved_oge_outputs([args.output, args.audit_output],
                    inputs=[args.saved_observations, args.catalog, args.base,
                            *([args.saved_exhibit_observations] if args.saved_exhibit_observations else [])],
                    protected_roots=[args.evidence_root, args.extractions_dir,
                                     args.legacy_extractions_dir, args.catalog_history_dir,
                                     args.manual_cover_dates_dir])
            catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
            selected_extractions = {}
            required_current_documents = set()
            legacy_extractions = {}
            if args.legacy_extractions_dir and not scoped_generic_ids:
                if not args.generic:
                    raise OgeCatalogError("Legacy inheritance input requires explicit generic mode")
                for path in sorted(args.legacy_extractions_dir.rglob("*.json")):
                    value = json.loads(path.read_text(encoding="utf-8"))
                    if value.get("parser_version") != OGE_REPORT_PARSER_VERSION:
                        continue
                    key = value.get("document_id")
                    if isinstance(key, str) and key in document_sources:
                        if value.get('source_sha256') != document_sources[key]:
                            continue
                        pinned_source_inputs.extend(verify_selected_native_cache(
                            path, args.legacy_extractions_dir, value, args.evidence_root))
                    if not isinstance(key, str) or key in legacy_extractions:
                        raise OgeCatalogError("Legacy extraction document identity is missing or duplicated")
                    legacy_extractions[key] = value
            for path in sorted(args.extractions_dir.rglob("*.json")):
                value = json.loads(path.read_text(encoding="utf-8"))
                version = value.get("parser_version")
                document_id = value.get("document_id")
                if isinstance(document_id, str) and document_id in document_sources:
                    if value.get('source_sha256') != document_sources[document_id]:
                        continue
                use_generic = args.generic or document_id in scoped_generic_ids
                if version not in {OGE_REPORT_PARSER_VERSION, *VISIBLE_DEFAULTS,
                                   TRUMP_SEPT_2026_PARSER_VERSION,
                                   TRUMP_SEPT_2026_SECOND_PASS_VERSION,
                                   TRUMP_SEPT_2026_STRUCTURAL_PASS_VERSION,
                                   TRUMP_SEPT_2026_PAGE7_PASS_VERSION} and not (
                        use_generic and version in {OGE_GENERIC_PARSER_VERSION, *VISIBLE_GENERICS}):
                    continue
                if args.evidence_root is not None:
                    metadata_path = args.evidence_root / f"oge/reports/{document_id}/{value.get('source_sha256')}.json"
                    metadata, source_path, source_sha = _read_archived_source(args.evidence_root, metadata_path)
                    expected = selected_parser_version(metadata, source_path, source_sha, generic=use_generic)
                    required_current_documents.add(document_id)
                    if version != expected:
                        continue
                if document_id in document_sources:
                    pinned_source_inputs.extend(verify_selected_native_cache(
                        path, args.extractions_dir, value, args.evidence_root))
                if version in {TRUMP_SEPT_2026_PARSER_VERSION,
                               TRUMP_SEPT_2026_SECOND_PASS_VERSION,
                               TRUMP_SEPT_2026_STRUCTURAL_PASS_VERSION,
                               TRUMP_SEPT_2026_PAGE7_PASS_VERSION} and not (
                        document_id == TRUMP_SEPT_2026_DOCUMENT_ID and
                        value.get("source_url") == TRUMP_SEPT_2026_SOURCE_URL and
                        value.get("source_sha256") == TRUMP_SEPT_2026_SOURCE_SHA256):
                    raise OgeCatalogError("Source-bound OGE extraction is not the fixed report")
                previous = selected_extractions.get(document_id)
                if previous is not None:
                    if use_generic and OGE_GENERIC_PARSER_VERSION in {version, previous.get("parser_version")}:
                        if version == previous.get("parser_version") or any(
                                value.get(field) != previous.get(field)
                                for field in ("source_sha256", "source_url", "document_id")):
                            raise OgeCatalogError("Generic and legacy source versions are ambiguous")
                        older = previous if version == OGE_GENERIC_PARSER_VERSION else value
                        if older.get("parser_version") == OGE_REPORT_PARSER_VERSION:
                            supplied = legacy_extractions.get(document_id)
                            if supplied is not None and supplied != older:
                                raise OgeCatalogError("Conflicting bound legacy extractions")
                            legacy_extractions[document_id] = older
                        if version == OGE_GENERIC_PARSER_VERSION:
                            selected_extractions[document_id] = value
                        continue
                    if (document_id != TRUMP_SEPT_2026_DOCUMENT_ID or
                            version == previous.get("parser_version")):
                        raise OgeCatalogError("OGE extraction document ID is duplicated")
                    priority = {
                        OGE_REPORT_PARSER_VERSION: 0,
                        TRUMP_SEPT_2026_PARSER_VERSION: 1,
                        TRUMP_SEPT_2026_SECOND_PASS_VERSION: 2,
                        TRUMP_SEPT_2026_STRUCTURAL_PASS_VERSION: 3,
                        TRUMP_SEPT_2026_PAGE7_PASS_VERSION: 4,
                    }
                    if priority[version] < priority[previous.get("parser_version")]:
                        continue
                selected_extractions[document_id] = value
            if required_current_documents - set(selected_extractions):
                raise OgeCatalogError('A current source-selected OGE extraction is missing')
            if document_sources.keys() - selected_extractions.keys():
                raise OgeCatalogError('A requested archived OGE document source was not selected')
            if scoped_generic_ids - set(selected_extractions):
                raise OgeCatalogError('A requested scoped generic document was not selected')
            for document_id in sorted(scoped_generic_ids):
                selected = selected_extractions[document_id]
                sha = selected.get('source_sha256')
                metadata, source_path, actual_sha = _read_archived_source(args.evidence_root,
                    args.evidence_root / f'oge/reports/{document_id}/{sha}.json')
                if (selected.get('parser_version') != OGE_GENERIC_PARSER_VERSION
                        or selected_parser_version(metadata, source_path, actual_sha) != OGE_REPORT_PARSER_VERSION):
                    # Other cached families retain their existing source-plan
                    # contract; do not manufacture an ordinary v2 predecessor.
                    continue
                legacy_root = args.legacy_extractions_dir or args.extractions_dir
                legacy_path = legacy_root / document_id / str(sha) / (OGE_REPORT_PARSER_VERSION.replace('/', '-') + '.json')
                if not legacy_path.is_file():
                    raise OgeCatalogError('Scoped generic selection lacks its canonical same-SHA v2 predecessor')
                older = json.loads(legacy_path.read_text(encoding='utf-8'))
                if document_id in document_sources:
                    pinned_source_inputs.extend(verify_selected_native_cache(
                        legacy_path, legacy_root, older, args.evidence_root))
                if (older.get('parser_version') != OGE_REPORT_PARSER_VERSION or any(
                        older.get(field) != selected.get(field) for field in
                        ('document_id', 'source_sha256', 'source_url', 'source_id',
                         'catalog_filer_name', 'agency', 'position_title', 'catalog_added_date'))):
                    raise OgeCatalogError('Scoped generic v2 predecessor differs from the selected source')
                legacy_extractions[document_id] = older
            extractions = list(selected_extractions.values())
            # Bind the selected cache before an existing interpretation layer
            # (such as the manual cover-date sidecar) changes the receiver view.
            source_extractions = {row['document_id']: deepcopy(row) for row in extractions}
            if args.manual_cover_dates_dir:
                from .oge_manual_dates import apply_manual_cover_dates
                extractions = apply_manual_cover_dates(
                    extractions, args.manual_cover_dates_dir)
            catalog_history = []
            if args.catalog_history_dir:
                current_bytes = encode(catalog)
                for path in sorted(args.catalog_history_dir.glob("*.json")):
                    value = json.loads(path.read_text(encoding="utf-8"))
                    if encode(value) != current_bytes:
                        catalog_history.append(value)
            base = json.loads(args.base.read_text(encoding="utf-8"))
            saved_observations = {}
            if args.saved_observations is not None:
                from .oge_saved_observations import load_saved_oge_observations
                saved_observations = load_saved_oge_observations(args.saved_observations,
                    evidence_root=args.evidence_root, extractions=extractions)
                source_inputs = []
                for document, request in saved_observations.items():
                    source_inputs.extend([Path(request["archived_pdf_path"]),
                        args.evidence_root / f"oge/reports/{document}/{request['observations'][0]['source_sha256']}.json"])
                guard_saved_oge_outputs([args.output, args.audit_output], inputs=source_inputs)
            result, audit = build_oge_candidate(
                catalog, extractions, base, data_cutoff_at=args.data_cutoff_at,
                evidence_root=args.evidence_root,
                source_extractions_by_document=source_extractions,
                catalog_history=catalog_history,
                **({"saved_observations_by_document": saved_observations} if args.saved_observations is not None else {}),
                **({"legacy_extractions_by_document": legacy_extractions} if args.generic or scoped_generic_ids else {}),
                **({"saved_exhibit_observations_by_document": json.loads(args.saved_exhibit_observations.read_text(encoding="utf-8"))}
                   if args.saved_exhibit_observations else {}))
            bundle = build(result, generated_at=args.data_cutoff_at, allow_production=True,
                           allow_empty_production=True)
            result["meta"].update(snapshot_id=bundle.manifest["snapshot_id"],
                                  generated_at=args.data_cutoff_at)
            audit["candidate_snapshot_id"] = bundle.manifest["snapshot_id"]
            audit["candidate_sha256"] = digest(encode(result))
            if document_sources:
                guard_saved_oge_outputs([args.output, args.audit_output], inputs=pinned_source_inputs)
            _write_atomic(args.output, result)
            _write_atomic(args.audit_output, audit)
            print(json.dumps({"people": len(result["people"]),
                              "transactions": len(result["transactions"]),
                              "quarantined_rows": audit["quarantined_row_count"],
                              "output": str(args.output.resolve()),
                              "audit": str(args.audit_output.resolve())}))
        elif args.command == "discover-house-members":
            result = discover_members(args.archive, client=HouseMemberClient(args.timeout))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(encode(result))
            print(json.dumps({"members": len(result["members"]),
                              "published_date": result["metadata"]["published_date"],
                              "sha256": result["metadata"]["sha256"],
                              "output": str(args.output.resolve())}))
        elif args.command == "suggest-house-identity":
            extraction = json.loads(args.extraction.read_text(encoding="utf-8"))
            roster = json.loads(args.members.read_text(encoding="utf-8"))
            result = suggest_identity(extraction, roster)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(encode(result))
            print(json.dumps({"document_id": result["document_id"], "status": result["status"],
                              "person_id": result.get("person_id"),
                              "output": str(args.output.resolve())}))
        elif args.command == "plan-house-ptr-sync":
            discovery = json.loads(args.discovery.read_text(encoding="utf-8"))
            previous = json.loads(args.checkpoint.read_text(encoding="utf-8")) if args.checkpoint else None
            result = plan_checkpoint(discovery, args.archive, previous, planned_at=args.planned_at)
            _write_atomic(args.output, result)
            print(json.dumps({"year": result["filing_year"], "index_sha256": result["index_sha256"],
                              "counts": result["counts"], "queue": len(result["queue"]),
                              "anomalies": len(result["anomalies"]),
                              "output": str(args.output.resolve())}))
        else:
            checkpoint = json.loads(args.checkpoint.read_text(encoding="utf-8"))
            metadata = json.loads(args.metadata.read_text(encoding="utf-8")) if args.metadata else None
            result = record_result(checkpoint, args.document_id, args.status,
                                   result_at=args.result_at, archive_metadata=metadata,
                                   error=args.error)
            _write_atomic(args.output, result)
            print(json.dumps({"document_id": args.document_id, "status": args.status,
                              "counts": result["counts"], "queue": len(result["queue"]),
                              "output": str(args.output.resolve())}))
    except (ValueError, RuntimeError, HouseIndexError, DisclosureCandidateError, AlpacaMarketError,
            SenateEfdError, SenateRosterError, SenateIdentityError, SenateHistoryError,
            CongressMemberError, OgeCatalogError, ReleaseReadinessError,
            KeyError, OSError,
            json.JSONDecodeError) as exc:
        if (args.command == "discover-senate-efd" and args.failure_output and
                isinstance(exc, SenateHttpError)):
            _write_atomic(args.failure_output, {
                "schema_version": "senate-collection-failure/v1",
                "source_id": "senate_efd", "status": "failed",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                **exc.diagnostic,
            })
        parser.exit(2, f"Snapshot operation failed: {exc}\n")


if __name__ == "__main__":
    main()
