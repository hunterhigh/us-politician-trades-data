"""Replay explicitly selected Senate amendment predecessors through normal source commands."""
import argparse
from datetime import date
import json
from pathlib import Path
import re
import subprocess
import sys

from unison_snapshot.codec import encode
from unison_snapshot.commands.data_trees import read_control, publish
from unison_snapshot.commands.assemble_source import assemble

UUID=re.compile('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')


def selected_ids(raw):
    values=raw.split(',')
    if (not raw or len(values)>12 or len(set(values))!=len(values)
            or any(not UUID.fullmatch(value) for value in values)):
        raise ValueError('History replay requires 1-12 unique canonical current amendment UUIDs')
    return values


def current_targets(review, documents):
    audit=json.loads((review/'senate_efd/qualifications/current.json').read_bytes())
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',str(audit.get('parser_version',''))):
        raise ValueError('Current Senate parser path is invalid')
    pending={row['document_id']:row for row in audit['quarantined_reports']
        if 'amendment_relationship_pending' in row['reasons']}
    if not set(documents).issubset(pending):raise ValueError('Requested reports are not current pending amendments')
    for document in documents:
        paths=list((review/'senate_efd/extractions'/document).glob('*/'+audit['parser_version']+'.json'))
        bound=[json.loads(p.read_bytes()) for p in paths if p.parent.name==pending[document]['source_sha256']]
        if (len(bound)!=1 or bound[0]['report_amendment_number']!=1 or
                bound[0]['document_id']!=document or bound[0]['source_sha256']!=pending[document]['source_sha256']
                or bound[0]['parser_version']!=audit['parser_version'] or bound[0].get('evidence_complete') is not True):
            raise ValueError('Requested amendment lacks one bound current first-amendment extraction')
    return audit


def verify_activation(before, after, candidate, supplement, current_documents):
    expected=before['input_transaction_count']
    primary=sum(row.get('filing_id') in current_documents for row in candidate['transactions'])
    if (after['input_transaction_count']!=expected or
            primary+after['quarantined_transaction_count']+after['superseded_report_transaction_count']!=expected or
            after['amendment_supplement_sha256']!=supplement['supplement_sha256'] or
            after['amendment_supplement_report_count']!=supplement['target_count']):
        raise ValueError('Activated amendment supplement does not conserve the fixed current input')
    if any(row.get('filing_id') in set(supplement['selected_document_ids']) for row in candidate['transactions']):
        raise ValueError('Historical predecessor escaped into the candidate transaction set')


def replay(workspace, documents, submitted_start_date, mode, limit, run, base, builder_commit):
    if mode not in {'plan','resolve'} or type(limit) is not int or not 1<=limit<=25:
        raise ValueError('History replay mode or archive bound is invalid')
    date.fromisoformat(submitted_start_date)
    control=read_control(workspace)
    if control['scope']!='senate':raise ValueError('History replay requires Senate-controlled source trees')
    evidence,review,state=(Path(control['trees'][role]['root']) for role in ('evidence','review','state'))
    before=current_targets(review,documents)
    run.mkdir(parents=True,exist_ok=True)
    def path(name):return run/(name+'.json')
    def command(name,*arguments):
        subprocess.run([sys.executable,'-m','unison_snapshot',name,*map(str,arguments)],check=True)
    def read(name):return json.loads(path(name).read_bytes())
    command('senate-efd-gate','--output',path('gate'))
    if read('gate')['status']!='enabled':raise ValueError('Senate source authorization is not enabled')
    command('discover-senate-efd','--archive',evidence,'--submitted-start-date',submitted_start_date,
            '--output',path('discovery'))
    status=json.loads((state/'status/senate_efd.json').read_bytes())
    roster_sha=status['roster']['sha256']
    if not re.fullmatch('[0-9a-f]{64}',roster_sha):raise ValueError('Senate roster hash is invalid')
    identity_arguments=[]
    historical=status.get('historical_roster',{})
    if historical.get('status')=='ok':
        sha=historical['sha256']
        if not re.fullmatch('[0-9a-f]{64}',sha):raise ValueError('Historical identity roster hash is invalid')
        identity_arguments=['--congress-roster',evidence/'senate_efd/congress_members'/sha/'roster.json']
    command('match-senate-catalog','--discovery',path('discovery'),
        '--roster',evidence/'senate_efd/members'/(roster_sha+'.roster.json'),*identity_arguments,
        '--output',path('identities'))
    selection=[]
    for document in documents:selection+=['--document-id',document]
    command('plan-senate-amendment-backfill','--review-root',review,
        '--historical-discovery',path('discovery'),'--historical-identities',path('identities'),
        '--expected-target-count',len(documents),*selection,'--output',path('plan'))
    plan=read('plan');candidates=plan['candidate_document_ids']
    if {row['amendment_document_id'] for row in plan['targets']}!=set(documents):
        raise ValueError('History plan differs from the explicitly selected current reports')
    if (not candidates or len(candidates)>limit or len(candidates)!=len(set(candidates)) or
            any(not UUID.fullmatch(value) for value in candidates) or
            set(plan['reasons'])-{'predecessor_not_unique'}):
        raise ValueError('History predecessor scope is unresolved or exceeds its archive bound')
    fingerprint=plan['plan_sha256']
    if not re.fullmatch('[0-9a-f]{64}',fingerprint):raise ValueError('History plan fingerprint is invalid')
    destination=review/'senate_efd/replay_history'/fingerprint
    def persist(names):
        destination.mkdir(parents=True,exist_ok=True)
        for name in names:
            payload=encode(read(name));target=destination/(name+'.json')
            if target.exists() and target.read_bytes()!=payload:raise ValueError('Immutable replay input collision')
            temporary=target.with_suffix('.next.json');temporary.write_bytes(payload);temporary.replace(target)
    if mode=='plan':
        publish(workspace,('evidence',),'selected Senate history catalog')
        persist(('discovery','identities','plan'))
        publish(workspace,('review',),'selected Senate predecessor plan')
        return {'status':plan['status'],'plan_sha256':fingerprint,'target_count':len(documents)}
    path('candidate-ids').write_bytes(encode(candidates))
    command('archive-senate-report-entrypoints','--discovery',path('discovery'),
        '--document-ids',path('candidate-ids'),'--archive',evidence,'--limit',len(candidates),'--output',path('reports'))
    batch=read('reports')
    if (batch['selected_record_count'],batch['archived_total'],batch['pending_count'],batch['failure_count'])!=(len(candidates),len(candidates),0,0):
        raise ValueError('Selected historical originals were not completely archived')
    publish(workspace,('evidence',),'selected Senate amendment predecessors')
    command('extract-senate-report-entrypoints','--batch',path('reports'),'--archive',evidence,
        '--output',path('extractions'))
    batch=read('extractions')
    if (batch['extraction_count'],batch['inspection_count'],batch['failure_count'])!=(len(candidates),0,0):
        raise ValueError('Selected predecessor bodies did not parse completely')
    command('resolve-senate-amendment-backfill','--review-root',review,'--plan',path('plan'),
        '--historical-extractions',path('extractions'),'--output',path('supplement'))
    supplement=read('supplement')
    if supplement['status']!='ready' or supplement['target_count']!=len(documents):
        raise ValueError('Selected amendments do not have unique content-matched predecessors')
    command('activate-senate-amendment-supplement','--review-root',review,'--supplement',path('supplement'),
        '--historical-extractions',path('extractions'),'--output',path('pointer'))
    candidate=run/'candidate.json';audit=run/'qualification.json'
    command('build-senate-candidate','--evidence-root',evidence,'--review-root',review,
        '--state-status',state/'status/senate_efd.json','--base',base,'--output',candidate,'--audit-output',audit)
    parser=before['parser_version']
    current_documents={p.parent.parent.name for p in (review/'senate_efd/extractions').glob('*/*/'+parser+'.json')}
    verify_activation(before,json.loads(audit.read_bytes()),json.loads(candidate.read_bytes()),supplement,current_documents)
    persist(('discovery','identities','plan','reports','extractions','supplement','pointer'))
    target=review/'candidates/sources/senate_efd-current.json';target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(candidate.read_bytes())
    status=json.loads((review/'status/senate_efd.json').read_bytes());qualified=json.loads(audit.read_bytes())
    archive=(review/'senate_efd/qualifications'/status['catalog_sha256']/status['identity_binding_sha256']/parser/
        qualified['builder_version']/(qualified['candidate_snapshot_id']+'.json'))
    archive.parent.mkdir(parents=True,exist_ok=True)
    if archive.exists() and archive.read_bytes()!=audit.read_bytes():raise ValueError('Immutable qualification collision')
    archive.write_bytes(audit.read_bytes());(review/'senate_efd/qualifications/current.json').write_bytes(audit.read_bytes())
    assembly=assemble(workspace,'senate_efd',base,None,builder_commit)
    return {'status':'resolved','assembly_status':assembly,'plan_sha256':fingerprint,'target_count':len(documents)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--documents',required=True)
    parser.add_argument('--submitted-start-date',required=True)
    parser.add_argument('--mode',choices=('plan','resolve'),required=True)
    parser.add_argument('--limit',type=int,default=25)
    parser.add_argument('--run-root',type=Path,required=True)
    parser.add_argument('--base',type=Path,required=True)
    parser.add_argument('--builder-commit',required=True)
    args=parser.parse_args()
    try:
        result=replay(args.workspace,selected_ids(args.documents),args.submitted_start_date,args.mode,
            args.limit,args.run_root,args.base,args.builder_commit)
    except (ValueError,OSError,subprocess.CalledProcessError) as error:parser.exit(2,str(error)+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
