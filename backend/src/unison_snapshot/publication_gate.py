"""Gate each source against its own collection and qualified waterlines."""
from datetime import datetime,timedelta,timezone
from .content_integrity import content_boundary
from .source_freshness import (collection_at, maximum_age, source_blocked,
                               source_unavailable, timestamp, senate_grace_allowed)

SOURCES=('house_clerk','senate_efd','oge')
MAX_AGE=timedelta(hours=48)


@content_boundary('snapshot_contract')
def evaluate(candidates,statuses,*,now=None,senate_days=None):
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
        collection=collection_at(source,status)
        age_limit=maximum_age(source,senate_days)
        waterlines[source]={'collection_at':collection,'qualified_at':qualified,
                           'max_age_hours':age_limit.total_seconds()/3600,
                           'unavailable':source_unavailable(status)}
        blocked=source_blocked(status)
        grace_source=source=='senate_efd' and senate_grace_allowed(status)
        if blocked and not grace_source:blockers.append({'source':source,'reason':'source_blocked'})
        ready_health=health[0].get('status') in {'ok','partial'} or (
            source=='senate_efd' and health[0].get('status')=='stale' and health[0].get('stale') is True)
        if not ready_health:
            blockers.append({'source':source,'reason':'qualified_source_not_ready'})
        for phase,value in [('collection',collection),('qualified',qualified)]:
            if value is None:
                blockers.append({'source':source,'reason':phase+'_waterline_missing'});continue
            instant=timestamp(value)
            if instant>now+timedelta(minutes=5):raise ValueError('Source waterline is in the future')
            if now-instant>age_limit:blockers.append({'source':source,'reason':phase+'_stale','waterline_at':value})
    return {'schema_version':'publication-source-gate/v1','checked_at':now.isoformat(),
            'ready':not blockers,'blocking_sources':blockers,'waterlines':waterlines}
