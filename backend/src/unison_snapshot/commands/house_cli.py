"""House discovery, parsing, qualification and candidate commands."""
import sys
import json
from pathlib import Path
from ..builder import build
from ..codec import encode
from ..house import HouseIndexClient, discover
from ..house_ptr import parse_archived_pdf, qualify_automatic
from ..house_paper_pipeline import bridge_paper_batch
from ..house_sync import plan_checkpoint
from ..house_members import HouseMemberClient, discover_members, suggest_identity
from ..house_candidate import load_house_candidate
from ..house_holdings import HouseFinancialDocumentClient, archive_indexed_financial_report, parse_archived_financial_report, qualify_financial_report
from ..legacy import load
from ._io import _write_atomic
from ._runtime import invoke


def configure_discover_house_index(parser):
    house = parser
    house.add_argument("--year", type=int, required=True)
    house.add_argument("--archive", type=Path, required=True)
    house.add_argument("--output", type=Path, required=True)
    house.add_argument("--timeout", type=float, default=30.0)


def execute_discover_house_index(args):
    result = discover(args.year, args.archive, client=HouseIndexClient(args.timeout))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encode(result))
    print(json.dumps({"year": args.year, "records": len(result["filings"]),
                      "sha256": result["metadata"]["sha256"],
                      "output": str(args.output.resolve())}))


def configure_archive_house_holding_report(parser):
    holding_archive = parser
    holding_archive.add_argument("--year", type=int, required=True)
    holding_archive.add_argument("--index-sha", required=True)
    holding_archive.add_argument("--document-id", required=True)
    holding_archive.add_argument("--archive", type=Path, required=True)
    holding_archive.add_argument("--output", type=Path, required=True)
    holding_archive.add_argument("--timeout", type=float, default=30.0)


def execute_archive_house_holding_report(args):
    result = archive_indexed_financial_report(
        args.archive, args.year, args.index_sha, args.document_id,
        client=HouseFinancialDocumentClient(args.timeout))
    _write_atomic(args.output, result)
    print(json.dumps({"year": args.year, "document_id": args.document_id,
                      "sha256": result["sha256"], "bytes": result["byte_length"],
                      "output": str(args.output.resolve())}))


def configure_parse_house_holding_report(parser):
    holding_parse = parser
    holding_parse.add_argument("--archive", type=Path, required=True)
    holding_parse.add_argument("--metadata", type=Path, required=True)
    holding_parse.add_argument("--output", type=Path, required=True)


def execute_parse_house_holding_report(args):
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    result = parse_archived_financial_report(args.archive, metadata)
    _write_atomic(args.output, result)
    print(json.dumps({"document_id": result["source"]["document_id"],
                      "rows": len(result["rows"]),
                      "report_period_end": result["report_period_end"],
                      "output": str(args.output.resolve())}))


def configure_qualify_house_holding_report(parser):
    holding_qualify = parser
    holding_qualify.add_argument("--extraction", type=Path, required=True)
    holding_qualify.add_argument("--identity", type=Path, required=True)
    holding_qualify.add_argument("--output", type=Path, required=True)


def execute_qualify_house_holding_report(args):
    extraction = json.loads(args.extraction.read_text(encoding="utf-8"))
    identity = json.loads(args.identity.read_text(encoding="utf-8"))
    result = qualify_financial_report(extraction, identity)
    _write_atomic(args.output, result)
    print(json.dumps({"document_id": result["source"]["document_id"],
                      "eligible": result["production_eligible"],
                      "holdings": len(result["holdings"]),
                      "quarantined": len(result["quarantined"]),
                      "output": str(args.output.resolve())}))


def configure_parse_house_ptr(parser):
    parse_ptr = parser
    parse_ptr.add_argument("--archive", type=Path, required=True)
    parse_ptr.add_argument("--metadata", type=Path, required=True)
    parse_ptr.add_argument("--output", type=Path, required=True)


def execute_parse_house_ptr(args):
    result = parse_archived_pdf(args.archive, args.metadata)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encode(result))
    print(json.dumps({"document_id": result["source"]["document_id"],
                      "transactions": len(result["transactions"]),
                      "status": result["review"]["status"],
                      "production_eligible": result["review"]["production_eligible"],
                      "output": str(args.output.resolve())}))


def configure_qualify_house_ptr(parser):
    qualify_ptr = parser
    qualify_ptr.add_argument("--extraction", type=Path, required=True)
    qualify_ptr.add_argument("--identity", type=Path, required=True)
    qualify_ptr.add_argument("--evidence-root", type=Path)
    qualify_ptr.add_argument("--output", type=Path, required=True)


def execute_qualify_house_ptr(args):
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


def configure_correct_reviewed_house_paper(parser):
    reviewed_paper = parser
    reviewed_paper.add_argument("--evidence-root", type=Path, required=True)
    reviewed_paper.add_argument("--review-root", type=Path, required=True)
    reviewed_paper.add_argument("--correction", type=Path, required=True)


def execute_correct_reviewed_house_paper(args):
    from ..reviewed_house_paper import qualify_reviewed_paper_paths
    result = qualify_reviewed_paper_paths(args.evidence_root, args.review_root, args.correction)
    print(json.dumps(result, sort_keys=True))


def configure_bridge_house_paper_batch(parser):
    bridge_paper = parser
    bridge_paper.add_argument("--evidence-root", type=Path, required=True)
    bridge_paper.add_argument("--review-root", type=Path, required=True)
    bridge_paper.add_argument("--parse-batch", type=Path, required=True)
    bridge_paper.add_argument("--tesseract", required=True)
    bridge_paper.add_argument("--document-id")
    bridge_paper.add_argument("--materialize-saved", action="store_true",
                              help="Interpret saved paper evidence without starting OCR")


def execute_bridge_house_paper_batch(args):
    result = bridge_paper_batch(
        evidence_root=args.evidence_root, review_root=args.review_root,
        parse_batch=args.parse_batch, tesseract=args.tesseract,
        document_id=args.document_id,
        **({"materialize_saved": True} if args.materialize_saved else {}))
    print(json.dumps(result, sort_keys=True))


def configure_build_house_candidate(parser):
    candidate = parser
    candidate.add_argument("--review-root", type=Path, required=True)
    candidate.add_argument("--evidence-root", type=Path)
    candidate.add_argument("--state-status", type=Path, required=True)
    candidate.add_argument("--base", type=Path, required=True)
    candidate.add_argument("--output", type=Path, required=True)
    candidate.add_argument("--html-output", type=Path)
    candidate.add_argument("--annual-transaction-audit-out", type=Path,
                           help="Save source-bound annual transaction relationship decisions")


def execute_build_house_candidate(args):
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
        renderer = load("render_dashboard", version="v2")
        html = renderer.render_html(renderer.load_dashboard_data(args.output))
        args.html_output.parent.mkdir(parents=True, exist_ok=True)
        args.html_output.write_text(html, encoding="utf-8")
    print(json.dumps({"people": len(result["people"]),
                      "transactions": len(result["transactions"]),
                      "reported_holdings": len(result["reported_holdings"]),
                      "output": str(args.output.resolve()),
                      "html": str(args.html_output.resolve()) if args.html_output else None}))


def configure_discover_house_members(parser):
    members = parser
    members.add_argument("--archive", type=Path, required=True)
    members.add_argument("--output", type=Path, required=True)
    members.add_argument("--timeout", type=float, default=30.0)


def execute_discover_house_members(args):
    result = discover_members(args.archive, client=HouseMemberClient(args.timeout))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encode(result))
    print(json.dumps({"members": len(result["members"]),
                      "published_date": result["metadata"]["published_date"],
                      "sha256": result["metadata"]["sha256"],
                      "output": str(args.output.resolve())}))


def configure_suggest_house_identity(parser):
    identity = parser
    identity.add_argument("--extraction", type=Path, required=True)
    identity.add_argument("--members", type=Path, required=True)
    identity.add_argument("--output", type=Path, required=True)


def execute_suggest_house_identity(args):
    extraction = json.loads(args.extraction.read_text(encoding="utf-8"))
    roster = json.loads(args.members.read_text(encoding="utf-8"))
    result = suggest_identity(extraction, roster)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encode(result))
    print(json.dumps({"document_id": result["document_id"], "status": result["status"],
                      "person_id": result.get("person_id"),
                      "output": str(args.output.resolve())}))


def configure_plan_house_ptr_sync(parser):
    sync = parser
    sync.add_argument("--discovery", type=Path, required=True)
    sync.add_argument("--archive", type=Path, required=True)
    sync.add_argument("--checkpoint", type=Path)
    sync.add_argument("--planned-at")
    sync.add_argument("--output", type=Path, required=True)


def execute_plan_house_ptr_sync(args):
    discovery = json.loads(args.discovery.read_text(encoding="utf-8"))
    previous = json.loads(args.checkpoint.read_text(encoding="utf-8")) if args.checkpoint else None
    result = plan_checkpoint(discovery, args.archive, previous, planned_at=args.planned_at)
    _write_atomic(args.output, result)
    print(json.dumps({"year": result["filing_year"], "index_sha256": result["index_sha256"],
                      "counts": result["counts"], "queue": len(result["queue"]),
                      "anomalies": len(result["anomalies"]),
                      "output": str(args.output.resolve())}))


COMMANDS = {
    'discover-house-index': (configure_discover_house_index, execute_discover_house_index),
    'archive-house-holding-report': (configure_archive_house_holding_report, execute_archive_house_holding_report),
    'parse-house-holding-report': (configure_parse_house_holding_report, execute_parse_house_holding_report),
    'qualify-house-holding-report': (configure_qualify_house_holding_report, execute_qualify_house_holding_report),
    'parse-house-ptr': (configure_parse_house_ptr, execute_parse_house_ptr),
    'qualify-house-ptr': (configure_qualify_house_ptr, execute_qualify_house_ptr),
    'correct-reviewed-house-paper': (configure_correct_reviewed_house_paper, execute_correct_reviewed_house_paper),
    'bridge-house-paper-batch': (configure_bridge_house_paper_batch, execute_bridge_house_paper_batch),
    'build-house-candidate': (configure_build_house_candidate, execute_build_house_candidate),
    'discover-house-members': (configure_discover_house_members, execute_discover_house_members),
    'suggest-house-identity': (configure_suggest_house_identity, execute_suggest_house_identity),
    'plan-house-ptr-sync': (configure_plan_house_ptr_sync, execute_plan_house_ptr_sync),
}


def main():
    command = sys.argv[0].rsplit(" ", 1)[-1]
    invoke(command, *COMMANDS[command])
