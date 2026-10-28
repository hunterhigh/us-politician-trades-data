"""Evaluate the publication source gate from fixed Git objects without changing branches."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from unison_snapshot.codec import encode
from unison_snapshot.publication_gate import SOURCES,evaluate
from unison_snapshot.content_integrity import open_failures
from unison_snapshot.production_health import GitHub


def from_git(repo,review_commit,state_commit):
    def fixed(ref):
        value=subprocess.check_output(['git','-C',str(repo),'rev-parse','--verify',ref+'^{commit}'],text=True).strip()
        if not re.fullmatch('[0-9a-f]{40}',value):raise ValueError('Publication source ref is not a fixed commit')
        return value
    review,state=fixed(review_commit),fixed(state_commit)
    def read(commit,path):
        raw=subprocess.check_output(['git','-C',str(repo),'show',commit+':'+path])
        if len(raw)>64*1024*1024:raise ValueError('Source gate input exceeds the source budget')
        return json.loads(raw)
    candidates={source:read(review,'candidates/sources/'+source+'-current.json') for source in SOURCES}
    statuses={source:read(state,'status/'+source+'.json') for source in SOURCES}
    return candidates,statuses,{'review_commit':review,'state_commit':state}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=Path.cwd())
    parser.add_argument('--review-commit',required=True)
    parser.add_argument('--state-commit',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--github-output',type=Path)
    requirement=parser.add_mutually_exclusive_group()
    requirement.add_argument('--require-ready',action='store_true')
    requirement.add_argument('--require-preparation-ready',action='store_true')
    parser.add_argument('--repository',help='Also hold for unresolved content-integrity Issues')
    args=parser.parse_args()
    try:
        candidates,statuses,provenance=from_git(args.repo,args.review_commit,args.state_commit)
        result={**evaluate(candidates,statuses),**provenance}
        result['preparation_ready']=result['ready']
        if args.repository:
            result['integrity_failures'] = open_failures(GitHub(args.repository, os.environ.get('GH_TOKEN')))
            for failure in result['integrity_failures']:
                result['blocking_sources'].append({'source': failure['scope'], 'reason': 'content_integrity',
                                                  'category': failure['category'], 'issue': failure['issue']})
            result['ready'] = not result['blocking_sources']
        args.output.parent.mkdir(parents=True,exist_ok=True)
        temporary=args.output.with_suffix('.next.json');temporary.write_bytes(encode(result));temporary.replace(args.output)
        if args.github_output:
            with args.github_output.open('a',encoding='utf-8') as stream:
                stream.write('publication_ready='+str(result['ready']).lower()+'\n')
                stream.write('preparation_ready='+str(result['preparation_ready']).lower()+'\n')
        print(json.dumps(result))
        if args.require_ready and not result['ready']:parser.exit(3,'Publication held by current source waterlines\n')
        if args.require_preparation_ready and not result['preparation_ready']:
            parser.exit(3,'Preparation held by current source waterlines\n')
    except (ValueError,OSError,subprocess.CalledProcessError) as error:parser.exit(2,str(error)+'\n')


if __name__=='__main__':main()
