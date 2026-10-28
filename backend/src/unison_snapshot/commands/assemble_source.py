"""Persist a validated source projection, then assemble peers without advancing stale data."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from unison_snapshot.codec import encode
from unison_snapshot.commands.data_trees import publish, read_control

SOURCES=('house_clerk','senate_efd','oge')


def assemble(workspace, source_id, base, html_output, builder_commit, *, verify_readiness=False):
    from unison_snapshot.builder import build
    from unison_snapshot.disclosure_candidate import project_source_candidate
    control=read_control(workspace)
    expected={'house':'house_clerk','senate':'senate_efd','oge':'oge','whitehouse':'oge'}[control['scope']]
    if source_id!=expected:raise ValueError('Assembly source differs from its controlled scope')
    review=Path(control['trees']['review']['root'])
    selected=review/'candidates/sources'/f'{source_id}-current.json'
    value=json.loads(selected.read_bytes())
    project_source_candidate(source_id,value)
    build(value, generated_at=value['meta']['data_cutoff_at'], allow_production=True,
          allow_empty_production=True)
    # The valid source remains durable even when another source is unavailable.
    publish(workspace,('review',),source_id+' source candidate')
    arguments=[sys.executable,'-m','unison_snapshot','build-disclosure-candidate',
        '--defer-stale-sources','--assembly-status-output',str(review/'status/disclosure_assembly.json'),
        '--base',str(base),'--harmonize-cutoffs','--output',str(review/'candidates/disclosure-current.json'),
        '--audit-output',str(review/'status/disclosure_cutoff.json')]
    if html_output:arguments+=['--html-output',str(html_output)]
    for source in SOURCES:
        path=review/'candidates/sources'/f'{source}-current.json'
        if path.is_file():arguments+=['--source',source+'='+str(path)]
    subprocess.run(arguments,check=True)
    result=subprocess.run([sys.executable,'-m','unison_snapshot','source-assembly-status',
        '--status',str(review/'status/disclosure_assembly.json')],check=True,capture_output=True,text=True)
    if result.stderr:print(result.stderr,end='',file=sys.stderr)
    status=result.stdout.strip()
    if status not in {'ready','deferred'}:raise ValueError('Unexpected source assembly disposition')
    if status=='ready':
        if verify_readiness:
            subprocess.run([sys.executable,'-m','unison_snapshot','verify-first-launch',
                '--review-root',str(review),'--input',str(review/'candidates/disclosure-current.json')],
                check=True)
        candidate=json.loads((review/'candidates/disclosure-current.json').read_bytes())
        included=sorted(row['source_id'] for row in candidate['source_health']
            if row.get('source_id') in SOURCES and row.get('status') in {'ok','partial'})
        run={'schema_version':'disclosure-candidate-run/v1','builder_commit':builder_commit,
             'data_cutoff_at':candidate['meta']['data_cutoff_at'],'included_sources':included,
             'pending_sources':sorted(set(SOURCES)-set(included)),'people_count':len(candidate['people']),
             'transaction_count':len(candidate['transactions']),
             'reported_holding_count':len(candidate['reported_holdings']),
             'status':'partial','cutoff_audit':'status/disclosure_cutoff.json'}
        path=review/'status/disclosure_candidate.json';temporary=path.with_suffix('.next.json')
        temporary.write_bytes(encode(run));temporary.replace(path)
    publish(workspace,('review',),source_id+' '+status+' source assembly')
    return status


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--source',choices=SOURCES,required=True)
    parser.add_argument('--base',type=Path,required=True)
    parser.add_argument('--html-output',type=Path)
    parser.add_argument('--builder-commit',required=True)
    parser.add_argument('--verify-readiness',action='store_true')
    args=parser.parse_args()
    try:
        status=assemble(args.workspace,args.source,args.base,args.html_output,args.builder_commit,
            verify_readiness=args.verify_readiness)
    except (ValueError,OSError,subprocess.CalledProcessError) as error:parser.exit(2,str(error)+'\n')
    print(json.dumps({'assembly_status':status}))


if __name__=='__main__':main()
