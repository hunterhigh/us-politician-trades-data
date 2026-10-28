"""Prepare and publish bounded source worktrees with fixed-head checks."""
import argparse
import json
from pathlib import Path
import re
import subprocess

from unison_snapshot.codec import encode

ROLES = ('evidence','review','state')
PREFIXES = {'house':('house_clerk',), 'senate':('senate_efd',),
            'oge':('oge','whitehouse'), 'whitehouse':('oge','whitehouse')}
SCHEMA = 'source-data-worktrees/v1'


def git(repo, *arguments):
    result = subprocess.run(['git','-c','core.autocrlf=false','-C',str(repo),*map(str,arguments)],
        capture_output=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise ValueError('Source worktree Git '+str(arguments[0])+' failed ('+str(result.returncode)+')')
    return result.stdout.strip()


def write(path,value):
    temporary=path.with_suffix('.next.json');temporary.write_bytes(encode(value));temporary.replace(path)


def emit(value,output=None,environment=None):
    if output:
        with output.open('a',encoding='utf-8') as stream:
            for role,row in value['trees'].items():
                stream.write(role+'_root='+row['root']+'\n'+role+'_commit='+row['base']+'\n')
    if environment:
        with environment.open('a',encoding='utf-8') as stream:
            for role,row in value['trees'].items():stream.write(role.upper()+'_ROOT='+row['root']+'\n')
    return value


def read_control(workspace):
    workspace=workspace.resolve(strict=True)
    value=json.loads((workspace/'trees.json').read_bytes())
    if (value.get('schema_version')!=SCHEMA or value.get('scope') not in PREFIXES or
            not isinstance(value.get('trees'),dict) or set(value['trees'])-set(ROLES)):
        raise ValueError('Source worktree control is invalid')
    repo=Path(value['repo']).resolve(strict=True)
    if git(repo,'rev-parse','HEAD')!=value['code_commit']:
        raise ValueError('Source worktree code commit changed')
    common=Path(git(repo,'rev-parse','--path-format=absolute','--git-common-dir')).resolve()
    for role,row in value['trees'].items():
        tree=Path(row['root']).resolve(strict=True)
        if (tree!=workspace/role or not re.fullmatch('[0-9a-f]{40}',str(row.get('base'))) or
                row.get('pending_commit') is not None and
                    (not isinstance(row['pending_commit'],str) or not re.fullmatch('[0-9a-f]{40}',row['pending_commit'])) or
                Path(git(tree,'rev-parse','--show-toplevel')).resolve()!=tree or
                Path(git(tree,'rev-parse','--path-format=absolute','--git-common-dir')).resolve()!=common):
            raise ValueError('Source worktree role escaped its controlled repository')
    return value


def paths_for(scope,role):
    return ('status','checkpoints') if role=='state' else (
        (*PREFIXES[scope],'candidates','status') if role=='review' else PREFIXES[scope])


def prepare(repo,workspace,scope,roles,*,github_output=None,github_environment=None):
    repo=repo.resolve(strict=True);workspace=workspace.resolve()
    if scope not in PREFIXES or not roles or set(roles)-set(ROLES) or len(roles)!=len(set(roles)):
        raise ValueError('Source worktree scope or roles are invalid')
    if workspace==repo or repo.is_relative_to(workspace) or '\n' in str(workspace):
        raise ValueError('Source worktree workspace overlaps the code checkout')
    workspace.mkdir(parents=True,exist_ok=True)
    if (workspace/'trees.json').exists():
        value=read_control(workspace)
        if value['scope']!=scope or set(value['trees'])!=set(roles):
            raise ValueError('Existing source worktrees have a different scope')
        return emit(value,github_output,github_environment)
    if any(workspace.iterdir()):
        raise ValueError('Source worktree workspace must be empty on first preparation')
    git(repo,'fetch','--no-tags','--filter=blob:none','origin',
        *(role+':refs/remotes/origin/'+role for role in roles))
    value={'schema_version':SCHEMA,'repo':str(repo),'scope':scope,
           'code_commit':git(repo,'rev-parse','HEAD'),'trees':{}}
    for role in roles:
        tree=workspace/role;base=git(repo,'rev-parse','refs/remotes/origin/'+role)
        git(repo,'worktree','add','--detach','--no-checkout',tree,base)
        git(tree,'sparse-checkout','init','--cone')
        git(tree,'sparse-checkout','set',*paths_for(scope,role))
        # --no-checkout starts with an empty index. Initialize it once here;
        # repeated preparation never resets an existing, possibly dirty tree.
        git(tree,'read-tree','-mu','HEAD')
        value['trees'][role]={'root':str(tree),'base':base}
    write(workspace/'trees.json',value)
    return emit(read_control(workspace),github_output,github_environment)


def publish(workspace,roles,message):
    workspace=workspace.resolve(strict=True);value=read_control(workspace)
    if not roles or set(roles)-set(value['trees']) or len(roles)!=len(set(roles)):
        raise ValueError('Source publication roles are invalid')
    # Evidence is durable before review/state can claim it. This helper never
    # writes main, market, code, demo, health or a publication tag.
    results=[]
    for role in ROLES:
        if role not in roles:continue
        row=value['trees'][role];tree=Path(row['root'])
        head=git(tree,'rev-parse','HEAD')
        if pending:=row.get('pending_commit'):
            if head!=pending or git(tree,'rev-parse',pending+'^')!=row['base']:
                raise ValueError('Pending source commit differs from its expected parent')
            remote=git(tree,'ls-remote','origin','refs/heads/'+role).split()
            if not remote or remote[0] not in {row['base'],pending}:
                raise ValueError('Source remote head moved; refusing a stale publication')
            changed=remote[0]!=pending
            if changed:git(tree,'push','origin','HEAD:refs/heads/'+role)
            row['base']=pending;row.pop('pending_commit');write(workspace/'trees.json',value)
            results.append({'role':role,'changed':changed,'commit':pending,'recovered':True});continue
        if head!=row['base']:
            raise ValueError('Source worktree HEAD differs from its expected base')
        changed=git(tree,'status','--porcelain','--untracked-files=all')
        if not changed:
            results.append({'role':role,'changed':False,'commit':row['base']});continue
        allowed=paths_for(value['scope'],role)
        files=git(tree,'diff','--name-only','HEAD','-z').split('\0')
        files+=git(tree,'ls-files','--others','--exclude-standard','-z').split('\0')
        if any(path and path!='README.md' and not any(path.startswith(p+'/') for p in allowed) for path in files):
            raise ValueError('Source worktree contains changes outside its role scope')
        if role=='evidence' and git(tree,'diff','--name-only','--diff-filter=D','HEAD'):
            raise ValueError('Source publication cannot delete archived originals')
        remote=git(tree,'ls-remote','origin','refs/heads/'+role).split()
        if not remote or remote[0]!=row['base']:
            raise ValueError('Source remote head moved; refusing a stale publication')
        selected=[path for path in ('README.md',*allowed)
                  if (tree/path).exists() or git(tree,'ls-files','--',path)]
        git(tree,'add','--all','--',*selected)
        git(tree,'-c','user.name=github-actions[bot]','-c',
            'user.email=41898282+github-actions[bot]@users.noreply.github.com',
            'commit','-m',role+': '+message)
        commit=git(tree,'rev-parse','HEAD')
        row['pending_commit']=commit;write(workspace/'trees.json',value)
        git(tree,'push','origin','HEAD:refs/heads/'+role)
        row['base']=commit;row.pop('pending_commit');write(workspace/'trees.json',value)
        results.append({'role':role,'changed':True,'commit':commit})
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='operation',required=True)
    setup=sub.add_parser('prepare');setup.add_argument('--repo',type=Path,default=Path.cwd())
    setup.add_argument('--workspace',type=Path,required=True)
    setup.add_argument('--scope',choices=PREFIXES,required=True)
    setup.add_argument('--role',choices=ROLES,action='append',required=True)
    setup.add_argument('--github-output',type=Path)
    setup.add_argument('--github-environment',type=Path)
    commit=sub.add_parser('publish');commit.add_argument('--workspace',type=Path,required=True)
    commit.add_argument('--role',choices=ROLES,action='append',required=True)
    commit.add_argument('--message',required=True)
    commit.add_argument('--github-output',type=Path)
    args=parser.parse_args()
    try:
        result=(prepare(args.repo,args.workspace,args.scope,args.role,github_output=args.github_output,
                        github_environment=args.github_environment)
            if args.operation=='prepare' else publish(args.workspace,args.role,args.message))
        if args.operation=='publish' and args.github_output:
            emit(read_control(args.workspace),args.github_output)
    except (ValueError,OSError) as error:parser.exit(2,str(error)+'\n')
    print(json.dumps(result))
