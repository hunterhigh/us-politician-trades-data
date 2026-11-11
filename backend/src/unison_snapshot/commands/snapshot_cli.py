"""Existing disclosure assembly and snapshot preparation commands."""
import sys
from datetime import datetime, timezone
import json
from pathlib import Path
from ..builder import build, normalize
from ..codec import digest, encode
from ..materialize import materialize
from ..disclosure_candidate import DisclosureCandidateError, SourceCutoffsStaleError, build_disclosure_candidate, project_source_candidate
from ..legacy import load, PROCESSOR_V2_SHA256
from ..release_readiness import validate_publication
from ._io import _write_atomic
from ._runtime import invoke


def configure_prepare_publication(parser):
    prepare = parser
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


def execute_prepare_publication(args):
    input_bytes = args.input.read_bytes()
    payload = json.loads(input_bytes)
    if args.consumer_validation_file:
        receipt = json.loads(args.consumer_validation_file.read_bytes())
        if receipt != {"input_sha256": digest(input_bytes),
                       "processor_sha256": PROCESSOR_V2_SHA256}:
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
        from ..disclosure_status import attach_disclosure_status
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


def configure_verify_publication_readiness(parser):
    readiness = parser
    readiness.add_argument("--review-root", type=Path, required=True)
    readiness.add_argument("--input", type=Path, required=True)
    readiness.add_argument("--cutoff-audit", type=Path,
                           help="Audit produced with this exact disclosure candidate")
    readiness.add_argument("--previous-board", type=Path,
                           help="Fixed published board for unchanged deferred OGE facts")
    readiness.add_argument("--output", type=Path)


def execute_verify_publication_readiness(args):
    result = validate_publication(args.review_root, args.input,
                                  cutoff_audit_path=args.cutoff_audit,
                                  previous_board_path=args.previous_board)
    if args.output:
        _write_atomic(args.output, result)
    print(json.dumps({**result, "status": "ready",
                      "output": str(args.output.resolve()) if args.output else None}))


def configure_build_disclosure_candidate(parser):
    disclosure_candidate = parser
    disclosure_candidate.add_argument("--base", type=Path, required=True)
    disclosure_candidate.add_argument(
        "--source", action="append", required=True, metavar="SOURCE_ID=PATH",
        help="Repeat once per source candidate, for example house_clerk=house-current.json")
    disclosure_candidate.add_argument("--output", type=Path, required=True)
    disclosure_candidate.add_argument("--audit-output", type=Path)
    disclosure_candidate.add_argument("--html-output", type=Path)
    disclosure_candidate.add_argument("--assembly-status-output", type=Path)
    source_state = disclosure_candidate.add_mutually_exclusive_group()
    source_state.add_argument('--source-state-root', type=Path)
    source_state.add_argument('--source-gate-file', type=Path)
    disclosure_candidate.add_argument("--defer-stale-sources", action="store_true",
                                      help="Record stale-source assembly as deferred; never replace the candidate")
    disclosure_candidate.add_argument(
        "--harmonize-cutoffs", action="store_true",
        help="Compatibility flag; sources retain their own cutoffs and all qualified facts")


def execute_build_disclosure_candidate(args):
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
    source_statuses = None
    if args.source_state_root:
        source_statuses = {source: json.loads((args.source_state_root / 'status' /
            (source + '.json')).read_bytes()) for source in sources}
    elif args.source_gate_file:
        gate = json.loads(args.source_gate_file.read_bytes())
        if gate.get('schema_version') != 'publication-source-gate/v1':
            raise DisclosureCandidateError('Unsupported source gate metadata')
        source_statuses = {}
        for source, candidate in sources.items():
            waterline = gate.get('waterlines', {}).get(source, {})
            if waterline.get('qualified_at') != candidate['meta']['data_cutoff_at']:
                raise DisclosureCandidateError('Source gate metadata differs from its candidate')
            source_statuses[source] = {
                'status': 'unavailable' if waterline.get('unavailable') else 'ok',
                'last_successful_collection_at': waterline.get('collection_at')}
    try:
        result = build_disclosure_candidate(
            base, sources, harmonize_cutoffs=args.harmonize_cutoffs,
            harmonization_audit=cutoff_audit, source_statuses=source_statuses)
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
        renderer = load("render_dashboard", version="v2")
        html = renderer.render_html(renderer.load_dashboard_data(args.output))
        args.html_output.parent.mkdir(parents=True, exist_ok=True)
        args.html_output.write_text(html, encoding="utf-8")
    if args.assembly_status_output:
        _write_atomic(args.assembly_status_output, {
            'schema_version': 'disclosure-assembly-status/v1', 'status': 'ready',
            'attempted_at': datetime.now(timezone.utc).isoformat(),
            'data_cutoff_at': generated_at,
            'source_cutoffs': {key: value['meta']['data_cutoff_at']
                               for key, value in sorted(sources.items())},
            'candidate_sha256': digest(encode(result)),
        })
    print(json.dumps({"sources": sorted(sources), "people": len(result["people"]),
                      "transactions": len(result["transactions"]),
                      "reported_holdings": len(result["reported_holdings"]),
                      "output": str(args.output.resolve()),
                      "html": str(args.html_output.resolve()) if args.html_output else None}))


COMMANDS = {
    'prepare-publication': (configure_prepare_publication, execute_prepare_publication),
    'verify-publication-readiness': (configure_verify_publication_readiness, execute_verify_publication_readiness),
    'build-disclosure-candidate': (configure_build_disclosure_candidate, execute_build_disclosure_candidate),
}


def main():
    command = sys.argv[0].rsplit(" ", 1)[-1]
    invoke(command, *COMMANDS[command])
