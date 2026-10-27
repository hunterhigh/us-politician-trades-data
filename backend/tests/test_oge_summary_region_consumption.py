"""A fixed Summary region explains only itself in normal OGE consumers.

Generated public boilerplate and synthetic upper financial inputs exercise
transport/replay contracts, not historical transactions or OCR accuracy.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pymupdf

from test_oge_candidate import base, catalog, direct, DOCUMENT_ID
from test_oge_cover_consumption import CUTOFF, write
from test_oge_native_cover_roles import native_cover
from test_oge_observed_candidate import original
from test_oge_content_relations import saved
from test_oge_summary_region import summary_pdf
from unison_snapshot.disclosure_status_oge import build_oge_status
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.oge_reports import parse_table_rows, selected_parser_version
from unison_snapshot.oge_278t_page_shadow import inspect_pdf
from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns
from unison_snapshot.oge_transaction_adapter import (
    _cover_identity_context, adapt_oge_278t_extraction, materialize_oge_278t_observations,
)
from unison_snapshot.source_row_ledger import oge_278t_shadow_pages

ROOT = Path(__file__).resolve().parents[2]


def summary_consumption_fixture(root, *, mutation=None):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    cover=root/'cover.pdf';context=native_cover(cover)
    region=summary_pdf(root/'summary.pdf',page_number=2,mutation=mutation)
    document=pymupdf.open(cover)
    with pymupdf.open(region['source_path']) as second:document.insert_pdf(second)
    raw=document.tobytes();document.close();sha=hashlib.sha256(raw).hexdigest()
    evidence,review=root/'evidence',root/'review'
    relative=f'oge/reports/{DOCUMENT_ID}/{sha}.pdf';source=evidence/relative
    source.parent.mkdir(parents=True);source.write_bytes(raw)
    record=direct(agency=context['agency'])
    record.update(filer_name=context['filer_name'],position_title=context['position_title'],
                  catalog_added_date='2026-06-20')
    # These are the generated fixture's upper-row cells, not official trades.
    cells=['1','Example Company','Purchase','06/01/2026','No','$1,001 - $15,000']
    good,bad=parse_table_rows([(2,cells)],source_sha=sha,default_owner='Self')
    old=original();old.update(source_sha256=sha,catalog_filer_name=record['filer_name'],
        agency=record['agency'],position_title=record['position_title'],
        catalog_added_date='2026-06-20',filed_at='2026-06-13',transactions=good,quarantined=bad,
        source_rows=[{'page_number':2,'cells':cells}])
    empty=base();empty['meta']['data_cutoff_at']=CUTOFF
    formal,_=build_oge_candidate(catalog(record),[old],empty,data_cutoff_at=CUTOFF)
    old.pop('source_rows')  # Existing complete legacy-reference input contract.
    metadata={'schema_version':'oge-278t-archive/v1','source_id':'oge','document_id':DOCUMENT_ID,
        'document_url':record['document_url'],'sha256':sha,'archive_path':relative,'byte_length':len(raw),
        **{key:record[key] for key in ('filer_name','agency','position_title','catalog_added_date',
                                      'amended_label','pending_final_oge_disposition')}}
    write(source.with_suffix('.json'),metadata)
    write(review/f'oge/extractions/{DOCUMENT_ID}/{sha}/oge-278t-pdf-v2.json',old)
    source_catalog=catalog(record);encoded=json.dumps(source_catalog,sort_keys=True).encode()
    digest=hashlib.sha256(encoded).hexdigest()
    normalized=evidence/f'oge/catalog/normalized/{digest}.json'
    normalized.parent.mkdir(parents=True);normalized.write_bytes(encoded)
    write(evidence/f'oge/catalog/{digest}.json',{'schema_version':'oge-catalog-archive/v1',
        'sha256':digest,'retrieved_at':CUTOFF})
    write(review/'status/oge.json',{'catalog_sha256':digest})
    observation=saved([cells]);observation['source_sha256']=sha
    page=observation['pages'][0];edges=[36,65,360,430,500,610,806]
    page['column_edges_pdf_points']=edges
    slot=page['slots'][0];slot['band_pdf_points']=[106.,136.]
    slot['words']=[{'text':text,'bbox_pdf_points':[edges[index]+2.,117.,
        edges[index]+2.+pymupdf.get_text_length(text,fontname='helv',fontsize=10),127.]}
        for index,text in enumerate(cells)]
    slot.update(_observe_slot_columns(slot['words'],edges));slot['raw_ocr_text']=' '.join(cells)
    request={'observations':[observation],'document_page_count':2,'archived_pdf_path':str(source)}
    return dict(source=source,sha=sha,evidence=evidence,review=review,record=record,catalog=source_catalog,
        old=old,formal=formal,request=request,context=_cover_identity_context(old,record))


class SummaryRegionConsumptionTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)

    def consume(self,data):
        return build_oge_candidate(data['catalog'],[data['old']],data['formal'],data_cutoff_at=CUTOFF,
            saved_observations_by_document={DOCUMENT_ID:data['request']})

    def status(self,data,candidate,audit):
        write(data['review']/'oge/qualifications/current.json',audit)
        return build_oge_status(data['evidence'],data['review'],candidate['transactions'],[])

    def materialize(self,data,request=None):
        return materialize_oge_278t_observations(data['old'],data['record'],
            formal_reference_ids=[row['id'] for row in data['formal']['transactions']],
            **(data['request'] if request is None else request))

    def test_mixed_summary_reaches_shadow_materializer_candidate_status_without_whole_page_exemption(self):
        data=summary_consumption_fixture(self.root);before=deepcopy(data['old']);raw=data['source'].read_bytes()
        report=inspect_pdf(data['source'],cover_identity_context=data['context'])
        mixed=report['pages'][1]
        self.assertEqual(len(mixed['non_transaction_regions']),1)
        proof=mixed['non_transaction_regions'][0]
        self.assertEqual((proof['status'],proof['page_number']),('observed',2))
        self.assertFalse(proof['financial_fact_qualification'])
        self.assertEqual(proof['page_coverage'],'partial')
        shadow=oge_278t_shadow_pages(report,source_id='oge',archived_pdf_path=data['source'],
                                    cover_identity_context=data['context'])
        self.assertEqual(shadow[1]['non_transaction_regions'],[proof])
        self.assertNotEqual(shadow[1]['coverage'],'non_table')
        materialized=self.materialize(data);receipt=materialized['observation_receipt']
        self.assertEqual(receipt['non_transaction_regions'],[proof])
        self.assertFalse(receipt['coverage_complete'])
        adapted=adapt_oge_278t_extraction(materialized,data['record'],
            raster_observations=data['request']['observations'],document_page_count=2,
            archived_pdf_path=data['source'])
        ledger=adapted['source_report_ledger'];page=ledger['pages'][1]
        self.assertEqual(page['non_transaction_regions'],[proof])
        self.assertEqual([slot['bounds'] for slot in page['slots']],[[106.,136.]])
        self.assertEqual(ledger['logical_row_count'],1)
        self.assertIsNone(ledger['qualified_transaction_count'])
        self.assertNotEqual(page['coverage'],'non_table')
        write(self.root/'catalog.json',data['catalog']);write(self.root/'base.json',data['formal'])
        write(self.root/'saved.json',{DOCUMENT_ID:{key:value for key,value in data['request'].items()
            if key != 'archived_pdf_path'}})
        process=subprocess.run([sys.executable,'-m','unison_snapshot','build-oge-candidate',
            '--catalog',str(self.root/'catalog.json'),'--base',str(self.root/'base.json'),
            '--extractions-dir',str(data['review']/'oge/extractions'),'--evidence-root',str(data['evidence']),
            '--data-cutoff-at',CUTOFF,'--saved-observations',str(self.root/'saved.json'),
            '--output',str(self.root/'candidate.json'),'--audit-output',str(self.root/'audit.json')],
            cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT/'backend/src'),'PYTHONIOENCODING':'utf-8'},
            capture_output=True,text=True)
        self.assertEqual(process.returncode,0,process.stdout+process.stderr)
        candidate=json.loads((self.root/'candidate.json').read_bytes())
        audit=json.loads((self.root/'audit.json').read_bytes())
        self.assertEqual(candidate['transactions'],data['formal']['transactions'])
        self.assertEqual(audit['reports'][0]['observation_receipt']['non_transaction_regions'],[proof])
        scopes=self.status(data,candidate,audit)
        record=next(scope for scope in scopes if scope['scope_id']=='oge_direct_278t')['reports'][0]
        self.assertEqual(record['non_transaction_regions'],[proof])
        self.assertEqual(record['qualification_state'],'partial')
        self.assertIsNone(record['unique_source_quarantined_row_count'])
        from unison_snapshot.builder import build
        from unison_snapshot.disclosure_status import attach_disclosure_status
        from unison_snapshot.public_repo import PublicSnapshotRepository
        from test_public_repo import FakeTransport
        bundle=build(candidate,generated_at=CUTOFF,allow_production=True)
        bundle=attach_disclosure_status(bundle,scopes,transactions=candidate['transactions'],
            provenance={key:value*40 for key,value in [('code_commit','a'),('evidence_commit','b'),
                ('review_commit','c'),('previous_main_commit','d')]})
        public=PublicSnapshotRepository('contract','data',transport=FakeTransport(bundle.files))
        self.assertEqual(public.fetch().snapshot['transactions'],candidate['transactions'])
        status=public.disclosure_status(public.resolve(),include_records=True)
        published=[item for source in status['sources'] for item in source['records']
                   if item.get('non_transaction_regions')]
        self.assertEqual([item['non_transaction_regions'] for item in published],[[proof]])
        repeated,repeated_audit=build_oge_candidate(data['catalog'],[data['old']],candidate,data_cutoff_at=CUTOFF,
            saved_observations_by_document={DOCUMENT_ID:data['request']})
        self.assertEqual(repeated,candidate)
        self.assertEqual(repeated_audit['reports'][0]['observation_receipt'],audit['reports'][0]['observation_receipt'])
        self.assertEqual(data['old'],before);self.assertEqual(data['source'].read_bytes(),raw)

    def test_claimed_data_or_unknown_slot_overlapping_summary_is_retained_without_region_exemption(self):
        data=summary_consumption_fixture(self.root)
        for kind in ('crossing_data','additional_unknown'):
            with self.subTest(kind=kind):
                request=deepcopy(data['request']);page=request['observations'][0]['pages'][0]
                if kind=='crossing_data':
                    slot=page['slots'][0];slot['band_pdf_points']=[390.,420.]
                    for word in slot['words']:
                        word['bbox_pdf_points'][1]+=284;word['bbox_pdf_points'][3]+=284
                    slot.update(_observe_slot_columns(slot['words'],page['column_edges_pdf_points']))
                else:
                    words=[{'text':'UNRESOLVED','bbox_pdf_points':[67.,412.,130.,420.]}]
                    page['slots'].append({'slot_index':2,'band_pdf_points':[410.,430.],
                        'raw_ocr_text':'UNRESOLVED','words':words,
                        **_observe_slot_columns(words,page['column_edges_pdf_points'])})
                    page['physical_slot_count']=2
                result=self.materialize(data,request);receipt=result['observation_receipt']
                self.assertEqual(receipt.get('non_transaction_regions',[]),[])
                self.assertFalse(receipt['coverage_complete'])
                adapted=adapt_oge_278t_extraction(result,data['record'],raster_observations=request['observations'],
                    document_page_count=2,archived_pdf_path=data['source'])
                ledger=adapted['source_report_ledger'];mixed=ledger['pages'][1]
                self.assertEqual(mixed.get('non_transaction_regions',[]),[])
                self.assertEqual(len(mixed['slots']),len(page['slots']))
                self.assertEqual([slot['bounds'] for slot in mixed['slots']],
                                 [slot['band_pdf_points'] for slot in page['slots']])
                self.assertNotEqual(mixed['coverage'],'non_table')
                self.assertTrue(ledger['logical_rows'])

    def test_extra_crossing_or_paint_source_content_cannot_be_excused_as_summary(self):
        for mutation in ('extra_text','crossing_text','white_cover'):
            with self.subTest(mutation=mutation):
                data=summary_consumption_fixture(self.root/mutation,mutation=mutation)
                candidate,audit=self.consume(data);receipt=audit['reports'][0]['observation_receipt']
                self.assertEqual(receipt.get('non_transaction_regions',[]),[])
                self.assertFalse(receipt['coverage_complete'])
                self.assertEqual(candidate['transactions'],data['formal']['transactions'])
                if mutation=='white_cover':
                    # The added actual white paint also selects an existing
                    # visibility parser. A stale ordinary cache is not entitled
                    # to a successful status merely because Summary failed.
                    metadata=json.loads(data['source'].with_suffix('.json').read_bytes())
                    self.assertNotEqual(selected_parser_version(metadata,data['source'],data['sha']),
                                        data['old']['parser_version'])
                    with self.assertRaisesRegex(ValueError,'selected original or parser differs'):
                        self.status(data,candidate,audit)
                    continue
                scopes=self.status(data,candidate,audit)
                record=next(scope for scope in scopes if scope['scope_id']=='oge_direct_278t')['reports'][0]
                self.assertEqual(record.get('non_transaction_regions',[]),[])
                self.assertEqual(record['qualification_state'],'partial')

    def test_status_replays_actual_source_and_rejects_modified_region_proof(self):
        data=summary_consumption_fixture(self.root);candidate,audit=self.consume(data)
        self.status(data,candidate,audit)
        source=data['source'].read_bytes();old_cache=next((data['review']/'oge/extractions').rglob('*.json'))
        cached=old_cache.read_bytes()
        changed=deepcopy(audit)
        changed['reports'][0]['observation_receipt']['non_transaction_regions'][0]['region_bbox_pdf_points'][1]-=1
        with self.assertRaises(ValueError):self.status(data,candidate,changed)
        self.assertEqual(old_cache.read_bytes(),cached)
        write(data['review']/'oge/qualifications/current.json',audit)
        data['source'].write_bytes(source+b'\n% changed controlled source\n')
        try:
            with self.assertRaises(ValueError):build_oge_status(data['evidence'],data['review'],candidate['transactions'],[])
        finally:data['source'].write_bytes(source)
        self.assertEqual(old_cache.read_bytes(),cached)


if __name__=='__main__':unittest.main()
