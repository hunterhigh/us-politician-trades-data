"""Apply source-bound reviewed rows through the shared correction engine."""
from ..content_integrity import content_boundary
from pathlib import Path
import re

from ..corrections import load_correction

PROFILE = 'reviewed-senate-paper/v1'
DOCUMENT = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
CORRECTIONS = Path(__file__).resolve().parents[3]/'data/corrections/senate_efd'


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
