"""Read and replay one canonical saved paper-owner correction, without OCR."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .house import MAX_PDF_BYTES

MAX_JSON_BYTES = 32 * 1024 * 1024
_NULLABLE = {'ticker', 'ticker_mapping_basis', 'option_type', 'strike_price',
             'expiration_date', 'position_effect_basis'}


def _read(root, relative, *, binary=False, optional=False):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('House paper owner input escapes its controlled root')
    if optional and not path.exists():
        return None
    limit = MAX_PDF_BYTES if binary else MAX_JSON_BYTES
    if path.stat().st_size > limit:
        raise ValueError('House paper owner input exceeds byte budget')
    with path.open('rb') as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('House paper owner input exceeds byte budget')
    if binary:
        return raw

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('House paper owner JSON has duplicate keys')
            result[key] = value
        return result

    def constant(_):
        raise ValueError('House paper owner JSON has nonfinite numbers')

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(value, dict):
        raise ValueError('House paper owner input must be an object')
    return value


def load_house_paper_owner_inputs(evidence_root, review_root, *, year,
                                 document_id, source_sha256):
    if (not re.fullmatch(r'\d{4}', str(year)) or
            not isinstance(document_id, str) or not re.fullmatch(r'\d{1,12}', document_id) or
            not isinstance(source_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', source_sha256)):
        raise ValueError('House paper owner canonical source selection is invalid')
    relative = f'{year}/{document_id}/{source_sha256}'
    prefix = f'house_clerk/documents/{relative}'
    metadata = _read(evidence_root, prefix + '.json')
    pdf = _read(evidence_root, prefix + '.pdf', binary=True)
    if (metadata.get('archive_path') != prefix + '.pdf' or
            str(metadata.get('filing_year')) != str(year) or
            metadata.get('document_id') != document_id or metadata.get('sha256') != source_sha256 or
            hashlib.sha256(pdf).hexdigest() != source_sha256):
        raise ValueError('House paper owner archived original binding differs')
    folders = {'identity': 'identities', 'observation': 'paper_observations',
               'amount_header': 'paper_amount_headers', 'field_reread': 'paper_key_field_rereads',
               'extraction': 'extractions', 'qualification': 'qualifications'}
    result = {'pdf_bytes': pdf, 'metadata': metadata}
    for key, folder in folders.items():
        result[key] = _read(review_root, f'house_clerk/{folder}/{relative}.json',
                            optional=key == 'field_reread')
    if (result['extraction'].get('source') != metadata or
            result['identity'] != result['qualification'].get('identity')):
        raise ValueError('House paper owner identity or metadata differs from saved source')
    return result


def replay_house_paper_owner_correction(evidence_root, review_root, *, qualification,
                                       extraction=None):
    from .house_paper_owner_correction import PROFILE_KEY, prepare_paper_owner_correction
    profile = qualification.get(PROFILE_KEY)
    if profile is None:
        return None
    if evidence_root is None or review_root is None or not isinstance(profile, dict):
        raise ValueError('House paper owner correction requires its controlled original and saved inputs')
    saved = extraction if extraction is not None else qualification.get('materialized_extraction')
    metadata = saved.get('source') if isinstance(saved, dict) else None
    if not isinstance(metadata, dict):
        raise ValueError('House paper owner correction has no source extraction')
    inputs = load_house_paper_owner_inputs(evidence_root, review_root,
        year=metadata.get('filing_year'), document_id=qualification.get('document_id'),
        source_sha256=qualification.get('source_sha256'))
    if inputs['qualification'] != qualification or inputs['extraction'] != saved:
        raise ValueError('House paper owner correction differs from canonical saved objects')
    result = prepare_paper_owner_correction(inputs['pdf_bytes'], inputs['observation'],
        inputs['metadata'], inputs['identity'], amount_header=inputs['amount_header'],
        field_reread=inputs['field_reread'], prior_extraction=profile.get('legacy_extraction'),
        prior_qualification=profile.get('legacy_qualification'))
    if (result is None or result['final_qualification'] != qualification or
            qualification.get('materialized_extraction') != saved):
        raise ValueError('House paper owner correction differs from complete original replay')
    return result


def represented_paper_owner_fact(projection, formal):
    """Keep existing shard null omission and existing market enrichment only."""
    if not isinstance(projection, dict) or not isinstance(formal, dict) or set(formal) - set(projection):
        return None
    missing = set(projection) - set(formal)
    if any(key not in _NULLABLE or projection[key] is not None for key in missing):
        return None
    differences = [key for key in formal if projection[key] != formal[key]]
    if differences:
        from .fact_policy.source_objects import _market_enrichment_only
        if not _market_enrichment_only(formal, projection, differences):
            return None
    return deepcopy(formal)
