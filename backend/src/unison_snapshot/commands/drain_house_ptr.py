"""Archive the indexed House PTR queue until its time budget is exhausted."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import time

from unison_snapshot.codec import encode
from unison_snapshot.house import (HouseDocumentClient, HouseDownloadError, HouseIndexError,
    archive_document, parse_index)
from unison_snapshot.house_sync import record_result


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.'+path.name, suffix='.tmp', delete=False) as stream:
        stream.write(encode(value));temporary=Path(stream.name)
    temporary.replace(path)


def drain(checkpoint_path, evidence_root, *, budget_seconds=900, client=None,
          clock=time.monotonic, sleep=time.sleep,
          utc_now=lambda: datetime.now(timezone.utc).isoformat()):
    if type(budget_seconds) is not int or not 1 <= budget_seconds <= 1500:
        raise ValueError('House archive budget must be 1–1500 seconds')
    checkpoint = json.loads(checkpoint_path.read_bytes())
    year, index_sha = checkpoint.get('filing_year'), checkpoint.get('index_sha256')
    import re
    if (checkpoint.get('schema_version') != 'house-ptr-checkpoint/v1' or type(year) is not int or
            not re.fullmatch('[0-9a-f]{64}',str(index_sha))):
        raise HouseIndexError('House archive requires a bound checkpoint')
    raw = (evidence_root/'house_clerk/index'/str(year)/(index_sha+'.zip')).read_bytes()
    if hashlib.sha256(raw).hexdigest() != index_sha:
        raise HouseIndexError('House archive index hash changed')
    # Read and verify the index once, not once per PDF in a large backlog.
    candidates = {row.document_id: row for row in parse_index(raw,year) if row.filing_type=='P'}
    queue = checkpoint.get('queue')
    if (not isinstance(queue,list) or any(not isinstance(d,str) for d in queue) or len(queue)!=len(set(queue)) or
            any(document not in candidates for document in queue)):
        raise HouseIndexError('House archive queue differs from the indexed PTR inventory')
    for document in queue:
        state = checkpoint['documents'].get(document, {})
        row = candidates[document]
        if any(state.get(key) != getattr(row, field) for key,field in
                [('document_url','document_url'),('filer_name','filer_name'),('filed_date','filed_date')]):
            raise HouseIndexError('House archive queue differs from its source index row')
    started = clock();deadline=started+budget_seconds
    counts = {'attempted_count':0,'archived_count':0,'failed_count':0,'request_count':0,'retry_count':0}
    interrupted = False
    for document in queue:
        if clock() >= deadline:
            interrupted = True;break
        metadata = None;failure = None
        for attempt in range(3):
            if clock() >= deadline:
                interrupted = True;break
            downloader = client or HouseDocumentClient(timeout=max(.1,min(30,deadline-clock())))
            counts['request_count'] += 1
            try:
                content,headers = downloader.download(candidates[document])
                metadata = archive_document(evidence_root,candidates[document],content,headers)
                failure = None;break
            except HouseIndexError as error:
                failure = error
                if not isinstance(error,HouseDownloadError) or not error.transient or attempt==2:
                    break
                delay = 2**attempt
                if clock()+delay >= deadline:
                    interrupted=True;break
                counts['retry_count'] += 1;sleep(delay)
        if metadata is None and failure is None:
            break
        counts['attempted_count'] += 1
        if metadata is not None:
            record_result(checkpoint,document,'archived',result_at=utc_now(),archive_metadata=metadata)
            counts['archived_count'] += 1
        else:
            record_result(checkpoint,document,'failed',result_at=utc_now(),error=str(failure)[:480])
            counts['failed_count'] += 1
        save(checkpoint_path,checkpoint)
        # The existing source request pacing remains in force, independently of
        # the number of documents that fit in this run.
        remaining = deadline-clock()
        if remaining > 0:sleep(min(1,remaining))
    unarchived = checkpoint['counts']['pending']+checkpoint['counts']['failed']
    reason = ('time_budget_exhausted' if interrupted or clock()>=deadline and checkpoint['queue'] else
              'retry_deferred' if unarchived else 'queue_drained')
    return {'schema_version':'house-archive-budget-run/v1',**counts,
        'budget_seconds':budget_seconds,'elapsed_seconds':round(clock()-started,3),
        'pending_queue_count':len(checkpoint['queue']),
        'remaining_unarchived_count':unarchived,'stop_reason':reason}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint',type=Path,required=True)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--budget-seconds',type=int,default=900)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve()==args.checkpoint.resolve():
        parser.error('Budget receipt and checkpoint must be separate files')
    try:
        result=drain(args.checkpoint,args.archive,budget_seconds=args.budget_seconds)
    except (ValueError,HouseIndexError,OSError) as error:
        parser.exit(2,'House archive failed: '+str(error)+'\n')
    save(args.output,result);print(json.dumps(result))
