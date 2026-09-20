import argparse
from dataclasses import asdict
import json
from pathlib import Path
import tempfile

from .builder import build
from .codec import digest, encode
from .materialize import materialize
from .house import HouseDocumentClient, HouseIndexClient, HouseIndexError, archive_indexed_ptr, discover
from .house_ptr import make_review_template, parse_archived_pdf, promote_review, qualify_automatic
from .house_sync import plan_checkpoint, record_result
from .house_members import HouseMemberClient, discover_members, suggest_identity
from .house_candidate import load_house_candidate
from .disclosure_candidate import DisclosureCandidateError, build_disclosure_candidate
from .legacy import load
from .senate import SenateEfdError, collection_gate_status, discover_ptrs, parse_search_page, \
    source_config_from_environment
from .senate_members import SenateMemberClient, SenateRosterError, build_roster, \
    discover_members as discover_senate_members
from .senate_identity import SenateIdentityError, build_catalog_identities
from .senate_reports import archive_catalog_report_entrypoints, extract_archived_report_batch
from .senate_candidate import load_senate_candidate
from .public_repo import HTTPTransport, PublicSnapshotRepository
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
    fetch = sub.add_parser("fetch-public")
    fetch.add_argument("--owner", required=True)
    fetch.add_argument("--repo", required=True)
    fetch.add_argument("--ref", choices=["main", "demo"], default="main")
    fetch.add_argument("--mode", choices=["dashboard", "search", "person", "ticker"], default="dashboard")
    fetch.add_argument("--key")
    fetch.add_argument("--token-env", default="GITHUB_TOKEN")
    fetch.add_argument("--output", type=Path, required=True)
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
    qualify_ptr.add_argument("--output", type=Path, required=True)
    candidate = sub.add_parser("build-house-candidate")
    candidate.add_argument("--review-root", type=Path, required=True)
    candidate.add_argument("--state-status", type=Path, required=True)
    candidate.add_argument("--base", type=Path, required=True)
    candidate.add_argument("--output", type=Path, required=True)
    candidate.add_argument("--html-output", type=Path)
    senate_candidate = sub.add_parser("build-senate-candidate")
    senate_candidate.add_argument("--review-root", type=Path, required=True)
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
    disclosure_candidate.add_argument(
        "--harmonize-cutoffs", action="store_true",
        help="Align source candidates to their earliest cutoff and exclude later-filed facts")
    senate_roster = sub.add_parser("parse-senate-members")
    senate_roster.add_argument("--input", type=Path, required=True)
    senate_roster.add_argument("--output", type=Path, required=True)
    senate_roster_live = sub.add_parser("discover-senate-members")
    senate_roster_live.add_argument("--archive", type=Path, required=True)
    senate_roster_live.add_argument("--output", type=Path, required=True)
    senate_roster_live.add_argument("--timeout", type=float, default=30.0)
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
    senate_discovery.add_argument("--submitted-start-date", required=True)
    senate_discovery.add_argument("--page-size", type=int, default=100)
    senate_discovery.add_argument("--enabled-env", default="SENATE_EFD_COLLECTION_ENABLED")
    senate_discovery.add_argument("--terms-env", default="SENATE_EFD_TERMS_ACKNOWLEDGED")
    senate_identities = sub.add_parser("match-senate-catalog")
    senate_identities.add_argument("--discovery", type=Path, required=True)
    senate_identities.add_argument("--roster", type=Path, required=True)
    senate_identities.add_argument("--output", type=Path, required=True)
    senate_reports = sub.add_parser("archive-senate-report-entrypoints")
    senate_reports.add_argument("--discovery", type=Path, required=True)
    senate_reports.add_argument("--archive", type=Path, required=True)
    senate_reports.add_argument("--output", type=Path, required=True)
    senate_reports.add_argument("--limit", type=int, default=2)
    senate_reports.add_argument("--enabled-env", default="SENATE_EFD_COLLECTION_ENABLED")
    senate_reports.add_argument("--terms-env", default="SENATE_EFD_TERMS_ACKNOWLEDGED")
    senate_extract = sub.add_parser("extract-senate-report-entrypoints")
    senate_extract.add_argument("--batch", type=Path, required=True)
    senate_extract.add_argument("--archive", type=Path, required=True)
    senate_extract.add_argument("--output", type=Path, required=True)
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
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            if payload.get("meta", {}).get("is_demo") is not False:
                raise ValueError("Public production publication requires is_demo=false")
            bundle = build(payload, generated_at=args.generated_at, allow_production=True,
                           allow_empty_production=args.allow_empty_production)
            result = materialize(args.root, bundle)
            print(json.dumps({"changed": result.changed, "business_changed": result.business_changed,
                              "snapshot_id": bundle.manifest["snapshot_id"],
                              "written": result.written, "removed": result.removed}))
        elif args.command == "fetch-public":
            import os
            token = os.environ.get(args.token_env) if args.token_env else None
            selection = PublicSnapshotRepository(args.owner, args.repo, ref=args.ref,
                transport=HTTPTransport(token=token)).fetch(args.mode, args.key)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(encode(selection.snapshot))
            print(json.dumps({"commit": selection.commit, "output": str(args.output.resolve())}))
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
            result = qualify_automatic(extraction, identity)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(encode(result))
            print(json.dumps({"document_id": result["document_id"],
                              "qualified": result["qualification"]["qualified_count"],
                              "quarantined": result["qualification"]["quarantined_count"],
                              "output": str(args.output.resolve())}))
        elif args.command == "build-house-candidate":
            result = load_house_candidate(args.review_root, args.state_status, args.base)
            generated_at = result["meta"]["data_cutoff_at"]
            bundle = build(result, generated_at=generated_at, allow_production=True)
            result["meta"].update(snapshot_id=bundle.manifest["snapshot_id"],
                                  generated_at=generated_at)
            _write_atomic(args.output, result)
            if args.html_output:
                renderer = load("render_dashboard")
                html = renderer.render_html(renderer.load_dashboard_data(args.output))
                args.html_output.parent.mkdir(parents=True, exist_ok=True)
                args.html_output.write_text(html, encoding="utf-8")
            print(json.dumps({"people": len(result["people"]),
                              "transactions": len(result["transactions"]),
                              "output": str(args.output.resolve()),
                              "html": str(args.html_output.resolve()) if args.html_output else None}))
        elif args.command == "build-senate-candidate":
            result, audit = load_senate_candidate(
                args.review_root, args.state_status, args.base)
            generated_at = result["meta"]["data_cutoff_at"]
            bundle = build(result, generated_at=generated_at, allow_production=True)
            result["meta"].update(snapshot_id=bundle.manifest["snapshot_id"],
                                  generated_at=generated_at)
            audit["candidate_snapshot_id"] = bundle.manifest["snapshot_id"]
            audit["candidate_sha256"] = digest(encode(result))
            _write_atomic(args.output, result)
            _write_atomic(args.audit_output, audit)
            if args.html_output:
                renderer = load("render_dashboard")
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
            sources: dict[str, dict] = {}
            for item in args.source:
                source_id, separator, source_path = item.partition("=")
                if not separator or not source_id or not source_path or source_id in sources:
                    raise DisclosureCandidateError(
                        "Each --source must be a unique SOURCE_ID=PATH value")
                sources[source_id] = json.loads(Path(source_path).read_text(encoding="utf-8"))
            base = json.loads(args.base.read_text(encoding="utf-8"))
            cutoff_audit = {} if args.audit_output else None
            result = build_disclosure_candidate(
                base, sources, harmonize_cutoffs=args.harmonize_cutoffs,
                harmonization_audit=cutoff_audit)
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
                renderer = load("render_dashboard")
                html = renderer.render_html(renderer.load_dashboard_data(args.output))
                args.html_output.parent.mkdir(parents=True, exist_ok=True)
                args.html_output.write_text(html, encoding="utf-8")
            print(json.dumps({"sources": sorted(sources), "people": len(result["people"]),
                              "transactions": len(result["transactions"]),
                              "reported_holdings": len(result["reported_holdings"]),
                              "output": str(args.output.resolve()),
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
            config = source_config_from_environment(
                enabled_name=args.enabled_env, terms_name=args.terms_env)
            result = discover_ptrs(
                args.archive, config, submitted_start_date=args.submitted_start_date,
                page_size=args.page_size)
            _write_atomic(args.output, result)
            print(json.dumps({"records": result["records_total"],
                              "pages": result["metadata"]["page_count"],
                              "sha256": result["metadata"]["sha256"],
                              "output": str(args.output.resolve())}))
        elif args.command == "match-senate-catalog":
            discovery = json.loads(args.discovery.read_text(encoding="utf-8"))
            roster = json.loads(args.roster.read_text(encoding="utf-8"))
            result = build_catalog_identities(discovery, roster)
            _write_atomic(args.output, result)
            print(json.dumps({"reports": result["report_count"],
                              "identity_counts": result["identity_counts"],
                              "output": str(args.output.resolve())}))
        elif args.command == "archive-senate-report-entrypoints":
            discovery = json.loads(args.discovery.read_text(encoding="utf-8"))
            config = source_config_from_environment(
                enabled_name=args.enabled_env, terms_name=args.terms_env)
            result = archive_catalog_report_entrypoints(
                args.archive, discovery, limit=args.limit, config=config)
            _write_atomic(args.output, result)
            print(json.dumps({"attempted": result["attempted_count"],
                              "archived": result["archived_count"],
                              "failures": result["failure_count"],
                              "archived_total": result["archived_total"],
                              "pending": result["pending_count"],
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
    except (ValueError, RuntimeError, HouseIndexError, DisclosureCandidateError,
            SenateEfdError, SenateRosterError, SenateIdentityError, KeyError, OSError,
            json.JSONDecodeError) as exc:
        parser.exit(2, f"Snapshot operation failed: {exc}\n")


if __name__ == "__main__":
    main()
