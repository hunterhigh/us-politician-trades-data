"""Existing browser-suite fixtures, moved from the retired backend unit modules."""
from unison_snapshot.builder import build
from fixtures.public_repo import production_payload

def freshness_fixture(stale=True):
    """Contract-only data, built and read through the ordinary public consumer."""
    payload = production_payload()
    payload['meta']['data_cutoff_at'] = '2026-09-26T00:00:00Z'
    payload['meta']['source_cutoff_policy'] = 'per_source/v1'
    for row in payload['source_health']:
        if row['source_id'] == 'house_clerk':
            row.update(data_cutoff_at=payload['meta']['data_cutoff_at'], stale=False)
        elif row['source_id'] == 'senate_efd':
            row.update(data_cutoff_at='2026-09-18T00:00:00Z' if stale else payload['meta']['data_cutoff_at'], status='stale' if stale else 'ok', stale=stale, unavailable=stale, max_stale_days=30)
    return (payload, build(payload, generated_at='2026-09-26T00:00:00Z', allow_production=True))
