"""Existing browser-suite fixtures, moved from the retired backend unit modules."""
from unison_snapshot.builder import build
from unison_snapshot.disclosure_status import attach_disclosure_status
from fixtures.disclosure_status import scopes, PROVENANCE, NOW, record
from fixtures.public_repo import production_payload

def fixture():
    payload = production_payload()
    sources = scopes(payload)
    sources[0]['reports'][0].update(qualification_state='partial', quarantined_row_count=3, reason_codes=['transaction_date_invalid'], filed_date='2026-09-01', filed_date_basis='qualified_report')
    sources[0]['reports'].append(record('unknown-report', acquisition_state='request_required', qualification_state='not_evaluated', publication_binding='unknown', qualified_transaction_count=None, quarantined_row_count=None, reason_codes=['request_required']))
    bundle = build(payload, generated_at=NOW, allow_production=True)
    attached = attach_disclosure_status(bundle, sources, transactions=payload['transactions'], provenance=PROVENANCE)
    return (payload, bundle, attached)
