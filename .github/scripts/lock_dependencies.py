"""Add official PyPI distribution hashes to fully resolved dependency pins."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def release(requirement):
    name,version=requirement
    for attempt in range(3):
        try:
            with urlopen('https://pypi.org/pypi/'+name+'/'+version+'/json',timeout=20) as response:
                raw=response.read()
            break
        except (HTTPError,URLError,TimeoutError) as error:
            if isinstance(error,HTTPError) and error.code not in {408,425,429,500,502,503,504}:raise
            if attempt==2:raise
            time.sleep(2**attempt)
    value=json.loads(raw)
    canonical=lambda text:re.sub('[-_.]+','-',text).lower()
    if canonical(value['info']['name'])!=canonical(name) or value['info']['version']!=version:
        raise ValueError('PyPI release identity differs from the resolved dependency')
    hashes=sorted({item['digests']['sha256'] for item in value['urls']
                   if item['packagetype'] in {'bdist_wheel','sdist'}})
    if not hashes or any(not re.fullmatch('[0-9a-f]{64}',v) for v in hashes):
        raise ValueError('PyPI release has no complete distribution hashes')
    return name,version,hashes,hashlib.sha256(raw).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resolved',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--receipt',type=Path)
    args=parser.parse_args()
    pins=[]
    for line in args.resolved.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):continue
        match=re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)',line)
        if not match:raise ValueError('Resolved dependency must be one exact, unmarked package pin')
        pins.append(match.groups())
    if not pins or len({name.lower() for name,_ in pins})!=len(pins):raise ValueError('Dependency pins are empty or duplicated')
    with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(release,pins))
    output=['# Python 3.11 production/test dependencies, resolved with pip-tools 7.6.2.',
        '# Sources: backend/pyproject.toml extras house-ingest,native-source-proof,oge-source-quad,oge-handwritten-date',
        '# and backend/build-requirements.in. Hashes cover official PyPI wheels and source archives.',
        '# Re-resolve with pip-compile --allow-unsafe --strip-extras and these four extras;',
        '# then run .github/scripts/lock_dependencies.py against the resolved exact pins.','']
    for name,version,hashes,_ in rows:
        output.append(name+'=='+version+' \\')
        output.extend('    --hash=sha256:'+value+(' \\' if i<len(hashes)-1 else '') for i,value in enumerate(hashes))
    temporary=args.output.with_suffix('.next');temporary.write_bytes(('\n'.join(output)+'\n').encode('utf-8'));temporary.replace(args.output)
    if args.receipt:
        args.receipt.write_text(json.dumps({'requirements_sha256':hashlib.sha256(args.output.read_bytes()).hexdigest(),
            'releases':[{'name':n,'version':v,'metadata_sha256':sha,'distribution_count':len(h)} for n,v,h,sha in rows]},indent=2),encoding='utf-8')
    print(json.dumps({'packages':len(rows),'requirements_sha256':hashlib.sha256(args.output.read_bytes()).hexdigest()}))


if __name__=='__main__':main()
