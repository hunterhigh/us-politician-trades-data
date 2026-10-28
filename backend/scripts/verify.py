"""Run shared checks plus changed source groups; full runs partition every module."""
from __future__ import annotations
import argparse
import compileall
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

BACKEND=Path(__file__).resolve().parents[1]
REPO=BACKEND.parent
SOURCES={'house','senate','oge','whitehouse'}


def source_tag(path):
    value=path.replace('\\','/').lower()
    name=Path(value).stem.removeprefix('test_')
    for source in sorted(SOURCES):
        if name.startswith(source+'_') or name==source or name.startswith('reviewed_'+source+'_'):
            return source
    if name.startswith('reviewed_annual_'):return 'whitehouse'
    for prefix,source in [('house_clerk','house'),('senate_efd','senate'),
                          ('whitehouse_annual','whitehouse'),('whitehouse','whitehouse'),('oge','oge')]:
        if '/data/' in value and '/'+prefix+'/' in value:return source
    return 'common'


def affected_sources(paths):
    affected=set()
    for path in paths:
        tag=source_tag(path)
        if tag=='common':
            if path.startswith(('docs/','README.md','AGENTS.md')):continue
            return set(SOURCES)
        affected.add(tag)
    # The OGE candidate owns both direct and public White House disclosures.
    if affected & {'oge','whitehouse'}:affected.update(('oge','whitehouse'))
    return affected


def selected_modules(paths=None, *, shard=0, shards=1):
    groups=set(SOURCES) if paths is None else affected_sources(paths)
    selected=[]
    for path in sorted((BACKEND/'tests').rglob('test_*.py')):
        module='.'.join(path.relative_to(BACKEND/'tests').with_suffix('').parts)
        if source_tag(str(path)) not in groups | {'common'}:continue
        if int(hashlib.sha256(module.encode()).hexdigest(),16)%shards==shard:selected.append(module)
    return selected


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--full',action='store_true')
    mode.add_argument('--base',help='PR base commit for source-impact selection')
    parser.add_argument('--shard',default='0/1',help='Zero-based INDEX/COUNT')
    parser.add_argument('--list',action='store_true')
    args=parser.parse_args()
    if not re.fullmatch(r'\d+/[1-9]\d*',args.shard):parser.error('shard must be INDEX/COUNT')
    shard,shards=map(int,args.shard.split('/'))
    if shard>=shards or shards>16:parser.error('invalid shard range')
    paths=None
    if args.base:
        if not re.fullmatch('[0-9a-f]{40}',args.base):parser.error('base must be a fixed commit')
        subprocess.run(['git','cat-file','-e',args.base+'^{commit}'],cwd=REPO,check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        result=subprocess.run(['git','diff','--name-only',args.base,'HEAD'],cwd=REPO,
                              check=True,capture_output=True,encoding='utf-8')
        paths=result.stdout.splitlines()
    modules=selected_modules(paths,shard=shard,shards=shards)
    print(json.dumps({'groups':sorted(SOURCES if paths is None else affected_sources(paths)),
                     'shard':shard,'shards':shards,'module_count':len(modules),
                     **({'modules':modules} if args.list else {})}),flush=True)
    if args.list:return 0
    if not compileall.compile_dir(str(BACKEND/'src'),quiet=1):return 1
    os.chdir(BACKEND)
    sys.path[:0]=[str(BACKEND/'tests'),str(BACKEND/'src'),str(BACKEND)]
    loader=unittest.TestLoader()
    suite=unittest.TestSuite(loader.loadTestsFromName(module) for module in modules)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
