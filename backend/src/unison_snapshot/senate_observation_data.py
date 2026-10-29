"""Load saved source observations separately from qualified correction facts."""
import hashlib
import json
from pathlib import Path
import re

DEFAULT_ROOT = Path(__file__).resolve().parents[2]/'data/source_observations/senate_efd'


def load_bundle(root=DEFAULT_ROOT):
    root = Path(root).resolve()
    index = json.loads((root/'index.json').read_text(encoding='utf-8'))
    if (index.get('schema_version') != 'senate-source-observation-bundle/v1' or
            not re.fullmatch('[0-9a-f]{40}',str(index.get('evidence_commit')))):
        raise ValueError('Unsupported Senate source observation bundle')
    names = {*index['ledgers'],index['inventory'],*index['account_reviews'].values()}
    if names != set(index['file_sha256']):
        raise ValueError('Senate observation bundle inventory differs from its source bindings')
    for name in names:
        if not isinstance(name,str) or Path(name).name != name or not name.endswith('.json') or '\\' in name:
            raise ValueError('Senate observation file path is not canonical')
        path = (root/name).resolve(strict=True)
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest()!=index['file_sha256'][name]:
            raise ValueError('Senate saved source observation hash changed')
    return index
