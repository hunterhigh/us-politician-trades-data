"""Persist failed official collection attempts separately from successful waterlines."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from .codec import encode

COLLECTIONS={'house_clerk':{'discover-house-index','discover-house-members'},
    'senate_efd':{'discover-senate-efd','discover-senate-members','discover-congress-senate-members'},
    'oge':{'discover-oge'}}


def record(command,error,*,filing_year=None):
    source=os.environ.get('UNISON_INTEGRITY_SCOPE')
    directory=os.environ.get('UNISON_SOURCE_FAILURE_DIRECTORY')
    if not directory or command not in COLLECTIONS.get(source,set()):return
    status=None;seen=set();current=error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for attribute in ('http_status','status','code'):
            value=getattr(current,attribute,None)
            if type(value) is int and 100<=value<=599:status=value;break
        if status is not None:break
        current=current.__cause__ or current.__context__
    year=filing_year or os.environ.get('UNISON_HOUSE_FILING_YEAR') if source=='house_clerk' else None
    value={'schema_version':'source-operation-failure/v1','source_id':source,'command':command,
        'http_status':status,'recorded_at':datetime.now(timezone.utc).isoformat(),
        'run_id':os.environ.get('GITHUB_RUN_ID'),'repository':os.environ.get('GITHUB_REPOSITORY'),
        'code_commit':os.environ.get('GITHUB_SHA'),'filing_year':int(year) if year else None}
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    target=root/(source+'.json');temporary=target.with_suffix('.next.json')
    temporary.write_bytes(encode(value));temporary.replace(target)


def persist(workspace,directory):
    from .commands.data_trees import prepare,publish,read_control
    control=read_control(Path(workspace))
    if 'state' not in control['trees']:return []
    source={'house':'house_clerk','senate':'senate_efd','oge':'oge','whitehouse':'oge'}[control['scope']]
    path=Path(directory)/(source+'.json')
    if not path.is_file():return []
    event=json.loads(path.read_bytes())
    if (event.get('schema_version')!='source-operation-failure/v1' or event.get('source_id')!=source
            or event.get('command') not in COLLECTIONS[source]
            or not re.fullmatch('[0-9]+',str(event.get('run_id')))
            or event.get('code_commit')!=control['code_commit']):
        raise ValueError('Source failure differs from its controlled run')
    # A separate clean state tree cannot accidentally commit dirty candidate or
    # checkpoint work left behind by the failed operation.
    recovery=Path(workspace).parent/('failure-state-'+source)
    prepared=prepare(Path(control['repo']),recovery,control['scope'],('state',))
    root=Path(prepared['trees']['state']['root'])
    name=source
    if source=='house_clerk' and event.get('filing_year') not in (None,datetime.now(timezone.utc).year):
        if event['filing_year']!=2025:raise ValueError('Historical failure escaped fixed 2025 House scope')
        name='house_backfill_2025'
    target=root/'status'/(name+'.json')
    prior=json.loads(target.read_bytes()) if target.is_file() else {}
    run_url='https://github.com/'+event['repository']+'/actions/runs/'+event['run_id']
    attempt=prior.get('last_collection_attempt',{})
    if attempt.get('workflow_run_url')==run_url and attempt.get('http_status')==event['http_status']:
        return [{'source':source,'already_recorded':True}]
    last=prior.get('last_successful_collection_at') or (
        prior.get('run_at') if source=='house_clerk' and prior.get('status') in {'ok','attention'} else
        (prior.get('catalog') or {}).get('retrieved_at'))
    value={**prior,'schema_version':prior.get('schema_version',{'house_clerk':'house-source-run/v1',
        'senate_efd':'senate-source-run/v1','oge':'oge-source-run/v1'}[source]),
        'source_id':source,'run_at':event['recorded_at'],
        'status':'blocked' if event['http_status']==403 else 'unavailable',
        'last_successful_collection_at':last,'workflow_run_url':run_url,
        'last_collection_attempt':{'failed_step':event['command'],'http_status':event['http_status'],
            'recorded_at':event['recorded_at'],'workflow_run_url':run_url},
        'detail':'Official collection failed; prior success and qualified data are preserved.'}
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(encode(value))
    return publish(recovery,('state',),'preserve failed '+source+' collection attempt')
