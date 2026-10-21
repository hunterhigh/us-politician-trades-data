"""Reconsume fixed saved account observations through the normal pipeline; no OCR."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch


def load(path):return json.loads(path.read_text(encoding='utf8'))
def write(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding='utf8')
def compact(value):return ''.join(value.split()) if isinstance(value,str) else value


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('output-root','review-root','evidence-root','grid-input','word-input','previous-candidate','state'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--code-root',type=Path,default=Path(__file__).resolve().parents[2])
    args=p.parse_args();sys.path.insert(0,str(args.code_root/'backend/src'))
    from unison_snapshot.senate_paper import extract_archived_paper_reports
    from unison_snapshot.senate_candidate import build_senate_candidate
    from unison_snapshot.disclosure_status_senate import build_senate_status
    from unison_snapshot.disclosure_status import attach_disclosure_status
    from unison_snapshot.builder import build
    out=args.output_root
    if out.exists() and any(out.iterdir()):p.error('output-root must be empty; choose short Windows path')
    out.mkdir(parents=True,exist_ok=True);review=out/'r';shutil.copytree(args.review_root,review)
    grids,words=load(args.grid_input),load(args.word_input);before={}
    for doc in grids:
        paths=list((review/'senate_efd/extractions'/doc).glob('*/*.json'))
        if len(paths)!=1:raise ValueError('expected exactly one fixed baseline extraction')
        before[doc]=load(paths[0])
    with patch('unison_snapshot.senate_paper.tesseract_words',side_effect=AssertionError('new OCR forbidden')):
        batch=extract_archived_paper_reports(args.evidence_root,review,grid_observations=grids,
            cached_word_pages=words,selected_document_ids=sorted(grids))
    if batch['failure_count'] or len(batch['extractions'])!=len(grids):raise ValueError(batch['failures'])
    comparisons=[]
    for new in batch['extractions']:
        old=before[new['document_id']];oldrows={r['extraction_id']:r for r in old['transactions']};newrows={r['extraction_id']:r for r in new['transactions']}
        if set(oldrows)!=set(newrows):raise ValueError('source row IDs changed')
        changes=[]
        for identifier,row in newrows.items():
            prev=oldrows[identifier]
            for key in set(prev)|set(row):
                if prev.get(key)==row.get(key):continue
                if key in {'asset_name_raw','account_heading_raw'} and compact(prev.get(key))==compact(row.get(key)):
                    changes.append({'id':identifier,'field':key,'before':prev.get(key),'after':row.get(key)})
                elif key=='account_heading_source_slot':
                    cleaned={k:v for k,v in row[key].items() if k!='spacing_interpretation'}
                    if cleaned!=prev[key]:raise ValueError('account slot changed')
                elif key=='field_observations':
                    a,b=deepcopy(prev[key]),deepcopy(row[key]);a.pop('cached_word_pages_sha256',None);b.pop('cached_word_pages_sha256',None)
                    if a!=b:raise ValueError('raw observations changed')
                else:raise ValueError('unrelated source field changed: '+key)
        for key in ['observed_physical_slot_count','logical_row_count','coverage_unknown_pages','linked_data_slot_count']:
            if old['source_report_ledger'][key]!=new['source_report_ledger'][key]:raise ValueError('coverage/row count changed')
        comparisons.append({'document_id':new['document_id'],'rows':len(newrows),'changes':changes,
            'heading_roles':new.get('account_heading_role_interpretations',[]),
            'before_dispositions':old['source_report_ledger']['disposition_counts'],
            'after_dispositions':new['source_report_ledger']['disposition_counts']})
        write(review/'senate_efd/extractions'/new['document_id']/new['source_sha256']/(new['parser_version']+'.json'),new)
    previous=load(args.previous_candidate);state=load(args.state)
    candidate,audit=build_senate_candidate(review,state,previous,evidence_root=args.evidence_root)
    oldfacts={r['id']:r for r in previous['transactions']};facts={r['id']:r for r in candidate['transactions']}
    if set(facts)!=set(oldfacts):raise ValueError('formal IDs changed')
    changed=[]
    for identifier,row in facts.items():
        prev=oldfacts[identifier]
        if row==prev:continue
        left,right=deepcopy(prev),deepcopy(row);a,b=left.pop('asset_name'),right.pop('asset_name')
        if left!=right or compact(a)!=compact(b):raise ValueError('formal non-whitespace field changed')
        changed.append({'id':identifier,'before':a,'after':b})
    write(review/'senate_efd/qualifications/current.json',audit)
    scopes=build_senate_status(args.evidence_root,review,candidate['transactions'],[])
    bundle=build(candidate,generated_at=state['run_at'],allow_production=True)
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=args.code_root,text=True).strip()
    attach_disclosure_status(bundle,scopes,transactions=candidate['transactions'],provenance={
        'code_commit':commit,'evidence_commit':'80f086267677b8fde34314f24024bdc505d97cb0',
        'review_commit':'869375f8c41571b5ff76ba701cf0a2dcea5afb0f','previous_main_commit':'24c3616977625ef3bbac0b2f5888c9572f396248'})
    again,_=build_senate_candidate(review,state,candidate,evidence_root=args.evidence_root)
    if again['transactions']!=candidate['transactions']:raise ValueError('repeat changed formal facts')
    files=['backend/src/unison_snapshot/'+x+'.py' for x in ['senate_paper','senate_paper_accounts','senate_candidate']]
    receipt={'code_commit':commit,'code_sha256':{f:hashlib.sha256((args.code_root/f).read_bytes()).hexdigest() for f in files},
        'scope':'existing fixed sources and saved observations; not whole-catalog or production','new_ocr_calls':0,
        'source_comparisons':comparisons,'candidate_rows':len(facts),'original_formal_ids_equal':True,
        'unchanged_formal_objects':len(facts)-len(changed),'whitespace_only_formal_changes':changed,
        'repeat_formal_equal':True,'status_builder_attach':'passed','snapshot_id':bundle.manifest['snapshot_id']}
    for name,value in [('batch',batch),('candidate',candidate),('audit',audit),('status',scopes),('receipt',receipt)]:write(out/(name+'.json'),value)
    print(json.dumps({k:v for k,v in receipt.items() if k not in ['source_comparisons','code_sha256','whitespace_only_formal_changes']}))


if __name__=='__main__':main()
