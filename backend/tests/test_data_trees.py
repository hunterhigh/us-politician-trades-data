"""Source worktree publication against real local Git remotes."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.commands import data_trees as trees


class SourceTreeFixture:
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='source trees with spaces ');self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.repo=self.root/'code';self.remote=self.root/'remote.git'
        subprocess.run(['git','init','--bare',str(self.remote)],check=True,capture_output=True)
        subprocess.run(['git','init','-b','code',str(self.repo)],check=True,capture_output=True)
        for key,value in [('user.name','Source Tree Test'),('user.email','source@example.invalid')]:trees.git(self.repo,'config',key,value)
        (self.repo/'README.md').write_text('code only\n');self.commit(self.repo)
        self.code=trees.git(self.repo,'rev-parse','HEAD')
        trees.git(self.repo,'remote','add','origin',self.remote)
        for role,path in [('evidence','house_clerk/original.txt'),('review','house_clerk/qualification.json'),('state','status/house_clerk.json')]:
            trees.git(self.repo,'checkout','-b',role,self.code)
            self.write(self.repo/path,role+' original\n')
            if role=='evidence':self.write(self.repo/'oge/hidden.txt','other source\n')
            self.commit(self.repo)
        trees.git(self.repo,'checkout','code')
        trees.git(self.repo,'push','origin','code',*trees.ROLES)
        self.workspace=self.root/'data'

    @staticmethod
    def write(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value)

    @staticmethod
    def commit(repo):trees.git(repo,'add','--all');trees.git(repo,'commit','-m','fixture')

    def prepare(self,roles=trees.ROLES,workspace=None):
        return trees.prepare(self.repo,workspace or self.workspace,'house',roles)


class DataTreeTests(SourceTreeFixture, unittest.TestCase):
    def test_sparse_prepare_is_repeatable_and_roles_publish_in_evidence_first_order(self):
        output=self.root/'output';environment=self.root/'environment'
        prepared=trees.prepare(self.repo,self.workspace,'house',trees.ROLES,
            github_output=output,github_environment=environment)
        values=dict(line.split('=',1) for line in environment.read_text().splitlines())
        self.assertEqual(set(values),{'EVIDENCE_ROOT','REVIEW_ROOT','STATE_ROOT'})
        self.assertEqual(values['EVIDENCE_ROOT'],prepared['trees']['evidence']['root'])
        self.assertIn('evidence_commit='+prepared['trees']['evidence']['base'],output.read_text())
        evidence=Path(prepared['trees']['evidence']['root'])
        self.assertTrue((evidence/'house_clerk/original.txt').is_file())
        self.assertFalse((evidence/'oge/hidden.txt').exists())
        for role in trees.ROLES:
            root=Path(prepared['trees'][role]['root'])
            self.write(root/('status/new.json' if role=='state' else 'house_clerk/new.json'),'new\n')
        self.assertEqual(self.prepare(),prepared)
        result=trees.publish(self.workspace,tuple(reversed(trees.ROLES)),'bounded source run')
        self.assertEqual([r['role'] for r in result],list(trees.ROLES))
        for row in result:
            self.assertEqual(trees.git(self.repo,'ls-remote','origin','refs/heads/'+row['role']).split()[0],row['commit'])
        self.assertEqual(trees.git(self.repo,'rev-parse','HEAD'),self.code)
        self.assertTrue(all(not r['changed'] for r in trees.publish(self.workspace,trees.ROLES,'repeat')))

    def test_remote_movement_preserves_unpublished_work(self):
        prepared=self.prepare(('evidence',));evidence=Path(prepared['trees']['evidence']['root'])
        self.write(evidence/'house_clerk/new.json','pending work\n')
        other=self.root/'other'
        subprocess.run(['git','clone','--branch','evidence',str(self.remote),str(other)],check=True,capture_output=True)
        trees.git(other,'config','user.name','Other');trees.git(other,'config','user.email','other@example.invalid')
        self.write(other/'house_clerk/other.json','concurrent\n');self.commit(other);trees.git(other,'push','origin','evidence')
        with self.assertRaisesRegex(ValueError,'remote head moved'):
            trees.publish(self.workspace,('evidence',),'stale')
        self.assertEqual((evidence/'house_clerk/new.json').read_text(),'pending work\n')
        self.assertEqual(trees.git(evidence,'rev-parse','HEAD'),prepared['trees']['evidence']['base'])

    def test_wrong_namespace_and_deleting_originals_fail_before_committing(self):
        prepared=self.prepare(('evidence',));evidence=Path(prepared['trees']['evidence']['root'])
        self.write(evidence/'outside.json','wrong role\n')
        with self.assertRaisesRegex(ValueError,'outside its role scope'):
            trees.publish(self.workspace,('evidence',),'wrong')
        (evidence/'outside.json').unlink();(evidence/'house_clerk/original.txt').unlink()
        with self.assertRaisesRegex(ValueError,'delete archived originals'):
            trees.publish(self.workspace,('evidence',),'deleted')
        self.assertEqual(trees.git(evidence,'rev-parse','HEAD'),prepared['trees']['evidence']['base'])

    def test_source_cannot_replace_a_peer_projection(self):
        prepared=self.prepare(('review',));review=Path(prepared['trees']['review']['root'])
        self.write(review/'candidates/sources/senate_efd-current.json','peer data')
        with self.assertRaisesRegex(ValueError,'peer projection'):
            trees.publish(self.workspace,('review',),'wrong peer')
        self.assertEqual(trees.git(review,'rev-parse','HEAD'),prepared['trees']['review']['base'])

    def test_senate_failure_writer_uses_shared_publisher_and_push_errors_remain_failures(self):
        import os, sys, textwrap
        from test_senate_production_recovery import step_script, bash_path
        shell=bash_path()
        if not shell:self.skipTest('Bash required for the actual composite action')
        prepared=self.prepare(('state',));state=Path(prepared['trees']['state']['root'])
        prior={'status':'catalog_ready_for_review','catalog':{'sha256':'a'*64},
               'reports':{'evidence_count':129},'roster':{'sha256':'b'*64}}
        self.write(state/'status/senate_efd.json',json.dumps(prior))
        trees.publish(self.workspace,('state',),'prior source')
        before=trees.git(self.repo,'ls-remote','origin','refs/heads/state').split()[0]
        local=self.root/'.local';local.mkdir()
        self.write(local/'senate-efd-failure.json',json.dumps({'http_status':403,
            'request_stage':'agreement_page','error_code':'edge_access_denied','private':'hidden'}))
        code=Path(__file__).resolve().parents[2]
        action=(code/'.github/actions/publish-data/action.yml').read_text(encoding='utf-8')
        publish=textwrap.dedent(action.split('      run: |\n',1)[1])
        wrapper='python() { PYTHONPATH="$SOURCE_CODE" "$REAL_PYTHON" "$@"; }\n'
        env={**os.environ,'SOURCE_CODE':str(code/'backend/src'),'REAL_PYTHON':sys.executable,
            'STATE_ROOT':str(state),'SOURCE_STATUS':'blocked','COLLECT_OUTCOME':'success',
            'WORKFLOW_RUN_URL':'https://github.com/example/repo/actions/runs/1',
            'GITHUB_STEP_SUMMARY':str(self.root/'summary'),'GITHUB_OUTPUT':str(self.root/'output'),
            'WORKSPACE':str(self.workspace),'ROLES':'state','MESSAGE':'source failed','MSYS_NO_PATHCONV':'1'}
        def run(body):return subprocess.run([shell,'-e','-c',wrapper+body],cwd=self.root,
            env=env,capture_output=True,text=True,encoding='utf-8')
        result=run(step_script('senate.yml','Record failed catalog collection without advancing source data'))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(trees.git(self.repo,'ls-remote','origin','refs/heads/state').split()[0],before)
        result=run(publish);self.assertEqual(result.returncode,0,result.stderr)
        saved=json.loads(trees.git(self.repo,'--git-dir',self.remote,'show','state:status/senate_efd.json'))
        self.assertEqual(saved['status'],'blocked')
        self.assertEqual(saved['last_collection_attempt']['http_status'],403)
        self.assertNotIn('private',saved['last_collection_attempt'])
        for key in ('catalog','reports','roster'):self.assertEqual(saved[key],prior[key])
        self.write(state/'status/senate_efd.json',json.dumps({**saved,'status':'failed'}))
        trees.git(self.repo,'remote','set-url','origin',self.root/'missing.git')
        result=run(publish);self.assertNotEqual(result.returncode,0)
        self.assertIn('Git ls-remote failed',result.stderr)
        self.assertEqual(json.loads((state/'status/senate_efd.json').read_bytes())['status'],'failed')

    def test_interrupted_push_and_lost_acknowledgement_recover_without_new_commit(self):
        real_git=trees.git
        for index,after_push in enumerate((False,True)):
            with self.subTest(after_push=after_push):
                workspace=self.root/('retry-'+str(index));prepared=self.prepare(('evidence',),workspace)
                evidence=Path(prepared['trees']['evidence']['root'])
                self.write(evidence/('house_clerk/retry-'+str(index)+'.json'),'durable pending commit\n')
                def fail(repo,*args):
                    if args[0]=='push':
                        if after_push:real_git(repo,*args)
                        raise ValueError('simulated lost connection')
                    return real_git(repo,*args)
                with patch.object(trees,'git',side_effect=fail),self.assertRaisesRegex(ValueError,'lost connection'):
                    trees.publish(workspace,('evidence',),'recoverable')
                pending=json.loads((workspace/'trees.json').read_bytes())['trees']['evidence']['pending_commit']
                result=trees.publish(workspace,('evidence',),'resume')
                self.assertTrue(result[0]['recovered']);self.assertEqual(result[0]['commit'],pending)
                self.assertEqual(real_git(evidence,'rev-parse','HEAD'),pending)
                self.assertEqual(real_git(evidence,'rev-parse',pending+'^'),prepared['trees']['evidence']['base'])


if __name__=='__main__':unittest.main()
