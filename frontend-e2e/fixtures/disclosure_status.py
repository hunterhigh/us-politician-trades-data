"""Existing browser-suite fixtures, moved from the retired backend unit modules."""
PROVENANCE = {'code_commit': '1' * 40, 'evidence_commit': '2' * 40, 'review_commit': '3' * 40, 'previous_main_commit': '4' * 40}
NOW = '2026-10-16T01:00:00Z'

def record(key, ids=(), **changes):
    return {'record_key': key, 'report_id': key, 'document_type': 'ptr', 'source_url': None, 'catalog_state': 'current', 'acquisition_state': 'archived', 'extraction_state': 'extracted', 'qualification_state': 'qualified', 'qualified_transaction_count': len(ids), 'quarantined_row_count': 0, 'reason_codes': [], 'source_versions': ['a' * 64], 'publication_transaction_ids': list(ids), 'publication_binding': 'verified', **changes}

def scopes(payload):
    return [{'scope_id': source, 'source_id': source, 'scope': {'document_type': 'test PTR fixture'}, 'observed_at': NOW, 'limitations': [], 'reports': [record(source, [r['id'] for r in payload['transactions'] if r['source_id'] == source])], 'unmatched_publication_transaction_ids': []} for source in sorted({r['source_id'] for r in payload['transactions']})]
