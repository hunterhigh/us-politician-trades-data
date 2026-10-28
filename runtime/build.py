"""Build and verify the immutable source runtime; source code is not baked into it."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--tag',required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    if not re.fullmatch(r'ghcr.io/[a-z0-9_-]+/[a-z0-9._-]+',args.image):parser.error('invalid repository image')
    if not re.fullmatch('[0-9a-f]{40}',args.tag):parser.error('tag must be a fixed source commit')
    pins=json.loads((ROOT/'runtime/inputs.lock.json').read_bytes())
    if pins['schema_version']!='source-runtime-inputs/v1':raise ValueError('Unknown runtime input schema')
    if hashlib.sha256((ROOT/'backend/requirements.lock').read_bytes()).hexdigest()!=pins['requirements_sha256']:
        raise ValueError('Runtime requirements differ from the fixed inputs')
    for key in ('python_image','node_image'):
        if not re.fullmatch(r'[a-z]+:[a-zA-Z0-9_.-]+@sha256:[0-9a-f]{64}',pins[key]):raise ValueError('Runtime base is not digest-pinned')
    for key,value in pins.items():
        if key.endswith('_sha256') and not re.fullmatch('[0-9a-f]{64}',value):raise ValueError('Invalid runtime hash')
        if key.endswith('_commit') and not re.fullmatch('[0-9a-f]{40}',value):raise ValueError('Invalid source commit')
    build=['docker','build','--platform','linux/amd64','--file','runtime/Dockerfile',
        '--label','org.opencontainers.image.source=https://github.com/'+args.image.removeprefix('ghcr.io/').removesuffix('-runtime'),
        '--label','org.opencontainers.image.revision='+args.tag,'--tag',args.image+':'+args.tag]
    mapping={'PYTHON_BASE':'python_image','NODE_BASE':'node_image','DEBIAN_SNAPSHOT':'debian_snapshot',
        'TESSERACT_COMMIT':'tesseract_commit','TESSERACT_ARCHIVE_SHA256':'tesseract_archive_sha256',
        'TESSDATA_COMMIT':'tessdata_commit','ENG_SHA256':'eng_sha256','OSD_SHA256':'osd_sha256'}
    for argument,key in mapping.items():build+=['--build-arg',argument+'='+pins[key]]
    subprocess.run([*build,'.'],cwd=ROOT,check=True)
    smoke=subprocess.check_output(['docker','run','--rm','--volume',str(ROOT)+':/verification:ro',
        args.image+':'+args.tag,'python','/verification/runtime/smoke.py',
        '/verification/backend/requirements.lock'],text=True,cwd=ROOT)
    verified=json.loads(smoke)
    subprocess.run(['docker','push',args.image+':'+args.tag],check=True)
    digests=json.loads(subprocess.check_output(['docker','image','inspect','--format','{{json .RepoDigests}}',
        args.image+':'+args.tag],text=True))
    matches=[value for value in digests if re.fullmatch(re.escape(args.image)+r'@sha256:[0-9a-f]{64}',value)]
    if len(matches)!=1:raise ValueError('Published runtime has no unique verified digest')
    receipt={'schema_version':'source-runtime-build/v1','image':matches[0],'build_commit':args.tag,
        'inputs_sha256':hashlib.sha256((ROOT/'runtime/inputs.lock.json').read_bytes()).hexdigest(),
        'requirements_sha256':pins['requirements_sha256'],'verification':verified}
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    args.receipt.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt))


if __name__=='__main__':main()
