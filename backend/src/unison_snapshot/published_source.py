"""Load the immutable formal predecessor for source-proven fact comparison."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
from .oge import OgeCatalogError
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
