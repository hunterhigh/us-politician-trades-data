"""Keep disclosed open thresholds separate from financial-row qualification."""
from collections import Counter


def threshold(row, parser_version):
    if row.get('amount_kind') != 'open_ended' or row.get('amount_high', ...) is not None:
        return None
    low = row.get('amount_low')
    if type(low) is not int or low < 0:
        return None
    from .house_ptr import AMOUNT_OVER_RE, LEGACY_AMOUNT_BUCKETS, LEGACY_PARSER_VERSION
    raw = row.get('amount_raw')
    matched = AMOUNT_OVER_RE.fullmatch(raw) if isinstance(raw, str) else None
    if matched:
        disclosed = int(matched.group(1).replace(',', ''))
        # v04 encoded the first whole dollar above the printed threshold.
        if low == disclosed or parser_version == 'house-ptr-2026-04' and low == disclosed + 1:
            if row.get('amount_low_exclusive', True) is True:
                return disclosed, True
        return None
    if (parser_version == LEGACY_PARSER_VERSION and raw is None and
            low == LEGACY_AMOUNT_BUCKETS[-1][0]):
        # The legacy fixed checkbox's tenth column is printed "Over $50m".
        # Its old inclusive integer minimum remains unchanged in review.
        return low - 1, True
    exclusive = row.get('amount_low_exclusive')
    return (low, exclusive) if type(exclusive) is bool else None


def quarantined_limitations(extraction, qualification):
    """Expose observations still blocked by other reasons; never grant facts."""
    observed = [(row, threshold(row, extraction.get('parser_version')))
                for row in extraction.get('transactions', [])]
    observed = [(row, amount) for row, amount in observed if amount is not None]
    if not observed:
        return []
    if (extraction.get('source_sha256') != qualification.get('source_sha256') or
            extraction.get('source', {}).get('document_id') != qualification.get('document_id')):
        raise ValueError('House open-amount observation differs from its qualified source')
    blocked = {row['extraction_id']: row['reasons'] for row in qualification.get('quarantined', [])
               if isinstance(row.get('extraction_id'), str)}
    groups = Counter()
    for row, amount in observed:
        reasons = blocked.get(row.get('extraction_id'))
        if amount is None or not reasons:
            continue
        # Old artifacts remain readable; representation is not a blocker.
        reasons = tuple(sorted(set(reasons) - {'open_ended_amount_not_representable',
                                             'spouse_dc_open_ended_amount_not_representable'}))
        if reasons:
            groups[(*amount, reasons)] += 1
    return [f'报告 {qualification["document_id"]} · 开口金额观察 {count} 行 · 原阈值 '
            f'{"Over" if exclusive else "At least"} ${low:,}；仍因 {", ".join(reasons)} 隔离，'
            '不作为合格交易、不进入发布数组。'
            for (low, exclusive, reasons), count in sorted(groups.items())]


def refresh_qualifications(review_root, evidence_root):
    """Migrate the retired qualification reason through the normal qualifier."""
    import json
    from pathlib import Path
    from .codec import encode
    from .house_ptr import qualify_automatic
    review = Path(review_root)
    changed = []
    for path in sorted((review / 'house_clerk/qualifications').glob('*/*/*.json')):
        prior = json.loads(path.read_bytes())
        if not any('open_ended_amount_not_representable' in row.get('reasons', [])
                   for row in prior.get('quarantined', [])):
            continue
        relative = path.relative_to(review / 'house_clerk/qualifications')
        extraction = json.loads((review / 'house_clerk/extractions' / relative).read_bytes())
        current = qualify_automatic(extraction, prior['identity'], evidence_root=evidence_root)
        old = {row['id']: row for row in prior['transactions']}
        new = {row['id']: row for row in current['transactions']}
        if (len(old) != len(prior['transactions']) or len(new) != len(current['transactions']) or
                any(new.get(key) != row for key, row in old.items()) or
                any(new[key].get('amount_high', ...) is not None for key in new.keys() - old.keys())):
            raise ValueError('House open-amount refresh changed another eligible fact')
        old_blocked = {row['extraction_id']: set(row['reasons']) for row in prior['quarantined']}
        new_blocked = {row['extraction_id']: set(row['reasons']) for row in current['quarantined']}
        for key, reasons in old_blocked.items():
            remaining = reasons - {'open_ended_amount_not_representable'}
            if remaining and new_blocked.get(key) != remaining:
                raise ValueError('House open-amount refresh changed another quarantine reason')
        temporary = path.with_suffix('.next')
        temporary.write_bytes(encode(current)); temporary.replace(path)
        changed.append({'document_id': current['document_id'], 'added_open_rows': len(new.keys() - old.keys())})
    return changed


def refresh_candidate(candidate, review_root, evidence_root):
    """Add verified open observations without rebuilding unrelated parser caches.

    This is an explicit policy-only operation on an existing source projection.
    Its real cutoff and every published financial object must remain unchanged.
    Normal source assembly and original-proof verification still follow it.
    """
    from copy import deepcopy
    from datetime import datetime
    import json
    from pathlib import Path
    from .builder import build
    from .house_ptr import qualify_automatic

    result = deepcopy(candidate)
    cutoff = datetime.fromisoformat(candidate['meta']['data_cutoff_at'].replace('Z', '+00:00'))
    people = {row['id'] for row in candidate['people']}
    existing = {row['id'] for row in candidate['transactions']}
    review = Path(review_root)
    for path in sorted((review / 'house_clerk/qualifications').glob('*/*/*.json')):
        qualification = json.loads(path.read_bytes())
        added = [row for row in qualification.get('transactions', [])
                 if row.get('amount_high', ...) is None and row['id'] not in existing]
        if not added:
            continue
        relative = path.relative_to(review / 'house_clerk/qualifications')
        extraction = json.loads((review / 'house_clerk/extractions' / relative).read_bytes())
        if (path.stem != extraction.get('source_sha256') or
                path.parent.name != str(extraction.get('source', {}).get('document_id')) or
                qualification != qualify_automatic(extraction, qualification['identity'],
                                                    evidence_root=evidence_root)):
            raise ValueError('House open-amount candidate lacks its exact source qualification')
        for row in added:
            if (row.get('source_id') != 'house_clerk' or row['person_id'] not in people or
                    datetime.fromisoformat(row['filed_at'].replace('Z', '+00:00')) > cutoff):
                raise ValueError('House open-amount refresh exceeds the existing source scope')
            result['transactions'].append(row)
            existing.add(row['id'])

    def financial_arrays(payload):
        bundle = build(payload, generated_at=candidate['meta']['data_cutoff_at'], allow_production=True)
        board = json.loads(bundle.files['board/' + bundle.manifest['board'] + '.json'])
        return {key: board[key] for key in ('people', 'transactions', 'reported_holdings')}

    if financial_arrays(candidate) != financial_arrays(result):
        raise ValueError('House open-amount refresh changed published financial objects')
    return result
