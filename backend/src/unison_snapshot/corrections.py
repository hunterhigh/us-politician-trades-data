"""Source-bound corrections with atomic preconditions and a fixed action vocabulary."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

OPS = {'set_field', 'set_report', 'add_row', 'drop_row', 'quarantine', 'relate'}


def correction_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def load_correction(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if value.get('schema_version') != 'source-correction/v1':
        raise ValueError('Unsupported correction schema')
    for key in ('source', 'document_id', 'source_sha256', 'reviewed_at'):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ValueError('Correction lacks source identity or review date')
    if len(value['source_sha256']) != 64 or any(c not in '0123456789abcdef' for c in value['source_sha256']):
        raise ValueError('Invalid correction source hash')
    if not isinstance(value.get('actions'), list):
        raise ValueError('Correction actions must be a list')
    for action in value['actions']:
        if action.get('op') not in OPS or not action.get('evidence'):
            raise ValueError('Correction has an unknown operation or no evidence')
    return value


def apply_correction(document, correction):
    """Return a new document; an invalid action leaves the caller's input intact.

    Adapters supply stable physical row keys and verify source-specific evidence.
    Every write asserts the old value, including absence. Relations carry both
    source hashes and remain observations until the normal resolver qualifies them.
    """
    if any(document.get(k) != correction[k] for k in ('source', 'document_id', 'source_sha256')):
        raise ValueError('Correction source hash or identity mismatch')
    result = deepcopy(document)
    touched = set()
    for action in correction['actions']:
        op = action['op']
        if op not in OPS or not action.get('evidence'):
            raise ValueError('Correction has an unknown operation or no evidence')
        target = action.get('row', '@report')
        field = action.get('field', op)
        key = target, field
        if key in touched:
            raise ValueError('Conflicting correction actions')
        touched.add(key)
        rows = result['rows']
        if op == 'add_row':
            if target in rows or action.get('expect_absent') is not True:
                raise ValueError('Correction row already exists or has no absence assertion')
            rows[target] = deepcopy(action['value'])
            continue
        if op == 'set_report':
            container = result['report']
        else:
            if target not in rows:
                raise ValueError('Correction target row does not exist')
            container = rows[target]
        if op in {'set_field', 'set_report'}:
            exists = field in container
            if ('expect' not in action or container.get(field) != action['expect'] or
                    action.get('expect_present', True) != exists):
                raise ValueError('Correction field precondition failed')
            container[field] = deepcopy(action['value'])
        elif op == 'drop_row':
            if action.get('expect') != container:
                raise ValueError('Correction deletion precondition failed')
            del rows[target]
        elif op == 'quarantine':
            if action.get('expect') != container:
                raise ValueError('Correction quarantine precondition failed')
            result.setdefault('quarantined', {})[target] = {
                'row': rows.pop(target), 'reason': action['reason']}
        elif op == 'relate':
            relation = action.get('value', {})
            if (action.get('expect') != container or relation.get('kind') not in {'amendment', 'duplicate'}
                    or not relation.get('other_source_sha256') or not relation.get('other_document_id')):
                raise ValueError('Correction relation precondition failed')
            result.setdefault('relations', []).append(deepcopy(relation))
    return result
