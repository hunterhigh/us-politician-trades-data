"""Fixed Git classification proof for generated ordinary native OGE originals."""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

from test_oge_owner_correction import fixture, write_files, encoded, DOCUMENT, CUTOFF
from unison_snapshot.oge import build_catalog, parse_catalog_page, archive_catalog
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.whitehouse_native_correction import _historical_projection
from unison_snapshot.pipeline_qa.migration_inventory import InventoryError
from unison_snapshot.pipeline_qa.oge_classification_correction import verified_oge_classification_corrections


@lru_cache(maxsize=16)
def classification_fixture(*, generic=False, owner=None, prior_owner=None, unknown=False):
    """Return a complete Hpre board and the actual selected normal candidate.

    Endnote is source text in a generated PDF, not a saved rule marker. An
    empty physical heading bypasses the older owner-only fixture assertion.
    """
    heading = {'Self':'Filer assets','Spouse':'Spouse assets',
               'Dependent Child':'Dependent children assets'}.get(owner,' ')
    def mutate(raw):
        pdf = pymupdf.open(stream=raw,filetype='pdf')
        pdf[1].insert_text((30,80),'Additional information')
        result = pdf.tobytes(); pdf.close(); return result
    old,_,review,evidence,base = fixture(asset='Example Common Stock See Endnote (EXM)',
        heading=heading, pdf_mutator=mutate if unknown else None)
    catalog = json.loads(next(raw for path,raw in evidence.items() if '/catalog/normalized/' in path))
    legacy = json.loads(next(raw for path,raw in review.items() if path.endswith('v2.json')))
    native = json.loads(next(raw for path,raw in review.items() if path.endswith('v7.json')))
    if owner is not None:
        # v2 does not interpret all explicit owner headings. The supported
        # generic source row supplies its own owner and physical stable ID;
        # never relabel the legacy Self fact as a spouse/dependent fact.
        old = _historical_projection(native,native['transactions'][0],base['people'][0]['id'])
    if prior_owner is not None:
        old['owner'] = prior_owner
    base['transactions'] = [deepcopy(old)]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); write_files(root,evidence)
        result,audit = build_oge_candidate(catalog,[native if generic else legacy],base,
            data_cutoff_at=CUTOFF,evidence_root=root,
            **({'legacy_extractions_by_document':{DOCUMENT:legacy}} if generic else {}))
    review['oge/qualifications/current.json'] = encoded(audit)
    actual = next(row for row in result['transactions'] if row['id'] == old['id'])
    return old,actual,review,evidence,base


class OgeClassificationCorrectionTests(unittest.TestCase):
    def verify(self, data, *, edit=None, supplied_people=None):
        old,current,review,evidence,board = deepcopy(data)
        if edit:
            edit(old,current,review,evidence,board)
        with tempfile.TemporaryDirectory(prefix='oge-classification-fixed-') as directory:
            repo = Path(directory)
            write_files(repo,{**evidence,**review})
            raw = encoded(board); sha = hashlib.sha256(raw).hexdigest()
            write_files(repo,{'manifest.json':encoded({'is_demo':False,'board':sha}),f'board/{sha}.json':raw})
            def git(*args):
                return subprocess.check_output(['git','-C',str(repo),*args],
                    env=dict(os.environ,GIT_NO_LAZY_FETCH='1'),stderr=subprocess.STDOUT).decode().strip()
            for args in (('init','-q'),('config','user.email','contract@example.invalid'),
                         ('config','user.name','Contract'),('add','.'),('commit','-qm','fixed source')):
                git(*args)
            commit = git('rev-parse','HEAD')
            return verified_oge_classification_corrections(
                old={old['id']:old},current={current['id']:current},
                repo=repo,evidence_commit=commit,review_commit=commit,main_commit=commit,
                previous_people=board['people'] if supplied_people is None else supplied_people)

    def test_actual_default_bond_to_stock_and_combined_generic_emit_one_event(self):
        for generic,fields in ((False,['instrument_type']),(True,['instrument_type','owner'])):
            data = classification_fixture(generic=generic)
            self.assertEqual(data[0]['instrument_type'],'Bond')
            self.assertEqual(data[1]['instrument_type'],'Stock')
            with self.subTest(generic=generic):
                events = self.verify(data)
                self.assertEqual(len(events),1)
                self.assertEqual(events[0]['reason'],'source_classification_correction')
                self.assertEqual(events[0]['changed_fields'],fields)
                self.assertEqual(events[0]['old_id'],events[0]['new_id'])
                self.assertEqual(self.verify(data),events)

    def test_explicit_owner_and_already_undisclosed_change_only_classification(self):
        for owner in ('Self','Spouse','Dependent Child'):
            with self.subTest(owner=owner):
                data = classification_fixture(generic=True,owner=owner)
                self.assertEqual(data[0]['owner'],owner)
                self.assertEqual(data[1]['owner'],owner)
                self.assertEqual(self.verify(data)[0]['changed_fields'],['instrument_type'])
        for generic in (False,True):
            data = classification_fixture(generic=generic,prior_owner='Undisclosed')
            self.assertEqual(self.verify(data)[0]['changed_fields'],['instrument_type'])
        # Default selection must conserve an already published explicit-owner
        # native ID, rather than manufacture the v2 Self ID as a new fact.
        data = classification_fixture(owner='Spouse')
        self.assertEqual(data[1]['owner'],'Spouse')
        self.assertEqual(self.verify(data)[0]['changed_fields'],['instrument_type'])

    def test_fixed_main_complete_objects_and_source_person_fields_are_not_self_proof(self):
        data = classification_fixture()
        for field,value in (('amount_low',1002),('asset_name','Changed Endnote asset'),
                            ('option_type','call'),('unexpected_null',None)):
            def edit(old,current,review,evidence,board):
                old[field] = current[field] = board['transactions'][0][field] = value
            with self.subTest(field=field),self.assertRaises(InventoryError):
                self.verify(data,edit=edit)
        def person_edit(old,current,review,evidence,board):
            board['people'][0]['display_name'] = 'Wrong identity'
        with self.assertRaisesRegex(InventoryError,'person differs'):
            self.verify(data,edit=person_edit)
        with self.assertRaisesRegex(InventoryError,'previous person'):
            self.verify(data,supplied_people=[])
        def different_main(old,current,review,evidence,board):
            board['transactions'][0]['amount_low'] = 1002
        with self.assertRaisesRegex(InventoryError,'predecessor differs'):
            self.verify(data,edit=different_main)

    def test_known_nullable_representation_and_existing_person_annotations_survive(self):
        def edit(old,current,review,evidence,board):
            for fact in (old,current,board['transactions'][0]):
                for key in ('option_type','strike_price','expiration_date','position_effect_basis'):
                    fact.pop(key,None)
            board['people'][0].update(portrait_url='https://example.invalid/a',priority=True)
        self.assertEqual(self.verify(classification_fixture(),edit=edit)[0]['changed_fields'],['instrument_type'])

    def test_explicit_selection_cannot_be_erased_forged_or_replaced_by_another_lane(self):
        data = classification_fixture()
        for kind in ('missing','hash','family','saved','duplicate','wrong_mode'):
            def edit(old,current,review,evidence,board):
                audit = json.loads(review['oge/qualifications/current.json']); report = audit['reports'][0]
                if kind == 'missing': report.pop('source_interpretation_binding')
                elif kind == 'hash': report['source_interpretation_binding']['extraction_canonical_sha256'] = '0'*64
                elif kind == 'family': report['source_interpretation_binding']['parser_version'] = 'oge-278t-pdf/v6'
                elif kind == 'saved': report['observation_receipt'] = {'coverage_complete':True}
                elif kind == 'duplicate': audit['reports'].append(deepcopy(report))
                else:
                    from unison_snapshot.oge_native_owner_scope import source_interpretation_binding
                    generic = json.loads(next(raw for path,raw in review.items() if path.endswith('v7.json')))
                    report['source_interpretation_binding'] = source_interpretation_binding(generic)
                review['oge/qualifications/current.json'] = encoded(audit)
            with self.subTest(kind=kind),self.assertRaises(InventoryError):
                self.verify(data,edit=edit)

    def test_full_original_cache_metadata_and_directory_cannot_be_forged(self):
        data = classification_fixture()
        for kind in ('pdf','metadata','cache','catalog','version'):
            def edit(old,current,review,evidence,board):
                if kind == 'pdf':
                    path = next(path for path in evidence if path.endswith('.pdf'))
                    evidence[path] += b'\nchanged original\n'
                elif kind == 'metadata':
                    path = next(path for path in evidence if '/reports/' in path and path.endswith('.json'))
                    value = json.loads(evidence[path]); value['agency'] = 'Other Agency'
                    evidence[path] = encoded(value)
                elif kind == 'cache':
                    path = next(path for path in review if path.endswith('v2.json'))
                    value = json.loads(review[path]); value['transactions'][0]['asset_name'] = 'Fake Endnote'
                    review[path] = encoded(value)
                    from unison_snapshot.oge_native_owner_scope import source_interpretation_binding
                    audit = json.loads(review['oge/qualifications/current.json'])
                    audit['reports'][0]['source_interpretation_binding'] = source_interpretation_binding(value)
                    review['oge/qualifications/current.json'] = encoded(audit)
                elif kind == 'catalog':
                    path = next(path for path in evidence if '/catalog/pages/' in path)
                    payload = json.loads(evidence[path]); payload['data'][0]['agency'] = 'Other Agency'
                    evidence[path] = encoded(payload)
                else:
                    path = next(path for path in evidence if '/reports/' in path and path.endswith('.json'))
                    metadata = json.loads(evidence[path])
                    raw = evidence[metadata['archive_path']] + b'\n% alternate source version\n'
                    sha = hashlib.sha256(raw).hexdigest()
                    relative = f'oge/reports/{DOCUMENT}/{sha}.pdf'
                    evidence[relative] = raw
                    evidence[relative[:-4]+'.json'] = encoded({**metadata,'sha256':sha,
                        'archive_path':relative,'byte_length':len(raw)})
            with self.subTest(kind=kind),self.assertRaises(InventoryError):
                self.verify(data,edit=edit)

    def test_historical_catalog_source_reuses_full_raw_api_replay(self):
        data = deepcopy(classification_fixture())
        _,_,review,evidence,_ = data
        normalized = next(path for path in evidence if '/catalog/normalized/' in path)
        sha = Path(normalized).stem
        review[f'oge/discoveries/{sha}.json'] = encoded({**json.loads(evidence[normalized]),
            'metadata':json.loads(evidence[f'oge/catalog/{sha}.json'])})
        payload = {'draw':1,'recordsTotal':0,'recordsFiltered':0,'data':[]}
        catalog = build_catalog([parse_catalog_page(payload,start=0,length=1)])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = archive_catalog(root,[(0,1,encoded(payload),{'content-type':'application/json'})],
                                       catalog,retrieved_at=CUTOFF)
            evidence.update({path.relative_to(root).as_posix():path.read_bytes()
                             for path in root.rglob('*') if path.is_file()})
        review['status/oge.json'] = encoded({'catalog_sha256':metadata['sha256']})
        self.assertEqual(self.verify(data)[0]['changed_fields'],['instrument_type'])
        path = next(path for path in evidence if '/catalog/pages/' in path and json.loads(evidence[path])['data'])
        del evidence[path]
        with self.assertRaises(InventoryError): self.verify(data)

    def test_unknown_source_scope_is_not_authorized_by_a_review_rule_marker(self):
        data = deepcopy(classification_fixture(unknown=True))
        old,_,review,evidence,board = data
        data = (old,{**old,'instrument_type':'Stock'},review,evidence,board)
        audit = json.loads(review['oge/qualifications/current.json'])
        audit['reports'][0]['classification_source_scope_complete'] = True
        review['oge/qualifications/current.json'] = encoded(audit)
        with self.assertRaisesRegex(InventoryError,'outside transaction tables'):
            self.verify(data)

    def test_fixed_refs_and_ocr_free_source_path(self):
        data = classification_fixture()
        old,current = data[:2]
        with self.assertRaises(InventoryError):
            verified_oge_classification_corrections(old={old['id']:old},current={current['id']:current},
                repo=None,evidence_commit=None,review_commit=None)
        with patch.object(pymupdf.Page,'get_textpage_ocr',side_effect=AssertionError('no OCR')) as ocr:
            self.assertEqual(self.verify(data)[0]['changed_fields'],['instrument_type'])
        ocr.assert_not_called()


if __name__ == '__main__':
    unittest.main()
