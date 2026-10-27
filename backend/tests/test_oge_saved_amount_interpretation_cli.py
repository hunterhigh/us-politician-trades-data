"""Actual saved writer/normal CLI contracts, with generated source/readings.

These tests certify transport, isolation and preservation, not OCR accuracy or
successful reinterpretation of the historical 44-page official package.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_oge_content_relations import saved
from test_oge_cover_consumption import CUTOFF, DOCUMENT_ID, write
from test_oge_saved_observations_cli import ROOT, normal_fixture, run_cli
from test_public_repo import FakeTransport
from unison_snapshot.builder import build
from unison_snapshot.disclosure_status import attach_disclosure_status
from unison_snapshot.oge_278t_field_shadow import (
    LEGACY_AMOUNT_INTERPRETATION_MODE as MODE, _amount_shape,
)
from unison_snapshot.oge_saved_observations import load_saved_oge_observations
from unison_snapshot.public_repo import PublicSnapshotRepository


def secondary_amounts(observation, affected_index):
    slots = []
    for source in observation['pages'][0]['slots']:
        columns = source['column_observation']
        primary = ' '.join(columns[name]['raw_ocr_text'] for name in ('notification', 'amount'))
        raw = 'Yos $50,001-$100,000' if source['slot_index'] == affected_index else primary.strip()
        top = source['band_pdf_points'][0]
        shape = ({'status':'ocr_shape_only', 'values':[50001,100000], 'reason':None}
                 if source['slot_index'] == affected_index else _amount_shape(raw))
        slots.append({'slot_index':source['slot_index'], 'band_pdf_points':source['band_pdf_points'],
            'page_raw_ocr_text':primary, 'column_raw_ocr_text':raw, 'column_shape':shape,
            'column_detections':([{'text':raw, 'bbox_pdf_points':[111,top+2,148,top+8]}] if raw else [])})
    return {'schema_version':'oge-278t-amount-column-observation/v2',
        'source_sha256':observation['source_sha256'], 'ocr_engine':'synthetic-column',
        'ocr_resolution_dpi':300, 'input_ocr_resolution_dpi':200,
        'summary':{'observed_slots':len(slots)}, 'pages':[{'page_number':2,
            'status':'amount_column_observed', 'slot_count':len(slots), 'slots':slots}]}


class SavedAmountInterpretationCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = normal_fixture(self.root)
        rows = [self.data['old']['transactions'][0]['cells'],
            ['', 'Filer assets', '', '', '', ''],
            ['2', 'INDEPENDENT', 'purchase', '05/02/2025', 'No', '$1,001 - $15,000'],
            ['3', 'AFFECTED', 'purchase', '05/02/2025', 'No', '$50,001 - $100,000']]
        self.observation = saved(rows); self.observation['source_sha256'] = self.data['sha']
        self.secondary = secondary_amounts(self.observation, 4)
        self.primary_path, self.secondary_path = self.root/'primary.json', self.root/'secondary.json'
        write(self.primary_path, self.observation); write(self.secondary_path, self.secondary)
        self.ledger = self.root/'field-ledger.json'
        self.package = (self.data['review']/'oge/saved_observations'/DOCUMENT_ID/
                        self.data['sha']/'saved-observations.json')

    def writer(self, *, mode=True, output=None):
        command = [sys.executable, str(ROOT/'backend/scripts/observe_oge_278t_fields.py'),
            '--observations', str(self.primary_path), '--secondary-observations', str(self.secondary_path),
            '--review-root', str(self.data['review']), '--evidence-root', str(self.data['evidence']),
            '--metadata', str(self.data['source'].with_suffix('.json')), '--output', str(output or self.ledger)]
        if mode:
            command += ['--field-interpretation-mode', MODE]
        return subprocess.run(command,cwd=ROOT,env=dict(os.environ,PYTHONPATH=str(ROOT/'backend/src'),
            PYTHONIOENCODING='utf-8'),capture_output=True,text=True)

    def normal(self):
        return run_cli(self.root, '--evidence-root', self.data['evidence'],
                       '--saved-observations', self.package)

    def sentinels(self):
        write(self.ledger, {'keep':'ledger'}); write(self.package, {'keep':'package'})
        return [path.read_bytes() for path in (self.ledger,self.package,self.data['source'],self.primary_path,self.secondary_path)]

    def assert_sentinels(self, before):
        self.assertEqual(before,[path.read_bytes() for path in
            (self.ledger,self.package,self.data['source'],self.primary_path,self.secondary_path)])

    def test_explicit_writer_normal_CLI_status_reader_and_repeat_keep_old_and_independent_good(self):
        before = self.sentinels()
        strict = self.writer(mode=False)
        self.assertNotEqual(strict.returncode,0)
        self.assertIn('secondary field shape differs',strict.stderr)
        self.assert_sentinels(before)
        process = self.writer()
        self.assertEqual(process.returncode,0,process.stderr)
        payload = json.loads(self.package.read_bytes())[DOCUMENT_ID]
        self.assertEqual(payload['field_interpretation_mode'],MODE)
        self.assertEqual(payload['observations'],[self.observation])
        self.assertEqual(payload['secondary_observations'],[self.secondary])
        ledger = json.loads(self.ledger.read_bytes())
        self.assertEqual(ledger['field_interpretation_mode'],MODE)
        block = ledger['pages'][0]['slots'][3]['interpretation_blockers'][0]
        self.assertEqual(block['reason'],'legacy_amount_shape_current_unresolved')
        self.assertEqual(block['current_shape']['status'],'unknown')
        pair = self.package.read_bytes(),self.ledger.read_bytes()
        self.assertEqual(self.writer().returncode,0)
        self.assertEqual(pair,(self.package.read_bytes(),self.ledger.read_bytes()))
        bound = load_saved_oge_observations(self.package,evidence_root=self.data['evidence'],
                                            extractions=[self.data['old']])
        self.assertEqual(bound[DOCUMENT_ID]['field_interpretation_mode'],MODE)
        result = self.normal(); self.assertEqual(result.returncode,0,result.stderr)
        candidate = json.loads((self.root/'candidate.json').read_bytes())
        audit = json.loads((self.root/'audit.json').read_bytes())
        self.assertIn(self.data['formal']['transactions'][0],candidate['transactions'])
        self.assertEqual(len(candidate['transactions']),2)
        self.assertIn('INDEPENDENT',{r['asset_name'] for r in candidate['transactions']})
        self.assertNotIn('AFFECTED',{r['asset_name'] for r in candidate['transactions']})
        self.assertFalse(audit['reports'][0]['observation_receipt']['coverage_complete'])
        report = audit['reports'][0]
        old_id = self.data['formal']['transactions'][0]['id']
        current_id = next(r['id'] for r in candidate['transactions'] if r['asset_name']=='INDEPENDENT')
        self.assertEqual(report['amount_recompute_current_qualified_ids'],[current_id])
        self.assertEqual(report['amount_recompute_retained_formal_ids'],[old_id])
        self.assertEqual(set(report['amount_recompute_published_ids']),{old_id,current_id})
        before_outputs = [(self.root/name).read_bytes() for name in ('candidate.json','audit.json')]
        self.assertEqual(self.normal().returncode,0)
        self.assertEqual(before_outputs,[(self.root/name).read_bytes() for name in ('candidate.json','audit.json')])
        write(self.data['review']/'oge/qualifications/current.json',audit)
        for tree in (self.data['evidence'],self.data['review']):
            for args in (('init','-q'),('config','user.name','Contract'),
                    ('config','user.email','contract@example.invalid'),('add','.'),('commit','-qm','saved contract')):
                subprocess.run(['git','-C',str(tree),*args],check=True,capture_output=True)
        status = subprocess.run([sys.executable,str(ROOT/'backend/scripts/build_disclosure_status.py'),
            '--evidence-root',str(self.data['evidence']),'--review-root',str(self.data['review']),
            '--input',str(self.root/'candidate.json'),'--code-commit','b'*40,'--previous-main-commit','a'*40,
            '--output',str(self.root/'status.json')],cwd=ROOT,
            env=dict(os.environ,PYTHONPATH=str(ROOT/'backend/src')),capture_output=True,text=True)
        self.assertEqual(status.returncode,0,status.stderr)
        status = json.loads((self.root/'status.json').read_bytes())
        record = next(r for scope in status['scopes'] for r in scope['reports'] if r['report_id']==DOCUMENT_ID)
        self.assertEqual(record['qualification_state'],'partial')
        # The old normalized formal is retained, without pretending that the
        # current inputs independently reproduced its complete qualification.
        self.assertEqual(record['qualified_transaction_count'],1)
        self.assertEqual(set(record['publication_transaction_ids']),{old_id,current_id})
        self.assertIsNone(record['quarantined_row_count'])
        self.assertGreaterEqual(record['observed_quarantined_slot_count'],1)
        bundle = attach_disclosure_status(build(candidate,generated_at=CUTOFF,allow_production=True),
            status['scopes'],transactions=candidate['transactions'],provenance=status['provenance'])
        public = PublicSnapshotRepository('contract','data',transport=FakeTransport(bundle.files))
        self.assertEqual(public.fetch().snapshot['transactions'],candidate['transactions'])
        self.assertTrue(public.disclosure_status(public.resolve(),include_records=True))
        self.assertEqual(before[2:],[path.read_bytes() for path in
            (self.data['source'],self.primary_path,self.secondary_path)])

    def test_mode_never_accepts_wrong_source_geometry_raw_or_arbitrary_derived_shape(self):
        original = deepcopy(self.secondary)
        for kind in ('source','band','raw','shape','duplicate'):
            with self.subTest(kind=kind):
                bad = deepcopy(original)
                slot = bad['pages'][0]['slots'][3]
                if kind=='source':bad['source_sha256']='0'*64
                elif kind=='band':slot['band_pdf_points']=[100,115]
                elif kind=='raw':slot['column_raw_ocr_text']+=' altered'
                elif kind=='shape':slot['column_shape']['values']=[7,9]
                else:bad['pages'][0]['slots'].append(deepcopy(slot))
                write(self.secondary_path,bad)
                before = self.sentinels()
                result = self.writer(); self.assertNotEqual(result.returncode,0)
                self.assert_sentinels(before)
        write(self.secondary_path,original)

    def test_unknown_or_null_portable_policy_and_aliases_fail_before_outputs(self):
        process = self.writer(); self.assertEqual(process.returncode,0,process.stderr)
        good = json.loads(self.package.read_bytes())
        for mode in (None,'unsupported/v1'):
            changed = deepcopy(good); changed[DOCUMENT_ID]['field_interpretation_mode']=mode
            write(self.package,changed)
            for name in ('candidate.json','audit.json'):write(self.root/name,{'keep':name})
            before = [(self.root/name).read_bytes() for name in ('candidate.json','audit.json')]
            result = self.normal(); self.assertNotEqual(result.returncode,0)
            self.assertEqual(before,[(self.root/name).read_bytes() for name in ('candidate.json','audit.json')])
        write(self.package,good)
        before = self.sentinels()
        result = self.writer(output=self.primary_path)
        self.assertNotEqual(result.returncode,0); self.assert_sentinels(before)


if __name__ == '__main__':
    unittest.main()
