"""Consume a fixed archived PTR inventory without requalifying another year."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import time

from .codec import encode
from .house import HouseIndexError


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary=path.with_suffix('.next.json');temporary.write_bytes(encode(value));temporary.replace(path)


def project(review, evidence, state, prior, year):
    """Recheck selected reports through the normal builder; retain prior facts exactly."""
    from .builder import build
    from .house_candidate import build_house_candidate
    from .disclosure_candidate import project_source_candidate
    project_source_candidate('house_clerk',prior)
    build(prior,generated_at=prior['meta']['data_cutoff_at'],allow_production=True,allow_empty_production=True)
    with tempfile.TemporaryDirectory(prefix='house-year-projection-') as directory:
        scoped=Path(directory)
        for bucket in ('extractions','identities','qualifications','failures','paper_observations',
                       'paper_key_field_rereads','paper_materializations'):
            source=review/'house_clerk'/bucket/str(year)
            if source.is_dir():shutil.copytree(source,scoped/'house_clerk'/bucket/str(year))
        qualifications=list(scoped.glob('house_clerk/qualifications/*/*/*.json'))
        failures=list(scoped.glob('house_clerk/failures/*/*/*.json'))
        extracted=list(scoped.glob('house_clerk/extractions/*/*/*.json'))
        summary={'parser_commit':'0'*40,'pending_parse_count':0,'status_conflict_count':0,
                 'qualification_count':len(qualifications),'evidence_count':len(qualifications)+len(failures),
                 'extracted_count':len(extracted),'failure_count':len(failures),
                 'qualified_transaction_count':sum(len(json.loads(p.read_bytes())['transactions']) for p in qualifications),
                 'quarantined_row_count':sum(len(json.loads(p.read_bytes())['quarantined']) for p in qualifications)}
        write(scoped/'status/house_clerk.json',summary)
        empty={**deepcopy(prior),'people':[],'transactions':[],'reported_holdings':[],'security_market_data':[]}
        # Do not replay unrelated annual relations from the retained projection.
        empty['meta'].pop('transaction_disclosure_relations',None)
        selected=build_house_candidate(scoped,state,empty,evidence_root=evidence)
    result=deepcopy(prior)
    people={p['id']:p for p in result['people']};rows={r['id']:r for r in result['transactions']}
    for person in selected['people']:people.setdefault(person['id'],person)
    for row in selected['transactions']:
        if row['id'] in rows and rows[row['id']]!=row:
            raise HouseIndexError('Archived year projection changes an existing qualified fact')
        rows.setdefault(row['id'],row)
    result['people']=[people[k] for k in sorted(people)]
    result['transactions']=[rows[k] for k in sorted(rows)]
    # Historical collection never advances current-source freshness or holdings.
    assert all(rows[r['id']]==r for r in prior['transactions'])
    build(result,generated_at=result['meta']['data_cutoff_at'],allow_production=True,allow_empty_production=True)
    return result


def consume(workspace, *, year, batch_size=25, budget_seconds=2700, clock=time.monotonic):
    from .commands.data_trees import read_control, publish
    from .house_ptr import parse_archived_pdf, qualify_automatic, PARSER_RETRY_VERSION, DETERMINISTIC_IDENTITY_BASES
    from .house_members import suggest_identity
    control=read_control(workspace)
    if control['scope']!='house' or year!=2025 or not 1<=batch_size<=25 or not 1<=budget_seconds<=2700:
        raise HouseIndexError('Fixed House PTR archive selection is invalid')
    evidence,review,state=(Path(control['trees'][r]['root']) for r in ('evidence','review','state'))
    checkpoint=json.loads((state/f'checkpoints/house/{year}.json').read_bytes())
    if checkpoint['filing_year']!=year or checkpoint['queue'] or checkpoint['counts']['failed']:
        raise HouseIndexError('House archive inventory is not fully durable')
    paths=sorted(evidence.glob(f'house_clerk/documents/{year}/*/*.json'))
    if {p.parent.name for p in paths}!=set(checkpoint['documents']) or len(paths)!=len(checkpoint['documents']):
        raise HouseIndexError('House archived reports differ from the fixed index')
    rosters=sorted(evidence.glob('house_clerk/members/*.roster.json'))
    if not rosters:raise HouseIndexError('House official member roster is missing')
    roster=json.loads(rosters[-1].read_bytes())
    receipt_path=review/f'status/house_backfill_{year}.json'
    receipt=json.loads(receipt_path.read_bytes()) if receipt_path.is_file() else {}
    records={r['document_id']:r for r in receipt.get('reports',[])}
    if set(records)-set(checkpoint['documents']):raise HouseIndexError('House review contains reports outside the fixed index')
    deadline=clock()+budget_seconds;completed=0
    for metadata_path in paths:
        if clock()>=deadline:break
        metadata=json.loads(metadata_path.read_bytes());document=metadata['document_id'];sha=metadata['sha256']
        if metadata.get('filing_year')!=year or document!=metadata_path.parent.name or sha!=metadata_path.stem:
            raise HouseIndexError('House source metadata differs from its fixed year and document path')
        if hashlib.sha256((evidence/metadata['archive_path']).read_bytes()).hexdigest()!=sha:
            raise HouseIndexError('House archived original hash differs from the fixed inventory')
        relative=Path(str(year))/str(document)/(sha+'.json')
        destinations={key:review/'house_clerk'/key/relative for key in ('extractions','identities','qualifications','failures')}
        previous=records.get(document)
        if (previous and previous.get('source_sha256')==sha and previous.get('parser_version')==PARSER_RETRY_VERSION
                and (destinations['qualifications'] if previous['status']!='parse_failed' else destinations['failures']).is_file()):
            continue
        row={'document_id':document,'source_sha256':sha,'parser_version':PARSER_RETRY_VERSION,
             'source_url':metadata['document_url'],'qualified_row_count':0,'quarantined_row_count':None,
             'person_id':None,'reasons':[]}
        try:
            extraction=parse_archived_pdf(evidence,metadata_path)
            identity=suggest_identity(extraction,roster)
            qualified=qualify_automatic(extraction,identity,evidence_root=evidence)
            for key,value in [('extractions',extraction),('identities',identity),('qualifications',qualified)]:
                write(destinations[key],value)
            if destinations['failures'].exists():destinations['failures'].unlink()
            good,bad=len(qualified['transactions']),len(qualified['quarantined'])
            reasons=sorted({reason for r in qualified['quarantined'] for reason in r.get('reasons',[])})
            status='partial' if good and bad else 'qualified' if good else 'quarantined'
            if not good and not bad:
                zero=qualified.get('qualification',{}).get('zero_transaction_document_count')==1
                status='zero_transactions' if zero else 'quarantined'
                reasons=[] if zero else ['no_qualified_rows_or_complete_zero_proof']
            row.update(status=status,qualified_row_count=good,quarantined_row_count=bad,
                person_id=identity.get('person_id') if identity.get('match_basis') in DETERMINISTIC_IDENTITY_BASES else None,
                reasons=reasons or (['source_rows_qualified'] if good else ['explicit_zero_transaction_report'] if status=='zero_transactions' else ['qualification_incomplete']))
        except HouseIndexError as error:
            for key in ('extractions','identities','qualifications'):
                if destinations[key].exists():destinations[key].unlink()
            row.update(status='parse_failed',reasons=[str(error)[:480]])
            write(destinations['failures'],{'schema_version':'house-ptr-parse-failure/v1','filing_year':year,
                'document_id':document,'source_sha256':sha,'parser_version':PARSER_RETRY_VERSION,
                'parser_commit':control['code_commit'],'last_attempt_at':datetime.now(timezone.utc).isoformat(),
                'attempts':1,'error':str(error)[:480]})
        records[document]=row;completed+=1
        receipt={'schema_version':'house-archive-review/v1','filing_year':year,'index_sha256':checkpoint['index_sha256'],
            'report_count':len(paths),'processed_count':len(records),'queue_count':len(paths)-len(records),
            'qualified_row_count':sum(r['qualified_row_count'] for r in records.values()),
            'quarantined_row_count':sum(r['quarantined_row_count'] or 0 for r in records.values()),
            'qualified_report_count':sum(r['status'] in {'qualified','zero_transactions'} for r in records.values()),
            'partial_report_count':sum(r['status']=='partial' for r in records.values()),
            'quarantined_report_count':sum(r['status'] in {'quarantined','parse_failed'} for r in records.values()),
            'parse_failure_count':sum(r['status']=='parse_failed' for r in records.values()),
            'reports':[records[k] for k in sorted(records)]}
        write(receipt_path,receipt)
        if completed%batch_size==0:publish(workspace,('review',),'Fixed House PTR archive review')
    publish(workspace,('review',),'Fixed House PTR archive review checkpoint')
    if receipt.get('queue_count')==0:
        source=review/'candidates/sources/house_clerk-current.json'
        prior=json.loads(source.read_bytes())
        historical_state=json.loads((state/f'status/house_backfill_{year}.json').read_bytes())
        value=project(review,evidence,historical_state,prior,year)
        write(source,value);write(review/'candidates/house-current.json',value)
        from .commands.assemble_source import assemble
        assemble(workspace,'house_clerk',Path(__file__).parents[2]/'data/empty-disclosure.json',None,control['code_commit'])
    return receipt
