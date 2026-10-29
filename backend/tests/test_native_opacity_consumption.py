"""Original binary-alpha programs through the four ordinary source consumers."""
import json
import unittest
from unittest.mock import patch

import pymupdf

import test_native_partition_consumption as normal
from test_native_bichrome_visibility import inject

ASSET = 'Example Common Stock (EXM)'


def source_pdf(kind):
    document = pymupdf.open(stream=normal.normal_pdf('control'), filetype='pdf')
    page = document[1]
    states = {'Off':'/ca 0', 'On':'/ca 1', 'StrokeOff':'/CA 0',
        'Half':'/ca 0.5', 'Blend':'/BM /Multiply', 'Mask':'/SMask /None'}
    references = []
    for name, fields in states.items():
        ref = document.get_new_xref()
        document.update_object(ref, '<< /Type /ExtGState ' + fields + ' >>')
        references.append(f'/{name} {ref} 0 R')
    resources = int(document.xref_get_key(page.xref, 'Resources')[1].split()[0])
    document.xref_set_key(resources, 'ExtGState', '<< ' + ' '.join(references) + ' >>')
    state = {'ca0':b'/Off gs', 'ca1':b'/On gs', 'CA0':b'/StrokeOff gs',
        'restored':b'/Off gs /On gs', 'half':b'/Half gs',
        'blend':b'/Blend gs', 'mask':b'/Mask gs', 'missing':b'/Missing gs'}.get(kind)
    needle = ASSET.encode().hex().encode()
    for ref in page.get_contents():
        stream = document.xref_stream(ref)
        if needle in stream.lower():
            if state:
                document.update_stream(ref, stream.replace(b'BT', state + b'\nBT', 1))
            elif kind == 'restored_in_BT':
                document.update_stream(ref, stream.replace(b'BT', b'BT\n/Off gs /On gs', 1))
            elif kind == 'alias':
                document.update_stream(ref, stream.replace(b'BT', b'/Off gs\nBT', 1))
            elif kind == 'cross_page_white':
                document.update_stream(ref, stream.replace(b'BT', b'1 g\nBT', 1))
    if kind == 'cross_page_white':
        cover = document[0]
        resources = int(document.xref_get_key(cover.xref,'Resources')[1].split()[0])
        document.xref_set_key(resources,'ExtGState','<< ' + ' '.join(references) + ' >>')
        inject(document,cover,b'/On gs',before=True)
    if kind == 'alias':
        page.insert_text((52,190), ASSET, fontsize=8, color=(0,0,0))
    if kind in {'transparent_fill','opaque_fill'}:
        inject(document,page,b'q /Off gs ' + (b'/On gs ' if kind == 'opaque_fill' else b'') +
            b'1 g 51.25 594 297.5 21 re f Q')
    if kind in {'transparent_grid','fill_zero_grid'}:
        # One original physical divider is invisible only with CA=0. The raw
        # grid must remain inventoried instead of merging its semantic cells.
        for ref in page.get_contents():
            stream = document.xref_stream(ref)
            if b'30 592 m' in stream and b'680 592 l' in stream:
                operation = b'/StrokeOff gs' if kind == 'transparent_grid' else b'/Off gs'
                document.update_stream(ref, stream.replace(b'q', b'q\n' + operation, 1))
    if kind == 'footer':
        previous = set(page.get_contents())
        page.insert_text((30,740), 'Unrelated invisible footer', fontsize=8)
        for ref in set(page.get_contents()) - previous:
            stream = document.xref_stream(ref)
            document.update_stream(ref, stream.replace(b'BT', b'/Off gs\nBT', 1))
    raw = document.tobytes()
    document.close()
    return raw


class NativeOpacityConsumptionTests(unittest.TestCase):
    def setUp(self):
        self.driver = normal.NativePartitionConsumptionTests()
        self.driver.setUp()
        self.addCleanup(self.driver.doCleanups)

    def test_four_normal_routes_hide_zero_alpha_and_recover_actual_visible_state(self):
        expected = {'ca0':1, 'ca1':2, 'CA0':2, 'restored':2,
            'restored_in_BT':2, 'transparent_fill':2, 'opaque_fill':1, 'alias':2, 'footer':2}
        self.assert_normal_routes(expected)

    def test_opacity_on_cover_cannot_bypass_white_text_on_a_different_page(self):
        self.assert_normal_routes({'cross_page_white':1})

    def assert_normal_routes(self, expected):
        for kind, count in expected.items():
            for generic in (False, True):
                for whitehouse in (False, True):
                    with self.subTest(kind=kind,generic=generic,whitehouse=whitehouse):
                        raw = source_pdf(kind)
                        with patch.object(normal,'normal_pdf',return_value=raw):
                            if whitehouse:
                                root, metadata, _ = self.driver.wh_inputs(kind)
                            else:
                                root, metadata, _ = self.driver.oge_inputs(kind)
                        if whitehouse:
                            self.driver.wh_extract_coverage(root,metadata,generic)
                            candidate,_ = self.driver.wh_candidate(root)
                            coverage = json.loads((root/'review/whitehouse/coverage-current.json').read_bytes())
                            extraction = json.loads((root/'review'/coverage['reports'][0]['extraction_path']).read_bytes())
                            rows = [row for row in candidate['transactions'] if row.get('ticker') != 'PRIOR']
                        else:
                            extraction = self.driver.oge_extract(root,generic)
                            candidate = self.driver.oge_candidate(root,generic)
                            rows = candidate['transactions']
                        self.assertIn('native-opacity', extraction['parser_version'])
                        self.assertEqual(len(rows),count)
                        if count == 1:
                            self.assertIn('Other Visible', rows[0]['asset_name'])
                        self.driver.assert_reader(candidate)
                        before = tuple((root/name).read_bytes() for name in ('candidate.json','audit.json'))
                        if whitehouse:
                            self.driver.wh_candidate(root,input_path=root/'candidate.json')
                        else:
                            self.driver.oge_candidate(root,generic)
                        self.assertEqual(before,tuple((root/name).read_bytes() for name in ('candidate.json','audit.json')))

    def test_nonpainting_strokes_never_certify_a_new_merged_table(self):
        from unison_snapshot.native_opacity_partition import observe_native_opacity_partition
        from hashlib import sha256
        for kind, expected in [('transparent_grid',0),('fill_zero_grid',2)]:
            with self.subTest(kind=kind):
                raw = source_pdf(kind)
                with patch.object(normal,'normal_pdf',return_value=raw):
                    root,metadata,_ = self.driver.oge_inputs(kind)
                proof = observe_native_opacity_partition(root/metadata['archive_path'],sha256(raw).hexdigest())
                page = proof['pages'][1]
                self.assertEqual(page['profile_status'],'observed',page.get('reasons'))
                self.assertEqual(page['table_grid_basis'],'source_bound_displayed_strokes_only')
                self.driver.oge_extract(root,False)
                candidate = self.driver.oge_candidate(root,False)
                self.assertEqual(len(candidate['transactions']),expected)
                if not expected:
                    self.assertTrue(any('grid_projection_unresolved' in reason
                        for row in proof['rows'] for reason in row['reasons']))
                    self.assertTrue(proof['raw_inventory'])

    def test_used_unknown_graphics_state_cannot_fall_back_to_raw_facts(self):
        for kind in ('half','blend','mask','missing'):
            with self.subTest(kind=kind):
                raw = source_pdf(kind)
                with patch.object(normal,'normal_pdf',return_value=raw):
                    root,_,_ = self.driver.oge_inputs(kind)
                extraction = self.driver.oge_extract(root,False)
                self.assertIn('native-opacity',extraction['parser_version'])
                self.assertTrue(extraction['native_text_partition']['unknown_regions'])
                self.assertEqual(self.driver.oge_candidate(root,False)['transactions'],[])

    def test_unused_graphics_resources_preserve_actual_legacy_source_selection(self):
        from unison_snapshot.oge_reports import selected_parser_version
        from unison_snapshot.whitehouse_278t import plan_whitehouse_278t_source
        with patch.object(normal,'normal_pdf',return_value=source_pdf('unused')):
            root,metadata,_ = self.driver.oge_inputs('unused')
        path = root/metadata['archive_path']
        self.assertEqual(selected_parser_version(metadata,path,metadata['sha256']), 'oge-278t-pdf/v2')
        self.assertEqual(selected_parser_version(metadata,path,metadata['sha256'],generic=True), 'oge-278t-pdf/v7')
        self.assertEqual(plan_whitehouse_278t_source(path,source_url=metadata['document_url'],
            source_sha256=metadata['sha256'])['parser_version'], 'whitehouse-278t-hybrid-geometry/v2')

    def test_document_event_and_encoded_proof_limits_cannot_return_partial_positive(self):
        from unison_snapshot import native_opacity_partition as partition
        raw = source_pdf('restored')
        with patch.object(normal,'normal_pdf',return_value=raw):
            root,metadata,_ = self.driver.oge_inputs('budget')
        path = root/metadata['archive_path']
        for name in ('MAX_TOTAL_GRAPHICS_STATE_EVENTS','MAX_TOTAL_PAINT_EVENTS','MAX_PROOF_BYTES'):
            with self.subTest(name=name),patch.object(partition,name,0):
                result = partition.observe_native_opacity_partition(path,metadata['sha256'])
                self.assertEqual(result['status'],'unknown')
                self.assertEqual(result['inventory'],[])
                self.assertTrue(result['document_reasons'])


if __name__ == '__main__':
    unittest.main()
