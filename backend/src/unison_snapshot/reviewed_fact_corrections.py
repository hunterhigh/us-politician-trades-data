"""Asserted changes to existing facts after independent original-source proof."""
from pathlib import Path

from .codec import encode, digest
from .corrections import apply_correction, load_correction

ROOT = Path(__file__).resolve().parents[2] / 'data/corrections/published_facts'
PROFILE = 'reviewed-published-fact/v1'


def correction_for(report):
    document = report.get('document_id')
    if not isinstance(document, str):
        return None
    name = document.removeprefix('wh-url:')
    if not name or any(c not in '0123456789abcdef' for c in name):
        return None
    path = ROOT / (name + '.json')
    if not path.is_file():
        return None
    correction = load_correction(path)
    if (correction.get('profile') != PROFILE or
            correction['document_id'] != document or
            correction['source_url'] != report.get('source_url') or
            correction['source_sha256'] != report.get('source_sha256')):
        raise ValueError('Published fact correction source binding changed')
    if any(a['op'] != 'set_field' or a.get('field') != 'instrument_type' or
            a.get('expect') != 'Bond' or a.get('value') != 'Stock'
            for a in correction['actions']):
        raise ValueError('Published fact correction contains an unsupported field or value')
    rows = report.get('transactions', [])
    for identifier, expected in correction['assertions']['source_rows'].items():
        selected = [r for r in rows if r.get('extraction_id') == identifier]
        if len(selected) != 1 or any(selected[0].get(k) != v for k, v in expected.items()):
            raise ValueError('Published fact correction original row changed')
    return correction


def correction_dependencies(reports):
    values = [value for report in reports if (value := correction_for(report)) is not None]
    return digest(encode(sorted(values, key=lambda c: c['document_id'])))


def apply_verified_fact_correction(report, row, prior, verified):
    """Data cannot authorize a change that the normal source proof rejects."""
    correction = correction_for(report)
    if correction is None or prior['id'] not in correction['assertions']['row_inventory']:
        return verified
    guards = correction['assertions']
    if digest(encode(verified)) != guards['output_sha256'][prior['id']]:
        raise ValueError('Published fact correction differs from verified source output')
    if prior == verified:
        return verified
    if digest(encode(prior)) not in guards['predecessor_sha256'][prior['id']]:
        raise ValueError('Published fact correction predecessor changed')
    actions = [a for a in correction['actions'] if a.get('row') == prior['id']]
    model = {k: correction[k] for k in ('source', 'document_id', 'source_sha256')}
    model.update(report={}, rows={prior['id']: prior})
    corrected = apply_correction(model, {**correction, 'actions': actions})['rows'][prior['id']]
    if any(corrected[a['field']] != verified[a['field']] for a in actions):
        raise ValueError('Published fact correction conflicts with original source proof')
    # The existing owner policy has its own independent proof. This correction
    # changes only the field explicitly named in its asserted actions.
    return {**verified, **{a['field']: corrected[a['field']] for a in actions}}
