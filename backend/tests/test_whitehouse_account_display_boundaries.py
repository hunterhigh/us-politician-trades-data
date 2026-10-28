"""Adversarial complete-object and fixed-source account-display contracts."""
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

from whitehouse_account_display_fixture import fixture, encoded, write_files
from unison_snapshot.fact_policy.source_objects import InventoryError
from unison_snapshot.fact_policy.whitehouse_account_display_correction import (
    verified_account_display_corrections)
from unison_snapshot.fact_policy.whitehouse_owner_correction import verified_owner_corrections
from unison_snapshot.fact_policy.whitehouse_classification_correction import verified_classification_corrections


def source_mutator(kind):
    """Change real PDF painting state, then let the fixture rebuild all inputs."""
    def mutate(raw):
        import pymupdf
        document=pymupdf.open(stream=raw,filetype='pdf');page=document[1]
        title='Investment account #7'
        box=page.search_for(title)[0]
        streams=[xref for xref in page.get_contents()
                 if title.encode().hex().encode() in document.xref_stream(xref).lower()]
        assert len(streams)==1
        xref=streams[0];stream=document.xref_stream(xref)
        if kind in {'hidden','white_gray','white_rgb','white_cmyk','clipped','opacity'}:
            operators={'hidden':b'3 Tr', 'white_gray':b'1 g', 'white_rgb':b'1 1 1 rg',
                'white_cmyk':b'0 0 0 0 k','clipped':b'0 0 0 0 re W n',
                'opacity':b'/AccountHidden gs'}
            document.update_stream(xref,stream.replace(b'BT',operators[kind]+b'\nBT',1))
            if kind=='opacity':
                resource=document.xref_get_key(page.xref,'Resources')
                assert resource[0]=='xref'
                state=document.get_new_xref();document.update_object(state,'<< /Type /ExtGState /ca 0 /CA 0 >>')
                document.xref_set_key(int(resource[1].split()[0]),'ExtGState',f'<< /AccountHidden {state} 0 R >>')
        elif kind=='white_cover':
            page.draw_rect(box,color=None,fill=(1,1,1))
        elif kind=='scaled_line':
            document.update_stream(xref,stream+b'\nq 20 0 0 20 0 0 cm 1 w 1 1 m 2 1 l S Q\n')
        elif kind=='font_mapping':
            cmap=document.get_new_xref();document.update_object(cmap,'<<>>')
            document.update_stream(cmap,b'/CIDInit /ProcSet findresource begin 12 dict begin begincmap '
                b'/CMapType 2 def 1 begincodespacerange <00> <ff> endcodespacerange '
                b'1 beginbfrange <20> <7e> <0020> endbfrange endcmap '
                b'CMapName currentdict /CMap defineresource pop end end')
            for font in page.get_fonts(full=True):document.xref_set_key(font[0],'ToUnicode',f'{cmap} 0 R')
        elif kind=='crop':page.set_cropbox(pymupdf.Rect(0,150,700,792))
        elif kind=='page_group':document.xref_set_key(page.xref,'Group','<< /S /Transparency /CS /DeviceRGB >>')
        elif kind in {'ink_overhang','inset_ink'}:
            document[0].insert_text((0 if kind=='ink_overhang' else 4,700),'j',fontname='helv',fontsize=8)
        else:raise AssertionError(kind)
        result=document.tobytes();document.close();return result
    return mutate


class WhiteHouseAccountDisplayBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=fixture()

    def options(self,data=None):
        old,current,review,evidence,base=deepcopy(data or self.data)
        raw=encoded(base); digest=hashlib.sha256(raw).hexdigest()
        formal={'manifest.json':encoded({'is_demo':False,'board':digest}),f'board/{digest}.json':raw}
        stores={'a'*40:formal,'b'*40:review,'e'*40:evidence}
        return dict(old={old['id']:old},current={current['id']:current},repo='unused',
            main_commit='a'*40,review_commit='b'*40,evidence_commit='e'*40,
            previous_people=base['people'],read_object=lambda repo,ref,path:stores[ref][path])

    def verify(self,data=None,**kwargs):
        options=self.options(data);options.update(kwargs)
        return verified_account_display_corrections(**options)

    def test_complete_original_and_owner_normalized_states_have_one_same_id_proof(self):
        for owner in ('Self','Undisclosed'):
            data=deepcopy(self.data); data[0]['owner']=owner
            data[4]['transactions']=[deepcopy(data[0])]
            before=deepcopy(data)
            result=self.verify(data)
            self.assertEqual(data,before)
            self.assertEqual(len(result),1)
            self.assertEqual(result[0]['old_id'],data[0]['id'])
            self.assertEqual(result[0]['new_id'],data[0]['id'])
            self.assertEqual(result[0]['changed_fields'],['asset_name','owner'] if owner=='Self' else ['asset_name'])
            self.assertEqual(result[0]['reason'],'source_identity_correction')
            self.assertEqual(result[0]['source_sha256'],hashlib.sha256(next(
                raw for path,raw in data[3].items() if path.endswith('.pdf'))).hexdigest())

    def test_pinned_but_wrong_both_sides_cannot_supply_reconstructed_fields(self):
        for field,value in [('amount_low',1002),('amount_high',50000),('ticker','FORGE'),
                ('transaction_type','sale'),('transaction_date','2026-06-02'),
                ('filed_at','2026-06-14T00:00:00Z'),('instrument_type','Bond'),
                ('source','Another source'),('ticker_mapping_basis','market'),
                ('position_effect','open'),('option_type','call'),('unknown_optional',None),
                ('amount_low_exclusive',False),('person_id','oge:'+'0'*16)]:
            data=deepcopy(self.data);data[0][field]=data[1][field]=value
            data[4]['transactions']=[deepcopy(data[0])]
            with self.subTest(field=field),self.assertRaises(InventoryError):self.verify(data)
        data=deepcopy(self.data)
        data[0]['asset_name']='FORGED ASSET';data[1]['asset_name']='FORGED ASSET — Investment account #7'
        data[4]['transactions']=[deepcopy(data[0])]
        with self.assertRaises(InventoryError):self.verify(data)

    def test_old_main_board_and_people_bind_supplied_identity_independently(self):
        options=self.options();old=next(iter(options['old'].values()))
        old['amount_low']=1002;next(iter(options['current'].values()))['amount_low']=1002
        with self.assertRaisesRegex(InventoryError,'predecessor differs from fixed main'):
            verified_account_display_corrections(**options)
        options=self.options();options['previous_people'][0]['role']='Forged role'
        with self.assertRaisesRegex(InventoryError,'previous identity differs from fixed main'):
            verified_account_display_corrections(**options)
        options=self.options();old=next(iter(options['old'].values()));current=next(iter(options['current'].values()))
        for row in (old,current):row['person_id']='oge:'+'0'*16
        options['previous_people'][0]['id']='oge:'+'0'*16
        with self.assertRaises(InventoryError):verified_account_display_corrections(**options)

    def test_other_changes_owner_classification_and_rekeys_never_get_receipts(self):
        for field,value in [('amount_high',50000),('instrument_type','Bond'),('owner','Spouse'),
                ('id','oge-278t:'+'0'*24),('source_id','house_clerk')]:
            data=deepcopy(self.data);data[1][field]=value
            with self.subTest(field=field):self.assertEqual(self.verify(data),[])
        options=self.options()
        self.assertEqual(verified_owner_corrections(**options),[])
        self.assertEqual(verified_classification_corrections(**options),[])
        data=deepcopy(self.data);data[1]['unknown_optional']=None
        with self.assertRaises(InventoryError):self.verify(data)
        data=deepcopy(self.data);data[0].clear();data[0].update(deepcopy(data[1]))
        data[4]['transactions']=[deepcopy(data[0])]
        self.assertEqual(self.verify(data),[])

    def test_wrong_title_punctuation_or_double_suffix_is_not_a_display_correction(self):
        for value in ['Example Common Stock — Investment account #8',
                      'Example Common Stock - Investment account #7',
                      'Example Common Stock — Investment account #7 — Investment account #7',
                      'EXAMPLE Common Stock — Investment account #7']:
            data=deepcopy(self.data);data[1]['asset_name']=value
            with self.subTest(value=value),self.assertRaises(InventoryError):self.verify(data)
        data=deepcopy(self.data);data[0]['asset_name']+=' — Investment account #6'
        data[4]['transactions']=[deepcopy(data[0])]
        with self.assertRaises(InventoryError):self.verify(data)

    def test_saved_markers_scope_and_raw_inventory_do_not_replace_original_pdf(self):
        def title(report):report['transactions'][0]['account_title']='Investment account #8'
        def marker(report):report['transactions'][0]['legacy_inheritance']['account_display_correction']['new_value']='FORGED'
        def scope(report):report['transactions'][0]['account_context']['status']='observed_because_receipt'
        def raw(report):report['source_rows']=[]
        def legacy(report):report['transactions'][0]['amount_low']=1002
        for token,mutate in [('native-roles',title),('native-roles',marker),('native-roles',scope),
                             ('native-roles',raw),('hybrid-geometry',legacy)]:
            data=deepcopy(self.data);path=next(path for path in data[2] if token in path)
            value=json.loads(data[2][path]);mutate(value);data[2][path]=encoded(value)
            with self.subTest(token=token,mutation=mutate.__name__),self.assertRaises(InventoryError):self.verify(data)

    def test_fixed_archive_review_version_and_directory_cannot_be_substituted(self):
        for role,token,mutate in [
                (2,'whitehouse/coverage-current.json',lambda value:value['reports'][0].update(archive_sha256_versions=['0'*64])),
                (2,'whitehouse/coverage-current.json',lambda value:value['reports'][0].update(extraction_parser_version='other')),
                (3,'/reports/',lambda value:value.update(byte_length=value['byte_length']+1))]:
            data=deepcopy(self.data);path=next(path for path in data[role]
                if (path==token if token.endswith('.json') else token in path and path.endswith('.json')))
            value=json.loads(data[role][path]);mutate(value);data[role][path]=encoded(value)
            with self.subTest(token=token),self.assertRaises(InventoryError):self.verify(data)
        for token in ('.pdf','.html'):
            data=deepcopy(self.data);path=next(path for path in data[3] if path.endswith(token));data[3][path]+=b' altered'
            with self.subTest(token=token),self.assertRaises(InventoryError):self.verify(data)
        data=deepcopy(self.data);path=next(path for path in data[2] if 'hybrid-geometry' in path);del data[2][path]
        with self.assertRaises(InventoryError):self.verify(data)
        for options in ({'main_commit':None},{'review_commit':'invalid'},{'evidence_commit':'d'*40},
                        {'repo':None},{'previous_people':[]}):
            with self.subTest(options=options),self.assertRaises(InventoryError):self.verify(**options)

    def test_unrelated_isolation_does_not_become_a_whole_report_gate(self):
        result=self.verify(fixture(extra='unrelated'))
        self.assertEqual(len(result),1)
        with self.assertRaises(InventoryError):self.verify(fixture(extra='scope'))

    def test_same_asset_in_two_accounts_uses_target_cells_and_stable_id(self):
        data=fixture(extra='two_accounts')
        report=json.loads(next(raw for path,raw in data[2].items() if 'native-roles' in path))
        self.assertEqual([row['account_title'] for row in report['transactions']],
                         ['Investment account #7','Investment account #8'])
        self.assertEqual(len({row['extraction_id'] for row in report['transactions']}),2)
        self.assertEqual(len(self.verify(data)),1)
        wrong=deepcopy(data);wrong[1]['asset_name']=wrong[1]['asset_name'].replace('#7','#8')
        with self.assertRaises(InventoryError):self.verify(wrong)

    def test_shared_read_and_page_budgets_apply_before_source_qualification(self):
        with patch('unison_snapshot.fact_policy.whitehouse_owner_correction.MAX_TOTAL_BYTES',10):
            with self.assertRaisesRegex(InventoryError,'byte budget'):self.verify()
        with patch('unison_snapshot.fact_policy.whitehouse_owner_correction.MAX_PAGES',1):
            with self.assertRaisesRegex(InventoryError,'page count'):self.verify()

    def test_rehashed_real_painting_and_font_state_cannot_authorize_a_title(self):
        for kind in ('hidden','white_gray','white_rgb','white_cmyk','clipped','opacity',
                     'white_cover','scaled_line','font_mapping','crop','page_group','ink_overhang'):
            with self.subTest(kind=kind):
                data=fixture(pdf_mutator=source_mutator(kind))
                before=deepcopy(data)
                with self.assertRaisesRegex(InventoryError,'source visibility is unknown'):self.verify(data)
                self.assertEqual(data,before)
        self.assertEqual(len(self.verify(fixture(pdf_mutator=source_mutator('inset_ink')))),1)

    def test_hidden_title_normal_candidate_cannot_pass_fixed_git_publication_or_touch_outputs(self):
        from scripts import build_whitehouse_278t_candidate as candidate_cli
        from unison_snapshot.house_native_visibility import _source_characters
        import pdfplumber
        data=fixture(pdf_mutator=source_mutator('hidden'))
        old,expected,review,evidence,base=data
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);repo=root/'repo';repo.mkdir()
            write_files(repo,{**review,**evidence})
            pdf=next(repo/path for path in evidence if path.endswith('.pdf'))
            with pdfplumber.open(pdf) as document:
                chars,modes,_,_=_source_characters(document.pages[1])
            self.assertEqual(''.join(char['text'] for char,mode in zip(chars,modes) if mode==3),
                             'Investment account #7')
            raw=encoded(base);digest=hashlib.sha256(raw).hexdigest()
            write_files(repo,{'manifest.json':encoded({'is_demo':False,'board':digest}),f'board/{digest}.json':raw})
            env=dict(os.environ,GIT_NO_LAZY_FETCH='1',PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'))
            def git(*args):return subprocess.check_output(['git','-C',str(repo),*args],env=env,stderr=subprocess.STDOUT)
            git('init','-q');git('config','user.name','Contract');git('config','user.email','contract@example.invalid')
            git('add','.');git('commit','-qm','fixed source with invisible account title');ref=git('rev-parse','HEAD').decode().strip()
            write_files(root,{'base.json':raw})
            candidate,audit=root/'candidate.json',root/'audit.json'
            candidate.write_bytes(b'prior candidate must survive\n')
            audit.write_bytes(b'prior audit must survive\n')
            old_outputs=candidate.read_bytes(),audit.read_bytes()
            with self.assertRaisesRegex(ValueError,'current original source plan'):
                candidate_cli.run(oge_candidate=root/'base.json',review_root=repo,evidence_root=repo,
                    candidate_out=candidate,audit_out=audit,expected_report_count=1)
            self.assertEqual(old_outputs,(candidate.read_bytes(),audit.read_bytes()))
            # Also exercise the independent publication gate with a supplied
            # invalid candidate, even though normal source selection rejects it.
            current=deepcopy(base);current['transactions']=[expected]
            candidate.write_bytes(encoded(current))
            for name in ('house_clerk','senate_efd','oge'):
                value=current if name=='oge' else {**current,'people':[],'transactions':[],'reported_holdings':[]}
                write_files(root/'sources',{f'{name}-current.json':encoded(value)})
            output=root/'delta.json';output.write_bytes(b'prior delta must survive\n')
            protected={path:path.read_bytes() for path in (candidate,audit,output)}
            command=[sys.executable,'-m','unison_snapshot.fact_policy.publication_delta',
                '--previous-board',str(root/'base.json'),'--unified',str(candidate),'--prepared',str(candidate),
                '--source-root',str(root/'sources'),'--output',str(output),'--repo',str(repo),
                '--main-commit',ref,'--review-commit',ref,'--evidence-commit',ref]
            failed=subprocess.run(command,env=env,capture_output=True,text=True)
            self.assertNotEqual(failed.returncode,0)
            self.assertIn('source visibility is unknown',failed.stderr)
            self.assertEqual({path:path.read_bytes() for path in protected},protected)
            self.assertEqual({path:(repo/path).read_bytes() for path in evidence},evidence)


if __name__=='__main__':unittest.main()
