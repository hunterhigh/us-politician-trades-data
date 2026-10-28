"""Apply data-driven reviews to printed House PTRs before normal candidate use."""
from copy import deepcopy
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import re

from .corrections import apply_correction, load_correction
from .house_ptr import DETERMINISTIC_IDENTITY_BASES
from .house_ptr_amount_header import evaluate_amount_header_pages
from .house_ptr_fixed_source_core import canonical_json, canonical_sha256, check_source_cells, disclosure_transaction
from .house_ptr_paper_qualification import qualify_paper_observation
from .house_ptr_paper_temporal import validate_paper_source_metadata


def qualify_reviewed_paper(pdf_bytes, prior_pdf_bytes, metadata, prior_metadata,
        observation, amount_header, identity, current, correction):
    guards, review = correction['assertions'], correction['source_review']
    if correction.get('profile') != 'reviewed-printed-house-ptr/v1' or correction['source'] != 'house_clerk':
        raise ValueError('Unsupported reviewed paper correction profile')
    document_id, source_sha, filer, district = validate_paper_source_metadata(metadata)
    prior_id, prior_sha, prior_filer, prior_district = validate_paper_source_metadata(prior_metadata)
    if (document_id != correction['document_id'] or source_sha != correction['source_sha256'] or
            hashlib.sha256(pdf_bytes).hexdigest() != source_sha or
            prior_id != review['prior_document_id'] or prior_sha != review['prior_source_sha256'] or
            hashlib.sha256(prior_pdf_bytes).hexdigest() != prior_sha or
            (filer, district) != (prior_filer, prior_district) or
            metadata['filed_date'] != guards['filed_date'] or
            prior_metadata['filed_date'] != review['prior_filed_date']):
        raise ValueError('Reviewed paper official source or prior relation changed')
    import pdfplumber
    with pdfplumber.open(io.BytesIO(prior_pdf_bytes)) as pdf:
        if len(pdf.pages) != review['prior_page_count']:
            raise ValueError('Reviewed paper prior page count changed')
    if (canonical_sha256(review) != guards['source_review_sha256'] or
            canonical_sha256(observation) != review['observation_sha256'] or
            review['source_sha256'] != source_sha or review['document_id'] != document_id):
        raise ValueError('Reviewed paper source review is stale')
    if (identity.get('document_id') != document_id or identity.get('person_id') != guards['person_id'] or
            identity.get('match_basis') not in DETERMINISTIC_IDENTITY_BASES or
            identity.get('status') != 'matched_automatically'):
        raise ValueError('Reviewed paper filer identity changed')
    checked = evaluate_amount_header_pages(source_sha, amount_header['pages'])
    if (checked != amount_header or checked.get('status') != 'printed_header_correlated' or
            checked.get('printed_a_variant') != guards['printed_a_variant']):
        raise ValueError('Reviewed paper amount legend changed')
    expected = qualify_paper_observation(observation, metadata, identity, amount_header=amount_header)
    if current.get('transactions'):
        # Recompute from the bound original and saved observations; a prior
        # qualification flag never grants authority on its own.
        replayed = qualify_reviewed_paper(pdf_bytes, prior_pdf_bytes, metadata, prior_metadata,
            observation, amount_header, identity, expected, correction)
        if current != replayed:
            raise ValueError('Reviewed paper existing qualification differs from source replay')
        return replayed
    if (canonical_sha256(expected['quarantined']) != review['qualification_raw_sha256'] or
            any(current.get(k) != expected.get(k) for k in ('quarantined', 'transactions', 'source_sha256',
                'identity', 'document_disposition', 'pending_transaction_projection'))):
        raise ValueError('Reviewed paper pending qualification changed')
    slots = check_source_cells(pdf_bytes, observation, page_count=review['reviewed_page_count'])
    counts = review['physical_counts']
    if (len(slots) != sum(counts.values()) or
            [sum(row['disposition'] == 'data_candidate' for row in page['rows']) for page in observation['pages']]
                != review['target_page_data_counts'] or
            expected['document_disposition']['physical_counts'] !=
                {'data_candidate': counts['data_candidate'], 'unknown': counts['section_heading'], 'blank': counts['blank']} or
            len(expected['structural_observations']) != counts['section_heading'] or
            len(expected['quarantined']) != review['expected_eligible_count']):
        raise ValueError('Reviewed paper physical row census changed')
    rows = {}
    raw_rows = {}
    for row in expected['quarantined']:
        key = row['evidence']['page'], row['evidence']['physical_band_index']
        raw = row['raw_fields']; amount = row.get('amount_range_observation') or {}
        letter = raw.get('amount_letter')
        if (slots[key]['disposition'] != 'data_candidate' or
                raw.get('direction_mark') not in {'purchase', 'sale'} or
                raw.get('direction_status') != 'single_strong_mark_unverified' or
                raw.get('amount_status') != 'single_strong_mark_unverified' or
                letter not in checked['ranges'] or amount.get('kind') != 'range' or
                any(amount.get(k) != checked['ranges'][letter][k] for k in ('low', 'high')) or
                not isinstance(raw.get('asset_raw'), str) or not raw['asset_raw'].strip() or
                raw.get('event_date_status') not in {'ocr_date_shape_unverified', 'ocr_date_shape_with_edge_noise_unverified'} or
                raw.get('owner_code') not in {None, *guards['owner_codes']}):
            raise ValueError('Reviewed paper source field is not reviewable')
        event = date.fromisoformat(raw['event_date'])
        if event.strftime('%Y-%m') != review['target_data_event_month']:
            raise ValueError('Reviewed paper transaction date outside reviewed month')
        target = f'p{key[0]}#{key[1]}'
        if target in rows:
            raise ValueError('Reviewed paper has duplicate transaction slots')
        rows[target] = {'asset_name': raw['asset_raw'], 'account_title': None,
            'owner': guards['owner_codes'].get(raw.get('owner_code'))}
        raw_rows[target] = row
    if list(rows) != guards['row_inventory']:
        raise ValueError('Reviewed paper physical row identity changed')
    for action in correction['actions']:
        if action['op'] != 'set_field' or action.get('field') not in {'asset_name', 'account_title', 'owner'}:
            raise ValueError('This paper profile only permits reviewed display and owner corrections')
        evidence = action['evidence']; cell = slots.get((evidence.get('page'), evidence.get('band')), {}).get('cells', {}).get(evidence.get('cell'))
        if not isinstance(cell, dict) or cell.get('crop_sha256') != evidence.get('crop_sha256'):
            raise ValueError('Reviewed paper correction is not bound to a source cell')
        target = action.get('row')
        if target not in raw_rows:
            raise ValueError('Reviewed paper correction target is unknown')
        raw_row = raw_rows[target]
        if action['field'] == 'account_title':
            section_key = raw_row.get('account_section_observation', {}).get('section_key')
            if section_key != f"paper-section:{source_sha}:{evidence['page']}:{evidence['band']}":
                raise ValueError('Reviewed paper account heading does not own this row')
        elif (raw_row['evidence']['page'], raw_row['evidence']['physical_band_index']) != (evidence['page'], evidence['band']):
            raise ValueError('Reviewed paper field evidence points at another row')
    corrected = apply_correction({'source': 'house_clerk', 'document_id': document_id,
        'source_sha256': source_sha, 'rows': rows, 'report': {}}, correction)
    transactions = []
    for target, row in raw_rows.items():
        fields = corrected['rows'][target]
        if not fields['account_title'] or fields['owner'] not in {'Self', 'Spouse', 'Dependent Child', 'Joint', 'Undisclosed'}:
            raise ValueError('Reviewed paper has unresolved owner or account title')
        raw = row['raw_fields']; evidence = row['evidence']
        transactions.append(disclosure_transaction(evidence['page'], evidence['physical_band_index'],
            raw['event_date'], fields['asset_name'], fields['account_title'], raw['direction_mark'],
            row['amount_range_observation'], metadata['source_url'], document_id=document_id,
            source_sha256=source_sha, person_id=identity['person_id'], owner=fields['owner'],
            filed_at=metadata['filed_date'] + 'T00:00:00Z'))
    count = review['expected_eligible_count']
    if len(transactions) != count or len({r['id'] for r in transactions}) != count:
        raise ValueError('Reviewed paper transaction rows or IDs do not conserve')
    result = deepcopy(current)
    result.update(transactions=transactions, quarantined=[], source_review_sha256=guards['source_review_sha256'])
    result.pop('pending_transaction_projection', None)
    result.pop('temporal_relation_observation', None)
    result['document_disposition']['status'] = 'paper_source_bound_complete'
    result['qualification'] = {'method': 'fixed_source_visual_review_complete_v2', 'status': 'qualified_rows',
        'qualified_count': count, 'quarantined_count': 0, 'advisory_count': len(result.get('advisories', [])),
        'zero_transaction_document_count': 0, 'document_reasons': [], 'production_eligible': True}
    return result


def qualify_reviewed_paper_paths(evidence_root, review_root, correction_path):
    correction = load_correction(correction_path)
    if not re.fullmatch(r'\d{1,12}', correction['document_id']) or type(correction.get('filing_year')) is not int:
        raise ValueError('Invalid House correction location')
    source_dir = Path(evidence_root).resolve() / 'house_clerk/documents'
    review_dir = Path(review_root).resolve() / 'house_clerk'
    year, document, sha = correction['filing_year'], correction['document_id'], correction['source_sha256']
    prior = correction['source_review']['prior_document_id']
    if not re.fullmatch(r'\d{1,12}', prior):
        raise ValueError('Invalid House correction predecessor')
    prior_paths = list(source_dir.glob(f'{year}/{prior}/*.json'))
    if len(prior_paths) != 1:
        raise ValueError('Reviewed paper prior official document is not unique')
    suffix = f'{year}/{document}/{sha}'
    read = lambda p: json.loads(p.read_text(encoding='utf-8'))
    result = qualify_reviewed_paper((source_dir / (suffix + '.pdf')).read_bytes(),
        prior_paths[0].with_suffix('.pdf').read_bytes(), read(source_dir / (suffix + '.json')),
        read(prior_paths[0]), read(review_dir / 'paper_observations' / (suffix + '.json')),
        read(review_dir / 'paper_amount_headers' / (suffix + '.json')),
        read(review_dir / 'identities' / (suffix + '.json')),
        read(review_dir / 'qualifications' / (suffix + '.json')), correction)
    target = review_dir / 'qualifications' / (suffix + '.json')
    temporary = target.with_suffix('.json.tmp')
    temporary.write_bytes(canonical_json(result))
    temporary.replace(target)
    return {'document_id': document, 'qualified_count': len(result['transactions']), 'quarantined_count': len(result['quarantined'])}
