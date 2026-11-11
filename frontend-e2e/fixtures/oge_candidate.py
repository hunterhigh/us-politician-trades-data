"""Existing browser-suite fixtures, moved from the retired backend unit modules."""
from unison_snapshot.oge_reports import PARSER_VERSION, EXTRACTION_SCHEMA
DOCUMENT_ID = '42300720a4227e9e85258e77002dd1b3'
URL = f'https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/{DOCUMENT_ID}/$FILE/Example-278T.pdf'

def direct(amended=None, *, agency='Example Agency'):
    return {'catalog_index': 0, 'catalog_added_date': '2025-06-04', 'document_type': '278_transaction', 'filer_name': 'Example, Ada', 'agency': agency, 'position_title': 'Director', 'level': 'n/a', 'amended_label': amended, 'pending_final_oge_disposition': False, 'access_method': 'direct_pdf', 'source_document_id': DOCUMENT_ID, 'document_url': URL, 'source_id': 'oge'}

def catalog(row=None):
    return {'schema_version': 'oge-disclosure-catalog/v1', 'source_id': 'oge', 'source_url': 'https://www.oge.gov/example', 'records_total': 1, 'catalog_rows_covered': 1, 'transactions': [row or direct()]}

def extraction():
    return {'schema_version': EXTRACTION_SCHEMA, 'parser_version': PARSER_VERSION, 'source_id': 'oge', 'document_id': DOCUMENT_ID, 'source_url': URL, 'source_sha256': 'a' * 64, 'catalog_filer_name': 'Example, Ada', 'agency': 'Example Agency', 'position_title': 'Director', 'catalog_added_date': '2025-06-04', 'filed_at': '2025-06-03', 'document_reasons': [], 'evidence_complete': True, 'quarantined': [], 'transactions': [{'extraction_id': 'oge-278t:' + 'b' * 24, 'page_number': 1, 'row_number': 1, 'owner': 'Self', 'asset_name': 'Example Inc.', 'ticker': 'EXM', 'transaction_type_raw': 'Purchase', 'transaction_type': 'purchase', 'transaction_date': '2025-05-01', 'late_notification_raw': None, 'amount_raw': '$1,001 - $15,000', 'amount_low': 1001, 'amount_high': 15000}]}

def base():
    return {'meta': {'is_demo': False, 'data_cutoff_at': '2025-06-04T00:00:00Z', 'title': 'test', 'subtitle': 'test'}, 'people': [], 'transactions': [], 'reported_holdings': [], 'security_market_data': [], 'source_health': []}
