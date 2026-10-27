"""Add original-bound financial evidence to an existing native contrast route.

The original contrast reading is retained in full. This module does not parse
words, render, or certify source paint: the shared source reader must replay
those proofs before an existing financial rule can consume a raw native row.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path, PurePosixPath

from .house import HouseIndexError

PROFILE_KEY = 'native_contrast_financial_profile'
PROFILE_VERSION = 'house-native-source-financial/v1'


def _row_map(rows, key, label):
    if not isinstance(rows, list):
        raise HouseIndexError(f'House source financial {label} must be a list')
    result = {}
    for row in rows:
        identifier = row.get(key) if isinstance(row, dict) else None
        if not isinstance(identifier, str) or not identifier or identifier in result:
            raise HouseIndexError(f'House source financial {label} identity is missing or duplicated')
        result[identifier] = row
    return result


def _source_report(base, reading):
    """Validate source API structure; source semantics belong to its replay."""
    from .house_ptr import SCHEMA, PARSER_VERSION
    if (not isinstance(reading, dict) or reading.get('profile_version') != PROFILE_VERSION or
            reading.get('source_sha256') != base.get('source_sha256') or
            reading.get('document_id') != str(base.get('source', {}).get('document_id')) or
            reading.get('status') not in {'observed', 'unknown'}):
        raise HouseIndexError('House source financial proof binding differs')
    if reading['status'] == 'unknown':
        return None, {}
    raw = reading.get('raw_projection')
    scope = reading.get('scope')
    if (not isinstance(raw, dict) or raw.get('schema_version') != SCHEMA or
            raw.get('parser_version') != PARSER_VERSION or
            raw.get('extraction_method') != 'native_pdf_text' or
            any(key.startswith('native_contrast_') for key in raw) or
            raw.get('source_sha256') != base.get('source_sha256') or
            raw.get('source') != base.get('source') or
            not isinstance(scope, dict) or scope.get('status') != 'observed_scope' or
            scope.get('scope_complete') is not True or
            scope.get('source_sha256') != base.get('source_sha256') or
            scope.get('document_id') != reading['document_id']):
        raise HouseIndexError('House source financial ordinary projection or scope differs')
    original = _row_map(base.get('transactions'), 'extraction_id', 'original rows')
    projected = _row_map(raw.get('transactions'), 'extraction_id', 'source rows')
    # This profile supplies evidence, never corrections to raw financial cells,
    # membership, coordinates, IDs or the parser's original field problems.
    if original != projected:
        raise HouseIndexError('House source financial raw rows differ from original contrast reading')
    proofs = _row_map(reading.get('rows'), 'raw_row_id', 'row proofs')
    if set(proofs) != set(original):
        raise HouseIndexError('House source financial row proof coverage differs')
    for proof in proofs.values():
        if (proof.get('status') not in {'observed', 'unknown'} or
                not isinstance(proof.get('reasons'), list) or
                any(not isinstance(reason, str) for reason in proof['reasons']) or
                not isinstance(proof.get('proof'), dict) or
                (proof['status'] == 'observed' and (not proof['proof'] or proof['reasons']))):
            raise HouseIndexError('House source financial row proof is invalid')
    return raw, proofs


def augment_selected_native_contrast(root, metadata_path, base):
    """Retain the base and separately explain any newly source-proven row."""
    from .house_native_contrast import PARSER_VERSIONS, _digest, _json
    from .house_revision_source_scope import replay_native_house_financial_source
    if (not isinstance(base, dict) or base.get('parser_version') not in PARSER_VERSIONS or
            PROFILE_KEY in base):
        raise HouseIndexError('House source financial extension requires an original contrast reading')
    reading = replay_native_house_financial_source(root, metadata_path, base)
    raw, proofs = _source_report(base, reading)
    if raw is None or not _upgradable_ids(base, proofs):
        return deepcopy(base)
    profile = {'profile_version': PROFILE_VERSION, 'base_sha256': _digest(base),
               'source': deepcopy(reading),
               'original_base_dispositions': deepcopy(base['native_contrast_dispositions'])}
    report = deepcopy(base)
    if raw is not None:
        projected = _row_map(raw['transactions'], 'extraction_id', 'source rows')
        dispositions = _row_map(report['native_contrast_dispositions'], 'raw_row_id', 'original dispositions')
        if set(dispositions) != set(projected):
            raise HouseIndexError('House source financial disposition coverage differs')
        for identifier, proof in proofs.items():
            disposition = dispositions[identifier]
            if proof['status'] != 'observed' or disposition['status'] == 'current_projected':
                continue
            # Extraction dispositions carry raw rows for the existing revision
            # resolver. Qualification dispositions separately carry formal facts.
            # The old paint result remains unchanged in the profile's base copy.
            disposition.update(status='current_projected', current_projected=deepcopy(projected[identifier]),
                               reasons=[])
            disposition[PROFILE_KEY] = {'profile_version': PROFILE_VERSION, 'raw_row_id': identifier}
    # Reuse the existing bounded contrast serializer, including its proof budget.
    return _json({**report, PROFILE_KEY: profile})


def _upgradable_ids(base, proofs):
    dispositions = _row_map(base.get('native_contrast_dispositions'), 'raw_row_id', 'original dispositions')
    if set(dispositions) != set(proofs):
        raise HouseIndexError('House source financial disposition coverage differs')
    return {identifier for identifier, proof in proofs.items()
            if proof['status'] == 'observed' and dispositions[identifier].get('status') == 'unknown'}


def requires_source_financial_upgrade(root, metadata_path, value):
    """Select a normal reparse only; this flag never grants current facts."""
    from .house_native_contrast import PARSER_VERSIONS
    from .house_revision_source_scope import replay_native_house_financial_source
    if (not isinstance(value, dict) or PROFILE_KEY in value or
            value.get('parser_version') not in PARSER_VERSIONS):
        return False
    if not any(isinstance(row, dict) and row.get('status') == 'unknown'
               for row in value.get('native_contrast_dispositions', [])):
        return False
    reading = replay_native_house_financial_source(root, metadata_path, value)
    raw, proofs = _source_report(value, reading)
    return raw is not None and bool(_upgradable_ids(value, proofs))


def _metadata_path(extraction, evidence_root):
    if evidence_root is None:
        raise HouseIndexError('House source financial qualification requires its original evidence root')
    root = Path(evidence_root).resolve(strict=True)
    relative = extraction.get('source', {}).get('archive_path')
    if (not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative or
            PurePosixPath(relative).is_absolute() or
            any(part in {'', '.', '..'} for part in relative.split('/'))):
        raise HouseIndexError('House source financial archived path is invalid')
    metadata = (root / relative).with_suffix('.json').resolve(strict=True)
    if not root.is_dir() or not metadata.is_relative_to(root):
        raise HouseIndexError('House source financial archive escapes its evidence root')
    return root, metadata


def _merge_records(original, extra, proven_ids):
    """Keep unaffected diagnostics and avoid duplicate row-level observations."""
    from .house_native_contrast import _digest
    result, seen = [], set()
    for item in [*original, *(item for item in extra if item.get('extraction_id') in proven_ids)]:
        digest = _digest(item)
        if digest not in seen:
            result.append(deepcopy(item)); seen.add(digest)
    return result


def qualify_source_financial(extraction, identity, evidence_root, revision_ids=frozenset()):
    """Rebuild both source layers before the private existing financial phase."""
    from .house_native_contrast import parse_selected_native_contrast, qualify_native_contrast
    from .house_ptr import qualify_automatic
    if not isinstance(extraction, dict) or PROFILE_KEY not in extraction:
        raise HouseIndexError('House source financial qualification lacks its explicit profile')
    if not isinstance(revision_ids, frozenset):
        raise HouseIndexError('House source financial revision permissions must be a frozen set')
    root, metadata = _metadata_path(extraction, evidence_root)
    base = parse_selected_native_contrast(root, metadata, required=True)
    if base is None:
        raise HouseIndexError('House source financial original no longer selects contrast')
    fresh = augment_selected_native_contrast(root, metadata, base)
    if fresh != extraction:
        raise HouseIndexError('House source financial saved profile differs from original replay')
    reading = fresh[PROFILE_KEY]['source']
    raw, proofs = _source_report(base, reading)
    proven = {identifier for identifier, proof in proofs.items() if proof['status'] == 'observed'}
    result = deepcopy(qualify_native_contrast(base, identity, evidence_root=root, revision_ids=revision_ids))
    if raw is None or not proven:
        result[PROFILE_KEY] = deepcopy(fresh[PROFILE_KEY])
        return result
    # Deliberately private: an independent full source replay has already passed.
    # The raw report retains its original parser, fields and word observations.
    # No public ordinary-cache fallback or source-root bypass is exposed.
    financial = qualify_automatic(raw, identity, _source_bound_revision_ids=revision_ids & proven)
    good = _row_map(result['transactions'], 'id', 'existing qualified rows')
    bad = _row_map(result['quarantined'], 'extraction_id', 'existing quarantined rows')
    new_good = _row_map(financial['transactions'], 'id', 'financial qualified rows')
    new_bad = _row_map(financial['quarantined'], 'extraction_id', 'financial quarantined rows')
    raw_rows = _row_map(base['transactions'], 'extraction_id', 'original rows')
    dispositions = _row_map(result['native_contrast_dispositions'], 'raw_row_id', 'qualified dispositions')
    if set(dispositions) != set(raw_rows) or set(good) | set(bad) != set(raw_rows) or set(good) & set(bad):
        raise HouseIndexError('House source financial existing qualification coverage differs')
    if set(new_good) | set(new_bad) != set(raw_rows) or set(new_good) & set(new_bad):
        raise HouseIndexError('House source financial rule coverage differs')
    for identifier in proven:
        # An independently positive old interpretation is not a correction target.
        if identifier in good:
            if identifier in new_good and good[identifier] != new_good[identifier]:
                raise HouseIndexError('House source financial positive interpretations conflict')
            continue
        disposition = dispositions[identifier]
        if identifier in new_good:
            fact = deepcopy(new_good[identifier])
            good[identifier] = fact
            bad.pop(identifier)
            disposition.update(status='current_projected', current_projected=fact, reasons=[])
        else:
            # Retain every existing bad-row reason as well as independent
            # financial failures, including unresolved amendment relationships.
            failure = new_bad[identifier]
            bad[identifier] = {**deepcopy(bad[identifier]),
                'reasons': sorted(set(bad[identifier].get('reasons', [])) | set(failure.get('reasons', [])))}
            disposition.update(status='unknown', current_projected=None,
                               reasons=deepcopy(bad[identifier]['reasons']))
        disposition[PROFILE_KEY] = {'profile_version': PROFILE_VERSION, 'raw_row_id': identifier}
    order = list(raw_rows)
    result['transactions'] = [good[identifier] for identifier in order if identifier in good]
    result['quarantined'] = [bad[identifier] for identifier in order if identifier in bad]
    result['native_contrast_dispositions'] = [dispositions[identifier] for identifier in order]
    result['advisories'] = _merge_records(result.get('advisories', []), financial.get('advisories', []), proven)
    revisions = _merge_records(result.get('revision_observations', []), financial.get('revision_observations', []), proven)
    if revisions:
        result['revision_observations'] = revisions
    result[PROFILE_KEY] = deepcopy(fresh[PROFILE_KEY])
    result['source_status'], result['coverage_complete'] = 'partial', False
    result['qualification'].update(status='qualified_rows' if good else 'quarantined_rows',
        qualified_count=len(good), quarantined_count=len(bad), advisory_count=len(result['advisories']),
        zero_transaction_document_count=0, production_eligible=bool(good))
    return result
