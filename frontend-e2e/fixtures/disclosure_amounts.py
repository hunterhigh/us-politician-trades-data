"""Existing browser-suite fixtures, moved from the retired backend unit modules."""
from copy import deepcopy
from unison_snapshot.oge_candidate import build_oge_candidate
from fixtures.oge_candidate import base, extraction, catalog

def fixture():
    parsed = extraction()
    parsed['transactions'][0]['transaction_date'] = '2025-05-20'
    data, _ = build_oge_candidate(catalog(), [parsed], base(), data_cutoff_at='2025-06-04T00:00:00Z')
    data['meta']['snapshot_id'] = 'test-open-amounts'
    data['people'][0]['priority'] = True
    finite = data['transactions'][0]
    finite.update(amount_low=1000001, amount_high=5000000)
    opened = deepcopy(finite)
    opened.update(id='open-transaction', amount_low=50000000, amount_high=None, amount_low_exclusive=True)
    data['transactions'].append(opened)
    holding = {key: finite[key] for key in ('filing_id', 'person_id', 'owner', 'asset_name', 'ticker', 'ticker_mapping_basis', 'instrument_type', 'filed_at', 'source_id', 'source', 'source_url', 'verification_status')}
    holding.update(id='bounded-holding', report_period_end='2024-12-31', value_low=1001, value_high=15000, change_from_prior=None)
    open_holding = deepcopy(holding)
    open_holding.update(id='open-holding', value_low=1000000, value_high=None, value_low_exclusive=True, owner='Spouse')
    data['reported_holdings'] = [holding, open_holding]
    return data
