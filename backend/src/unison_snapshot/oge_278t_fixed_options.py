"""Bounded interpretations of a complete OCR cell in an established OGE field.

These functions do not establish geometry, report periods, identity, or row
eligibility. Raw cells and identifier inputs must remain unchanged.
"""
from datetime import date, datetime
from copy import deepcopy
from itertools import combinations
import re


RULES_VERSION = 'oge-278t-fixed-options/v1'
TYPES = ('purchase', 'sale', 'exchange')


def distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j-1] + (a != b)))
        previous = current
    return previous[-1]


def _unique(raw, candidates):
    values = {value for value, cost in candidates}
    def serial(value):
        return list(value) if isinstance(value, tuple) else value
    return {'rules_version': RULES_VERSION, 'raw_text': raw,
            'status': 'interpreted' if len(values) == 1 else 'ambiguous' if values else 'unsupported',
            'value': serial(next(iter(values))) if len(values) == 1 else None,
            'candidates': [{'value': serial(value), 'distance': cost} for value, cost in candidates]}


def align_type(raw: str) -> dict:
    if not isinstance(raw, str):
        return _unique(raw, [])
    observed = raw.strip().casefold()
    token = observed.strip('[](){}|:;.,!?')
    if len(observed) - len(token) > 2:
        return _unique(raw, [])
    if not re.fullmatch('[a-z]+', token):
        return _unique(raw, [])
    return _unique(raw, [(value, cost) for value in TYPES
                        if (cost := distance(token, value)) <= (1 if value == 'sale' else 2)])


def align_amount(raw: str) -> dict:
    from .oge_reports import _AMOUNTS
    if not isinstance(raw, str) or '\n' in raw or not raw.strip():
        return _unique(raw, [])
    # Require both endpoints and the complete cell; never take a substring of
    # two merged rows or invent a missing amount.
    number = r'[0-9]+(?:[,. ]+[0-9]+)*'
    match = re.fullmatch(r'\s*[$S]\s*(' + number + r')\s*[-–—•·]\s*[$S]\s*(' + number + r')\s*', raw)
    if not match:
        return _unique(raw, [])
    tokens = [re.sub('[,. ]', '', value) for value in match.groups()]
    if any(len(value) > 1 and value.startswith('0') for value in tokens):
        return _unique(raw, [])
    observed = ':'.join(tokens)
    candidates = []
    for low, high in sorted(set(_AMOUNTS.values())):
        cost = distance(observed, f'{low}:{high}')
        # Separators may be noisy; the two complete endpoint digit sequences
        # must already identify the category. Missing/replaced digits do not
        # become new monetary evidence through nearest-neighbor guessing.
        if cost == 0:
            candidates.append(((low, high), cost))
    return _unique(raw, candidates)


def align_date(raw: str, *, period_start: str, period_end: str) -> dict:
    """Replace only slash-as-one errors, inside an independently bound period."""
    try:
        start, end = date.fromisoformat(period_start), date.fromisoformat(period_end)
    except (ValueError, TypeError):
        return {**_unique(raw, []), 'reason': 'source_reporting_period_missing'}
    if start > end or not isinstance(raw, str):
        return _unique(raw, [])
    token = raw.strip()
    if not re.fullmatch(r'[0-9/]+', token) or not re.search(r'20\d{2}$', token):
        return _unique(raw, [])
    prefix, year = token[:-4], token[-4:]
    missing = 2 - prefix.count('/')
    ones = [i for i, c in enumerate(prefix) if c == '1']
    candidates = set()
    if 0 <= missing <= 2:
        for selected in combinations(ones, missing):
            rendered = ''.join('/' if i in selected else c for i, c in enumerate(prefix)) + year
            if not re.fullmatch(r'\d{1,2}/\d{1,2}/20\d{2}', rendered):
                continue
            try:
                value = datetime.strptime(rendered, '%m/%d/%Y').date()
            except ValueError:
                continue
            if start <= value <= end:
                candidates.add(value.isoformat())
    result = _unique(raw, [(value, missing) for value in sorted(candidates)])
    return {**result, 'period': [period_start, period_end]}


def source_reporting_period(report: dict) -> tuple[str, str] | None:
    """Read an explicit interval from authenticated complete cover evidence."""
    import hashlib
    from .oge_reports import read_278t_cover_dates
    proof = report.get('cover_date_evidence')
    if not isinstance(proof, dict) or not isinstance(proof.get('cover_text'), str):
        return None
    text = proof['cover_text']
    if (proof.get('source_sha256') != report.get('source_sha256') or
            proof.get('cover_text_sha256') != hashlib.sha256(text.encode()).hexdigest()):
        return None
    try:
        if read_278t_cover_dates(text, source_sha256=report['source_sha256']) != proof:
            return None
    except (ValueError, TypeError):
        return None
    matches = list(re.finditer(r'^\s*Reporting\s+Period\s*:\s*(\d{1,2}/\d{1,2}/20\d{2})'
        r'\s*(?:-|–|—|to|through)\s*(\d{1,2}/\d{1,2}/20\d{2})\s*$', text, re.I | re.M))
    if len(matches) != 1:
        return None
    try:
        start, end = (datetime.strptime(value, '%m/%d/%Y').date() for value in matches[0].groups())
    except ValueError:
        return None
    return (start.isoformat(), end.isoformat()) if start <= end else None


def interpret_report(report: dict, *, period: tuple[str, str] | None = None) -> dict:
    """Interpret saved single-row cells after the caller authenticates the source.

    Existing qualified rows are immutable. All unrelated quarantine reasons,
    including low confidence and merged-row geometry, remain in force.
    No period is inferred from signatures, filenames, or neighboring rows.
    """
    if 'fixed_option_interpretation' in report:
        raise ValueError('Fixed-option input must be the authenticated original extraction')
    period = period if period is not None else source_reporting_period(report)
    result = deepcopy(report)
    good, bad, receipts = list(result['transactions']), [], []
    for row in result['quarantined']:
        cells = row.get('cells')
        reasons = set(row.get('reasons', []))
        if (not isinstance(cells, list) or len(cells) != 6 or
                not isinstance(cells[0], str) or not re.fullmatch('[1-9][0-9]*', cells[0]) or
                len(re.findall(r'\d+', str(row.get('printed_row_number_raw', cells[0])))) > 1 or
                any(not isinstance(cells[i], str) or '\n' in cells[i] for i in (2, 3, 5)) or
                '$' in str(cells[4]) or
                'row_number_duplicated' in reasons):
            bad.append(row)
            continue
        evidence = {}
        if 'transaction_type_unsupported' in reasons:
            value = align_type(cells[2])
            if value['status'] == 'interpreted':
                row['transaction_type'] = value['value']
                reasons.remove('transaction_type_unsupported')
                evidence['type'] = value
        if 'amount_range_unsupported' in reasons:
            value = align_amount(cells[5])
            if value['status'] == 'interpreted':
                row['amount_low'], row['amount_high'] = value['value']
                reasons.remove('amount_range_unsupported')
                evidence['amount'] = value
        if 'transaction_date_invalid' in reasons and period is not None:
            value = align_date(cells[3], period_start=period[0], period_end=period[1])
            if value['status'] == 'interpreted':
                row['transaction_date'] = value['value']
                reasons.remove('transaction_date_invalid')
                evidence['transaction_date'] = value
        if evidence:
            row['fixed_option_evidence'] = evidence
            receipts.append({'extraction_id': row['extraction_id'], 'fields': evidence})
            if period is not None and row.get('transaction_date') and not (
                    period[0] <= row['transaction_date'] <= period[1]):
                reasons.add('transaction_date_outside_source_reporting_period')
        if reasons or not evidence:
            if evidence:
                row['reasons'] = sorted(reasons)
            bad.append(row)
        else:
            row.pop('reasons', None)
            good.append(row)
    if not receipts:
        return deepcopy(report)
    result['transactions'], result['quarantined'] = good, bad
    from .codec import digest, encode
    result['fixed_option_interpretation'] = {
        'rules_version': RULES_VERSION, 'base_extraction_sha256': digest(encode(report)),
        'reporting_period': list(period) if period else None,
        'source_sha256': report['source_sha256'], 'rows': receipts}
    return result
