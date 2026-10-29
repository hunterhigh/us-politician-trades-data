"""Compact source-inventory gaps carried beside immutable published facts."""
from collections import Counter
from pathlib import Path
import json
import re

from .codec import digest, encode

SCHEMA = 'known-gaps/v2'
LEGACY_SCHEMA = 'known-gaps/v1'
REFERENCE_SCHEMA = 'known-gaps-reference/v1'
MAX_BYTES = 256 * 1024
COUNTS = ('report_count', 'partial_report_count', 'quarantined_report_count',
          'unassessed_report_count', 'request_required_report_count',
          'quarantined_row_count', 'unknown_row_count_report_count',
          'unidentified_affected_report_count')


def attach(manifest, files, gaps):
    """Keep small summaries inline; never truncate a growing source inventory."""
    manifest['known_gaps'] = validate(gaps)
    if len(encode(manifest)) <= 16 * 1024:
        return
    content = encode(gaps)
    if len(content) > MAX_BYTES:
        raise ValueError('Known data gaps exceed their size budget')
    sha = digest(content)
    files[f'status/{sha}.json'] = content
    manifest['known_gaps'] = {'schema_version': REFERENCE_SCHEMA,
                              'sha256': sha, 'byte_length': len(content)}


def validate_reference(value):
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'sha256', 'byte_length'} or
            value['schema_version'] != REFERENCE_SCHEMA or
            not isinstance(value['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['sha256']) or
            type(value['byte_length']) is not int or not 0 < value['byte_length'] <= MAX_BYTES):
        raise ValueError('Known data gaps reference is invalid')
    return value


def published_bindings(board):
    facts = {r['id']: r for kind in ('transactions','reported_holdings') for r in board.get(kind, [])}
    by_source_url = {}
    for fact in facts.values():
        by_source_url.setdefault((fact['source_id'],fact['source_url']), set()).add(fact['person_id'])
    return facts, by_source_url


def bound_people(source_id, row, bindings):
    """Use only the hash-verified board's official report associations."""
    facts, by_source_url = bindings
    people = set(by_source_url.get((source_id,row.get('source_url')), ()))
    for key in ('publication_transaction_ids','publication_holding_ids'):
        for identifier in row.get(key, []):
            if identifier not in facts or facts[identifier]['source_id'] != source_id:
                raise ValueError('Known data gaps publication binding is invalid')
            if row['publication_binding'] == 'verified':
                people.add(facts[identifier]['person_id'])
    return sorted(people)


def from_scopes(scopes, board):
    bindings = published_bindings(board)
    sources = []
    person_reports = {}
    for scope in sorted(scopes, key=lambda s: s['scope_id']):
        reports = scope['reports']
        reasons = Counter(reason for row in reports for reason in row['reason_codes'])
        dates = sorted(row['filed_date'] for row in reports if row.get('filed_date'))
        affected_people, unidentified = set(), 0
        for row in reports:
            affected = (row['qualification_state'] in {'partial','quarantined','unknown','not_evaluated'} or
                        row['quarantined_row_count'] is None or row['quarantined_row_count'] > 0 or
                        row['acquisition_state'] == 'request_required')
            if not affected:
                continue
            people = bound_people(scope['source_id'],row,bindings)
            affected_people.update(people)
            unidentified += not bool(people)
            report_key = (scope['source_id'], row.get('report_id') or row.get('source_url') or
                          (scope['scope_id'], row['record_key']))
            for person in people:
                person_reports.setdefault(person, set()).add(report_key)
        sources.append({'source_id': scope['source_id'], 'scope_id': scope['scope_id'],
            'report_count': len(reports),
            'partial_report_count': sum(r['qualification_state'] == 'partial' for r in reports),
            'quarantined_report_count': sum(r['qualification_state'] == 'quarantined' for r in reports),
            'unassessed_report_count': sum(r['qualification_state'] in {'unknown','not_evaluated'} for r in reports),
            'request_required_report_count': sum(r['acquisition_state'] == 'request_required' for r in reports),
            'quarantined_row_count': sum(r['quarantined_row_count'] or 0 for r in reports),
            'unknown_row_count_report_count': sum(r['quarantined_row_count'] is None for r in reports),
            'unidentified_affected_report_count': unidentified,
            'affected_person_ids': sorted(affected_people),
            'person_binding_basis': 'published_fact_report_binding',
            'filed_date_range': [dates[0], dates[-1]] if dates else [],
            'reason_report_counts': dict(sorted(reasons.items())),
            'limitations': sorted(scope['limitations'])})
    return validate({'schema_version': SCHEMA, 'scope': 'source_inventory', 'sources': sources,
                     'person_unparsed_report_counts': {p:len(person_reports[p]) for p in sorted(person_reports)}})


def validate(value):
    if not isinstance(value, dict):
        raise ValueError('Known data gaps schema is invalid')
    version = value.get('schema_version')
    fields = {'schema_version','scope','sources'}
    if version == SCHEMA:
        fields.add('person_unparsed_report_counts')
    if (set(value) != fields or version not in {SCHEMA,LEGACY_SCHEMA} or value['scope'] != 'source_inventory' or
            not isinstance(value['sources'], list) or len(value['sources']) > 16):
        raise ValueError('Known data gaps schema is invalid')
    scopes = set()
    for row in value['sources']:
        fields = {'source_id','scope_id','filed_date_range','reason_report_counts','limitations',
                  'affected_person_ids','person_binding_basis',*COUNTS}
        if (not isinstance(row, dict) or set(row) != fields or
                row['source_id'] not in {'house_clerk','senate_efd','oge'} or
                not isinstance(row['scope_id'], str) or not row['scope_id'] or row['scope_id'] in scopes or
                any(type(row[k]) is not int or row[k] < 0 for k in COUNTS) or
                any(row[k] > row['report_count'] for k in COUNTS if k not in {'report_count','quarantined_row_count'})):
            raise ValueError('Known data gaps source counts are invalid')
        scopes.add(row['scope_id'])
        people = row['affected_person_ids']
        if (row['person_binding_basis'] != 'published_fact_report_binding' or
                not isinstance(people, list) or any(not isinstance(p, str) or not p for p in people) or
                people != sorted(set(people))):
            raise ValueError('Known data gaps person bindings are invalid')
        dates = row['filed_date_range']
        from datetime import date
        try:
            if (not isinstance(dates, list) or len(dates) not in {0,2} or
                    any(date.fromisoformat(d).isoformat() != d for d in dates) or dates != sorted(dates)):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError('Known data gaps date range is invalid') from None
        if (not isinstance(row['reason_report_counts'], dict) or
                any(not isinstance(k, str) or not k or type(n) is not int or
                    not 0 < n <= row['report_count'] for k,n in row['reason_report_counts'].items()) or
                not isinstance(row['limitations'], list) or
                any(not isinstance(s, str) or not s for s in row['limitations'])):
            raise ValueError('Known data gaps reasons are invalid')
    if version == SCHEMA:
        counts = value['person_unparsed_report_counts']
        people = {p for row in value['sources'] for p in row['affected_person_ids']}
        report_count = sum(row['report_count'] for row in value['sources'])
        if (not isinstance(counts, dict) or set(counts) != people or
                any(not isinstance(p, str) or not p or type(n) is not int or
                    not 0 < n <= min(report_count, 2**53-1) for p,n in counts.items())):
            raise ValueError('Known data gaps person report counts are invalid')
    return value


def render(html, data):
    gaps = data.get('meta', {}).get('known_gaps')
    if gaps is None:
        return html
    validate(gaps)
    encoded = json.dumps(gaps, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    script = Path(__file__).with_name('known_gaps.js').read_text(encoding='utf-8')
    if html.count('</body>') != 1:
        raise ValueError('Report body boundary is invalid for known gaps')
    return html.replace('</body>', '<script>\nconst PUBLISHED_KNOWN_GAPS = ' + encoded + ';\n' + script + '\n</script>\n</body>')
