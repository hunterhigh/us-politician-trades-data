"""OGE catalog, archived-report and candidate commands."""
import sys
from copy import deepcopy
import json
from pathlib import Path
import re
from ..builder import build
from ..codec import digest, encode
from ..oge import MAX_CATALOG_PAGE_SIZE as OGE_MAX_CATALOG_PAGE_SIZE, OgeCatalogError, collection_gate_status as oge_collection_gate_status, discover_catalog as discover_oge_catalog, source_config_from_environment as oge_source_config_from_environment
from ..oge_reports import PARSER_VERSION as OGE_REPORT_PARSER_VERSION, GENERIC_PARSER_VERSION as OGE_GENERIC_PARSER_VERSION, archive_direct_batch as archive_oge_direct_batch, load_reusable_extraction as load_reusable_oge_extraction, parse_archived_pdf as parse_oge_archived_pdf
from ..oge_candidate import build_oge_candidate
from ..reviewed_oge_extraction import approved_extraction as approved_oge_extraction, parser_versions as reviewed_oge_versions, version_priority as oge_version_priority
from ._io import _write_atomic
from ._runtime import invoke


def configure_oge_gate(parser):
    oge_gate = parser
    oge_gate.add_argument("--output", type=Path, required=True)
    oge_gate.add_argument("--enabled-env", default="OGE_COLLECTION_ENABLED")
    oge_gate.add_argument("--terms-env", default="OGE_TERMS_ACKNOWLEDGED")


def execute_oge_gate(args):
    config = oge_source_config_from_environment(
        enabled_name=args.enabled_env, terms_name=args.terms_env)
    result = oge_collection_gate_status(config)
    _write_atomic(args.output, result)
    print(json.dumps({"status": result["status"],
                      "collection_enabled": result["collection_enabled"],
                      "terms_acknowledged": result["terms_acknowledged"],
                      "output": str(args.output.resolve())}))


def configure_discover_oge(parser):
    oge_discovery = parser
    oge_discovery.add_argument("--archive", type=Path, required=True)
    oge_discovery.add_argument("--output", type=Path, required=True)
    oge_discovery.add_argument("--failure-output", type=Path)
    oge_discovery.add_argument("--page-size", type=int, default=OGE_MAX_CATALOG_PAGE_SIZE)
    oge_discovery.add_argument("--enabled-env", default="OGE_COLLECTION_ENABLED")
    oge_discovery.add_argument("--terms-env", default="OGE_TERMS_ACKNOWLEDGED")


def execute_discover_oge(args):
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


def configure_archive_oge_direct_pdfs(parser):
    oge_reports = parser
    oge_reports.add_argument("--catalog", type=Path, required=True)
    oge_reports.add_argument("--archive", type=Path, required=True)
    oge_reports.add_argument("--output", type=Path, required=True)
    oge_reports.add_argument("--limit", type=int, default=2)
    oge_reports.add_argument("--enabled-env", default="OGE_COLLECTION_ENABLED")
    oge_reports.add_argument("--terms-env", default="OGE_TERMS_ACKNOWLEDGED")


def execute_archive_oge_direct_pdfs(args):
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


def configure_extract_oge_direct_pdfs(parser):
    oge_extract = parser
    oge_extract.add_argument("--batch", type=Path, required=True)
    oge_extract.add_argument("--archive", type=Path, required=True)
    oge_extract.add_argument("--reuse-root", type=Path)
    oge_extract.add_argument("--generic", action="store_true")
    oge_extract.add_argument("--output-dir", type=Path, required=True)
    oge_extract.add_argument("--output", type=Path, required=True)


def execute_extract_oge_direct_pdfs(args):
    batch = json.loads(args.batch.read_text(encoding="utf-8"))
    reports = batch.get("reports") if isinstance(batch, dict) else None
    if not isinstance(reports, list):
        raise OgeCatalogError("OGE archive batch has no report metadata")
    report_by_source = {(row['document_id'], row['sha256']): row for row in reports}
    for path in ([] if args.generic else sorted((args.archive / 'oge/reports').glob('*/*.json'))):
        metadata = json.loads(path.read_text(encoding='utf-8'))
        report_by_source.setdefault((metadata['document_id'], metadata['sha256']), metadata)
    reports = [report_by_source[key] for key in sorted(report_by_source)]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    extracted = []
    failures = []
    reused_count = parsed_count = 0
    for metadata in reports:
        if (not isinstance(metadata, dict) or
                not re.fullmatch('[0-9a-f]{32}',str(metadata.get('document_id',''))) or
                not re.fullmatch('[0-9a-f]{64}',str(metadata.get('sha256','')))):
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
            _write_atomic(args.output_dir / (metadata['document_id'] + '.failure.json'), {
                'schema_version': 'oge-278t-extraction-failure/v1', 'source_id': 'oge',
                'document_id': metadata['document_id'], 'source_sha256': metadata.get('sha256'),
                'reason': 'extraction_failed', 'error_type': type(exc).__name__})
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


def configure_build_oge_candidate(parser):
    oge_candidate = parser
    oge_candidate.add_argument('--prior-publication', action='store_true')
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


def execute_build_oge_candidate(args):
    from ..oge_source_selection import parse_document_sources, verify_selected_native_cache
    document_sources = parse_document_sources(args.document_source)
    if document_sources and args.evidence_root is None:
        raise OgeCatalogError('Explicit OGE document sources require --evidence-root')
    pinned_source_inputs = [args.catalog, args.base]
    if document_sources:
        from ..oge_saved_observations import guard_saved_oge_outputs
        guard_saved_oge_outputs([args.output, args.audit_output],
            inputs=pinned_source_inputs,
            protected_roots=[args.evidence_root, args.extractions_dir,
                             args.legacy_extractions_dir, args.catalog_history_dir,
                             args.manual_cover_dates_dir])
    from ..oge_native_partition import DEFAULT_VERSIONS as VISIBLE_DEFAULTS, GENERIC_VERSIONS as VISIBLE_GENERICS
    from ..oge_reports import selected_parser_version, _read_archived_source
    scoped_generic_ids = args.generic_document_id
    if (len(scoped_generic_ids) > 32 or len(set(scoped_generic_ids)) != len(scoped_generic_ids)
            or any(re.fullmatch(r'[0-9a-f]{32}', value) is None for value in scoped_generic_ids)):
        raise OgeCatalogError('Scoped generic document IDs must be unique canonical IDs, at most 32')
    scoped_generic_ids = set(scoped_generic_ids)
    if scoped_generic_ids and args.evidence_root is None:
        raise OgeCatalogError('Scoped generic selection requires --evidence-root')
    if args.saved_observations is not None:
        from ..oge_saved_observations import guard_saved_oge_outputs
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
    supplemented = set()
    for path in (args.saved_observations, args.saved_exhibit_observations):
        if path is not None:
            supplied = json.loads(path.read_bytes())
            if not isinstance(supplied, dict):
                raise OgeCatalogError('OGE saved source inputs must be document mappings')
            supplemented.update(supplied)
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
                           *reviewed_oge_versions()} and not (
                use_generic and version in {OGE_GENERIC_PARSER_VERSION, *VISIBLE_GENERICS}):
            continue
        if args.evidence_root is not None:
            metadata_path = args.evidence_root / f"oge/reports/{document_id}/{value.get('source_sha256')}.json"
            metadata, source_path, source_sha = _read_archived_source(args.evidence_root, metadata_path)
            expected = selected_parser_version(metadata, source_path, source_sha, generic=use_generic)
            required_current_documents.add(document_id)
        if args.evidence_root is not None:
            from ..oge_reports import parser_target
            if not parser_target(metadata, expected, observed=document_id in supplemented).accepts(value):
                continue
        if document_id in document_sources:
            pinned_source_inputs.extend(verify_selected_native_cache(
                path, args.extractions_dir, value, args.evidence_root))
        if version in reviewed_oge_versions() and not approved_oge_extraction(value):
            raise OgeCatalogError("Reviewed OGE extraction differs from its approved source output")
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
            priority = oge_version_priority(document_id, OGE_REPORT_PARSER_VERSION)
            if (not priority or version not in priority or
                    previous.get("parser_version") not in priority or
                    version == previous.get("parser_version")):
                raise OgeCatalogError("OGE extraction document ID is duplicated")
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
        from ..oge_manual_dates import apply_manual_cover_dates
        extractions = apply_manual_cover_dates(
            extractions, args.manual_cover_dates_dir)
    catalog_history = []
    if args.catalog_history_dir:
        current_bytes = encode({key:value for key,value in catalog.items() if key!='observed_at'})
        for path in sorted(args.catalog_history_dir.glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            if encode(value) != current_bytes:
                catalog_history.append(value)
    base = json.loads(args.base.read_text(encoding="utf-8"))
    saved_observations = {}
    if args.saved_observations is not None:
        from ..oge_saved_observations import load_saved_oge_observations
        saved_observations = load_saved_oge_observations(args.saved_observations,
            evidence_root=args.evidence_root, extractions=extractions)
        source_inputs = []
        for document, request in saved_observations.items():
            source_inputs.extend([Path(request["archived_pdf_path"]),
                args.evidence_root / f"oge/reports/{document}/{request['observations'][0]['source_sha256']}.json"])
        guard_saved_oge_outputs([args.output, args.audit_output], inputs=source_inputs)
    publication_cache = None
    if args.prior_publication:
        if args.evidence_root is None:
            raise OgeCatalogError('Published OGE comparison requires archived-source verification')
        from ..published_source import published_main
        publication_cache = None
        def load_publication():
            nonlocal publication_cache
            if publication_cache is None:
                publication_cache = published_main(Path.cwd())
            return publication_cache
        # Owner/classification conservation needs the actual formal
        # predecessor even for reports that do have current inputs.
        # The source builder emits only its own qualified scope.
        if not base.get('transactions'):
            board = load_publication()['board']
            base['transactions'] = [deepcopy(row) for row in board['transactions']
                                    if row.get('source_id') == 'oge']
            people = {row['person_id'] for row in base['transactions']}
            base['people'] = [deepcopy(row) for row in board['people'] if row['id'] in people]
    result, audit = build_oge_candidate(
        catalog, extractions, base, data_cutoff_at=args.data_cutoff_at,
        evidence_root=args.evidence_root,
        source_extractions_by_document=source_extractions,
        catalog_history=catalog_history,
        **({"saved_observations_by_document": saved_observations} if args.saved_observations is not None else {}),
        **({"legacy_extractions_by_document": legacy_extractions} if args.generic or scoped_generic_ids else {}),
        **({"saved_exhibit_observations_by_document": json.loads(args.saved_exhibit_observations.read_text(encoding="utf-8"))}
           if args.saved_exhibit_observations else {}))
    if args.prior_publication and publication_cache is not None:
        audit['prior_publication'] = {key: publication_cache[key]
            for key in ('main_commit', 'board_sha256', 'snapshot_id')}
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


COMMANDS = {
    'oge-gate': (configure_oge_gate, execute_oge_gate),
    'discover-oge': (configure_discover_oge, execute_discover_oge),
    'archive-oge-direct-pdfs': (configure_archive_oge_direct_pdfs, execute_archive_oge_direct_pdfs),
    'extract-oge-direct-pdfs': (configure_extract_oge_direct_pdfs, execute_extract_oge_direct_pdfs),
    'build-oge-candidate': (configure_build_oge_candidate, execute_build_oge_candidate),
}


def main():
    command = sys.argv[0].rsplit(" ", 1)[-1]
    invoke(command, *COMMANDS[command])
