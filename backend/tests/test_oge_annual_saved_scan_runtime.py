"""Offline saved-observation writers, preparation and normal overlay contracts."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_oge_annual_candidate import base
from unison_snapshot.oge_annual_runtime import controlled_scan_output_path, prepare_controlled_annual_input

DOC='d'*32


class SavedScanRuntimeTests(unittest.TestCase):
    def setUp(self):
        import pymupdf
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.review=self.root/'review';self.evidence=self.root/'evidence'
        self.review.mkdir();self.evidence.mkdir()
        document=pymupdf.open()
        page=document.new_page()
        page.insert_text((30,40),"OGE Form 278e\nReport Type: Annual\nYear (Annual Report only): 2025\nLast Name First Name\nExample Ada\nFiler's Certification\nDate: 06/01/2026\nAgency Ethics Official's Opinion\nDate: 06/02/2026")
        page=document.new_page()
        page.insert_text((30,40),'Part 7: Transactions\n# Description Type Date Amount\n1. (Self) Example purchase 05/01/2025 $1,001 - $15,000')
        raw=document.tobytes();document.close();self.sha=hashlib.sha256(raw).hexdigest()
        self.pdf_relative=f'oge/annual/reports/{DOC}/{self.sha}.pdf'
        self.pdf=self.evidence/self.pdf_relative;self.pdf.parent.mkdir(parents=True);self.pdf.write_bytes(raw)
        self.metadata={'schema_version':'oge-278e-annual-archive/v1','document_id':DOC,'sha256':self.sha,
            'document_url':f'https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/{DOC}/$FILE/Example.pdf',
            'filer_name':'Example, Ada','agency':'Example Office','position_title':'Director',
            'amended_label':None,'pending_final_oge_disposition':False,'archive_path':self.pdf_relative,'report_year':2025}
        self.write(self.pdf.with_suffix('.json'),self.metadata)
        self.input=self.root/'input.json';self.candidate=self.root/'candidate.json';self.audit=self.root/'audit.json'
        self.repo=Path(__file__).resolve().parents[2]

    @staticmethod
    def write(path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value),encoding='utf-8')

    def cli(self,name,args):
        return subprocess.run([sys.executable,'-m','unison_snapshot',name.removesuffix('.py').replace('_','-'),*map(str,args)],
            env=dict(os.environ,PYTHONPATH=str(self.repo/'backend/src')),capture_output=True)

    def seed(self, *, native_cover=False):
        from unison_snapshot.oge_annual_scan_sections import observe_scan_sections
        sections=observe_scan_sections(self.pdf,source_sha256=self.sha,document_id=DOC)
        section_path=controlled_scan_output_path(self.review,'section_observation',DOC,self.sha)
        self.write(section_path,sections)
        cover={'schema_version':'oge-278e-scan-cover-observation/v1','document_id':DOC,'source_sha256':self.sha,
            'page_count':sections['page_count'],'native_text_sha256':sections['pages'][0]['page_text_sha256'],
            'form_278e_text_observed':True,'annual_type_text_observed':True,'annual_year_text_candidates':[2025]}
        # Exercise the existing cover writer with a fixed prior OCR observation;
        # no recognizer is invoked by this test or by runtime preparation.
        self.write(self.review/'oge/annual/extraction-status.json',{'failures':[{'document_id':DOC,'source_sha256':self.sha}]})
        from unison_snapshot.commands.observe_oge_annual_scan_covers import observe_failed_covers
        with patch('unison_snapshot.commands.observe_oge_annual_scan_covers.observe_scan_cover',return_value=cover):
            result=observe_failed_covers(self.evidence,self.review)
        self.assertEqual(result['cover_observation_count'],1)
        if native_cover:
            result=self.cli('observe_oge_annual_scan_covers.py',['--native-cover-pdf',self.pdf,
                '--source-sha256',self.sha,'--document-id',DOC,'--review-root',self.review])
            self.assertEqual(result.returncode,0,result.stderr)
        return cover,sections

    def prepare(self, *, scans=True):
        return self.cli('prepare_oge_annual_runtime.py',['--review-root',self.review,'--evidence-root',self.evidence,
            '--output',self.input,'--code-commit','a'*40,'--started-at','2026-10-26T00:00:00Z',
            *(['--include-saved-scans'] if scans else [])])

    def overlay(self, audit=None):
        return self.cli('overlay_whitehouse_annual_candidate.py',['--review-root',self.review,
            '--evidence-root',self.evidence,'--candidate',self.candidate,'--audit-out',audit or self.audit,
            '--oge-annual-input',self.input,'--oge-annual-runtime-root',self.evidence])

    def test_normal_writers_prepare_and_overlay_preserve_unknown_filing_date(self):
        self.seed()
        result=self.prepare();self.assertEqual(result.returncode,0,result.stderr)
        prepared=self.input.read_bytes();value=json.loads(prepared)
        self.assertEqual(value['extractions'],[])
        self.assertEqual(value['controlled_annual_runtime']['mode'],'saved_native_and_scan_inputs')
        self.assertEqual(len(value['controlled_annual_runtime']['files']),4)
        self.assertNotIn('handwritten_date_inputs_by_document',value)
        self.assertFalse(value['controlled_annual_runtime']['recognizer_started'])
        self.assertEqual(value['saved_scan_observations_by_document'][DOC]['archived_pdf_path'],self.pdf_relative)
        self.write(self.candidate,base())
        result=self.overlay();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(self.candidate.read_bytes())['transactions'],base()['transactions'])
        audit=json.loads(self.audit.read_bytes())['oge_annual']['reports'][0]
        self.assertIsNone(audit['filing_date_evidence']['date'])
        result=self.prepare();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.input.read_bytes(),prepared)

    def test_optional_saved_native_cover_uses_existing_source_consumer_and_repeats(self):
        self.seed(native_cover=True)
        result=self.prepare();self.assertEqual(result.returncode,0,result.stderr)
        value=json.loads(self.input.read_bytes());self.assertEqual(len(value['controlled_annual_runtime']['files']),5)
        self.write(self.candidate,base())
        result=self.overlay();self.assertEqual(result.returncode,0,result.stderr)
        audit=json.loads(self.audit.read_bytes())['oge_annual']['reports'][0]
        self.assertEqual(audit['filing_date_evidence']['date'],'2026-06-01')
        previous=self.candidate.read_bytes();result=self.overlay();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.candidate.read_bytes(),previous)

    def test_missing_mismatched_or_ambiguous_scan_artifacts_do_not_replace_input(self):
        cover,sections=self.seed()
        self.input.write_bytes(b'input sentinel')
        path=controlled_scan_output_path(self.review,'section_observation',DOC,self.sha)
        raw=path.read_bytes();path.unlink()
        result=self.prepare();self.assertNotEqual(result.returncode,0);self.assertEqual(self.input.read_bytes(),b'input sentinel')
        path.write_bytes(raw)
        for key,value in (('source_sha256','0'*64),('page_count',3),('schema_version','unsupported')):
            altered=deepcopy(sections);altered[key]=value;self.write(path,altered)
            with self.subTest(key=key):
                result=self.prepare();self.assertNotEqual(result.returncode,0)
                self.assertEqual(self.input.read_bytes(),b'input sentinel')
        path.write_bytes(raw)
        duplicate=path.with_name('unsupported-version.json');duplicate.write_bytes(raw)
        result=self.prepare();self.assertNotEqual(result.returncode,0);self.assertEqual(self.input.read_bytes(),b'input sentinel')

    def test_modified_package_source_and_scan_inputs_stop_before_both_outputs(self):
        self.seed();result=self.prepare();self.assertEqual(result.returncode,0,result.stderr)
        original=self.input.read_bytes();section=controlled_scan_output_path(self.review,'section_observation',DOC,self.sha)
        section_raw=section.read_bytes();pdf_raw=self.pdf.read_bytes()
        self.candidate.write_bytes(b'candidate sentinel');self.audit.write_bytes(b'audit sentinel')
        for kind in ('package','scan','pdf'):
            self.input.write_bytes(original);section.write_bytes(section_raw);self.pdf.write_bytes(pdf_raw)
            if kind=='package':
                value=json.loads(original);value['saved_scan_observations_by_document'][DOC]['section_observation']['part7_slots'][0]['raw_lines']=['changed']
                self.write(self.input,value)
            if kind=='scan':section.write_bytes(section_raw+b' ')
            if kind=='pdf':self.pdf.write_bytes(pdf_raw+b' ')
            with self.subTest(kind=kind):
                result=self.overlay();self.assertNotEqual(result.returncode,0)
                self.assertEqual(self.candidate.read_bytes(),b'candidate sentinel');self.assertEqual(self.audit.read_bytes(),b'audit sentinel')

    def test_forged_source_row_cannot_be_prepared_or_repacked_for_overlay(self):
        self.seed(native_cover=True)
        result=self.prepare();self.assertEqual(result.returncode,0,result.stderr)
        package=json.loads(self.input.read_bytes())
        section=controlled_scan_output_path(self.review,'section_observation',DOC,self.sha)
        altered=json.loads(section.read_bytes())
        altered['part7_slots'][0]['raw_lines']=[line.replace('Example','FORGED ASSET')
            for line in altered['part7_slots'][0]['raw_lines']]
        self.write(section,altered)
        self.input.write_bytes(b'input sentinel')
        result=self.prepare();self.assertNotEqual(result.returncode,0)
        self.assertIn(b'sections differ from original source replay',result.stderr)
        self.assertEqual(self.input.read_bytes(),b'input sentinel')
        # Even a self-consistent new receipt cannot authorize altered source
        # rows: normal overlay rebuilds and replays before either output write.
        package['saved_scan_observations_by_document'][DOC]['section_observation']=altered
        for dependency in package['controlled_annual_runtime']['files']:
            if dependency['path']==section.relative_to(self.review).as_posix():
                package['controlled_annual_runtime']['total_bytes']+=section.stat().st_size-dependency['bytes']
                dependency.update(sha256=hashlib.sha256(section.read_bytes()).hexdigest(),bytes=section.stat().st_size)
        self.write(self.input,package)
        self.candidate.write_bytes(b'candidate sentinel');self.audit.write_bytes(b'audit sentinel')
        result=self.overlay();self.assertNotEqual(result.returncode,0)
        self.assertIn(b'sections differ from original source replay',result.stderr)
        self.assertEqual(self.candidate.read_bytes(),b'candidate sentinel')
        self.assertEqual(self.audit.read_bytes(),b'audit sentinel')

    def test_native_cover_must_prove_form_unique_year_and_filer_without_ocr(self):
        import pymupdf
        valid="OGE Form 278e\nReport Type: Annual\nYear (Annual Report only): 2025\nLast Name First Name\nExample Ada\nFiler's Certification\nDate: 06/01/2026"
        for label,text in (
            ('form',valid.replace('278e','278T')),
            ('report_type',valid.replace('Report Type: Annual','Report Type: Termination')),
            ('year',valid.replace('2025','2024')),
            ('missing_year',valid.replace('Year (Annual Report only): 2025','')),
            ('ambiguous_year',valid+'\nYear (Annual Report only): 2024'),
            ('filer',valid.replace('Example Ada','Other Person')),
            ('missing_filer',valid.replace('Last Name First Name\nExample Ada',''))):
            with self.subTest(label=label):
                case=SavedScanRuntimeTests();case.setUp();self.addCleanup(case.doCleanups)
                document=pymupdf.open(case.pdf);document.delete_page(0)
                page=document.new_page(pno=0);page.insert_text((30,40),text)
                raw=document.tobytes();document.close()
                case.sha=hashlib.sha256(raw).hexdigest()
                case.pdf_relative=f'oge/annual/reports/{DOC}/{case.sha}.pdf'
                case.pdf=case.evidence/case.pdf_relative;case.pdf.write_bytes(raw)
                case.metadata.update(sha256=case.sha,archive_path=case.pdf_relative)
                case.write(case.pdf.with_suffix('.json'),case.metadata)
                case.seed(native_cover=True)
                case.input.write_bytes(b'input sentinel')
                result=case.prepare();self.assertNotEqual(result.returncode,0)
                self.assertEqual(case.input.read_bytes(),b'input sentinel')

    def test_cover_ocr_year_tamper_fails_prepare_and_normal_overlay(self):
        self.seed(native_cover=True)
        result=self.prepare();self.assertEqual(result.returncode,0,result.stderr)
        package=json.loads(self.input.read_bytes())
        cover=controlled_scan_output_path(self.review,'cover_observation',DOC,self.sha)
        value=json.loads(cover.read_bytes());value['annual_year_text_candidates']=[2024]
        self.write(cover,value)
        self.input.write_bytes(b'input sentinel')
        result=self.prepare();self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.input.read_bytes(),b'input sentinel')
        self.write(self.input,package)
        self.candidate.write_bytes(b'candidate sentinel');self.audit.write_bytes(b'audit sentinel')
        result=self.overlay();self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.candidate.read_bytes(),b'candidate sentinel')
        self.assertEqual(self.audit.read_bytes(),b'audit sentinel')

    def test_native_scan_duplicate_is_rejected_and_default_native_mode_is_unchanged(self):
        self.seed()
        result=self.prepare(scans=False);self.assertNotEqual(result.returncode,0)
        from test_oge_annual_candidate import source
        x=source();x.update(source_document_id=DOC,source_sha256=self.sha,source_url=self.metadata['document_url'],evidence_archive_path=self.pdf_relative)
        path=self.review/f'oge/annual/extractions/{DOC}/{self.sha}/{x["parser_version"].replace("/","-")}.json';self.write(path,x)
        result=self.prepare();self.assertNotEqual(result.returncode,0)
        self.assertIn(b'native and scan inputs duplicate',result.stderr)

    def test_new_scan_dependencies_are_protected_as_outputs(self):
        self.seed();result=self.prepare();self.assertEqual(result.returncode,0,result.stderr)
        self.write(self.candidate,base());before=self.candidate.read_bytes()
        section=controlled_scan_output_path(self.review,'section_observation',DOC,self.sha);raw=section.read_bytes()
        result=self.overlay(audit=section)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(section.read_bytes(),raw);self.assertEqual(self.candidate.read_bytes(),before)

    def test_holding_writer_prepares_source_replayed_optional_pages(self):
        import pymupdf
        document=pymupdf.open(self.pdf)
        page=document.new_page(pno=1)
        page.insert_text((30,40),"Part 2: Filer's Employment Assets")
        edges=[30,60,300,330,430,500,570]
        for x in edges:page.draw_line((x,100),(x,145))
        for y in (100,120,145):page.draw_line((30,y),(570,y))
        for y,values in ((113,['#','Description','EIF','Value','Income Type','Income Amount']),
                          (135,['1','ALPHA Fund','Yes','$1,001 - $15,000','',''])):
            for x,text in zip(edges,values):
                if text:page.insert_text((x+2,y),text,fontsize=7)
        raw=document.tobytes();document.close()
        self.sha=hashlib.sha256(raw).hexdigest();self.pdf_relative=f'oge/annual/reports/{DOC}/{self.sha}.pdf'
        self.pdf=self.evidence/self.pdf_relative;self.pdf.write_bytes(raw)
        self.metadata.update(sha256=self.sha,archive_path=self.pdf_relative)
        self.write(self.pdf.with_suffix('.json'),self.metadata)
        self.seed(native_cover=True)
        from unison_snapshot.oge_annual_scan_sections import observe_holding_pages
        observations=observe_holding_pages(self.pdf,source_sha256=self.sha,document_id=DOC,page_numbers=[2])
        path=controlled_scan_output_path(self.review,'holding_page_observations',DOC,self.sha)
        self.write(path,observations)
        self.assertEqual(json.loads(path.read_bytes())['schema_version'],'oge-278e-native-holding-pages/v2')
        result=self.prepare();self.assertEqual(result.returncode,0,result.stderr)
        package=json.loads(self.input.read_bytes())
        self.assertEqual(len(package['controlled_annual_runtime']['files']),6)
        self.assertEqual(package['saved_scan_observations_by_document'][DOC]['holding_page_observations']['pages'][0]['page_number'],2)
        self.write(self.candidate,base())
        result=self.overlay();self.assertEqual(result.returncode,0,result.stderr)
        audit=json.loads(self.audit.read_bytes())['oge_annual']['reports'][0]
        self.assertEqual(audit['filing_date_evidence']['date'],'2026-06-01')

    def test_portable_package_can_move_with_both_controlled_trees(self):
        self.seed(native_cover=True)
        result=self.prepare();self.assertEqual(result.returncode,0,result.stderr)
        original=self.input.read_bytes()
        relocated=self.root/'relocated';relocated.mkdir()
        for path in (self.review,self.evidence,self.input):
            shutil.move(str(path),str(relocated/path.name))
        self.review,self.evidence,self.input=relocated/'review',relocated/'evidence',relocated/'input.json'
        self.write(self.candidate,base())
        result=self.overlay();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.input.read_bytes(),original)

    def test_mixed_native_scan_and_scan_source_version_conflicts_are_explicit(self):
        self.seed()
        from test_oge_annual_candidate import source,inputs,CONTENT,DOC as NATIVE_DOC,SHA
        native=source();metadata=inputs(native)['archive_metadata_by_document'][NATIVE_DOC]
        path=self.review/f'oge/annual/extractions/{NATIVE_DOC}/{SHA}/{native["parser_version"].replace("/","-")}.json'
        self.write(path,native)
        pdf=self.evidence/metadata['archive_path'];pdf.parent.mkdir(parents=True);pdf.write_bytes(CONTENT)
        self.write(pdf.with_suffix('.json'),metadata)
        result=self.prepare();self.assertEqual(result.returncode,0,result.stderr)
        package=json.loads(self.input.read_bytes())
        self.assertEqual(len(package['extractions']),1)
        self.assertEqual(set(package['archive_metadata_by_document']),{DOC,NATIVE_DOC})
        self.assertEqual(len(package['controlled_annual_runtime']['documents']),2)
        self.input.write_bytes(b'input sentinel')
        cover_path=controlled_scan_output_path(self.review,'cover_observation',DOC,self.sha)
        cover=json.loads(cover_path.read_bytes());cover['source_sha256']='b'*64
        duplicate=controlled_scan_output_path(self.review,'cover_observation',DOC,'b'*64)
        self.write(duplicate,cover)
        result=self.prepare();self.assertNotEqual(result.returncode,0)
        self.assertIn(b'ambiguous versions',result.stderr)
        self.assertEqual(self.input.read_bytes(),b'input sentinel')




if __name__=='__main__':unittest.main()
