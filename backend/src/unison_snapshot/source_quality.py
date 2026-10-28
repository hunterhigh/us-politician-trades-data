"""Weekly, explicitly denominated isolation observations; never infer unread source rows."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

from .codec import digest, encode


def snapshot(source, scopes, candidate, *, observed_at=None):
    result = []
    for scope in scopes:
        if scope['source_id'] != source:
            raise ValueError('Quality source scope differs from its source candidate')
        reports = scope['reports']
        rows = []
        layouts = set()
        for report in reports:
            good = report.get('qualified_transaction_count')
            holdings = report.get('qualified_holding_count')
            bad = report.get('quarantined_row_count')
            if holdings is not None:
                good = holdings + (good or 0)
            if all(type(value) is int and value >= 0 for value in (good, bad)):
                rows.append((good, bad))
            reasons = report.get('reason_codes', [])
            if any(reason == 'unknown_layout' or reason.startswith('unknown_layout:') for reason in reasons):
                # Bound each unknown layout to an observed report and parser/source
                # version. No guessed whole-source layout census is introduced.
                layouts.add(digest(encode({'report': report['record_key'],
                    'versions': report.get('source_versions', []), 'reasons': sorted(reasons)})))
        evaluated = [report for report in reports if report.get('qualification_state') in
                     {'qualified', 'partial', 'quarantined', 'zero_transactions'}]
        quarantined = sum(report['qualification_state'] == 'quarantined' for report in evaluated)
        good_rows = sum(good for good, bad in rows); bad_rows = sum(bad for good, bad in rows)
        result.append({'scope_id': scope['scope_id'], 'cohort': digest(encode(scope['scope'])),
            'report_quarantine': {'numerator': quarantined, 'denominator': len(evaluated)},
            'row_quarantine': {'numerator': bad_rows, 'denominator': good_rows + bad_rows},
            'unknown_row_report_count': len(reports) - len(rows), 'report_count': len(reports),
            'unknown_layouts': sorted(layouts)})
    return {'schema_version': 'source-quality/v1', 'source_id': source,
            'observed_at': observed_at or datetime.now(timezone.utc).isoformat(),
            'candidate_sha256': digest(encode(candidate)), 'scopes': result}


def observe(source, evidence, review, candidate):
    if source == 'house_clerk':
        from .disclosure_status_house import build_house_status as adapter
    elif source == 'senate_efd':
        from .disclosure_status_senate import build_senate_status as adapter
    elif source == 'oge':
        from .disclosure_status_oge import build_oge_status as adapter
    else:
        raise ValueError('Unknown quality source')
    scopes = adapter(Path(evidence), Path(review), candidate['transactions'], candidate['reported_holdings'])
    result = snapshot(source, scopes, candidate)
    target = Path(review) / 'status/quality' / (source + '.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.next.json'); temporary.write_bytes(encode(result)); temporary.replace(target)
    return result


def weekly(current, previous=None, *, now=None):
    now = now or datetime.now(timezone.utc)
    year, number, _ = now.isocalendar(); week = f'{year}-W{number:02}'
    previous = previous or {'schema_version': 'quality-history/v1', 'weeks': {}}
    if previous.get('schema_version') != 'quality-history/v1':
        raise ValueError('Unknown quality history schema')
    weeks = json.loads(json.dumps(previous['weeks']))
    weeks.setdefault(week, {})
    for source, observation in current.items():
        if observation.get('schema_version') != 'source-quality/v1' or observation.get('source_id') != source:
            raise ValueError('Quality observation has an invalid source binding')
        if not re.fullmatch('[0-9a-f]{64}', observation.get('candidate_sha256', '')):
            raise ValueError('Quality observation lacks its candidate hash')
        for scope in observation['scopes']:
            for metric in ('report_quarantine', 'row_quarantine'):
                pair = scope[metric]
                if (any(type(pair.get(key)) is not int for key in ('numerator', 'denominator')) or
                        not 0 <= pair['numerator'] <= pair['denominator']):
                    raise ValueError('Quality isolation counts do not conserve their denominator')
        observed = datetime.fromisoformat(observation['observed_at'].replace('Z', '+00:00'))
        if observed.tzinfo is None or observed > now:
            raise ValueError('Quality observation has an invalid clock')
        observed_year, observed_number, _ = observed.isocalendar()
        observed_week = f'{observed_year}-W{observed_number:02}'
        # A stale saved file does not become a new observation this week.
        if observed_week == week:
            weeks[week][source] = observation
    prior_year, prior_number, _ = (now - timedelta(days=7)).isocalendar()
    baseline = weeks.get(f'{prior_year}-W{prior_number:02}', {})
    alerts = []; summaries = []
    for source, observation in weeks[week].items():
        old_scopes = {scope['scope_id']: scope for scope in baseline.get(source, {}).get('scopes', [])}
        for scope in observation['scopes']:
            prior = old_scopes.get(scope['scope_id'])
            comparable = prior is not None and prior['cohort'] == scope['cohort']
            increased = []
            for metric in ('report_quarantine', 'row_quarantine'):
                a = scope[metric]; b = prior[metric] if comparable else None
                if b and a['denominator'] and b['denominator']:
                    # Strictly more than five percentage points, without float error.
                    if 20 * (a['numerator'] * b['denominator'] - b['numerator'] * a['denominator']) > a['denominator'] * b['denominator']:
                        increased.append(metric)
            new_layouts = sorted(set(scope['unknown_layouts']) - set(prior['unknown_layouts'] if prior else []))
            condition = {'week': week, 'source': source, **{key: value for key, value in scope.items() if key != 'unknown_layouts'},
                         'unknown_layout_count': len(scope['unknown_layouts']), 'baseline_comparable': comparable,
                         'increased_metrics': increased, 'new_unknown_layout_count': len(new_layouts),
                         'new_unknown_layouts': new_layouts[:20]}
            summaries.append(condition)
            if increased or new_layouts:
                alerts.append({'key': 'quality:' + source + ':' + scope['scope_id'],
                    'title': '[Quality] ' + source + ' isolation or unknown layout changed',
                    'detail': json.dumps(condition, ensure_ascii=False, sort_keys=True), 'condition': condition})
    if summaries:
        alerts.append({'key': 'quality-summary:' + week, 'title': '[Quality] Weekly isolation summary ' + week,
            'detail': json.dumps(summaries, ensure_ascii=False, sort_keys=True), 'condition': summaries})
    retained = {key: weeks[key] for key in sorted(weeks)[-4:]}
    return {'schema_version': 'quality-history/v1', 'weeks': retained}, alerts
