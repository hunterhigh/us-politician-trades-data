"""Source-bound reviewed table corrections before normal OGE qualification."""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import re

from .codec import digest,encode
from .corrections import apply_correction,load_correction
from .oge import OgeCatalogError

PROFILE = 'reviewed-six-column-extraction/v1'
ROOT = Path(__file__).resolve().parents[2]/'data/corrections/oge'


def correction_for(document):
    if not isinstance(document,str) or not re.fullmatch('[0-9a-f]{32}',document):
        return None
    path=ROOT/(document+'.json')
    if not path.is_file():return None
    correction=load_correction(path)
    if correction.get('profile') != PROFILE:return None
    if correction['source']!='oge' or correction['document_id']!=document:
        raise OgeCatalogError('Reviewed extraction correction identity changed')
    return correction


@lru_cache(maxsize=32)
def _binding_file(path,mtime,size):
    correction=load_correction(path)
    if correction.get('profile')!=PROFILE:return None
    guards=correction['assertions']
    return {**{k:correction[k] for k in ('document_id','source_sha256','parser_version','parser_versions')},
        'metadata':guards['metadata'],'filing_date_evidence':guards['filing_date_evidence'],
        'person':guards['person']}


def binding_for(document):
    if not isinstance(document,str) or not re.fullmatch('[0-9a-f]{32}',document):return None
    path=ROOT/(document+'.json')
    if not path.is_file():return None
    stat=path.stat()
    return _binding_file(str(path),stat.st_mtime_ns,stat.st_size)


def source_correction(metadata,source_sha):
    correction=correction_for(metadata.get('document_id'))
    if correction is None:return None
    if (correction['source_sha256']!=source_sha or
            any(metadata.get(k)!=v for k,v in correction['assertions']['metadata'].items())):
        raise OgeCatalogError('Reviewed extraction correction source hash or metadata mismatch')
    return correction


def profiles():
    return [c for p in sorted(ROOT.glob('*.json')) if (c:=correction_for(p.stem)) is not None]


def required_publication_sources():
    return [c for c in profiles() if c.get('publication_requirement') is True]


def required_publication_source(row):
    return any(row.get('filing_id')==c['document_id'] and
        row.get('source_url')==c['assertions']['metadata']['document_url'] and
        isinstance(row.get('id'),str) and row['id'].startswith('oge-278t:')
        for c in required_publication_sources())


def parser_versions(document=None):
    selected=profiles() if document is None else [c] if (c:=correction_for(document)) else []
    return {v for c in selected for v in c['parser_versions']}


def version_priority(document,base_version):
    correction=correction_for(document)
    return {base_version:0,**{v:i+1 for i,v in enumerate(correction['parser_versions'])}} if correction else {}


def fingerprint(extraction):
    return digest(encode(extraction))


def approved_extraction(extraction):
    correction=correction_for(extraction.get('document_id'))
    if not correction or extraction.get('source_sha256')!=correction['source_sha256']:
        return False
    expected=set(correction['assertions']['approved_output_sha256'])
    expected.update(h for v,h in correction['assertions']['legacy_versions'].items() if v!=correction['parser_version'])
    return fingerprint(extraction) in expected


def replay_base_checkpoint(reuse_root,correction):
    version=correction['base_parser_version']
    if not re.fullmatch('[a-z0-9-]+/v[0-9]+',version):
        raise OgeCatalogError('Reviewed extraction base parser location is invalid')
    path=Path(reuse_root)/correction['document_id']/correction['source_sha256']/(version.replace('/','-')+'.json')
    if not path.is_file():return None
    if path.stat().st_size>50*1024*1024:return None
    try:return correct_extraction(json.loads(path.read_text(encoding='utf-8')),correction)
    except (OgeCatalogError,OSError,ValueError,TypeError,KeyError):return None


def _base_fingerprint(base):
    # Legacy checkpoints predate additive source-cell inventories. Those
    # optional proof fields are retained in the result, never normalized facts.
    value=deepcopy(base)
    value.pop('source_rows',None)
    for row in value['transactions']:row.pop('cells',None)
    return digest(encode(value))


def correct_extraction(base,correction):
    guards=correction['assertions']
    if (correction.get('profile')!=PROFILE or base.get('source_id')!='oge' or
            base.get('document_id')!=correction['document_id'] or base.get('source_sha256')!=correction['source_sha256'] or
            base.get('parser_version')!=correction['base_parser_version'] or
            _base_fingerprint(base)!=guards['input_facts_sha256']):
        raise OgeCatalogError('Reviewed extraction original parser precondition changed')
    rows={}
    for disposition,key in [('transaction','transactions'),('quarantined','quarantined')]:
        for row in base[key]:
            identity=row.get('extraction_id')
            if not isinstance(identity,str) or identity in rows:
                raise OgeCatalogError('Reviewed extraction original row identity is ambiguous')
            rows[identity]={'record':deepcopy(row),'disposition':disposition}
    if list(rows)!=guards['input_row_inventory']:
        raise OgeCatalogError('Reviewed extraction original row inventory changed')
    report_fields={'parser_version','filed_at','evidence_complete','document_reasons','filing_date_evidence'}
    for action in correction['actions']:
        if action['op']=='set_report' and action.get('field') not in report_fields:
            raise OgeCatalogError('Reviewed extraction report correction cannot rewrite source identity')
        if action['op']=='set_field':
            if action.get('field') not in {'record','disposition'}:
                raise OgeCatalogError('Reviewed extraction correction target field is unsupported')
            if action['field']=='record' and any(action['expect'].get(k)!=action['value'].get(k)
                    for k in ('extraction_id','page_number','cells','owner','transaction_type_raw','amount_raw')):
                raise OgeCatalogError('Reviewed extraction correction cannot rewrite its original source cells or ID')
    model={'source':'oge','document_id':correction['document_id'],'source_sha256':correction['source_sha256'],
        'report':{k:deepcopy(v) for k,v in base.items() if k not in {'transactions','quarantined','source_rows'}},'rows':rows}
    try:corrected=apply_correction(model,correction)
    except (KeyError,ValueError,TypeError) as error:
        raise OgeCatalogError('Reviewed extraction correction precondition failed: '+str(error)) from error
    transaction_ids,quarantine_ids=guards['output_transactions'],guards['output_quarantined']
    if (len(set(transaction_ids+quarantine_ids))!=len(transaction_ids+quarantine_ids) or
            set(transaction_ids+quarantine_ids)!=set(corrected['rows'])):
        raise OgeCatalogError('Reviewed extraction output rows do not conserve')
    proof='source_rows' in base
    if proof:
        for action in correction['actions']:
            if action['op']=='add_row' and 'raw_cells' in action['evidence']:
                corrected['rows'][action['row']]['record']['cells']=deepcopy(action['evidence']['raw_cells'])
    result=corrected['report']
    for kind,ids,disposition in [('transactions',transaction_ids,'transaction'),('quarantined',quarantine_ids,'quarantined')]:
        result[kind]=[]
        for identity in ids:
            row=corrected['rows'][identity]
            if row['disposition']!=disposition or row['record'].get('extraction_id')!=identity:
                raise OgeCatalogError('Reviewed extraction row disposition or stable ID changed')
            result[kind].append(row['record'])
    if proof:
        result['source_rows']=[*deepcopy(base['source_rows']),*[
            {'page_number':r['page_number'],'cells':r['cells'],'source_bound_row_recovery':r['source_bound_row_recovery']}
            for r in result['transactions'] if r.get('source_bound_row_recovery')]]
    if fingerprint(result) not in guards['approved_output_sha256']:
        raise OgeCatalogError('Reviewed extraction differs from its approved source output')
    return result


def source_binding(extraction,record):
    correction=binding_for(extraction.get('document_id'))
    if not record or not correction:return False
    metadata=correction['metadata']
    matches = (record.get('source_document_id')==correction['document_id'] and
        extraction.get('source_sha256')==correction['source_sha256'] and
        extraction.get('source_url')==metadata['document_url'] and
        extraction.get('filed_at')==correction['filing_date_evidence']['filed_at'] and
        extraction.get('filing_date_evidence')==correction['filing_date_evidence'] and
        all(record.get(k)==metadata[k] for k in ('document_url','filer_name','agency','position_title')) and
        all(extraction.get(k)==metadata[k] for k in ('agency','position_title')) and
        extraction.get('catalog_filer_name')==metadata['filer_name'])
    return matches and approved_extraction(extraction)


def person_for(extraction):
    correction=binding_for(extraction.get('document_id'))
    return deepcopy(correction['person']) if correction else None


def recovery_is_bound(extraction,row):
    correction=correction_for(extraction.get('document_id'))
    if not correction or extraction.get('source_sha256')!=correction['source_sha256']:return False
    return any(a['op']=='add_row' and a['value']['record'].get('source_bound_row_recovery')==row.get('source_bound_row_recovery')
        and a['evidence'].get('raw_cells')==row.get('cells') and a['value']['record']['page_number']==row.get('page_number')
        for a in correction['actions'])
