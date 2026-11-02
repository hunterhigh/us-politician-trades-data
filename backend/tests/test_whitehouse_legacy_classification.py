"""Real original replay for legacy v2 Endnote classification, without new facts."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import subprocess

from test_whitehouse_owner_correction import fixture, encoded
from test_whitehouse_278t import TEXT
from unison_snapshot.whitehouse_278t import parse_whitehouse_278t_pdf, PARSER_VERSION
from unison_snapshot.whitehouse_278t_candidate import _transaction
from unison_snapshot.whitehouse_native_correction import _historical_projection
from unison_snapshot.fact_policy.whitehouse_classification_correction import verified_classification_corrections
from unison_snapshot.fact_policy.source_objects import InventoryError


def legacy_fixture(asset='Example Common Stock See Endnote (EXM)'):
    import pymupdf
    _,_,review,evidence,base=fixture(asset=asset)
    old_path=next(path for path in evidence if path.endswith('.pdf'))
    old=pymupdf.open(stream=evidence[old_path],filetype='pdf')
    pdf=pymupdf.open();cover=pdf.new_page(width=700,height=792)
    text=TEXT.replace('Deputy Counsel to the President, Trump-Vance (2025)','Director')
    text=text.replace('06/03/2025','06/13/2026').replace('06/12/2025','06/15/2026')
    cover.insert_text((30,40),text,fontsize=8);pdf.insert_pdf(old,from_page=1)
    raw=pdf.tobytes();pdf.close();old.close();sha=hashlib.sha256(raw).hexdigest()
    metadata=json.loads(evidence[old_path[:-4]+'.json'])
    path=old_path.replace(metadata['sha256'],sha)
    metadata.update(sha256=sha,byte_length=len(raw),archive_path=path)
    evidence.pop(old_path);evidence.pop(old_path[:-4]+'.json')
    evidence[path]=raw;evidence[path[:-4]+'.json']=encoded(metadata)
    with tempfile.TemporaryDirectory() as temporary:
        source=Path(temporary)/'source.pdf';source.write_bytes(raw)
        report=parse_whitehouse_278t_pdf(source,source_url=metadata['document_url'],source_sha256=sha,
            document_id=metadata['document_id'],filer_name=metadata['filer_name_from_label'],
            amended_label=metadata['link_label'])
    assert not report['document_reasons'],report
    coverage=json.loads(review['whitehouse/coverage-current.json'])
    selected=coverage['reports'][0]
    extraction=f"whitehouse/extractions/{metadata['document_id'][7:]}/{sha}/whitehouse-278t-hybrid-geometry-v2.json"
    selected.update(archive_sha256_versions=[sha],extraction_parser_version=PARSER_VERSION,
        extraction_path=extraction,review_state='extracted_review_only')
    review={path:raw for path,raw in review.items() if not path.startswith('whitehouse/extractions/')}
    review['whitehouse/coverage-current.json']=encoded(coverage)
    # Exercise real pre-inventory caches as well as current complete caches.
    cached=deepcopy(report)
    for field in ('filing_date_evidence','filer_identity_evidence'):
        if cached.get(field) is None:cached.pop(field,None)
    for row in cached['transactions']:row.pop('cells',None)
    review[extraction]=encoded(cached)
    person=base['people'][0]['id'];row=report['transactions'][0]
    prior=_historical_projection(report,row,person);current=_transaction(report,row,person)
    base['transactions']=[prior]
    return prior,current,review,evidence,base


class LegacyWhiteHouseClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.data=legacy_fixture()

    def verify(self,data=None,**kwargs):
        old,current,review,evidence,base=deepcopy(data or self.data)
        base['transactions']=[old]
        raw=encoded(base);sha=hashlib.sha256(raw).hexdigest()
        formal={'manifest.json':encoded({'is_demo':False,'board':sha}),f'board/{sha}.json':raw}
        options=dict(old={old['id']:old},current={current['id']:current},repo='unused',
            main_commit='a'*40,review_commit='b'*40,evidence_commit='e'*40,
            previous_people=base['people'],read_object=lambda repo,ref,path:
                (formal if ref=='a'*40 else evidence if ref=='e'*40 else review)[path])
        options.update(kwargs);return verified_classification_corrections(**options)

    def test_complete_native_stock_and_unspecified_projections(self):
        for asset,answer in [('Example Common Stock See Endnote (EXM)','Stock'),
                ('NY State Housing Finance See Endnote','Unspecified')]:
            with self.subTest(asset=asset):
                data=legacy_fixture(asset);self.assertEqual(data[1]['instrument_type'],answer)
                proof=self.verify(data);self.assertEqual(len(proof),1)
                self.assertEqual(proof[0]['changed_fields'],['instrument_type'])

    def test_two_edited_formal_objects_cannot_supply_source_facts(self):
        for field,value in [('amount_low',1002),('asset_name','Forged Stock See Endnote'),
                ('ticker','FORGE'),('person_id','oge:'+'0'*16),('option_type','call'),('extra',None)]:
            data=deepcopy(self.data);data[0][field]=data[1][field]=value
            with self.subTest(field=field),self.assertRaises(InventoryError):self.verify(data)

    def test_complete_cache_original_and_official_identity_are_required(self):
        for mutation in ('cache','pdf','directory','selected_version','person','missing_main'):
            data=deepcopy(self.data)
            if mutation=='cache':
                path=next(p for p in data[2] if p.startswith('whitehouse/extractions/'))
                value=json.loads(data[2][path]);value['transactions'][0]['amount_low']=1002
                data[2][path]=encoded(value)
            elif mutation=='pdf':
                path=next(p for p in data[3] if p.endswith('.pdf'));data[3][path]+=b'changed'
            elif mutation=='directory':
                path=next(p for p in data[3] if p.endswith('.html'));data[3][path]+=b'changed'
            elif mutation=='selected_version':
                value=json.loads(data[2]['whitehouse/coverage-current.json'])
                value['reports'][0]['extraction_parser_version']='forged/v1'
                data[2]['whitehouse/coverage-current.json']=encoded(value)
            with self.subTest(mutation=mutation),self.assertRaises(InventoryError):
                self.verify(data,**({'previous_people':[]} if mutation=='person' else
                    {'main_commit':None} if mutation=='missing_main' else {}))

    def test_no_owner_change_or_unrelated_classifier_permission(self):
        data=deepcopy(self.data);data[0]['owner']=data[1]['owner']='Spouse'
        self.assertEqual(self.verify(data),[])
        for asset in ('Treasury Bond See Endnote','Example ETF See Endnote (EXM)',
                      'Example Common Stock (EXM)'):
            with self.subTest(asset=asset):self.assertEqual(self.verify(legacy_fixture(asset)),[])

    def test_fixed_git_originals_ignore_working_files_and_reject_changed_archive(self):
        old,current,review,evidence,base=deepcopy(self.data)
        with tempfile.TemporaryDirectory(prefix='legacy-proof-') as temporary:
            root=Path(temporary)
            def git(*args):
                return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL)
            git('init','-q');git('config','user.name','Source contract test')
            git('config','user.email','source-test@example.invalid')
            def commit(files):
                for path,raw in files.items():
                    target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
                git('add','.');git('commit','-qm','fixed source')
                return git('rev-parse','HEAD').decode().strip()
            raw=encoded(base);sha=hashlib.sha256(raw).hexdigest()
            main=commit({'manifest.json':encoded({'is_demo':False,'board':sha}),f'board/{sha}.json':raw})
            review_ref=commit(review);evidence_ref=commit(evidence)
            options=dict(old={old['id']:old},current={current['id']:current},repo=root,
                main_commit=main,review_commit=review_ref,evidence_commit=evidence_ref,
                previous_people=base['people'])
            self.assertEqual(len(verified_classification_corrections(**options)),1)
            path=next(path for path in evidence if path.endswith('.pdf'))
            (root/path).write_bytes(evidence[path]+b'changed')
            self.assertEqual(len(verified_classification_corrections(**options)),1)
            altered=commit({path:evidence[path]+b'changed'})
            with self.assertRaises(InventoryError):
                verified_classification_corrections(**{**options,'evidence_commit':altered})


if __name__=='__main__':unittest.main()
