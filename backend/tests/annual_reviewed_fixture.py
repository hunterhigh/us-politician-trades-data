"""Synthetic admitted covers for geometry and row-ID unit fixtures.

These tests isolate format qualification from full-checkpoint review. The real
correction entry is covered separately by test_reviewed_annual_extraction and
frozen-source replay, including all current and saved legacy checkpoints.
"""
from copy import deepcopy
from unison_snapshot.reviewed_annual_extraction import profile_for_source


def synthetic_reviewed_cover(extraction):
    correction = profile_for_source(extraction.get('source_sha256'))
    if correction is None:
        return extraction
    result = deepcopy(extraction)
    result.setdefault('source_id', correction['source_context']['source_id'])
    for action in correction['actions']:
        if action['op'] == 'set_report':
            result[action['field']] = deepcopy(action['value'])
    return result


def synthetic_cover_valid(extraction):
    correction = profile_for_source(extraction.get('source_sha256'))
    if correction is None or extraction.get('source_url') != correction['source_url']:
        return False
    expected = next(a['value'] for a in correction['actions']
        if a['op'] == 'set_report' and a['field'] == 'signature_evidence')
    return (all(extraction.get(k) == v for k, v in correction['source_context'].items()) and
        extraction.get('signature_evidence') == expected)
