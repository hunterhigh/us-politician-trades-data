"""Referential notes through the actual saved CLI and public status reader.

Only generated source programs/public boilerplate are used. Neither note
wording nor a printed reference grants financial qualification or deduping.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_oge_summary_region_consumption as summary_controls
from test_oge_endnote_region import endnote_pdf
from test_oge_summary_region import _text
from test_oge_saved_observations_cli import run_cli
from test_oge_cover_consumption import CUTOFF, DOCUMENT_ID, write
from test_public_repo import FakeTransport
from unison_snapshot.builder import build
from unison_snapshot.disclosure_status import attach_disclosure_status
from unison_snapshot.disclosure_status_oge import build_oge_status
from unison_snapshot.oge_278t_page_coverage import inspect_pdf
from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns
from unison_snapshot.oge_278t_region_roles import replay_native_region
from unison_snapshot.oge_saved_observations import prepare_saved_oge_observations
from unison_snapshot.public_repo import PublicSnapshotRepository
from unison_snapshot.source_row_ledger import oge_278t_shadow_pages


ROLE='native_referential_endnotes'


def duplicate_upper_program():
    """Keep the original synthetic control row; add one physical duplicate #."""
    edges=[36,65,360,430,500,610,806]
    program=['q 2 J 0 G 1 w']
    for top in (76,106,136,166):
        for left,right in zip(edges,edges[1:]):
            program.append(f'{left} {595-top} m {right} {595-top} l S')
    program.append('Q')
    for text,x in zip(('#','DESCRIPTION','TYPE','DATE','NOTIFICATION','AMOUNT'),edges):
        program.append(_text(text,x+2,500))
    for y in (470,440):
        for text,x in zip(('1','Example Company','Purchase','06/01/2026','No','$1,001 - $15,000'),edges):
            program.append(_text(text,x+2,y))
    return '\n'.join(program)


def endnote_consumption_fixture(root, *, missing=False, duplicate=False):
    options={'targets':('1',),'note_numbers':('99',) if missing else ('1',)}
    if duplicate:options['upper_program']=duplicate_upper_program()
    def factory(path,**kwargs):
        return endnote_pdf(path,**kwargs,**options)
    # Replace only a test source factory, never a product observer, source
    # replay, candidate/status function, or CLI. All resulting bytes are read.
    with patch.object(summary_controls,'summary_pdf',side_effect=factory):
        data=summary_controls.summary_consumption_fixture(root)
    root=Path(root);data['root']=root
    request=data['request']
    package=prepare_saved_oge_observations(evidence_root=data['evidence'],
        metadata_path=data['source'].with_suffix('.json'),observations=request['observations'],
        document_page_count=request['document_page_count'])
    data['package']=root/'saved.json';write(data['package'],package)
    write(root/'extractions/original.json',data['old'])
    write(root/'catalog.json',data['catalog']);write(root/'base.json',data['formal'])
    return data


def note_proof(regions):
    return next(proof for proof in regions if proof['role']==ROLE)


class EndnoteRegionConsumptionTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)

    def normal(self,data):
        result=run_cli(data['root'],'--evidence-root',data['evidence'],'--saved-observations',data['package'])
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        candidate=json.loads((data['root']/'candidate.json').read_bytes())
        audit=json.loads((data['root']/'audit.json').read_bytes())
        return candidate,audit

    def status(self,data,candidate,audit):
        write(data['review']/'oge/qualifications/current.json',audit)
        scopes=build_oge_status(data['evidence'],data['review'],candidate['transactions'],[])
        report=next(scope for scope in scopes if scope['scope_id']=='oge_direct_278t')['reports'][0]
        return scopes,report

    def public(self,candidate,scopes):
        bundle=build(candidate,generated_at=CUTOFF,allow_production=True)
        bundle=attach_disclosure_status(bundle,scopes,transactions=candidate['transactions'],
            provenance={'code_commit':'a'*40,'evidence_commit':'b'*40,'review_commit':'c'*40,
                        'previous_main_commit':'d'*40})
        repository=PublicSnapshotRepository('contract','data',transport=FakeTransport(bundle.files))
        self.assertEqual(repository.fetch().snapshot['transactions'],candidate['transactions'])
        published=repository.disclosure_status(repository.resolve(),include_records=True)
        return next(record for source in published['sources'] for record in source['records']
                    if record.get('report_id')==DOCUMENT_ID)

    def test_actual_saved_cli_public_reader_and_nextbase_keep_financial_facts_and_raw_note_lines(self):
        data=endnote_consumption_fixture(self.root);source=data['source'].read_bytes()
        old=data['old'];before=deepcopy(old)
        candidate,audit=self.normal(data)
        self.assertEqual(candidate['transactions'],data['formal']['transactions'])
        receipt=audit['reports'][0]['observation_receipt'];regions=receipt['non_transaction_regions']
        self.assertEqual({proof['role'] for proof in regions},{ROLE,'fixed_native_summary_of_contents_2019'})
        self.assertFalse(receipt['coverage_complete'])
        self.assertEqual(receipt['observed_slot_count'],1)
        notes=note_proof(regions)
        self.assertEqual(notes['financial_effect'],'unknown')
        self.assertFalse(notes['financial_fact_qualification'])
        self.assertTrue(notes['reference_cover_context_used'])
        self.assertEqual(len(notes['notes']),1)
        note=notes['notes'][0]
        self.assertEqual(note['raw_text_lines'],['Synthetic note for source row 1.',
                                               'Its financial effect remains unknown.'])
        self.assertEqual(note['raw_text'],'\n'.join(note['raw_text_lines']))
        self.assertEqual(note['reference']['status'],'source_bound')
        target=note['reference']['target']
        self.assertEqual((target['source_sha256'],target['page_number'],target['printed_number']),
                         (data['sha'],2,'1'))
        self.assertEqual(target['band_pdf_points'],[106.,136.])
        self.assertNotIn('extraction_id',target)
        scopes,report=self.status(data,candidate,audit)
        self.assertEqual(report['qualification_state'],'partial')
        self.assertIsNone(report['unique_source_quarantined_row_count'])
        self.assertEqual(report['non_transaction_regions'],regions)
        self.assertEqual(self.public(candidate,scopes)['non_transaction_regions'],regions)
        # Once against the actual successful output as base; no parser/source
        # cache or hand-built old object substitutes for this next base.
        write(data['root']/'base.json',candidate)
        repeated,repeated_audit=self.normal(data)
        self.assertEqual(repeated,candidate)
        self.assertEqual(repeated_audit['reports'][0]['observation_receipt'],receipt)
        self.assertEqual(data['old'],before);self.assertEqual(data['source'].read_bytes(),source)

    def test_missing_and_duplicate_targets_transport_observed_notes_with_unknown_reference(self):
        for name,options,reason in (
                ('missing',{'missing':True},'target_number_missing'),
                ('duplicate',{'duplicate':True},'target_number_not_unique')):
            with self.subTest(case=name):
                data=endnote_consumption_fixture(self.root/name,**options)
                candidate,audit=self.normal(data)
                self.assertEqual(candidate['transactions'],data['formal']['transactions'])
                receipt=audit['reports'][0]['observation_receipt'];proof=note_proof(receipt['non_transaction_regions'])
                self.assertEqual(proof['status'],'observed')
                note=proof['notes'][0]
                self.assertEqual(note['reference']['status'],'unknown')
                self.assertEqual(note['reference']['reason'],reason)
                self.assertTrue(note['raw_text_lines']);self.assertEqual(proof['financial_effect'],'unknown')
                self.assertFalse(receipt['coverage_complete'])
                scopes,report=self.status(data,candidate,audit)
                self.assertEqual(report['qualification_state'],'partial')
                self.assertEqual(note_proof(report['non_transaction_regions']),proof)
                self.assertEqual(note_proof(self.public(candidate,scopes)['non_transaction_regions']),proof)

    def test_status_rejects_changed_reference_context_or_actual_source(self):
        data=endnote_consumption_fixture(self.root);candidate,audit=self.normal(data)
        self.status(data,candidate,audit)
        for kind in ('target','context'):
            with self.subTest(kind=kind):
                changed=deepcopy(audit)
                proof=note_proof(changed['reports'][0]['observation_receipt']['non_transaction_regions'])
                if kind=='target':proof['notes'][0]['reference']['target']['band_pdf_points']=[0.,595.]
                else:proof['reference_cover_context_sha256']='0'*64
                with self.assertRaises(ValueError):self.status(data,candidate,changed)
        write(data['review']/'oge/qualifications/current.json',audit)
        metadata_path=data['source'].with_suffix('.json');metadata=metadata_path.read_bytes()
        changed=json.loads(metadata);changed['agency']='Different catalog identity'
        metadata_path.write_text(json.dumps(changed))
        try:
            with self.assertRaises(ValueError):build_oge_status(data['evidence'],data['review'],candidate['transactions'],[])
        finally:metadata_path.write_bytes(metadata)
        raw=data['source'].read_bytes();data['source'].write_bytes(raw+b'\n%changed source\n')
        try:
            with self.assertRaises(ValueError):build_oge_status(data['evidence'],data['review'],candidate['transactions'],[])
        finally:data['source'].write_bytes(raw)

    def test_claimed_unknown_data_over_note_region_stays_unknown_and_keeps_upper_fact(self):
        data=endnote_consumption_fixture(self.root)
        package=json.loads(data['package'].read_bytes())
        page=package[DOCUMENT_ID]['observations'][0]['pages'][0]
        words=[{'text':'UNRESOLVED','bbox_pdf_points':[67.,260.,130.,268.]}]
        page['slots'].append({'slot_index':2,'band_pdf_points':[254.,284.],
            'raw_ocr_text':'UNRESOLVED','words':words,
            **_observe_slot_columns(words,page['column_edges_pdf_points'])})
        page['physical_slot_count']=2;write(data['package'],package)
        candidate,audit=self.normal(data)
        self.assertEqual(candidate['transactions'],data['formal']['transactions'])
        receipt=audit['reports'][0]['observation_receipt']
        self.assertFalse(receipt['coverage_complete'])
        self.assertNotIn(ROLE,{p['role'] for p in receipt.get('non_transaction_regions',[])})
        self.assertEqual(receipt['observed_slot_count'],2)
        self.assertGreaterEqual(receipt['unresolved_slot_count'],1)
        unchanged=json.loads(data['package'].read_bytes())[DOCUMENT_ID]['observations'][0]['pages'][0]
        self.assertEqual([slot['band_pdf_points'] for slot in unchanged['slots']],
                         [[106.,136.],[254.,284.]])

    def test_unbound_reading_replays_with_later_context_without_upgrading_reference_scope(self):
        data=endnote_consumption_fixture(self.root)
        report=inspect_pdf(data['source'])
        proof=note_proof(report['pages'][1]['non_transaction_regions'])
        before=deepcopy(proof)
        self.assertFalse(proof['reference_cover_context_used'])
        self.assertEqual(proof['notes'][0]['reference']['status'],'unknown')
        self.assertTrue(replay_native_region(data['source'],proof,source_sha256=data['sha'],
            page_number=2,cover_identity_context=data['context']))
        ledger=oge_278t_shadow_pages(report,source_id='oge',archived_pdf_path=data['source'],
                                    cover_identity_context=data['context'])
        self.assertEqual(note_proof(ledger[1]['non_transaction_regions']),before)
        self.assertEqual(proof,before)


if __name__=='__main__':unittest.main()
