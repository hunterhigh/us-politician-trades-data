"""Shared publication and presentation semantics for real source waterlines."""
from datetime import datetime, timedelta, timezone
import os

FRESH_AGE = timedelta(hours=48)
SENATE_WARNING_AGE = timedelta(days=20)
SENATE_MAX_STALE_DAYS_ENV = 'SENATE_MAX_STALE_DAYS'


def senate_max_stale_days(value=None):
    value = os.environ.get(SENATE_MAX_STALE_DAYS_ENV, '30') if value is None else value
    if value == '':
        value = '30'
    if isinstance(value, bool) or not str(value).isdigit() or int(value) < 1:
        raise ValueError('SENATE_MAX_STALE_DAYS must be a positive integer')
    return int(value)


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Source waterline must be an explicit timestamp')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Source waterline requires a timezone')
    return result.astimezone(timezone.utc)


def collection_at(source, status):
    return (status.get('last_successful_collection_at') or
            ((status.get('catalog') or {}).get('retrieved_at')
             if source in {'senate_efd', 'oge'} else
             status.get('run_at') if status.get('status') in {'ok', 'attention'} else None))


def source_blocked(status):
    return (status.get('status') in {'blocked', 'disabled', 'authorization_required'} or
            status.get('status') == 'failed' and
            status.get('last_collection_attempt', {}).get('http_status') == 403)


def source_unavailable(status):
    return source_blocked(status) or status.get('status') in {'failed', 'unavailable'}


def senate_grace_allowed(status):
    return (status.get('status') not in {'disabled', 'authorization_required'} and
            status.get('collection_enabled', True) is True and
            status.get('terms_acknowledged', True) is True)


def maximum_age(source, senate_days=None):
    return (timedelta(days=senate_max_stale_days(senate_days))
            if source == 'senate_efd' else FRESH_AGE)


def health_rows(rows, *, as_of, statuses=None, senate_days=None, included_sources=None):
    """Copy source metadata without changing any source cutoff or fact."""
    from copy import deepcopy
    result = deepcopy(rows)
    now = timestamp(as_of) if isinstance(as_of, str) else as_of
    days = senate_max_stale_days(senate_days)
    for row in result:
        source = row.get('source_id')
        if included_sources is not None and source not in included_sources:
            continue
        if source not in {'house_clerk', 'senate_efd', 'oge'} or not row.get('data_cutoff_at'):
            continue
        status = (statuses or {}).get(source, {})
        unavailable = source_unavailable(status) if status else row.get('unavailable', False)
        stale = now - timestamp(row['data_cutoff_at']) > FRESH_AGE
        collected = collection_at(source, status) if status else None
        if collected:
            stale |= now - timestamp(collected) > FRESH_AGE
        row['stale'] = bool(stale or source == 'senate_efd' and unavailable)
        row['unavailable'] = bool(unavailable)
        if source == 'senate_efd':
            row['max_stale_days'] = days
            if row['stale']:
                row['status'] = 'stale'
    return result


def render(html, data):
    """Annotate the normal rendered report, including its person navigation."""
    import json
    from pathlib import Path
    sources = [row for row in data.get('source_health', [])
               if row.get('source_id') == 'senate_efd' and row.get('stale') is True]
    if not sources:
        return html
    if len(sources) != 1 or not sources[0].get('data_cutoff_at'):
        raise ValueError('Stale Senate presentation requires one real cutoff')
    if html.count('</body>') != 1:
        raise ValueError('Report body boundary is invalid for source freshness')
    encoded = json.dumps(sources[0], ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    script = Path(__file__).with_name('source_freshness.js').read_text(encoding='utf-8')
    return html.replace('</body>', '<script>\nconst PUBLISHED_SENATE_FRESHNESS = ' + encoded +
                        ';\n' + script + '\n</script>\n</body>')
