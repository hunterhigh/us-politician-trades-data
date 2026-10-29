"""Account for unprocessed direct reports while retaining only exact published facts."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess
from .oge import OgeCatalogError
from .codec import digest, encode
from .content_integrity import content_boundary


@content_boundary('snapshot_contract')
def published_main(repo):
    def git(*args):
        return subprocess.check_output(['git','-C',str(repo),*args])
    commit=git('rev-parse','refs/remotes/origin/main').decode().strip()
    if not re.fullmatch('[0-9a-f]{40}',commit):raise OgeCatalogError('Published main is not a fixed commit')
    manifest=json.loads(git('show',commit+':manifest.json'))
    sha=manifest.get('board')
    if manifest.get('is_demo') is not False or not isinstance(sha,str) or not re.fullmatch('[0-9a-f]{64}',sha):
        raise OgeCatalogError('Retained OGE facts require the formal production board')
    raw=git('show',commit+':board/'+sha+'.json')
    if len(raw)>64*1024*1024:raise OgeCatalogError('Published retention board exceeds its byte budget')
    if hashlib.sha256(raw).hexdigest()!=sha:
        raise OgeCatalogError('Published OGE retention board hash differs')
    return {'main_commit':commit,'board_sha256':sha,'snapshot_id':manifest['snapshot_id'], 'board':json.loads(raw)}


def retain(pending, records, publication, people, transactions, evidence_root):
    board=publication['board'];prior_people={row['id']:row for row in board['people']}
    if (not re.fullmatch('[0-9a-f]{40}',publication['main_commit']) or
            not re.fullmatch('[0-9a-f]{64}',publication['board_sha256']) or
            digest(encode(board))!=publication['board_sha256']):
        raise OgeCatalogError('OGE prior publication has no immutable binding')
    output=[];retained=[]
    seen={row['id'] for row in transactions}
    for document in sorted(pending):
        archived=False
        if evidence_root is not None:
            from .oge_reports import _read_archived_source
            for metadata in sorted((Path(evidence_root)/'oge/reports'/document).glob('*.json')):
                value,_,_=_read_archived_source(Path(evidence_root),metadata)
                if value['document_id']!=document:raise OgeCatalogError('Pending OGE archive document differs')
                archived=True
        identifiers=[]
        for row in board['transactions']:
            if row.get('source_id')!='oge' or row.get('filing_id')!=document:continue
            if row.get('verification_status')!='official_matched' or row['id'] in seen:
                raise OgeCatalogError('Retained OGE publication facts are unqualified or collide')
            if row.get('source_url') != records[document]['document_url']:
                raise OgeCatalogError('Retained OGE fact differs from the pending official source URL')
            person=prior_people.get(row['person_id'])
            if person is None:raise OgeCatalogError('Retained OGE fact lacks its published person')
            people.setdefault(person['id'],deepcopy(person))
            transactions.append(deepcopy(row));identifiers.append(row['id']);retained.append(row['id']);seen.add(row['id'])
        output.append({'document_id':document,'source_url':records[document]['document_url'],
            'archive_state':'archived' if archived else 'not_archived',
            'disposition':'extraction_unavailable','retained_published_fact_ids':sorted(identifiers)})
    return output,sorted(retained)
