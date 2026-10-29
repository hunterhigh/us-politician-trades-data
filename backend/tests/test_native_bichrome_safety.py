"""Independent source/cache/formal/status contracts for new contrast consumers."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import test_native_partition_consumption as normal
import test_native_partition_status as status_helpers
import test_native_partition_exhibit as exhibit_helpers
from test_native_bichrome_consumption import source_pdf
from whitehouse_account_display_fixture import encoded, write_files
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.native_partition_rows import partition_binding


class BichromeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.status_helper=status_helpers.NativePartitionStatusTests()
        self.status_helper.setUp();self.addCleanup(self.status_helper.doCleanups)
        self.normal=self.status_helper.consumer

    def prepare(self,whitehouse,generic,kind='white_on_white'):
        raw=source_pdf(kind)
        with patch.object(normal,'normal_pdf',return_value=raw):
            return self.status_helper.prepare(whitehouse=whitehouse,generic=generic,kind=kind)

    def snapshot(self,data):
        return tuple((data['root']/name).read_bytes() for name in ('candidate.json','audit.json'))

    def candidate(self,data,generic,*,roots=True,success=True):
        if data['whitehouse']:
            return self.normal.wh_candidate(data['root'],roots=roots,success=success,
                input_path=data['root']/'candidate.json')
        return self.normal.oge_candidate(data['root'],generic,roots=roots,success=success)

    def test_source_fills_cannot_supply_physical_grid_edges(self):
        from unison_snapshot.native_text_partition import observe_native_text_partition
        for whitehouse in (False,True):
            with self.subTest(whitehouse=whitehouse):
                data=self.prepare(whitehouse,False,'white_on_dark')
                report=json.loads(data['cache'].read_bytes())
                boxes=[row['cell_boxes'] for row in report['native_text_partition']['rows']]
                expected=[[tuple([a,100+25*row,b,125+25*row]) for a,b in zip(
                    [30,50,350,410,480,550],[50,350,410,480,550,680])] for row in range(6)]
                self.assertEqual(boxes,[[list(cell) for cell in row] for row in expected])
                page=report['native_text_partition']['pages'][1]
                self.assertEqual(page['table_grid_basis'],'source_bound_strokes_only')
                self.assertTrue(page['historical_default_grids'])
                self.assertNotEqual(page['historical_default_grids'],[boxes])

    def test_saved_proof_resigning_roots_metadata_and_source_fail_before_output(self):
        for whitehouse in (False,True):
            for generic in (False,True):
                with self.subTest(whitehouse=whitehouse,generic=generic):
                    data=self.prepare(whitehouse,generic)
                    original=data['cache'].read_bytes();before=self.snapshot(data)
                    self.candidate(data,generic,roots=False,success=False)
                    self.assertEqual(self.snapshot(data),before)
                    value=json.loads(original)
                    value['native_text_partition']['pages'][1]['source_contrast']['glyph_states']=['displayed']*len(
                        value['native_text_partition']['pages'][1]['chars'])
                    value['native_text_partition']['row_reasons']={}
                    value['native_text_partition']['scope_barriers_before']={}
                    value['native_text_partition']['pages'][1]['text']+=' forged'
                    data['cache'].write_bytes(encoded(value))
                    if not whitehouse:
                        write_files(data['root'],{f'extractions/{data["metadata"]["document_id"]}.json':encoded(value)})
                    # The attacker also signs the new projection hash into audit.
                    changed=deepcopy(data['audit'])
                    changed['reports'][0]['native_source_partition_binding']=partition_binding(
                        value['native_text_partition'],value['parser_version'])
                    data['audit_path'].write_bytes(encoded(changed))
                    with self.assertRaises((ValueError,OgeCatalogError)):
                        self.status_helper.status(data)
                    self.candidate(data,generic,success=False)
                    self.assertEqual(self.snapshot(data),before)
                    data['cache'].write_bytes(original)
                    if not whitehouse:
                        write_files(data['root'],{f'extractions/{data["metadata"]["document_id"]}.json':original})
                    data['audit_path'].write_bytes(encoded(data['audit']))
                    metadata=data['evidence']/data['metadata']['archive_path'].replace('.pdf','.json')
                    raw_meta=metadata.read_bytes();wrong=json.loads(raw_meta)
                    wrong['filer_name_from_label' if whitehouse else 'agency']='FORGED SOURCE IDENTITY'
                    metadata.write_bytes(encoded(wrong))
                    self.candidate(data,generic,success=False)
                    self.assertEqual(self.snapshot(data),before)
                    metadata.write_bytes(raw_meta)
                    source=data['evidence']/data['metadata']['archive_path'];raw=source.read_bytes()
                    source.write_bytes(raw+b'\n% source changed')
                    self.candidate(data,generic,success=False)
                    self.assertEqual(self.snapshot(data),before)
                    source.write_bytes(raw)
                    self.assertEqual(self.status_helper.status(data),self.status_helper.status(data))

    def test_same_sha_historical_successes_cannot_replace_current_source_policy(self):
        from unison_snapshot.oge_reports import load_reusable_extraction
        for whitehouse in (False,True):
            for generic in (False,True):
                with self.subTest(whitehouse=whitehouse,generic=generic):
                    data=self.prepare(whitehouse,generic,'footer')
                    report=json.loads(data['cache'].read_bytes());saved=data['cache'].read_bytes()
                    self.assertIn('native-bichrome',report['parser_version'])
                    versions=(['whitehouse-278t-hybrid-geometry/v2','whitehouse-278t-native-roles/v5',
                               'whitehouse-278t-native-visible-default/v1','whitehouse-278t-native-visible-generic/v1']
                              if whitehouse else ['oge-278t-pdf/v2','oge-278t-pdf/v7',
                               'oge-278t-native-visible-default/v1','oge-278t-native-visible-generic/v1'])
                    for version in versions:
                        historical={**report,'parser_version':version,'transactions':report['transactions'],'quarantined':[]}
                        write_files(data['cache'].parent,{version.replace('/','-')+'.json':encoded(historical)})
                    self.candidate(data,generic)
                    data['cache'].unlink()
                    with self.assertRaises((ValueError,OSError,OgeCatalogError)):
                        self.status_helper.status(data)
                    if not whitehouse:
                        meta=data['evidence']/data['metadata']['archive_path'].replace('.pdf','.json')
                        self.assertIsNone(load_reusable_extraction(data['evidence'],meta,
                            data['review']/'oge/extractions',generic=generic))
                    data['cache'].write_bytes(saved)
                    self.assertIn('native-bichrome',json.loads(data['cache'].read_bytes())['parser_version'])

    def test_old_complete_formal_objects_and_ids_are_conserved_not_copied(self):
        from unison_snapshot.oge_candidate import build_oge_candidate
        from unison_snapshot.oge_reports import (PARSER_VERSION,GENERIC_PARSER_VERSION,
            parse_table_rows,parse_generic_table_rows)
        from test_oge_candidate import base as empty_base
        for generic in (False,True):
            with self.subTest(generic=generic):
                raw=source_pdf('white_on_white')
                with patch.object(normal,'normal_pdf',return_value=raw):
                    root,_,catalog=self.normal.oge_inputs('white_on_white')
                current=self.normal.oge_extract(root,generic)
                inventory=current['native_text_partition']['raw_inventory']
                if generic:good,bad,structure=parse_generic_table_rows(inventory,source_sha256=current['source_sha256'])
                else:good,bad=parse_table_rows(inventory,source_sha=current['source_sha256'])
                historical=deepcopy(current);historical.pop('native_text_partition')
                historical.update(parser_version=GENERIC_PARSER_VERSION if generic else PARSER_VERSION,
                    transactions=good,quarantined=bad,
                    source_rows=[{'page_number':page,'cells':cells} for page,cells in inventory])
                if generic:historical['structural_rows']=structure
                prior,_=build_oge_candidate(catalog,[historical],empty_base(),data_cutoff_at='2026-09-20T00:00:00Z')
                self.assertEqual(len(prior['transactions']),2)
                write_files(root,{'base.json':encoded(prior)})
                candidate=self.normal.oge_candidate(root,generic)
                self.assertEqual(candidate['transactions'],prior['transactions'])
                self.assertEqual(len({row['id'] for row in candidate['transactions']}),2)
                # The WH historical family has its own owner/projection policy;
                # preserve its full source facts too, without a new visible ID.
                data=self.prepare(True,generic,'white_on_white')
                from unison_snapshot.whitehouse_278t import (
                    parse_whitehouse_278t_pdf,parse_generic_whitehouse_278t_pdf)
                from unison_snapshot.oge_cross_source_replacements import _bound_whitehouse_candidate
                metadata=data['metadata'];root=data['root'];review=data['review']
                options=dict(source_url=metadata['document_url'],source_sha256=metadata['sha256'],
                    document_id=metadata['document_id'],filer_name='Example, Ada',amended_label=metadata['link_label'])
                historical_wh=(parse_generic_whitehouse_278t_pdf if generic else parse_whitehouse_278t_pdf)(
                    data['evidence']/metadata['archive_path'],**options)
                base=json.loads((root/'base.json').read_bytes())
                coverage=json.loads((review/'oge/whitehouse/coverage-current.json').read_bytes())
                directory=json.loads((review/'whitehouse/disclosures/index-current.json').read_bytes())
                previous_wh,_,_=_bound_whitehouse_candidate(dict(base=base,extractions=[historical_wh],
                    oge_whitehouse_coverage=coverage,directory_index=directory,
                    annual_extractions=None,annual_identity_people=None),data['evidence'],review)
                self.assertEqual(len(previous_wh['transactions']),3)
                write_files(root,{'historical-base.json':encoded(previous_wh)})
                current_wh,_=self.normal.wh_candidate(root,input_path=root/'historical-base.json')
                self.assertEqual(current_wh['transactions'],previous_wh['transactions'])
                self.assertEqual(len({row['id'] for row in current_wh['transactions']}),3)

    def test_normal_status_is_source_replayed_partial_and_counter_tampering_fails(self):
        for whitehouse in (False,True):
            with self.subTest(whitehouse=whitehouse):
                data=self.prepare(whitehouse,True)
                scopes=self.status_helper.status(data);record=self.status_helper.record(data,scopes)
                self.assertEqual(record['qualified_transaction_count'],1)
                self.assertEqual(record['qualification_state'],'partial')
                self.assertFalse(record['source_observation_coverage']['coverage_complete'])
                self.status_helper.reader(data,scopes)
                for field in ('promoted_count','quarantined_count'):
                    changed=deepcopy(data['audit']);changed['reports'][0][field]=99
                    data['audit_path'].write_bytes(encoded(changed))
                    with self.assertRaises(ValueError):self.status_helper.status(data)
                data['audit_path'].write_bytes(encoded(data['audit']))
                # Actual unsupported source profile cannot become zero just
                # because its new visible inventory contains no usable row.
                raw=source_pdf('white_on_dark')
                import pymupdf
                document=pymupdf.open(stream=raw,filetype='pdf')
                page=document[1];ref=document.get_new_xref();document.update_object(ref,'<< >>')
                document.update_stream(ref,b'q 1 0 0 1 0 0 cm Q')
                document.xref_set_key(page.xref,'Contents','['+' '.join(f'{r} 0 R' for r in [*page.get_contents(),ref])+']')
                bad=document.tobytes();document.close()
                with patch.object(normal,'normal_pdf',return_value=bad):
                    unknown=self.status_helper.prepare(whitehouse=whitehouse,generic=True,kind='unsupported_contrast')
                unknown_record=self.status_helper.record(unknown,self.status_helper.status(unknown))
                self.assertNotEqual(unknown_record['qualification_state'],'zero_transactions')
                self.assertEqual(unknown_record.get('publication_transaction_ids',[]),[])
                self.assertFalse(unknown_record['source_observation_coverage']['coverage_complete'])

    def test_bichrome_target_uses_normal_exhibit_apply_and_nested_source_replay(self):
        helper=exhibit_helpers.NativePartitionExhibitTests();helper.setUp();self.addCleanup(helper.doCleanups)
        from unison_snapshot.oge_cross_source_replacements import (
            apply_cross_source_native_replacements,verify_cross_source_native_applications,replay_cross_source_context)
        for generic in (False,True):
            with self.subTest(generic=generic):
                raw=source_pdf('white_then_black')
                with patch.object(normal,'normal_pdf',return_value=raw):
                    root,_,before,inputs,saved=helper.inputs('overlay',generic)
                self.assertTrue(all('native-bichrome' in row['parser_version'] for row in inputs['extractions']))
                result,proof=apply_cross_source_native_replacements(before,saved,inputs,root/'evidence',root/'review')
                self.assertEqual(len(proof['applications']),1)
                self.assertEqual(proof['context']['wh_inputs']['base'],inputs['base'])
                self.assertEqual(verify_cross_source_native_applications(proof,root/'evidence',root/'review'),(result,proof))
                self.assertEqual(replay_cross_source_context(proof['context'],root/'evidence',root/'review'),(result,proof))


if __name__=='__main__':unittest.main()
