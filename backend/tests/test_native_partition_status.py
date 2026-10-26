"""Real normal native consumers -> source-replayed status -> public reader."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import unittest

import test_native_partition_consumption as consumers
from whitehouse_account_display_fixture import encoded, write_files
from unison_snapshot.disclosure_status_oge import build_oge_status
from unison_snapshot.native_partition_rows import partition_binding
from unison_snapshot.oge import OgeCatalogError


class NativePartitionStatusTests(unittest.TestCase):
    def setUp(self):
        self.consumer=consumers.NativePartitionConsumptionTests()
        self.consumer.setUp()
        self.addCleanup(self.consumer.doCleanups)

    def prepare(self, *, whitehouse, generic, kind='hidden_scope'):
        h=self.consumer
        if whitehouse:
            root,metadata,_=h.wh_inputs(kind)
            h.wh_extract_coverage(root,metadata,generic)
            candidate,_=h.wh_candidate(root)
            evidence,review=root/'evidence',root/'review'
            coverage=json.loads((review/'whitehouse/coverage-current.json').read_bytes())
            cache=review/coverage['reports'][0]['extraction_path']
            scope='whitehouse_public'
        else:
            root,metadata,catalog=h.oge_inputs(kind)
            extraction=h.oge_extract(root,generic)
            candidate=h.oge_candidate(root,generic)
            evidence,review=root,root/'review'
            cache=review/f"oge/extractions/{metadata['document_id']}/{metadata['sha256']}/{extraction['parser_version'].replace('/','-')}.json"
            write_files(review,{str(cache.relative_to(review)):encoded(extraction)})
            raw=encoded(catalog);sha=hashlib.sha256(raw).hexdigest()
            write_files(evidence,{f'oge/catalog/normalized/{sha}.json':raw,
                f'oge/catalog/{sha}.json':encoded({'schema_version':'oge-catalog-archive/v1','sha256':sha,'retrieved_at':'2026-09-20T00:00:00Z'})})
            write_files(review,{'status/oge.json':encoded({'catalog_sha256':sha})})
            scope='oge_direct_278t'
        audit=json.loads((root/'audit.json').read_bytes())
        audit_path=review/('whitehouse/qualifications/candidate-current.json' if whitehouse else 'oge/qualifications/current.json')
        write_files(review,{str(audit_path.relative_to(review)):encoded(audit)})
        return dict(root=root,evidence=evidence,review=review,candidate=candidate,audit=audit,
            audit_path=audit_path,metadata=metadata,cache=cache,scope=scope,whitehouse=whitehouse)

    def status(self,data):
        return build_oge_status(data['evidence'],data['review'],data['candidate']['transactions'],[])

    def record(self,data,scopes):
        return next(row for scope in scopes if scope['scope_id']==data['scope']
            for row in scope['reports'] if row['report_id']==data['metadata']['document_id'])

    def reader(self,data,scopes,provenance=None):
        from unison_snapshot.builder import build
        from unison_snapshot.disclosure_status import attach_disclosure_status
        from unison_snapshot.public_repo import PublicSnapshotRepository
        from test_public_repo import FakeTransport
        from test_disclosure_status import PROVENANCE
        candidate=data['candidate']
        bundle=build(candidate,generated_at='2026-09-20T00:01:00Z',allow_production=True)
        bundle=attach_disclosure_status(bundle,scopes,transactions=candidate['transactions'],provenance=provenance or PROVENANCE)
        public=PublicSnapshotRepository('contract','data',transport=FakeTransport(bundle.files))
        self.assertEqual(public.fetch().snapshot['transactions'],candidate['transactions'])
        status=public.disclosure_status(public.resolve(),include_records=True)
        self.assertIsNotNone(status)
        return status

    def test_default_generic_both_sources_expose_visible_facts_and_partial_status(self):
        for whitehouse in (False,True):
            for generic in (False,True):
                for kind,count in [('overlay',2),('hidden_scope',1)]:
                    with self.subTest(whitehouse=whitehouse,generic=generic,kind=kind):
                        data=self.prepare(whitehouse=whitehouse,generic=generic,kind=kind)
                        scopes=self.status(data);record=self.record(data,scopes)
                        self.assertEqual(record['qualification_state'],'partial')
                        self.assertEqual(record['qualified_transaction_count'],count)
                        self.assertEqual(len(record.get('publication_transaction_ids',[])),count)
                        self.assertFalse(record['source_observation_coverage']['coverage_complete'])
                        self.assertEqual(record['source_versions'],[data['metadata']['sha256']])
                        self.assertEqual(self.status(data),scopes)
                        self.reader(data,scopes)

    def test_no_qualified_rows_never_become_zero_report(self):
        for whitehouse in (False,True):
            for generic in (False,True):
                with self.subTest(whitehouse=whitehouse,generic=generic):
                    data=self.prepare(whitehouse=whitehouse,generic=generic,kind='hidden_header')
                    record=self.record(data,self.status(data))
                    self.assertNotEqual(record['qualification_state'],'zero_transactions')
                    self.assertIn(record['qualification_state'],{'quarantined','unknown'})
                    self.assertEqual(record.get('publication_transaction_ids',[]),[])
                    self.assertFalse(record['source_observation_coverage']['coverage_complete'])

    def test_selected_new_cache_replays_and_resigned_observation_is_not_authority(self):
        for whitehouse in (False,True):
            with self.subTest(whitehouse=whitehouse):
                data=self.prepare(whitehouse=whitehouse,generic=True)
                original=data['cache'].read_bytes();expected=self.status(data)
                # Historical same-SHA versions cannot replace the chosen cache.
                old='whitehouse-278t-native-roles-v5.json' if whitehouse else 'oge-278t-pdf-v2.json'
                (data['cache'].parent/old).write_bytes(encoded({'historical_reference_only':True}))
                self.assertEqual(self.status(data),expected)
                report=json.loads(original)
                report['native_text_partition']['scope_barriers_before']={}
                data['cache'].write_bytes(encoded(report))
                changed=deepcopy(data['audit'])
                changed['reports'][0]['native_source_partition_binding']=partition_binding(report['native_text_partition'],report['parser_version'])
                data['audit_path'].write_bytes(encoded(changed))
                with self.assertRaises((ValueError,OgeCatalogError)):self.status(data)
                data['cache'].write_bytes(original);data['audit_path'].write_bytes(encoded(data['audit']))
                pdf=data['evidence']/data['metadata']['archive_path'];raw=pdf.read_bytes()
                pdf.write_bytes(raw+b'\n% altered source')
                with self.assertRaises((ValueError,OgeCatalogError)):self.status(data)
                pdf.write_bytes(raw)
                self.assertEqual(self.status(data),expected)

    def test_unproved_page_keeps_original_document_blockers_without_zero_claim(self):
        for whitehouse in (False,True):
            for generic in (False,True):
                with self.subTest(whitehouse=whitehouse,generic=generic):
                    data=self.prepare(whitehouse=whitehouse,generic=generic,kind='unsupported')
                    report=json.loads(data['cache'].read_bytes())
                    self.assertTrue(report['document_reasons'])
                    original=self.status(data);record=self.record(data,original)
                    self.assertEqual(record['qualification_state'],'quarantined')
                    self.assertFalse(record['source_observation_coverage']['coverage_complete'])
                    self.assertEqual(record.get('publication_transaction_ids',[]),[])
                    changed=deepcopy(data['audit']);changed['reports'][0]['document_reasons']=[]
                    data['audit_path'].write_bytes(encoded(changed))
                    with self.assertRaises(ValueError):self.status(data)
                    data['audit_path'].write_bytes(encoded(data['audit']))
                    self.assertEqual(self.status(data),original)

    def test_whitehouse_changed_input_repeat_counts_strict_retained_current_ids(self):
        for generic in (False,True):
            with self.subTest(generic=generic):
                data=self.prepare(whitehouse=True,generic=generic,kind='overlay')
                original=deepcopy(data['candidate']['transactions'])
                changed=deepcopy(data['candidate']);changed['meta']['title']='Harmless changed input title'
                path=data['root']/'changed-base.json';path.write_bytes(encoded(changed))
                candidate,result=self.consumer.wh_candidate(data['root'],input_path=path)
                self.assertFalse(result['idempotent'])
                self.assertEqual(candidate['transactions'],original)
                data['candidate']=candidate;data['audit']=json.loads((data['root']/'audit.json').read_bytes())
                data['audit_path'].write_bytes(encoded(data['audit']))
                report=data['audit']['reports'][0]
                self.assertEqual(report['promoted_count'],0)
                self.assertEqual(len(report['retained_ids']),2)
                record=self.record(data,self.status(data))
                self.assertEqual(record['qualified_transaction_count'],2)
                self.assertEqual(record['qualified_retained_transaction_ids'],report['retained_ids'])
                self.reader(data,self.status(data))

    def test_replayed_source_does_not_authorize_forged_qualification_totals(self):
        for whitehouse in (False,True):
            with self.subTest(whitehouse=whitehouse):
                data=self.prepare(whitehouse=whitehouse,generic=False)
                cases=[]
                for field in ('promoted_count','quarantined_count'):
                    changed=deepcopy(data['audit']);changed['reports'][0][field]=99
                    cases.append((field,changed))
                if whitehouse:
                    changed=deepcopy(data['audit']);changed['reports'][0]['retained_count']=99
                    cases.append(('retained_count',changed))
                    changed=deepcopy(data['audit']);report=changed['reports'][0]
                    rejected=next(row for row in report['rows'] if row['status']=='quarantined')
                    rejected.update(status='promoted',reasons=[])
                    report['promoted_count']+=1;report['quarantined_count']-=1
                    cases.append(('promote_bad_source_row',changed))
                    changed=deepcopy(data['audit'])
                    next(row for row in changed['reports'][0]['rows'] if row['status']=='quarantined')['reasons']=[]
                    cases.append(('erase_source_blocker',changed))
                else:
                    changed=deepcopy(data['audit']);changed['reports'][0].update(quarantined=[],quarantined_count=0)
                    cases.append(('erase_source_rejections',changed))
                    changed=deepcopy(data['audit']);changed['reports'][0]['quarantined'][0]['reasons']=[]
                    cases.append(('erase_source_blocker',changed))
                for label,changed in cases:
                    with self.subTest(tamper=label):
                        data['audit_path'].write_bytes(encoded(changed))
                        with self.assertRaises(ValueError):self.status(data)

    def test_fixed_tree_status_cli_and_public_reader_repeat(self):
        data=self.prepare(whitehouse=True,generic=False,kind='hidden_scope')
        for tree in (data['evidence'],data['review']):
            for args in [('init','-q'),('config','user.name','Contract'),
                         ('config','user.email','contract@example.invalid'),('add','.'),('commit','-qm','fixed native status inputs')]:
                subprocess.run(['git','-C',str(tree),*args],check=True,capture_output=True)
        output=data['root']/'status-input.json'
        args=[consumers.PROJECT/'backend/scripts/build_disclosure_status.py',
            '--evidence-root',data['evidence'],'--review-root',data['review'],
            '--input',data['root']/'candidate.json','--code-commit','b'*40,
            '--previous-main-commit','a'*40,'--output',output]
        self.consumer.run_cli(args)
        before=output.read_bytes();status=json.loads(before)
        record=self.record(data,status['scopes'])
        self.assertEqual(record['qualification_state'],'partial')
        self.assertEqual(record['qualified_transaction_count'],1)
        self.reader(data,status['scopes'],status['provenance'])
        self.consumer.run_cli(args)
        self.assertEqual(output.read_bytes(),before)
        # A newly fixed review commit does not turn a self-signed projection
        # into source evidence, and failure must preserve the old status file.
        report=json.loads(data['cache'].read_bytes())
        report['native_text_partition']['scope_barriers_before']={}
        data['cache'].write_bytes(encoded(report))
        audit=deepcopy(data['audit'])
        audit['reports'][0]['native_source_partition_binding']=partition_binding(
            report['native_text_partition'],report['parser_version'])
        data['audit_path'].write_bytes(encoded(audit))
        for git_args in [('add','.'),('commit','-qm','self-signed invalid projection')]:
            subprocess.run(['git','-C',str(data['review']),*git_args],check=True,capture_output=True)
        self.consumer.run_cli(args,success=False)
        self.assertEqual(output.read_bytes(),before)
