"""Senate discovery, saved-paper replay and candidate commands."""
import sys
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from ..builder import build
from ..codec import digest, encode
from ..congress_members import CongressMemberClient, CongressMemberError, DEFAULT_CONGRESS, discover_congress_senate_members
from ..legacy import load
from ..senate import SenateEfdError, SenateHttpError, SenateTransportError, collection_gate_status, discover_ptrs, source_config_from_environment
from ..senate_members import SenateMemberClient, discover_members as discover_senate_members
from ..senate_identity import build_catalog_identities
from ..senate_reports import archive_catalog_report_entrypoints, extract_archived_report_batch
from ..senate_paper import extract_archived_paper_reports
from ..senate_candidate import load_senate_candidate
from ..senate_history import activate_amendment_supplement, load_amendment_predecessor_plan, load_amendment_supplement
from ._io import _write_atomic
from ._runtime import invoke


def configure_build_senate_candidate(parser):
    senate_candidate = parser
    senate_candidate.add_argument("--review-root", type=Path, required=True)
    senate_candidate.add_argument("--evidence-root", type=Path)
    senate_candidate.add_argument("--state-status", type=Path, required=True)
    senate_candidate.add_argument("--base", type=Path, required=True)
    senate_candidate.add_argument("--output", type=Path, required=True)
    senate_candidate.add_argument("--audit-output", type=Path, required=True)
    senate_candidate.add_argument("--html-output", type=Path)


def execute_build_senate_candidate(args):
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
        renderer = load("render_dashboard", version="v2")
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


def configure_discover_senate_members(parser):
    senate_roster_live = parser
    senate_roster_live.add_argument("--archive", type=Path, required=True)
    senate_roster_live.add_argument("--output", type=Path, required=True)
    senate_roster_live.add_argument("--timeout", type=float, default=30.0)


def execute_discover_senate_members(args):
    result = discover_senate_members(
        args.archive, client=SenateMemberClient(args.timeout))
    _write_atomic(args.output, result)
    print(json.dumps({"members": len(result["members"]),
                      "sha256": result["metadata"]["sha256"],
                      "output": str(args.output.resolve())}))


def configure_discover_congress_senate_members(parser):
    congress_roster_live = parser
    congress_roster_live.add_argument("--archive", type=Path, required=True)
    congress_roster_live.add_argument("--output", type=Path, required=True)
    congress_roster_live.add_argument("--congress", type=int, default=DEFAULT_CONGRESS)
    congress_roster_live.add_argument("--api-key-env", default="CONGRESS_GOV_API_KEY")
    congress_roster_live.add_argument("--timeout", type=float, default=30.0)


def execute_discover_congress_senate_members(args):
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


def configure_senate_efd_gate(parser):
    senate_gate = parser
    senate_gate.add_argument("--output", type=Path, required=True)
    senate_gate.add_argument("--enabled-env", default="SENATE_EFD_COLLECTION_ENABLED")
    senate_gate.add_argument("--terms-env", default="SENATE_EFD_TERMS_ACKNOWLEDGED")


def execute_senate_efd_gate(args):
    config = source_config_from_environment(
        enabled_name=args.enabled_env, terms_name=args.terms_env)
    result = collection_gate_status(config)
    _write_atomic(args.output, result)
    print(json.dumps({"status": result["status"],
                      "collection_enabled": result["collection_enabled"],
                      "terms_acknowledged": result["terms_acknowledged"],
                      "output": str(args.output.resolve())}))


def configure_discover_senate_efd(parser):
    senate_discovery = parser
    senate_discovery.add_argument("--archive", type=Path, required=True)
    senate_discovery.add_argument("--output", type=Path, required=True)
    senate_discovery.add_argument("--failure-output", type=Path)
    senate_discovery.add_argument("--attempt-output", type=Path)
    senate_discovery.add_argument("--isolate-source-unavailable", action="store_true")
    senate_discovery.add_argument("--submitted-start-date", required=True)
    senate_discovery.add_argument("--page-size", type=int, default=100)
    senate_discovery.add_argument("--enabled-env", default="SENATE_EFD_COLLECTION_ENABLED")
    senate_discovery.add_argument("--terms-env", default="SENATE_EFD_TERMS_ACKNOWLEDGED")


def execute_discover_senate_efd(args):
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


def configure_match_senate_catalog(parser):
    senate_identities = parser
    senate_identities.add_argument("--discovery", type=Path, required=True)
    senate_identities.add_argument("--roster", type=Path, required=True)
    senate_identities.add_argument("--congress-roster", type=Path)
    senate_identities.add_argument("--output", type=Path, required=True)


def execute_match_senate_catalog(args):
    discovery = json.loads(args.discovery.read_text(encoding="utf-8"))
    roster = json.loads(args.roster.read_text(encoding="utf-8"))
    congress_roster = (json.loads(args.congress_roster.read_text(encoding="utf-8"))
                       if args.congress_roster else None)
    result = build_catalog_identities(discovery, roster, congress_roster)
    _write_atomic(args.output, result)
    print(json.dumps({"reports": result["report_count"],
                      "identity_counts": result["identity_counts"],
                      "output": str(args.output.resolve())}))


def configure_archive_senate_report_entrypoints(parser):
    senate_reports = parser
    senate_reports.add_argument("--discovery", type=Path, required=True)
    senate_reports.add_argument("--archive", type=Path, required=True)
    senate_reports.add_argument("--output", type=Path, required=True)
    senate_reports.add_argument("--limit", type=int, default=2)
    senate_reports.add_argument("--document-ids", type=Path)
    senate_reports.add_argument("--enabled-env", default="SENATE_EFD_COLLECTION_ENABLED")
    senate_reports.add_argument("--terms-env", default="SENATE_EFD_TERMS_ACKNOWLEDGED")


def execute_archive_senate_report_entrypoints(args):
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


def configure_extract_senate_report_entrypoints(parser):
    senate_extract = parser
    senate_extract.add_argument("--batch", type=Path, required=True)
    senate_extract.add_argument("--archive", type=Path, required=True)
    senate_extract.add_argument("--review-root", type=Path)
    senate_extract.add_argument("--output", type=Path, required=True)


def execute_extract_senate_report_entrypoints(args):
    batch = json.loads(args.batch.read_text(encoding="utf-8"))
    result = extract_archived_report_batch(args.archive, batch, review_root=args.review_root)
    _write_atomic(args.output, result)
    print(json.dumps({"entrypoints": result["entrypoint_count"],
                      "extractions": result["extraction_count"],
                      "inspections": result["inspection_count"],
                      "failures": result["failure_count"],
                      "transactions": result["transaction_count"],
                      "output": str(args.output.resolve())}))


def configure_extract_senate_paper_pages(parser):
    senate_paper_extract = parser
    senate_paper_extract.add_argument("--evidence-root", type=Path, required=True)
    senate_paper_extract.add_argument("--review-root", type=Path, required=True)
    senate_paper_extract.add_argument("--tesseract", default="tesseract")
    senate_paper_extract.add_argument("--saved-grid-observations", type=Path)
    senate_paper_extract.add_argument("--saved-word-pages", type=Path)
    senate_paper_extract.add_argument("--output", type=Path, required=True)


def execute_extract_senate_paper_pages(args):
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


def configure_plan_senate_amendment_backfill(parser):
    senate_history = parser
    senate_history.add_argument("--review-root", type=Path, required=True)
    senate_history.add_argument("--historical-discovery", type=Path, required=True)
    senate_history.add_argument("--historical-identities", type=Path, required=True)
    senate_history.add_argument("--expected-target-count", type=int, required=True)
    senate_history.add_argument("--document-id", action="append")
    senate_history.add_argument("--output", type=Path, required=True)


def execute_plan_senate_amendment_backfill(args):
    result = load_amendment_predecessor_plan(
        args.review_root, args.historical_discovery, args.historical_identities,
        expected_target_count=args.expected_target_count, selected_document_ids=args.document_id)
    _write_atomic(args.output, result)
    print(json.dumps({"status": result["status"],
                      "targets": result["target_count"],
                      "unique_predecessors": result["unique_predecessor_count"],
                      "output": str(args.output.resolve())}))


def configure_resolve_senate_amendment_backfill(parser):
    senate_history_resolve = parser
    senate_history_resolve.add_argument("--review-root", type=Path, required=True)
    senate_history_resolve.add_argument("--plan", type=Path, required=True)
    senate_history_resolve.add_argument("--historical-extractions", type=Path, required=True)
    senate_history_resolve.add_argument("--output", type=Path, required=True)


def execute_resolve_senate_amendment_backfill(args):
    result = load_amendment_supplement(
        args.review_root, args.plan, args.historical_extractions)
    _write_atomic(args.output, result)
    print(json.dumps({"status": result["status"],
                      "targets": result["target_count"],
                      "selected_predecessors": result["selected_predecessor_count"],
                      "output": str(args.output.resolve())}))


def configure_activate_senate_amendment_supplement(parser):
    senate_history_activate = parser
    senate_history_activate.add_argument("--review-root", type=Path, required=True)
    senate_history_activate.add_argument("--supplement", type=Path, required=True)
    senate_history_activate.add_argument("--historical-extractions", type=Path, required=True)
    senate_history_activate.add_argument("--output", type=Path, required=True)


def execute_activate_senate_amendment_supplement(args):
    supplement = json.loads(args.supplement.read_text(encoding="utf-8"))
    historical_extractions = json.loads(
        args.historical_extractions.read_text(encoding="utf-8"))
    result = activate_amendment_supplement(
        args.review_root, supplement, historical_extractions)
    _write_atomic(args.output, result)
    print(json.dumps({"status": result["status"],
                      "selected_predecessors": result["selected_predecessor_count"],
                      "output": str(args.output.resolve())}))


COMMANDS = {
    'build-senate-candidate': (configure_build_senate_candidate, execute_build_senate_candidate),
    'discover-senate-members': (configure_discover_senate_members, execute_discover_senate_members),
    'discover-congress-senate-members': (configure_discover_congress_senate_members, execute_discover_congress_senate_members),
    'senate-efd-gate': (configure_senate_efd_gate, execute_senate_efd_gate),
    'discover-senate-efd': (configure_discover_senate_efd, execute_discover_senate_efd),
    'match-senate-catalog': (configure_match_senate_catalog, execute_match_senate_catalog),
    'archive-senate-report-entrypoints': (configure_archive_senate_report_entrypoints, execute_archive_senate_report_entrypoints),
    'extract-senate-report-entrypoints': (configure_extract_senate_report_entrypoints, execute_extract_senate_report_entrypoints),
    'extract-senate-paper-pages': (configure_extract_senate_paper_pages, execute_extract_senate_paper_pages),
    'plan-senate-amendment-backfill': (configure_plan_senate_amendment_backfill, execute_plan_senate_amendment_backfill),
    'resolve-senate-amendment-backfill': (configure_resolve_senate_amendment_backfill, execute_resolve_senate_amendment_backfill),
    'activate-senate-amendment-supplement': (configure_activate_senate_amendment_supplement, execute_activate_senate_amendment_supplement),
}


def main():
    command = sys.argv[0].rsplit(" ", 1)[-1]
    invoke(command, *COMMANDS[command])
