"""Keep formal publication held when any source collection or qualification is stale."""
from datetime import datetime,timedelta,timezone

SOURCES=('house_clerk','senate_efd','oge')
MAX_AGE=timedelta(hours=48)


def timestamp(value):
    if not isinstance(value,str):raise ValueError('Source waterline must be an explicit timestamp')
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('Source waterline requires a timezone')
    return result.astimezone(timezone.utc)


def evaluate(candidates,statuses,*,now=None):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError('Publication clock requires a timezone')
    blockers=[];waterlines={}
    if set(candidates)!=set(SOURCES) or set(statuses)!=set(SOURCES):
        raise ValueError('Publication gate requires all three disclosure sources')
    for source in SOURCES:
        candidate=candidates[source];status=statuses[source]
        if candidate.get('meta',{}).get('is_demo') is not False:
            raise ValueError('Publication source candidate is not production data')
        health=[row for row in candidate.get('source_health',[]) if row.get('source_id')==source]
        if len(health)!=1 or health[0].get('data_cutoff_at')!=candidate['meta'].get('data_cutoff_at'):
            raise ValueError('Qualified source waterline is not bound to its candidate')
        qualified=candidate['meta']['data_cutoff_at']
        collection=(status.get('catalog',{}).get('retrieved_at') if source in {'senate_efd','oge'} else
            status.get('run_at') if status.get('status') in {'ok','attention'} else
            status.get('last_successful_collection_at'))
        waterlines[source]={'collection_at':collection,'qualified_at':qualified}
        blocked=(status.get('status') in {'blocked','disabled','authorization_required'} or
            status.get('status')=='failed' and status.get('last_collection_attempt',{}).get('http_status')==403)
        if blocked:blockers.append({'source':source,'reason':'source_blocked'})
        if health[0].get('status') not in {'ok','partial'}:
            blockers.append({'source':source,'reason':'qualified_source_not_ready'})
        for phase,value in [('collection',collection),('qualified',qualified)]:
            if value is None:
                blockers.append({'source':source,'reason':phase+'_waterline_missing'});continue
            instant=timestamp(value)
            if instant>now+timedelta(minutes=5):raise ValueError('Source waterline is in the future')
            if now-instant>MAX_AGE:blockers.append({'source':source,'reason':phase+'_stale','waterline_at':value})
    return {'schema_version':'publication-source-gate/v1','checked_at':now.isoformat(),
            'ready':not blockers,'blocking_sources':blockers,'waterlines':waterlines}
