"""Actual source-painted contrast through ordinary OGE/White House consumers."""
import unittest
from unittest.mock import patch

import pymupdf
import test_native_partition_consumption as normal


def source_pdf(kind):
    document=pymupdf.open(stream=normal.normal_pdf('control'),filetype='pdf')
    body=document[1]
    text='Example Common Stock (EXM)'
    target=text.encode().hex().encode()
    if kind in {'white_on_white','white_on_dark','white_then_black','erased_white'}:
        for ref in body.get_contents():
            stream=document.xref_stream(ref)
            if target in stream.lower():
                document.update_stream(ref,stream.replace(b'BT',b'1 g\nBT',1))
    if kind=='hidden_scope':
        target='Investment account #7'.encode().hex().encode()
        for ref in body.get_contents():
            stream=document.xref_stream(ref)
            if target in stream.lower():
                document.update_stream(ref,stream.replace(b'BT',b'1 g\nBT',1))

    def paint(code, *, first=False):
        ref=document.get_new_xref();document.update_object(ref,'<<>>')
        document.update_stream(ref,code)
        refs=body.get_contents()
        refs=[ref,*refs] if first else [*refs,ref]
        document.xref_set_key(body.xref,'Contents','['+' '.join(f'{value} 0 R' for value in refs)+']')
    rectangle=b'51.25 594 297.5 21 re h f'
    if kind in {'white_on_dark','black_on_dark','erased_white'}:
        paint(b'q 0 g '+rectangle+b' Q',first=True)
    if kind in {'erased_white','erased_black','erased_different'}:
        paint(b'q 1 g '+rectangle+b' Q')
    if kind in {'erased_black','erased_different'}:
        body.insert_text((52,190),text if kind=='erased_black' else 'Different Common Stock (DIF)',
            fontsize=8,color=(0,0,0))
    if kind=='white_then_black':body.insert_text((52,190),text,fontsize=8,color=(0,0,0))
    if kind=='black_then_white':body.insert_text((52,190),text,fontsize=8,color=(1,1,1))
    if kind=='partial_overlap':body.insert_text((52,190),'XXXX',fontsize=8,color=(1,1,1))
    if kind=='footer':body.insert_text((30,740),'Unrelated white footer',fontsize=8,color=(1,1,1))
    raw=document.tobytes();document.close()
    return raw


class BichromeConsumptionTests(unittest.TestCase):
    def setUp(self):
        self.normal=normal.NativePartitionConsumptionTests()
        self.normal.setUp()
        self.addCleanup(self.normal.doCleanups)

    def test_full_rect_cover_only_aliases_unique_complete_source_counterparts(self):
        for kind,count in {'erased_black':2,'erased_different':1}.items():
            for generic in (False,True):
                with self.subTest(kind=kind,generic=generic):
                    raw=source_pdf(kind)
                    with patch.object(normal,'normal_pdf',return_value=raw):
                        root,_,_=self.normal.oge_inputs(kind)
                    self.normal.oge_extract(root,generic)
                    oge=self.normal.oge_candidate(root,generic)
                    self.assertEqual(len(oge['transactions']),count)
                    with patch.object(normal,'normal_pdf',return_value=raw):
                        root,metadata,_=self.normal.wh_inputs(kind)
                    self.normal.wh_extract_coverage(root,metadata,generic)
                    wh,_=self.normal.wh_candidate(root)
                    self.assertEqual(len(wh['transactions'])-1,count)
                    if count==1:
                        self.assertIn('Other Visible',oge['transactions'][0]['asset_name'])
                    self.normal.assert_reader(oge);self.normal.assert_reader(wh)

    def test_oge_default_generic_normal_source_qualification_ledger_reader_repeat(self):
        from unison_snapshot.oge_transaction_adapter import adapt_oge_278t_extraction
        expected={'white_on_white':1,'white_on_dark':2,'black_on_dark':1,
            'erased_white':1,'white_then_black':2,'black_then_white':1,
            'partial_overlap':1,'hidden_scope':1,'footer':2}
        for kind,count in expected.items():
            for generic in (False,True):
                with self.subTest(kind=kind,generic=generic):
                    raw=source_pdf(kind)
                    with patch.object(normal,'normal_pdf',return_value=raw):
                        root,_,catalog=self.normal.oge_inputs(kind)
                    extraction=self.normal.oge_extract(root,generic)
                    self.assertIn('native-bichrome',extraction['parser_version'])
                    candidate=self.normal.oge_candidate(root,generic)
                    self.assertEqual(len(candidate['transactions']),count)
                    if count==1:self.assertIn('Other Visible',candidate['transactions'][0]['asset_name'])
                    self.assertFalse(any('EExxaammppllee' in row['asset_name'] for row in candidate['transactions']))
                    adapted=adapt_oge_278t_extraction(extraction,catalog['transactions'][0],evidence_root=root)
                    self.assertEqual(sum(row['disposition']=='qualified' for row in adapted['rows']),count)
                    self.normal.assert_reader(candidate)
                    before=(root/'candidate.json').read_bytes(),(root/'audit.json').read_bytes()
                    self.normal.oge_candidate(root,generic)
                    self.assertEqual(before,((root/'candidate.json').read_bytes(),(root/'audit.json').read_bytes()))

    def test_whitehouse_default_generic_normal_extract_coverage_candidate_reader(self):
        for kind,count in {'white_on_white':1,'white_on_dark':2,'white_then_black':2,
                'black_then_white':1,'erased_white':1,'hidden_scope':1,'footer':2}.items():
            for generic in (False,True):
                with self.subTest(kind=kind,generic=generic):
                    raw=source_pdf(kind)
                    with patch.object(normal,'normal_pdf',return_value=raw):
                        root,metadata,_=self.normal.wh_inputs(kind)
                    self.normal.wh_extract_coverage(root,metadata,generic)
                    candidate,_=self.normal.wh_candidate(root)
                    rows=[row for row in candidate['transactions'] if row['id']!='oge-278t:'+'f'*24]
                    self.assertEqual(len(rows),count)
                    if count==1:self.assertIn('Other Visible',rows[0]['asset_name'])
                    self.normal.assert_reader(candidate)
                    before=(root/'candidate.json').read_bytes(),(root/'audit.json').read_bytes()
                    _,repeated=self.normal.wh_candidate(root,input_path=root/'candidate.json')
                    self.assertTrue(repeated['idempotent'])
                    self.assertEqual(before,((root/'candidate.json').read_bytes(),(root/'audit.json').read_bytes()))
