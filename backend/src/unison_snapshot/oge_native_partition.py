"""Source-selected direct OGE native visibility variants and original replay."""
from __future__ import annotations

from pathlib import Path

from .oge import OgeCatalogError

DEFAULT_VERSION = 'oge-278t-native-visible-default/v1'
GENERIC_VERSION = 'oge-278t-native-visible-generic/v1'
VERSIONS = {DEFAULT_VERSION, GENERIC_VERSION}


def source_plan(path, *, source_sha256, generic=False):
    from .native_text_partition import plan_native_text_partition
    plan = plan_native_text_partition(path, expected_sha256=source_sha256)
    return plan, (GENERIC_VERSION if generic else DEFAULT_VERSION) if plan.get('activated') is not False else None


def parse_partitioned_oge(metadata, path, *, source_sha256, generic=False):
    from .native_text_partition import observe_native_text_partition
    from .native_partition_rows import interpret_partition_rows
    from .oge_reports import (EXTRACTION_SCHEMA, GENERIC_ROW_INTERPRETATION_MODE,
                              _SIGNATURE_DATE, _iso_date, read_278t_cover_dates)
    proof = observe_native_text_partition(path, expected_sha256=source_sha256)
    if proof.get('activated') is False:
        raise OgeCatalogError('Native visible OGE variant needs an activated source partition')
    pages = proof.get('pages', [])
    text = '\n'.join(page.get('text') or '' for page in pages)
    cover = next((page.get('text') or '' for page in pages if page['page_number'] == 1), '')
    date_evidence = None
    if generic:
        date_evidence = read_278t_cover_dates(cover, source_sha256=source_sha256)
        filed_at, reasons = date_evidence['filed_at'], list(date_evidence['document_reasons'])
    else:
        signatures = _SIGNATURE_DATE.findall(text)
        filed_at = _iso_date(signatures[0]) if len(signatures) == 1 else None
        reasons = [] if filed_at else ['filer_signature_date_not_unique']
    good, bad, structural = interpret_partition_rows(proof,
        source_sha256=source_sha256, generic=generic)
    if not proof.get('inventory'):
        reasons.append('native_visible_transaction_inventory_unknown')
    result = {'schema_version': EXTRACTION_SCHEMA,
        'parser_version': GENERIC_VERSION if generic else DEFAULT_VERSION,
        'source_id': 'oge', 'document_id': metadata['document_id'],
        'source_url': metadata['document_url'], 'source_sha256': source_sha256,
        'catalog_filer_name': metadata['filer_name'], 'agency': metadata['agency'],
        'position_title': metadata['position_title'],
        'catalog_added_date': metadata['catalog_added_date'], 'filed_at': filed_at,
        'document_reasons': sorted(set(reasons)), 'evidence_complete': not reasons,
        'transactions': good, 'quarantined': bad,
        'source_rows': [{'page_number': page, 'cells': cells,
            **({'native_visibility_reasons': proof['row_reasons'][str(index)]}
               if proof.get('row_reasons', {}).get(str(index)) else {})}
            for index, (page, cells) in enumerate(proof['inventory'])],
        'native_text_partition': proof,
        'document_page_count': len(pages)}
    if generic:
        result.update(cover_date_evidence=date_evidence,
            row_interpretation_mode=GENERIC_ROW_INTERPRETATION_MODE, structural_rows=structural)
    return result


def replay_partitioned_oge(extraction, evidence_root):
    """A saved marker cannot substitute for the canonical archived original."""
    from .oge_reports import _read_archived_source
    if evidence_root is None:
        raise OgeCatalogError('Native visible OGE qualification requires its evidence root')
    document, sha = extraction.get('document_id'), extraction.get('source_sha256')
    import re
    if not isinstance(document, str) or not re.fullmatch('[0-9a-f]{32}', document) or not re.fullmatch('[0-9a-f]{64}', str(sha)):
        raise OgeCatalogError('Native visible OGE source identity is invalid')
    root = Path(evidence_root).resolve()
    metadata, path, actual = _read_archived_source(root, root / f'oge/reports/{document}/{sha}.json')
    if actual != sha:
        raise OgeCatalogError('Native visible OGE original differs')
    replayed = parse_partitioned_oge(metadata, path, source_sha256=sha,
                                   generic=extraction.get('parser_version') == GENERIC_VERSION)
    if replayed != extraction:
        raise OgeCatalogError('Native visible OGE extraction differs from original source replay')
    return replayed


def validate_partitioned_interpretation(extraction):
    """Check saved row consistency; source authenticity is checked by replay."""
    from .native_partition_rows import interpret_partition_rows
    proof = extraction.get('native_text_partition')
    if not isinstance(proof, dict) or extraction.get('parser_version') not in VERSIONS:
        raise OgeCatalogError('Native visible OGE row contract is invalid')
    good, bad, structural = interpret_partition_rows(proof,
        source_sha256=extraction['source_sha256'],
        generic=extraction['parser_version'] == GENERIC_VERSION)
    if good != extraction.get('transactions') or bad != extraction.get('quarantined') or (
            extraction['parser_version'] == GENERIC_VERSION and structural != extraction.get('structural_rows')):
        raise OgeCatalogError('Native visible OGE rows differ from the saved projection')
