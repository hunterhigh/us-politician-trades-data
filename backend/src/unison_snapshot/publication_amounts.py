"""A publication boundary; qualified review observations remain untouched."""
from collections import defaultdict
from copy import deepcopy

from .disclosure_amounts import validate_amount

POLICY = 'open-amounts-known-gaps-only/v1'
KINDS = (('transactions', 'amount'), ('reported_holdings', 'value'))


def excluded_rows(payload):
    result = {}
    for kind, prefix in KINDS:
        rows = []
        for row in payload.get(kind, []):
            if row.get(prefix + '_high', ...) is None:
                validate_amount(row, prefix)
                rows.append(row)
        result[kind] = rows
    return result


def assert_bounded(payload):
    for kind, prefix in KINDS:
        if any(row.get(prefix + '_high') is None for row in payload.get(kind, [])):
            raise ValueError('Published arrays must not contain absent upper bounds: ' + kind)


def groups(payload, excluded):
    names = {row['id']: row['display_name'] for row in payload['people']}
    result = {}
    for kind, prefix in KINDS:
        grouped = defaultdict(list)
        for row in excluded[kind]:
            grouped[(row['source_id'], row['person_id'], row['filing_id'], row['source_url'],
                     row[prefix + '_low'], row[prefix + '_low_exclusive'])].append(row)
        result[kind] = []
        for key, rows in sorted(grouped.items()):
            source, person, filing, url, low, exclusive = key
            result[kind].append({'source_id': source, 'person_id': person,
                'person_name': names[person], 'filing_id': filing, 'source_url': url,
                'count': len(rows), 'threshold': ('Over ' if exclusive else 'At least ') + f'${low:,}',
                'minimum': low, 'minimum_exclusive': exclusive,
                'record_ids': sorted(row['id'] for row in rows),
                'filed_date_range': [min(r['filed_at'][:10] for r in rows), max(r['filed_at'][:10] for r in rows)]})
    return result


def merge_gaps(gaps, exclusions):
    """Use the existing limitations outlet; excluded does not mean unparsed."""
    from .known_gaps import SCHEMA, validate
    result = deepcopy(gaps or {'schema_version': SCHEMA, 'scope': 'source_inventory',
                              'sources': [], 'person_unparsed_report_counts': {}})
    result['excluded_open_amounts'] = deepcopy(exclusions)
    result['sources'] = [row for row in result['sources'] if not row['scope_id'].startswith('open_amount_')]
    for kind, _ in KINDS:
        label = '交易' if kind == 'transactions' else '持仓'
        for source in sorted({g['source_id'] for g in exclusions[kind]}):
            selected = [g for g in exclusions[kind] if g['source_id'] == source]
            dates = sorted(d for g in selected for d in g['filed_date_range'])
            count = len({(g['person_id'], g['filing_id']) for g in selected})
            result['sources'].append({'source_id': source, 'scope_id': 'open_amount_' + kind + '_' + source,
                'report_count': count, 'partial_report_count': 0, 'quarantined_report_count': 0,
                'unassessed_report_count': 0, 'request_required_report_count': 0,
                'quarantined_row_count': 0, 'unknown_row_count_report_count': 0,
                'unidentified_affected_report_count': 0, 'affected_person_ids': [],
                'person_binding_basis': 'published_fact_report_binding',
                'filed_date_range': [dates[0], dates[-1]],
                'reason_report_counts': {'open_amount_excluded_from_publication': count},
                'limitations': [f'开口金额{label}不进入发布数组；review 保留原披露，不补上限。'] + [
                    f'{g["person_name"]} · 报告 {g["filing_id"]} · {label} {g["count"]} 笔 · {g["threshold"]}'
                    for g in selected]})
    return validate(result)


def project(payload):
    """Idempotent finite-only view, with original thresholds and exact row IDs."""
    excluded = excluded_rows(payload)
    existing = payload.get('meta', {}).get('known_gaps', {}).get('excluded_open_amounts')
    if not any(excluded.values()):
        return payload
    if existing and any(existing.values()):
        raise ValueError('Publication input mixes excluded receipts with new open records')
    result = dict(payload, meta=deepcopy(payload['meta']))
    for kind, prefix in KINDS:
        result[kind] = [row for row in payload[kind] if row.get(prefix + '_high') is not None]
    result['meta']['amount_policy'] = POLICY
    result['meta']['known_gaps'] = merge_gaps(payload['meta'].get('known_gaps'), groups(payload, excluded))
    visible = {row['id'] for row in result['transactions']}
    if 'transaction_disclosure_relations' in result['meta']:
        relations = [row for row in result['meta']['transaction_disclosure_relations'] if row['transaction_id'] in visible]
        for row in relations:
            row['possible_ptr_transaction_ids'] = [i for i in row['possible_ptr_transaction_ids'] if i in visible]
            row['status'] = 'possible_ptr_overlap' if row['possible_ptr_transaction_ids'] else 'cross_report_relationship_unconfirmed'
        result['meta']['transaction_disclosure_relations'] = relations
    if 'holding_report_coverage' in result['meta']:
        visible = {row['id'] for row in result['reported_holdings']}
        coverage = result['meta']['holding_report_coverage']
        for row in coverage:
            removed = {i for i in row['published_holding_ids'] if i not in visible}
            if removed:
                row['excluded_open_holding_ids'] = sorted(set(row.get('excluded_open_holding_ids', [])) | removed)
            row['published_holding_ids'] = [i for i in row['published_holding_ids'] if i in visible]
        result['meta']['holding_report_coverage'] = [row for row in coverage if row['published_holding_ids']]
    market = result['meta'].get('market_coverage')
    if market is not None:
        disclosed = {row['ticker'] for kind, _ in KINDS for row in result[kind] if row.get('ticker')}
        if 'pending_tickers' in market:
            market['pending_tickers'] = [t for t in market['pending_tickers'] if t in disclosed]
        if 'unsupported_tickers' in market:
            market['unsupported_tickers'] = [row for row in market['unsupported_tickers'] if row['ticker'] in disclosed]
    assert_bounded(result)
    return result
