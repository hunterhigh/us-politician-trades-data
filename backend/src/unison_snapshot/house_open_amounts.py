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


