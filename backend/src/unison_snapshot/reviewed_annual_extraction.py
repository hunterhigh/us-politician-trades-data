"""Reviewed annual report corrections over immutable parser checkpoints."""
from .content_integrity import content_boundary
from copy import deepcopy
from contextvars import ContextVar
from functools import lru_cache, wraps
from pathlib import Path
import re

from .codec import encode, digest
from .corrections import apply_correction, load_correction, validate_correction
from .oge import OgeCatalogError

PROFILE = 'reviewed-annual-extraction/v1'
ROOT = Path(__file__).resolve().parents[2] / 'data/corrections/whitehouse_annual'
RECOVERY_METHOD = 'whitehouse-scanned-annual-source-bound/v2'
ROW_ARRAYS = ('holdings', 'quarantined')
REPORT_FIELDS = {'filing_date', 'source_bound_original_position_title_raw',
    'position_line_raw', 'position_title_raw', 'source_bound_resolved_document_reasons',
    'document_reasons', 'signature_evidence', 'source_bound_recovered_holding_count',
    'source_bound_correction_method'}
_RULES = ContextVar('annual_reviewed_rules', default=None)


def frozen_annual_rules(function):
    """Use one correction-data snapshot for an entire qualification operation."""
    @wraps(function)
    def run(*args, **kwargs):
        if _RULES.get() is not None:
            return function(*args, **kwargs)
        token = _RULES.set(profiles())
        try:
            return function(*args, **kwargs)
        finally:
            _RULES.reset(token)
    return run


@lru_cache(maxsize=16)
def _read(path, modified, size):
    value = load_correction(path)
    if value.get('profile') != PROFILE:
        raise OgeCatalogError('Unsupported reviewed annual correction profile')
    for version, variant in value.get('variants', {}).items():
        if variant.get('parser_version') != version:
            raise OgeCatalogError('Reviewed annual parser variant binding differs')
        validate_correction({**value, **variant})
    return value


def profiles():
    active = _RULES.get()
    if active is not None:
        return active
    result = []
    for path in sorted(ROOT.glob('*.json')):
        stat = path.stat()
        value = _read(str(path), stat.st_mtime_ns, stat.st_size)
        if (value['document_id'] != 'wh-url:' + path.stem or
                value['document_id'] != 'wh-url:' + digest(value['source_url'].encode())[:24]):
            raise OgeCatalogError('Reviewed annual correction document binding differs')
        result.append(value)
    return result


@content_boundary('correction_assertion')
def profile_for(extraction):
    return next((c for c in profiles() if extraction.get('source_url') == c['source_url'] and
        extraction.get('source_sha256') == c['source_sha256'] and
        all(extraction.get(k) == v for k, v in c['source_context'].items())), None)


@content_boundary('correction_assertion')
def profile_for_source(source_sha256):
    return next((c for c in profiles() if c['source_sha256'] == source_sha256), None)


@content_boundary('correction_assertion')
def parser_profile_for_source(source_url, source_sha256):
    correction = profile_for_source(source_sha256)
    return correction.get('parser_profile') if correction and correction['source_url'] == source_url else None


def required_source_urls():
    return {c['source_url'] for c in profiles() if c.get('publication_requirement') is True}


def _variant(correction, version):
    if version == correction['parser_version']:
        return correction
    variant = correction.get('variants', {}).get(version)
    return {**correction, **variant} if variant else None


def fingerprint(extraction):
    value = deepcopy(extraction)
    # Operational resume counters do not describe source facts or OCR geometry.
    if isinstance(value.get('ocr_checkpoint'), dict):
        value['ocr_checkpoint'].pop('created_shard_count', None)
    return digest(encode(value))


def apply_reviewed_annual_corrections(extraction):
    correction = profile_for(extraction)
    if correction is None:
        return extraction
    selected = _variant(correction, extraction.get('parser_version'))
    if selected is None or fingerprint(extraction) != selected['assertions']['input_sha256']:
        raise OgeCatalogError('Reviewed annual original parser precondition changed')
    guards = selected['assertions']
    if {key: len(extraction[key]) for key in ROW_ARRAYS} != guards['input_counts']:
        raise OgeCatalogError('Reviewed annual original row counts changed')
    rows = {}
    for key in guards['row_inventory']:
        match = re.fullmatch(r'(holdings|quarantined)/(0|[1-9][0-9]*)', key)
        if match is None or int(match[2]) >= len(extraction[match[1]]):
            raise OgeCatalogError('Reviewed annual correction row does not exist')
        rows[key] = {'disposition': match[1], 'record': extraction[match[1]][int(match[2])]}
    for action in selected['actions']:
        if (action['op'] == 'set_report' and action.get('field') in REPORT_FIELDS or
                action['op'] == 'set_field' and action.get('field') in {'record', 'disposition'}):
            continue
        raise OgeCatalogError('Reviewed annual correction target is unsupported')
    model = {key: correction[key] for key in ('source', 'document_id', 'source_sha256')}
    model.update(rows=rows, report={k: v for k, v in extraction.items()
        if k not in {*ROW_ARRAYS, 'transactions', 'excluded'}})
    try:
        corrected = apply_correction(model, selected)
    except (KeyError, ValueError, TypeError) as error:
        raise OgeCatalogError('Reviewed annual correction precondition failed: ' + str(error)) from error
    result = deepcopy(extraction)
    result.update(corrected['report'])
    output = {key: [] for key in ROW_ARRAYS}
    for kind in ROW_ARRAYS:
        for index, original in enumerate(extraction[kind]):
            row = corrected['rows'].get(kind + '/' + str(index), {'disposition': kind, 'record': original})
            if row['disposition'] not in output:
                raise OgeCatalogError('Reviewed annual correction disposition is invalid')
            output[row['disposition']].append(deepcopy(row['record']))
    result.update(output)
    if ({key: len(result[key]) for key in ROW_ARRAYS} != guards['output_counts'] or
            fingerprint(result) != guards['output_sha256']):
        raise OgeCatalogError('Reviewed annual correction differs from its approved output')
    return result


@frozen_annual_rules
def validate_reviewed_checkpoint(extraction):
    """Reject changed registered sources before accepting a cached or fresh parse.

    The checkpoint remains the original parser output. Qualification applies the
    same asserted correction later; collection must validate it before skipping
    an existing artifact or writing a new one.
    """
    registered = next((c for c in profiles()
        if extraction.get('source_url') == c['source_url']), None)
    if registered is None:
        return extraction
    if (extraction.get('source_sha256') != registered['source_sha256'] or
            profile_for(extraction) is None):
        raise OgeCatalogError('Reviewed annual source correction binding changed')
    apply_reviewed_annual_corrections(extraction)
    return extraction


def _approved_variant(extraction):
    correction = profile_for(extraction)
    return _variant(correction, extraction.get('parser_version')) if correction else None


def source_bound_holding_section_page_valid(row, extraction):
    correction = profile_for(extraction)
    bounds = correction.get('holding_page_ranges', {}).get(row.get('section')) if correction else None
    return (bounds is None or len(bounds) == 2 and type(row.get('page_number')) is int and
        bounds[0] <= row['page_number'] <= bounds[1])


def source_bound_parser_version_valid(extraction):
    from .oge_278e_public import PARSER_VERSION, LEGACY_PARSER_VERSIONS
    reserved = {v for c in profiles() for v in c['reserved_parser_versions']}
    version = extraction.get('parser_version')
    if version not in reserved and version in {PARSER_VERSION, *LEGACY_PARSER_VERSIONS}:
        return True
    return profile_for(extraction) is not None


def _matches_record(row, records):
    return any(all(row.get(key) == value for key, value in expected.items()) for expected in records)


def source_bound_existing_holding_valid(row, extraction):
    selected = _approved_variant(extraction)
    return selected is not None and _matches_record(row, selected['existing_holding_approvals'])


def recovered_ocr_holding_valid(row, extraction):
    selected = _approved_variant(extraction)
    recovery = row.get('ocr_recovery')
    return (selected is not None and isinstance(recovery, dict) and recovery.get('method') == RECOVERY_METHOD and
        source_bound_holding_section_page_valid(row, extraction) and
        _matches_record(row, selected['recovered_holdings']))


def scanned_signature_evidence_valid(extraction):
    selected = _approved_variant(extraction)
    if selected is None:
        return False
    expected = next((a['value'] for a in selected['actions']
        if a['op'] == 'set_report' and a['field'] == 'signature_evidence'), None)
    return (expected is not None and extraction.get('signature_evidence') == expected and
        fingerprint(extraction) == selected['assertions']['output_sha256'])
