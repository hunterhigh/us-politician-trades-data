"""Source-replayed directory attribution, separate from OCR and filing dates."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import io

from test_whitehouse_278t_saved_ocr import SavedWhiteHouseTests
from test_whitehouse_278t_candidate import _base
from test_oge_dual_axis_fields import dual_input
from unison_snapshot.whitehouse_278t import (materialize_saved_whitehouse_278t,
    validate_generic_whitehouse_report, saved_ocr_source_roots, DUAL_AXIS_MODE)
from unison_snapshot.whitehouse_disclosures import parse_disclosure_index
from unison_snapshot.oge_whitehouse import coverage_from_archive
from unison_snapshot.whitehouse_278t_audit import audit_whitehouse_278t
from unison_snapshot.whitehouse_278t_candidate import build_whitehouse_278t_review_candidate
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.builder import build


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw=json.dumps(value,sort_keys=True).encode();path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def fixture(root, *, info='', name='Wiles, Susie', extra_catalog=None):
    case=SavedWhiteHouseTests();inputs,_=case.fixture(root)
    inputs['geometry_mode']=DUAL_AXIS_MODE
    meta_path=Path(inputs['archive_metadata_path']);meta=json.loads(meta_path.read_bytes())
    url=meta['document_url']
    html=(f'<details><summary>Financial Disclosure Reports</summary></details>'
          f'<details><summary>Transaction Reports</summary><a href="{url}">{name} - Periodic Transaction Report 06.13.26</a></details>').encode()
    index=parse_disclosure_index(html);page_sha=index['page_sha256']
    page_rel=f'whitehouse/disclosures/pages/{page_sha}.html';path=root/page_rel;path.parent.mkdir(parents=True);path.write_bytes(html)
    index['page_archive']={'archive_path':page_rel,'sha256':page_sha,'byte_length':len(html)}
    meta.update(page_sha256=page_sha,filer_name_from_label=name,source_id='whitehouse_public_disclosures',
                link_label=f'{name} - Periodic Transaction Report 06.13.26')
    inputs['archive_metadata_sha256']=write(meta_path,meta)
    rows=[{'type':"<a href='https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/"+'a'*32+"/$FILE/Wiles.pdf'>278 Transaction</a>",
        'name':'Wiles, Susie','agency':'White House Office','title':'Assistant to the President & Chief of Staff',
        'level':'I','docDate':'2026-06-14T00:00:00','amended':''}]
    if extra_catalog:rows.append({**rows[0],**extra_catalog})
    catalog_page={'draw':1,'recordsTotal':len(rows),'recordsFiltered':len(rows),'data':rows}
    page_raw=json.dumps(catalog_page).encode();catalog_page_sha=hashlib.sha256(page_raw).hexdigest()
    cat_rel=f'oge/catalog/pages/{catalog_page_sha}.json';p=root/cat_rel;p.parent.mkdir(parents=True);p.write_bytes(page_raw)
    catalog={'schema_version':'oge-catalog-archive/v1','sha256':'c'*64,'record_count':len(rows),'retrieved_at':'2026-06-14T00:00:00Z',
        'pages':[{'archive_path':cat_rel,'sha256':catalog_page_sha,'byte_length':len(page_raw),'start':0}]}
    cat_path=root/'oge/catalog/source.json';cat_hash=write(cat_path,catalog)
    coverage=coverage_from_archive(root,cat_path)
    for key,path,obj in [('official_directory_index',root/'directory.json',index),
                        ('oge_identity_coverage',root/'coverage.json',coverage)]:
        inputs[key+'_path']=path.relative_to(root).as_posix();inputs[key+'_sha256']=write(path,obj)
    inputs.update(oge_identity_catalog_metadata_path='oge/catalog/source.json',oge_identity_catalog_metadata_sha256=cat_hash)
    cover="Periodic Transaction Report (OGE Form 278-T)\nFiler's Information\n"+info+"\nFiler's Certification\nDate: 06/13/2026\nAgency Ethics Official's Opinion\nDate: 06/15/2026\n"
    checkpoint=json.loads(Path(inputs['checkpoint_path']).read_bytes())
    checkpoint['pages'][0]['words']=[{'text':line,'x0':10.,'x1':700.,'top':10.+i*10.,'bottom':18.+i*10.,
        'ocr_confidence':95,'block_num':1,'par_num':1,'line_num':i+1} for i,line in enumerate(cover.splitlines())]
    inputs['checkpoint_sha256']=write(Path(inputs['checkpoint_path']),checkpoint)
    data=dual_input();data['source_sha256']=meta['sha256']
    return case,inputs,data,coverage


def consume(report,coverage,base=None):
    base=_base() if base is None else base
    audit=audit_whitehouse_278t([report],coverage,base)
    return build_whitehouse_278t_review_candidate(base,[report],audit,coverage,data_cutoff_at='2026-06-20T00:00:00Z')


class SavedDirectoryIdentityTests(unittest.TestCase):
    def test_normal_extract_coverage_candidate_cli_consumes_optional_inputs(self):
        from scripts.whitehouse_extract import main as extract_main
        from scripts.whitehouse_coverage import main as coverage_main
        from scripts.build_whitehouse_278t_candidate import main as candidate_main
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);case,inputs,data,cov=fixture(root)
            meta=json.loads(Path(inputs['archive_metadata_path']).read_bytes())
            write(root/'input.json',inputs)
            index=json.loads((root/inputs['official_directory_index_path']).read_bytes())
            write(root/'whitehouse/disclosures/index-current.json',index)
            write(root/'whitehouse/disclosures/batch-current.json',{'schema_version':'whitehouse-public-disclosures-batch/v1',
                'indexed_count':1,'reports':[meta],'failures':[]})
            write(root/'oge/whitehouse/coverage-current.json',cov)
            base=_base();base['meta']['data_cutoff_at']='2026-06-20T00:00:00Z';write(root/'base.json',base)
            with case.context(data),redirect_stdout(io.StringIO()):
                self.assertEqual(extract_main(['--evidence-root',str(root),'--review-root',str(root),'--limit','1',
                    '--document-id',meta['document_id'],'--source-sha256',meta['sha256'],'--saved-ocr-input',str(root/'input.json'),
                    '--status-output',str(root/'status.json')]),0)
                with patch('sys.argv',['whitehouse_coverage','--review-root',str(root),'--dual-axis-trade','--output',str(root/'public-coverage.json')]):
                    coverage_main()
                self.assertEqual(candidate_main(['--oge-candidate',str(root/'base.json'),'--review-root',str(root),
                    '--candidate-out',str(root/'candidate.json'),'--audit-out',str(root/'audit.json'),
                    '--coverage-input',str(root/'public-coverage.json'),'--extraction-status-input',str(root/'status.json'),
                    '--saved-ocr-evidence-root',str(root)]),0)
            result=json.loads((root/'candidate.json').read_bytes());audit=json.loads((root/'audit.json').read_bytes())
            self.assertEqual(len(result['transactions']),1)
            self.assertEqual(audit['reports'][0]['source_identity_resolution']['status'],'resolved')
            self.assertTrue(build(result,generated_at='2026-06-20T00:00:00Z',allow_production=True).manifest['snapshot_id'])

    def test_missing_pdf_identity_reaches_real_qualification_candidate_and_repeat(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);case,inputs,data,coverage=fixture(root)
            with saved_ocr_source_roots(root,root),case.context(data):
                report=materialize_saved_whitehouse_278t(inputs);validate_generic_whitehouse_report(report)
                candidate,audit=consume(report,coverage);again,second=consume(report,coverage,candidate)
            self.assertIsNone(report['pdf_filer_name']);self.assertIsNone(report['pdf_position_title'])
            self.assertEqual(report['source_identity_resolution']['status'],'resolved')
            self.assertEqual(report['source_identity_resolution']['resolved_missing_fields'],['name','position','agency'])
            self.assertNotIn('pdf_filer_information_not_verified',report['document_reasons'])
            self.assertEqual(len(candidate['transactions']),1)
            self.assertNotEqual(candidate['people'][0]['id'],'oge:076544f8ba0638cf')
            self.assertEqual(candidate['transactions'],again['transactions']);self.assertEqual(second['reports'][0]['retained_count'],1)
            self.assertEqual(audit['reports'][0]['source_identity_resolution'],report['source_identity_resolution'])
            self.assertTrue(build(candidate,generated_at='2026-06-20T00:00:00Z',allow_production=True).manifest['snapshot_id'])

    def test_explicit_name_role_agency_and_ambiguous_blocks_never_become_missing(self):
        cases=['Someone, Else','Wiles, Susie\nUnrelated Position - White House',
               'Wiles, Susie\nAssistant to the President & Chief of Staff - Other Agency',
               'Wiles, Susie\nAssistant to the President & Chief of Staff\nExtra unresolved line',
               "Filer's Certification\nNot A Date Field", "Filer's Certification Company\nSome Role"]
        for info in cases:
            with self.subTest(info=info),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);case,inputs,data,cov=fixture(root,info=info)
                with saved_ocr_source_roots(root,root),case.context(data):
                    report=materialize_saved_whitehouse_278t(inputs);candidate,audit=consume(report,cov)
                self.assertEqual(report['source_identity_resolution']['status'],'unresolved')
                self.assertEqual(candidate['transactions'],[])

    def test_partial_name_only_is_not_lost_and_middle_names_are_not_guessed(self):
        for name,info,expected in [('Wiles, Susie','Susie Wiles','resolved'),
                                   ('Wiles, Susie J','Susie Wiles','unresolved')]:
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder);case,inputs,data,cov=fixture(root,name=name,info=info)
                with saved_ocr_source_roots(root,root),case.context(data):report=materialize_saved_whitehouse_278t(inputs)
                self.assertEqual(report['source_identity_resolution']['status'],expected)

    def test_catalog_multiple_identities_missing_original_page_or_archive_labels_remain_unresolved(self):
        for kind in ('role','archive_label','missing_page'):
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder);case,inputs,data,cov=fixture(root,extra_catalog={'title':'Different Role'} if kind=='role' else None)
                if kind=='archive_label':
                    p=Path(inputs['archive_metadata_path']);m=json.loads(p.read_bytes());m.pop('link_label')
                    inputs['archive_metadata_sha256']=write(p,m)
                if kind=='missing_page':next((root/'whitehouse/disclosures/pages').glob('*.html')).unlink()
                with saved_ocr_source_roots(root,root),case.context(data):
                    report=materialize_saved_whitehouse_278t(inputs);candidate,_=consume(report,cov)
                self.assertEqual(report['source_identity_resolution']['status'],'unresolved');self.assertEqual(candidate['transactions'],[])

    def test_current_directory_new_snapshot_preserves_archived_pdf_attribution(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);case,inputs,data,cov=fixture(root)
            p=Path(inputs['archive_metadata_path']);m=json.loads(p.read_bytes());m['page_sha256']='f'*64
            inputs['archive_metadata_sha256']=write(p,m)
            with saved_ocr_source_roots(root,root),case.context(data):
                report=materialize_saved_whitehouse_278t(inputs);candidate,_=consume(report,cov)
            resolution=report['source_identity_resolution']
            self.assertEqual(resolution['status'],'resolved');self.assertEqual(len(candidate['transactions']),1)
            self.assertNotEqual(resolution['archive_page_sha256'],resolution['directory_page_sha256'])
            self.assertEqual(resolution['archive_attribution']['page_sha256'],'f'*64)
            # The same URL does not authorize applying the attribution to
            # different PDF bytes, nor to an archive label for another person.
            for kind in ('label','pdf'):
                changed=deepcopy(inputs);meta=deepcopy(m)
                if kind=='label':meta['filer_name_from_label']='Someone, Else'
                else:meta['sha256']='e'*64
                changed['archive_metadata_sha256']=write(p,meta)
                with saved_ocr_source_roots(root,root),case.context(data):
                    if kind=='pdf':
                        with self.assertRaises(OgeCatalogError):materialize_saved_whitehouse_278t(changed)
                    else:
                        bad=materialize_saved_whitehouse_278t(changed);self.assertEqual(bad['source_identity_resolution']['status'],'unresolved')

    def test_existing_person_requires_complete_name_role_and_agency_match(self):
        from unison_snapshot.whitehouse_278t_candidate import _person, _transaction
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);case,inputs,data,cov=fixture(root)
            with saved_ocr_source_roots(root,root),case.context(data):
                report=materialize_saved_whitehouse_278t(inputs)
                for field,value in [('display_name','Wiles, Susie J'),('role','Other Role'),('office_type','Other Agency')]:
                    base=_base();person=_person(cov['reports'][0]);person[field]=value;base['people']=[person]
                    base['transactions']=[_transaction(report,report['transactions'][0],person['id'])]
                    base['meta']['data_cutoff_at']='2026-06-20T00:00:00Z'
                    result,audit=consume(report,cov,base)
                    self.assertEqual(result['transactions'],base['transactions'])
                    self.assertIn('source_directory_existing_person_conflict',audit['reports'][0]['document_reasons'])

    def test_identity_source_does_not_supply_a_missing_filing_date(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);case,inputs,data,cov=fixture(root)
            p=Path(inputs['checkpoint_path']);checkpoint=json.loads(p.read_bytes())
            for word in checkpoint['pages'][0]['words']:
                if word['text']=='Date: 06/13/2026':word['text']='Date:'
            inputs['checkpoint_sha256']=write(p,checkpoint)
            with saved_ocr_source_roots(root,root),case.context(data):
                report=materialize_saved_whitehouse_278t(inputs);result,audit=consume(report,cov)
            self.assertIsNone(report['filed_at']);self.assertEqual(result['transactions'],[])
            self.assertEqual(report['source_identity_resolution']['status'],'resolved')
            self.assertIn('filer_signature_date_missing',report['document_reasons'])

    def test_source_tampering_does_not_self_certify_by_updating_input_hash(self):
        for kind in ('index','coverage','html','catalog_page','resolution'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);case,inputs,data,cov=fixture(root)
                with saved_ocr_source_roots(root,root),case.context(data):report=materialize_saved_whitehouse_278t(inputs)
                if kind in ('index','coverage'):
                    key='official_directory_index' if kind=='index' else 'oge_identity_coverage'
                    path=root/inputs[key+'_path'];value=json.loads(path.read_bytes());value['reports'][0]['filer_name_from_label' if kind=='index' else 'filer_name']='Someone Else'
                    inputs[key+'_sha256']=write(path,value)
                elif kind=='resolution':report['source_identity_resolution']['catalog_identity']['filer_name']='Someone Else'
                else:
                    p=next((root/('whitehouse/disclosures/pages' if kind=='html' else 'oge/catalog/pages')).glob('*'))
                    p.write_bytes(p.read_bytes()+b' ')
                with saved_ocr_source_roots(root,root),case.context(data),self.assertRaises((OgeCatalogError,ValueError)):
                    if kind=='resolution':validate_generic_whitehouse_report(report)
                    else:materialize_saved_whitehouse_278t(inputs)

    def test_no_optional_refs_preserves_old_result_and_v6_rejects_new_refs(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);case,inputs,data,cov=fixture(root)
            old={k:v for k,v in inputs.items() if not k.startswith(('official_directory_','oge_identity_'))}
            with case.context(data):before=materialize_saved_whitehouse_278t(old)
            with saved_ocr_source_roots(root,root),case.context(data):
                materialize_saved_whitehouse_278t(inputs);after=materialize_saved_whitehouse_278t(old)
                bad=deepcopy(inputs);bad.pop('geometry_mode')
                with self.assertRaisesRegex(OgeCatalogError,'explicit saved v7'):materialize_saved_whitehouse_278t(bad)
            self.assertEqual(before,after);self.assertNotIn('source_identity_resolution',before)


if __name__=='__main__':unittest.main()
