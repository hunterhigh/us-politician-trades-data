"""Actual source clip requests through default and explicit generic consumers."""
import unittest
from unittest.mock import patch

import pymupdf

import test_native_partition_consumption as normal
import test_native_opacity_consumption as shared
from test_native_bichrome_visibility import inject

TEXT = 'Example Common Stock (EXM)'


def source_pdf(kind):
    document = pymupdf.open(stream=normal.normal_pdf('control'),filetype='pdf')
    body = document[1]
    programs = {
        'inside':b'40 585 300 35 re W n',
        'outside':b'400 585 30 35 re W n',
        'partial':b'80 585 260 35 re W n',
        'nested':b'40 585 300 35 re W n 80 585 260 35 re W n',
        'empty':b'400 585 30 35 re W n 40 585 300 35 re W n',
        'outside_domain':b'-50 585 400 35 re W n',
        'evenodd':b'40 585 300 35 re W* n',
        'uncommitted':b'400 585 30 35 re W',
        'negative':b'340 585 -300 35 re W n',
        'curve':b'40 585 m 340 585 l 340 620 l 40 620 l h W n',
        'mode7':b'7 Tr',
        'inside_alpha0':b'/Zero gs 40 585 300 35 re W n',
        'inside_alpha1':b'/Opaque gs 40 585 300 35 re W n'}
    if kind.startswith('inside_alpha'):
        resource = int(document.xref_get_key(body.xref,'Resources')[1].split()[0])
        references = []
        for name,alpha in [('Zero',0),('Opaque',1)]:
            ref = document.get_new_xref()
            document.update_object(ref,f'<< /Type /ExtGState /ca {alpha} /CA 1 >>')
            references.append(f'/{name} {ref} 0 R')
        document.xref_set_key(resource,'ExtGState','<< '+' '.join(references)+' >>')
    needle = TEXT.encode().hex().encode()
    for ref in body.get_contents():
        stream = document.xref_stream(ref)
        if needle in stream.lower() and kind in programs:
            document.update_stream(ref,stream.replace(b'BT',programs[kind]+b'\nBT',1))
        elif needle in stream.lower() and kind == 'cross_page_white':
            document.update_stream(ref,stream.replace(b'BT',b'1 g\nBT',1))
    if kind == 'footer':
        before = set(body.get_contents())
        body.insert_text((30,740),'Unrelated clipped footer',fontsize=8)
        for ref in set(body.get_contents())-before:
            stream = document.xref_stream(ref)
            document.update_stream(ref,stream.replace(b'BT',b'400 585 30 35 re W n\nBT',1))
    if kind == 'cross_page_white':
        inject(document,document[0],b'q 0 0 700 792 re W n Q',before=True)
    if kind == 'contributing_path':
        inject(document,body,b'q 400 585 30 35 re W n 1 w 40 610 m 340 610 l S Q')
    raw = document.tobytes()
    document.close()
    return raw


class NativeClipConsumptionTests(unittest.TestCase):
    def setUp(self):
        self.contract = shared.NativeOpacityConsumptionTests()
        self.contract.setUp()
        self.addCleanup(self.contract.doCleanups)

    def test_four_normal_routes_keep_inside_and_independent_rows_without_substring_facts(self):
        expected = {'inside':2,'outside':1,'partial':1,'nested':1,'empty':1,
            'outside_domain':2,'inside_alpha0':1,'inside_alpha1':2,'footer':2}
        with patch.object(shared,'source_pdf',side_effect=source_pdf):
            self.contract.assert_normal_routes(expected,parser_family='native-clip')

    def test_clip_on_cover_cannot_bypass_other_page_contrast(self):
        with patch.object(shared,'source_pdf',side_effect=source_pdf):
            self.contract.assert_normal_routes({'cross_page_white':1},parser_family='native-clip')

    def test_unsupported_requests_text_clipping_and_changed_paths_cannot_use_legacy_fallback(self):
        for kind in ('evenodd','uncommitted','negative','curve','mode7','contributing_path'):
            with self.subTest(kind=kind):
                raw = source_pdf(kind)
                with patch.object(normal,'normal_pdf',return_value=raw):
                    root,_,_ = self.contract.driver.oge_inputs(kind)
                extraction = self.contract.driver.oge_extract(root,False)
                self.assertIn('native-clip',extraction['parser_version'])
                self.assertTrue(extraction['native_text_partition']['unknown_regions'])
                self.assertEqual(self.contract.driver.oge_candidate(root,False)['transactions'],[])

    def test_document_clip_event_path_copy_and_proof_budgets_never_return_partial_positive(self):
        from unison_snapshot import native_clip_partition as partition
        raw = source_pdf('inside')
        with patch.object(normal,'normal_pdf',return_value=raw):
            root,metadata,_ = self.contract.driver.oge_inputs('budget')
        for name in ('MAX_TOTAL_CLIPPING_EVENTS','MAX_TOTAL_CLIPPING_PATH_POINTS','MAX_PROOF_BYTES'):
            with self.subTest(name=name),patch.object(partition,name,0):
                result = partition.observe_native_clip_partition(root/metadata['archive_path'],metadata['sha256'])
                self.assertEqual(result['status'],'unknown')
                self.assertEqual(result['inventory'],[])
                self.assertTrue(result['document_reasons'])


if __name__ == '__main__':
    unittest.main()
