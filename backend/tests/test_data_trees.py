"""Source worktree publication against real local Git remotes."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.commands import data_trees as trees


class DataTreeTests(unittest.TestCase):
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
