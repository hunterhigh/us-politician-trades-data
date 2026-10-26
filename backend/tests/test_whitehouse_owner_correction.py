"""Generated native PDF + real Git/CLI migration contracts, not OCR accuracy."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.pipeline_qa.migration_inventory import InventoryError
from unison_snapshot.pipeline_qa.whitehouse_owner_correction import (
    _historical_instrument, _historical_projection, verified_owner_corrections)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n').encode()


def fixture(extra=None, asset='Example Common Stock See Endnote (EXM)'):
    import pymupdf
    from unison_snapshot.whitehouse_disclosures import parse_disclosure_index
    from unison_snapshot.whitehouse_278t import parse_generic_whitehouse_278t_pdf, PARSER_VERSION
    from unison_snapshot.oge_reports import parse_table_rows
    from unison_snapshot.oge_whitehouse import coverage_from_archive
    from unison_snapshot.oge_candidate import _identity_key, _person_id
    from unison_snapshot.whitehouse_278t_candidate import _person
    from tests.test_whitehouse_278t_candidate import _base
    from scripts.whitehouse_coverage import build_coverage
    url='https://www.whitehouse.gov/wp-content/uploads/2026/06/Example-PTR.pdf'
    doc='wh-url:'+hashlib.sha256(url.encode()).hexdigest()[:24]
    label='Example, Ada - Periodic Transaction Report 06.13.26'
    html=(f'<details><summary>Financial Disclosure Reports</summary></details>'
          f'<details><summary>Transaction Reports</summary><a href="{url}">{label}</a></details>').encode()
    index=parse_disclosure_index(html);page_sha=index['page_sha256']
    document=pymupdf.open();page=document.new_page(width=700,height=792)
    page.insert_text((30,40),"Periodic Transaction Report (OGE Form 278-T)\nFiler's Information\nExample, Ada\nDirector - White House\nFiler's Certification\nDate: 06/13/2026\nAgency Ethics Official's Opinion\nDate: 06/15/2026")
    page=document.new_page(width=700,height=792);page.insert_text((30,40),'Transactions')
    edges=[30,50,350,410,480,550,680]
    table=[['#','DESCRIPTION','TYPE','DATE','NOTIFICATION','AMOUNT']]
    if extra=='scope':table.append(['','Spouse retirement account unclear','','','',''])
    table.append(['1',asset,'Purchase','06/01/2026','No','$1,001 - $15,000'])
    if extra=='unrelated':table.append(['2','Unrelated Company (OTH)','Purchase','06/02/2026','No','unread amount'])
    for x in edges:page.draw_line((x,100),(x,100+len(table)*25))
    for y in range(100,101+len(table)*25,25):page.draw_line((30,y),(680,y))
    for row_index,values in enumerate(table):
        y=115+row_index*25
        for x,text in zip(edges,values):page.insert_text((x+2,y),text,fontsize=8)
    pdf=document.tobytes();document.close();sha=hashlib.sha256(pdf).hexdigest()
    pdfpath=f'whitehouse/disclosures/reports/{doc[7:]}/{sha}.pdf'
    folder=f'whitehouse/extractions/{doc[7:]}/{sha}'
    metadata={**index['reports'][0], 'schema_version':'whitehouse-public-disclosures-pdf/v1',
        'document_id':doc,'sha256':sha,'byte_length':len(pdf),'archive_path':pdfpath,'page_sha256':page_sha}
    catrow={'type':"<a href='https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/"+'a'*32+"/$FILE/Example.pdf'>278 Transaction</a>",
        'name':'Example, Ada','agency':'White House Office','title':'Director','level':'I',
        'docDate':'2026-06-14T00:00:00','amended':''}
    catpage=encoded({'draw':1,'recordsTotal':1,'recordsFiltered':1,'data':[catrow]})
    catsha=hashlib.sha256(catpage).hexdigest();catalogsha='c'*64
    catpath=f'oge/catalog/pages/{catsha}.json';catmetapath=f'oge/catalog/{catalogsha}.json'
    catmeta={'schema_version':'oge-catalog-archive/v1','sha256':catalogsha,'record_count':1,
        'retrieved_at':'2026-06-14T00:00:00Z','pages':[{'start':0,'archive_path':catpath,
            'sha256':catsha,'byte_length':len(catpage)}]}
    evidence={pdfpath:pdf,pdfpath[:-4]+'.json':encoded(metadata),
        f'whitehouse/disclosures/pages/{page_sha}.html':html,
        f'whitehouse/disclosures/pages/{page_sha}.json':encoded({'sha256':page_sha,
            'schema_version':'whitehouse-public-disclosures-page/v1','source_id':'whitehouse_public_disclosures',
            'archive_path':f'whitehouse/disclosures/pages/{page_sha}.html',
            'page_url':index['page_url'],'byte_length':len(html)}),
        catmetapath:encoded(catmeta),catpath:catpage}
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary)
        for path,raw in evidence.items():
            target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        coverage=coverage_from_archive(root,root/catmetapath)
        native=parse_generic_whitehouse_278t_pdf(root/pdfpath,source_url=url,source_sha256=sha,
            document_id=doc,filer_name='Example, Ada',amended_label=label)
        oldrows,bad=parse_table_rows([(r['page_number'],r['cells']) for r in native['source_rows']],source_sha=sha)
        assert len(oldrows)==1, (oldrows,bad,native)
        legacy={'schema_version':'whitehouse-278t-extraction/v1','parser_version':PARSER_VERSION,
            'document_id':doc,'source_url':url,'source_sha256':sha,'filer_name':'Example, Ada',
            'transactions':oldrows,'quarantined':bad}
        native=parse_generic_whitehouse_278t_pdf(root/pdfpath,source_url=url,source_sha256=sha,
            document_id=doc,filer_name='Example, Ada',amended_label=label,legacy_extraction=legacy)
        review={f'{folder}/whitehouse-278t-native-roles-v5.json':encoded(native),
                f'{folder}/whitehouse-278t-hybrid-geometry-v2.json':encoded(legacy),
                'oge/whitehouse/coverage-current.json':encoded(coverage),
                'whitehouse/disclosures/index-current.json':encoded(index)}
        for path,raw in review.items():
            target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        batch={'schema_version':'whitehouse-public-disclosures-batch/v1','indexed_count':1,
               'reports':[metadata],'failures':[]}
        review['whitehouse/coverage-current.json']=encoded(build_coverage(index,batch,root,generic_trade=True))
    review['whitehouse/extraction-status.json']=encoded({'schema_version':'whitehouse-public-extraction-status/v1',
        'source_id':'whitehouse_public_disclosures','pending_count':0})
    identity=coverage['reports'][0];person=_person(identity);personid=_person_id(_identity_key(identity))
    old=_historical_projection(native,oldrows[0],personid)
    base=_base();base['people']=[person];base['transactions']=[old];base['meta']['data_cutoff_at']='2026-06-20T00:00:00Z'
    return old,{**old,'owner':'Undisclosed'},review,evidence,base


class WhiteHouseOwnerCorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=fixture()

    def verify(self,data=None,**kwargs):
        old,current,review,evidence,base=deepcopy(data or self.data)
        raw=encoded(base);digest=hashlib.sha256(raw).hexdigest()
        formal={'manifest.json':encoded({'is_demo':False,'board':digest}),f'board/{digest}.json':raw}
        options=dict(old={old['id']:old},current={current['id']:current},repo='unused',
            evidence_commit='e'*40,review_commit='b'*40,main_commit='a'*40,
            previous_people=base['people'],
            read_object=lambda repo,commit,path:(formal if commit=='a'*40 else evidence if commit=='e'*40 else review)[path])
        options.update(kwargs)
        return verified_owner_corrections(**options)

    def test_original_cells_reconstruct_historical_projection_and_only_owner(self):
        result=self.verify();self.assertEqual(result[0]['changed_fields'],['owner'])
        old,current,*_=self.data
        self.assertEqual(old['instrument_type'],'Bond');self.assertEqual(current['instrument_type'],'Bond')
        for text,ticker,answer in [('Ordinary Stock','EXM','Stock'),('Senior Notes Due 2035',None,'Bond'),
                                  ('Example ETF',None,'ETF'),('Example Fund',None,'Mutual Fund'),
                                  ('Unspecified asset',None,'Unspecified'),('See Endnote',None,'Bond')]:
            self.assertEqual(_historical_instrument(text,ticker),answer)

    def test_wrong_old_values_both_sides_are_not_reconstructed_from_current(self):
        for field,value in [('amount_low',1002),('asset_name','FORGED ASSET'),('id','oge-278t:'+'0'*24),
                            ('instrument_type','Stock'),('person_id','oge:'+'0'*16),('source','Another source'),
                            ('amount_low_exclusive',False),('option_type','call'),('ticker_mapping_basis','market'),
                            ('unknown_optional',None)]:
            data=deepcopy(self.data);data[0][field]=value;data[1][field]=value
            with self.subTest(field=field),self.assertRaises(InventoryError):self.verify(data)

    def test_fixed_main_identity_and_source_projection_are_independent_constraints(self):
        data=deepcopy(self.data)
        altered=deepcopy(data[4]['people']);altered[0]['role']='A forged previous role'
        with self.assertRaisesRegex(InventoryError,'previous identity differs from fixed main'):
            self.verify(data,previous_people=altered)
        # Even a formally pinned predecessor cannot grant an unsupported old
        # classifier: the historic projection still has to reproduce it.
        for row in (data[0],data[1],data[4]['transactions'][0]):row['instrument_type']='Stock'
        with self.assertRaisesRegex(InventoryError,'complete old and new formal objects'):self.verify(data)

    def test_combined_fields_other_owner_and_other_source_do_not_get_permission(self):
        for field,value in [('amount_high',50000),('instrument_type','Stock'),('owner','Spouse'),
                            ('id','oge-278t:'+'0'*24),('source_id','house_clerk')]:
            data=deepcopy(self.data);data[1][field]=value
            with self.subTest(field=field):self.assertEqual(self.verify(data),[])

    def test_fixed_ref_archive_version_raw_fields_and_directory_tampering_fail(self):
        for role,token,mutate in [
            (2,'native-roles',lambda x:x['transactions'][0].update(asset_name='FORGED ASSET')),
            (2,'hybrid-geometry',lambda x:x['transactions'][0].update(amount_low=1002)),
            (2,'whitehouse/coverage-current',lambda x:x['reports'][0].update(archive_sha256_versions=['0'*64])),
            (2,'whitehouse/coverage-current',lambda x:x['reports'][0].update(extraction_parser_version='unsupported')),
            (3,'.pdf',None),
            (3,'/pages/',None),
            (2,'oge/whitehouse/coverage-current',lambda x:x['reports'][0].update(position_title='Unrelated Position'))]:
            data=deepcopy(self.data);path=next(p for p in data[role] if
                (p==token+'.json' if token.endswith('coverage-current') else token in p) and
                (p.endswith('.pdf') if token=='.pdf' else p.endswith('.json') if mutate else p.endswith('.html')))
            if mutate:
                value=json.loads(data[role][path]);mutate(value);data[role][path]=encoded(value)
            else:data[role][path]+=b' altered'
            with self.subTest(path=path),self.assertRaises(InventoryError):self.verify(data)
        for kwargs in ({'review_commit':'invalid'},{'evidence_commit':'d'*40},{'main_commit':None},{'repo':None}):
            with self.subTest(kwargs=kwargs),self.assertRaises(InventoryError):self.verify(**kwargs)
        data=deepcopy(self.data);path=next(p for p in data[2] if 'hybrid-geometry' in p);del data[2][path]
        with self.assertRaises(InventoryError):self.verify(data)

    def test_limits_fail_before_native_parse(self):
        with patch('unison_snapshot.pipeline_qa.whitehouse_owner_correction.MAX_TOTAL_BYTES',10):
            with self.assertRaisesRegex(InventoryError,'byte budget'):self.verify()
        with patch('unison_snapshot.pipeline_qa.whitehouse_owner_correction.MAX_PAGES',1):
            with self.assertRaisesRegex(InventoryError,'page count'):self.verify()

    def test_official_amendment_label_cannot_be_dropped_by_saved_cache(self):
        data=deepcopy(self.data);evidence=data[3]
        pdfmeta=next(path for path in evidence if '/reports/' in path and path.endswith('.json'))
        metadata=json.loads(evidence[pdfmeta]);oldsha=metadata['page_sha256']
        html=evidence[f'whitehouse/disclosures/pages/{oldsha}.html'].replace(b'06.13.26</a>',b'06.13.26 (Amended)</a>')
        self.assertIn(b'(Amended)',html)
        sha=hashlib.sha256(html).hexdigest();prefix=f'whitehouse/disclosures/pages/{sha}'
        pagemeta=json.loads(evidence[f'whitehouse/disclosures/pages/{oldsha}.json'])
        pagemeta.update(sha256=sha,archive_path=prefix+'.html',byte_length=len(html))
        evidence[prefix+'.html']=html;evidence[prefix+'.json']=encoded(pagemeta)
        metadata.update(page_sha256=sha,link_label=metadata['link_label']+' (Amended)')
        evidence[pdfmeta]=encoded(metadata)
        path=next(path for path in data[2] if 'native-roles' in path)
        native=json.loads(data[2][path]);native['amended_label']=None;data[2][path]=encoded(native)
        with self.assertRaisesRegex(InventoryError,'official directory identity differs'):self.verify(data)

    def test_original_html_suffices_without_redundant_page_metadata(self):
        data=deepcopy(self.data)
        path=next(path for path in data[3] if '/disclosures/pages/' in path and path.endswith('.json'))
        del data[3][path]
        self.assertEqual(self.verify(data)[0]['changed_fields'],['owner'])

    def test_only_target_url_directory_quarantine_blocks_identity(self):
        for target in (True,False):
            data=deepcopy(self.data);evidence=data[3]
            pdfmeta=next(path for path in evidence if '/reports/' in path and path.endswith('.json'))
            metadata=json.loads(evidence[pdfmeta]);oldsha=metadata['page_sha256']
            other=(metadata['document_url'] if target else 'https://unrelated.invalid/file.pdf')
            extra=f'<a href="{other}">Someone, Else - Periodic Transaction Report (Amended)</a></details>'.encode()
            original=evidence[f'whitehouse/disclosures/pages/{oldsha}.html']
            html=original.rsplit(b'</details>',1)[0]+extra
            sha=hashlib.sha256(html).hexdigest();evidence[f'whitehouse/disclosures/pages/{sha}.html']=html
            metadata['page_sha256']=sha;evidence[pdfmeta]=encoded(metadata)
            if target:
                with self.assertRaisesRegex(InventoryError,'official directory identity differs'):self.verify(data)
            else:self.assertEqual(self.verify(data)[0]['changed_fields'],['owner'])

    def test_existing_official_directory_person_reuse_is_not_rejected(self):
        from unison_snapshot.oge_whitehouse import build_whitehouse_coverage
        data=deepcopy(self.data)
        pagepath=next(path for path in data[3] if '/catalog/pages/' in path)
        payload=json.loads(data[3][pagepath]);payload['data'][0]['name']='Someone, Else'
        raw=encoded(payload);sha=hashlib.sha256(raw).hexdigest();newpath=f'oge/catalog/pages/{sha}.json'
        data[3][newpath]=raw
        metapath=next(path for path in data[3] if '/catalog/' in path and '/pages/' not in path)
        metadata=json.loads(data[3][metapath]);metadata['pages'][0].update(archive_path=newpath,sha256=sha,byte_length=len(raw))
        data[3][metapath]=encoded(metadata)
        coverage=build_whitehouse_coverage([(0,payload)])
        coverage.update(catalog_sha256=metadata['sha256'],catalog_retrieved_at=metadata['retrieved_at'])
        data[2]['oge/whitehouse/coverage-current.json']=encoded(coverage)
        self.assertEqual(self.verify(data)[0]['changed_fields'],['owner'])
        data[4]['people'][0]['display_name']='Other, Person'
        with self.assertRaises(InventoryError):self.verify(data)

    def test_unrelated_quarantined_row_does_not_block_target_but_unknown_scope_does(self):
        data=fixture(extra='unrelated')
        native=json.loads(next(raw for path,raw in data[2].items() if 'native-roles' in path))
        self.assertEqual(len(native['quarantined']),1)
        self.assertEqual(self.verify(data)[0]['changed_fields'],['owner'])
        with self.assertRaises(InventoryError):self.verify(fixture(extra='scope'))

    def test_normal_candidate_then_real_git_delta_cli_automatic_explicit_and_repeat(self):
        # Classification has its own migration now. Keep this normal CLI
        # contract exercising the distinct owner-only Bond -> Bond behavior.
        old,expected,review,evidence,base=fixture(asset='Example Treasury Bond (EXM)')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);repo=root/'repo';repo.mkdir();project=Path(__file__).resolve().parents[2]
            env=dict(os.environ,PYTHONPATH=str(project/'backend/src'),GIT_NO_LAZY_FETCH='1')
            def git(*args):
                return subprocess.check_output(['git','-C',str(repo),*args],stderr=subprocess.STDOUT).decode().strip()
            git('init','-q');git('config','user.email','contract@example.invalid');git('config','user.name','Contract')
            def save(path,value):
                path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value if isinstance(value,bytes) else encoded(value))
            for path,raw in {**evidence,**review}.items():save(repo/path,raw)
            raw=encoded(base);digest=hashlib.sha256(raw).hexdigest()
            save(repo/'manifest.json',{'is_demo':False,'board':digest});save(repo/f'board/{digest}.json',raw)
            git('add','.');git('commit','-qm','source contract');commit=git('rev-parse','HEAD')
            save(root/'base.json',base)
            cmd=[sys.executable,str(project/'backend/scripts/build_whitehouse_278t_candidate.py'),
                '--oge-candidate',str(root/'base.json'),'--review-root',str(repo),'--evidence-root',str(repo),
                '--candidate-out',str(root/'candidate.json'),'--audit-out',str(root/'audit.json'),'--expected-report-count','1']
            result=subprocess.run(cmd,capture_output=True,text=True,env=env)
            self.assertEqual(result.returncode,0,result.stderr)
            candidate=json.loads((root/'candidate.json').read_bytes());self.assertEqual(candidate['transactions'],[expected])
            sources=root/'sources'
            for name in ['house_clerk','oge','senate_efd']:
                save(sources/f'{name}-current.json',candidate if name=='oge' else
                    {**candidate,'people':[],'transactions':[],'reported_holdings':[]})
            output=root/'delta.json'
            gate=[sys.executable,'-m','unison_snapshot.pipeline_qa.publication_delta','--previous-board',str(root/'base.json'),
                '--unified',str(root/'candidate.json'),'--prepared',str(root/'candidate.json'),
                '--source-root',str(sources),'--output',str(output),'--repo',str(repo),
                '--evidence-commit',commit,'--review-commit',commit,'--main-commit',commit]
            result=subprocess.run(gate,capture_output=True,text=True,env=env);self.assertEqual(result.returncode,0,result.stderr)
            first=output.read_bytes();delta=json.loads(first);proof=delta['changes'][0]
            self.assertEqual(delta['counts']['transactions']['corrected'],1)
            self.assertIn('whitehouse_native_owner',delta['correction_verification_level'])
            result=subprocess.run(gate,capture_output=True,text=True,env=env);self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(output.read_bytes(),first)
            transition={'schema_version':'pipeline-fact-transitions/v1','from_main_commit':commit,
                'to_review_commit':commit,'evidence_commit':commit,'records':[proof]}
            save(root/'transitions.json',transition)
            result=subprocess.run(gate+['--transitions',str(root/'transitions.json')],capture_output=True,text=True,env=env)
            self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(output.read_bytes(),first)
            for badproof in ({**proof,'changed_fields':['owner','instrument_type']},{**proof,'source_sha256':'0'*64}):
                transition['records']=[badproof];save(root/'transitions.json',transition);output.write_bytes(b'sentinel')
                result=subprocess.run(gate+['--transitions',str(root/'transitions.json')],capture_output=True,text=True,env=env)
                self.assertNotEqual(result.returncode,0);self.assertEqual(output.read_bytes(),b'sentinel')
            for field,value in [('amount_low',1002),('asset_name','Changed source characters'),
                                ('id','oge-278t:'+'0'*24),('amount_low_exclusive',False),
                                ('instrument_type','Stock'),('source_url',old['source_url'].replace('Example','Other'))]:
                altered_base,altered_candidate=deepcopy(base),deepcopy(candidate)
                altered_base['transactions'][0][field]=value;altered_candidate['transactions'][0][field]=value
                save(root/'base.json',altered_base);save(root/'candidate.json',altered_candidate)
                save(sources/'oge-current.json',altered_candidate);output.write_bytes(b'sentinel')
                result=subprocess.run(gate,capture_output=True,text=True,env=env)
                with self.subTest(field=field):
                    self.assertNotEqual(result.returncode,0);self.assertEqual(output.read_bytes(),b'sentinel')
            save(root/'base.json',base);save(root/'candidate.json',candidate);save(sources/'oge-current.json',candidate)
            altered_base,altered_candidate=deepcopy(base),deepcopy(candidate)
            for value in (altered_base,altered_candidate):
                value['people'][0]['id']='oge:'+'0'*16
                value['transactions'][0]['person_id']='oge:'+'0'*16
            save(root/'base.json',altered_base);save(root/'candidate.json',altered_candidate)
            save(sources/'oge-current.json',altered_candidate);output.write_bytes(b'sentinel')
            result=subprocess.run(gate,capture_output=True,text=True,env=env)
            self.assertNotEqual(result.returncode,0);self.assertEqual(output.read_bytes(),b'sentinel')
            save(root/'base.json',base);save(root/'candidate.json',candidate);save(sources/'oge-current.json',candidate)
            # A new fixed tree with forged fields still cannot self-authorize.
            path=next(p for p in review if 'native-roles' in p);value=json.loads(review[path])
            value['transactions'][0]['asset_name']='FORGED ASSET';save(repo/path,value)
            git('add','.');git('commit','-qm','forged cache');badcommit=git('rev-parse','HEAD')
            badgate=gate[:];badgate[badgate.index('--review-commit')+1]=badcommit;output.write_bytes(b'sentinel')
            result=subprocess.run(badgate,capture_output=True,text=True,env=env)
            self.assertNotEqual(result.returncode,0);self.assertEqual(output.read_bytes(),b'sentinel')


if __name__=='__main__':unittest.main()
