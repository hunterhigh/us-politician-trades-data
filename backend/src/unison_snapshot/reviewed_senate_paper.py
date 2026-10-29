"""Apply source-bound reviewed rows through the shared correction engine."""
from .content_integrity import content_boundary
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re

from .codec import digest, encode
from .corrections import apply_correction, load_correction
from .senate_paper import PAPER_EXTRACTION_SCHEMA, PAPER_OCR_RULE_VERSION
from .senate_reports import PARSER_VERSION

PROFILE = 'reviewed-senate-paper/v1'
DOCUMENT = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
CORRECTIONS = Path(__file__).resolve().parents[2]/'data/corrections/senate_efd'


def _key(slot):
    return f"p{slot['page_number']}#{slot['grid_slot']}"


def _inside(root, relative):
    if (not isinstance(relative, str) or '\\' in relative or ':' in relative or
            PurePosixPath(relative).is_absolute() or any(p in {'','..','.'} for p in relative.split('/'))):
        raise ValueError('Reviewed Senate source path is not canonical')
    path = (root/relative).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('Reviewed Senate source path escapes evidence')
    return path


def _source_pages(evidence_root, manifest, guards):
    from PIL import Image
    root = Path(evidence_root).resolve(strict=True)
    pages, hashes = {}, {}
    for page in manifest['pages']:
        number = page['page_number']
        if number in pages:
            raise ValueError('Reviewed Senate page manifest repeats a page')
        raw = _inside(root,page['archive_path']).read_bytes()
        if digest(raw) != page['sha256']:
            raise ValueError('Reviewed Senate original page hash changed')
        with Image.open(BytesIO(raw)) as image:
            pages[number] = image.convert('L')
        hashes[number] = page['sha256']
    if sorted(pages) != list(range(1,manifest['page_count']+1)):
        raise ValueError('Reviewed Senate original page sequence changed')
    checked = set()
    for crop in guards['page_crops']:
        image = pages[crop['page']]
        x0,y0,x1,y1 = crop['box_px']
        if not 0 <= x0 < x1 <= image.width or not 0 <= y0 < y1 <= image.height:
            raise ValueError('Reviewed Senate source crop is outside the original')
        actual = digest(image.crop((x0,y0,x1,y1)).tobytes())
        if actual != crop['grayscale_sha256']:
            raise ValueError('Reviewed Senate source crop changed')
        checked.add((crop['page'],actual))
    for limit in guards['pixel_limits']:
        image = pages[limit['page']]
        x0,y0,x1,y1 = limit['box_px']
        if not 0 <= x0 < x1 <= image.width or not 0 <= y0 < y1 <= image.height:
            raise ValueError('Reviewed Senate source assertion is outside the original')
        if sum(p < limit['dark_below'] for p in image.crop((x0,y0,x1,y1)).tobytes()) > limit['max_dark_pixels']:
            raise ValueError('Reviewed Senate source pixel assertion changed')
    return hashes,checked


def qualify_reviewed_senate_paper(batch, pending, manifest, evidence_root, correction):
    guards = correction['assertions']
    document, source_sha = correction['document_id'],correction['source_sha256']
    template = guards['report']
    if (correction.get('profile') != PROFILE or correction['source'] != 'senate_efd' or
            not DOCUMENT.fullmatch(document) or batch.get('parser_version') != PARSER_VERSION or
            pending.get('schema_version') != 'senate-paper-pending-slot-projection/v3' or
            digest(encode(correction['source_review'])) != guards['source_review_sha256'] or
            digest(encode(manifest)) != guards['manifest_sha256'] or
            manifest.get('document_id') != document or manifest.get('entrypoint_source_sha256') != source_sha or
            manifest.get('evidence_complete') is not True or template.get('document_id') != document or
            template.get('source_sha256') != source_sha or template.get('parser_version') != PARSER_VERSION or
            template.get('schema_version') != PAPER_EXTRACTION_SCHEMA or
            template.get('paper_rule_version') != PAPER_OCR_RULE_VERSION or
            template.get('source_url') != f'https://efdsearch.senate.gov/search/view/paper/{document}/'):
        raise ValueError('Reviewed Senate source binding or saved assertions changed')
    if (batch.get('extraction_count') != len(batch['extractions']) or batch.get('failure_count') != len(batch['failures']) or
            batch['report_count'] != len(batch['extractions'])+len(batch['failures'])):
        raise ValueError('Reviewed Senate batch dispositions do not conserve')
    page_hashes, checked_crops = _source_pages(evidence_root,manifest,guards)
    pending_rows = {}
    for row in pending['pending_rows']:
        slot = row['source_slot']
        if slot['document_id'] != document:
            continue
        key = _key(slot)
        if (key in pending_rows or slot['entrypoint_source_sha256'] != source_sha or
                slot['page_sha256'] != page_hashes.get(slot['page_number']) or
                (slot['page_number'],slot['row_crop_sha256']) not in checked_crops or
                digest(encode(row)) != guards['pending_row_sha256'].get(key) or
                row.get('legacy_extraction_failed') is not True or row.get('legacy_extraction_report_row_count') != 0):
            raise ValueError('Reviewed Senate pending row precondition changed')
        pending_rows[key] = row
    if list(pending_rows) != guards['row_inventory'] or len(correction['actions']) != len(pending_rows):
        raise ValueError('Reviewed Senate correction row inventory changed')
    for action in correction['actions']:
        if action.get('op') != 'set_field' or action.get('field') != 'qualified_record':
            raise ValueError('Reviewed Senate profile permits only source-bound row qualification')
        key = action.get('row')
        if (key not in pending_rows or action['evidence'].get('source_slot') != pending_rows[key]['source_slot'] or
                action['evidence'].get('pending_sha256') != guards['pending_row_sha256'][key] or
                action.get('value',{}).get('source_slot') != pending_rows[key]['source_slot']):
            raise ValueError('Reviewed Senate correction evidence differs from its original row')
    corrected = apply_correction({'source':'senate_efd','document_id':document,'source_sha256':source_sha,
        'report':{},'rows':{k:{'qualified_record':None} for k in pending_rows}},correction)
    records = [r['qualified_record'] for r in corrected['rows'].values()]
    ids = set()
    for record in records:
        slot = record['source_slot']
        fingerprint = '|'.join((source_sha,str(slot['page_number']),slot['page_sha256'],str(slot['grid_slot']),slot['row_crop_sha256']))
        expected_id = 'senate-ptr:'+hashlib.sha256(fingerprint.encode('ascii')).hexdigest()[:24]
        if (record.get('extraction_id') != expected_id or expected_id in ids or
                record.get('qualification_status') != 'eligible' or record.get('quarantine_reasons') != [] or
                record.get('row_number') != slot['grid_slot'] or record.get('page_number') != slot['page_number']):
            raise ValueError('Reviewed Senate row identity or qualification changed')
        ids.add(expected_id)
    if len(records) != template['eligible_transaction_count'] or template['quarantined_transaction_count'] != 0:
        raise ValueError('Reviewed Senate transaction counts changed')
    extraction = {**deepcopy(template),'page_manifest_sha256':manifest['manifest_sha256'],
        'page_count':manifest['page_count'],'transactions':records}
    existing = [r for r in batch['extractions'] if r['document_id']==document]
    failures = [r for r in batch['failures'] if r['document_id']==document]
    if existing:
        if existing != [extraction] or failures:
            raise ValueError('Reviewed Senate existing qualification differs from original replay')
        return deepcopy(batch)
    if len(failures) != 1 or failures[0].get('source_sha256') != source_sha:
        raise ValueError('Reviewed Senate report has no unique failed disposition')
    result = deepcopy(batch)
    result['extractions'].append(extraction)
    result['failures'] = [r for r in result['failures'] if r['document_id']!=document]
    result['extraction_count'] += 1
    result['failure_count'] -= 1
    result['transaction_count'] += len(records)
    result['eligible_transaction_count'] += len(records)
    return result


@content_boundary('correction_assertion')
def correction_for(document):
    if not isinstance(document,str) or not DOCUMENT.fullmatch(document):
        return None
    path = CORRECTIONS/(document+'.json')
    if not path.is_file():
        return None
    correction = load_correction(path)
    if correction.get('profile') != PROFILE or correction['source'] != 'senate_efd' or correction['document_id'] != document:
        raise ValueError('Reviewed Senate correction identity changed')
    return correction


def reviewed_record(row, extraction, person_id):
    if row.get('qualification_basis') != 'fixed_source_visual_review_v1':
        return False
    correction = correction_for(extraction.get('document_id'))
    if not correction or person_id != correction['assertions']['person_id']:
        return False
    return (extraction.get('source_sha256') == correction['source_sha256'] and
        all(extraction.get(k)==v for k,v in correction['assertions']['report'].items()) and
        any(a['value']==row for a in correction['actions']))


def reviewed_counts():
    return {p.stem:c['assertions']['report']['eligible_transaction_count']
        for p in sorted(CORRECTIONS.glob('*.json')) if (c:=correction_for(p.stem)) is not None}


def qualify_reviewed_senate_paths(*, batch_path, pending_path, correction_path, evidence_root):
    correction = load_correction(correction_path)
    document = correction['document_id']
    if not DOCUMENT.fullmatch(document):
        raise ValueError('Invalid reviewed Senate document location')
    root = Path(evidence_root).resolve(strict=True)
    manifest = json.loads(_inside(root,f'senate_efd/paper_pages/{document}/manifest.json').read_text(encoding='utf-8'))
    read = lambda p:json.loads(Path(p).read_text(encoding='utf-8'))
    return qualify_reviewed_senate_paper(read(batch_path),read(pending_path),manifest,root,correction)
