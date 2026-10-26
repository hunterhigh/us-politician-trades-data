"""Real source-mode/grid observations; no OCR or parser mocks."""
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pdfplumber
import pymupdf

from unison_snapshot.native_text_partition import (
    observe_native_text_partition, plan_native_text_partition)
from unison_snapshot.native_source_visibility import inspect_standard_visible_page

TABLE = [['#','DESCRIPTION','TYPE','DATE','NOTIFICATION','AMOUNT'],
    ['','Investment account #6','','','',''],
    ['','Investment account #7','','','',''],
    ['1','Example Common Stock (EXM)','Purchase','06/01/2026','No','$1,001 - $15,000'],
    ['','Filer assets','','','',''],
    ['2','Other Visible Stock (OTH)','Purchase','06/02/2026','No','$1,001 - $15,000']]
EDGES = [30,50,350,410,480,550,680]


def source_pdf(kind='control'):
    document = pymupdf.open()
    cover = document.new_page(width=700, height=792)
    cover.insert_text((30,40), 'Visible native cover', fontsize=8,
                      fontname='tiro' if kind == 'ordinary_font' else 'helv')

    def show(page, point, text, hidden=False, mode=None):
        previous = set(page.get_contents())
        page.insert_text(point, text, fontsize=8)
        if hidden or mode is not None:
            for ref in set(page.get_contents()) - previous:
                document.update_stream(ref, document.xref_stream(ref).replace(
                    b'BT', b'BT\n' + str(3 if hidden else mode).encode() + b' Tr', 1))

    if kind in {'cover_footer','hidden_cover'}:
        show(cover, (30,740) if kind == 'cover_footer' else (30,80),
             'Unrelated footer' if kind == 'cover_footer' else 'Hidden filer identity', True)
    page = document.new_page(width=700, height=792)
    page.insert_text((30,40), 'Transactions', fontsize=8)
    for x in EDGES:
        page.draw_line((x,100), (x,100+25*len(TABLE)))
    for y in range(100,101+25*len(TABLE),25):
        page.draw_line((30,y), (680,y))
    for row, cells in enumerate(TABLE):
        for column, (x, text) in enumerate(zip(EDGES,cells)):
            hidden = ((kind == 'hidden_scope' and row == 2 and column == 1) or
                (kind == 'hidden_asset' and row == 3 and column == 1) or
                (kind == 'hidden_header' and row == 0 and column == 1) or
                (kind == 'hidden_financial_row' and row == 3))
            show(page, (x+2,115+row*25), text, hidden)
    if kind == 'overlay':
        for row in (2,3):
            for x, text in zip(EDGES,TABLE[row]):
                show(page,(x+2,115+row*25),text,True)
    if kind in {'footer','unsupported','mode1','cross_cell'}:
        show(page,(30,740),'Unrelated hidden footer',True)
    if kind == 'unsupported':
        page.draw_rect(pymupdf.Rect(20,600,30,610),fill=(0,0,0))
    if kind == 'mode1':
        show(page,(30,700),'Outline',mode=1)
    if kind == 'cross_cell':
        show(page,(45,165),'Cross boundary',True)
    if kind == 'unknown_predecessor':
        show(page,(30,740),'Hidden footer',True)
        page.draw_rect(pymupdf.Rect(20,600,30,610),fill=(0,0,0))
        following = document.new_page(width=700,height=792)
        for x in EDGES:
            following.draw_line((x,100),(x,150))
        for y in (100,125,150):
            following.draw_line((30,y),(680,y))
        for row,cells in enumerate((TABLE[0],TABLE[5])):
            for x,text in zip(EDGES,cells):
                show(following,(x+2,115+row*25),text)
    raw = document.tobytes(); document.close()
    return raw


class NativeTextPartitionTests(unittest.TestCase):
    def proof(self, raw, planner=False):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'source.pdf'; path.write_bytes(raw)
            return (plan_native_text_partition if planner else observe_native_text_partition)(
                path,sha256(raw).hexdigest())

    def test_no_mode3_keeps_original_inventory_without_new_font_gate(self):
        for kind in ('control','ordinary_font'):
            with self.subTest(kind=kind):
                raw = source_pdf(kind)
                self.assertIs(self.proof(raw,True)['activated'],False)
                proof = self.proof(raw)
                self.assertEqual(proof['status'],'observed',proof)
                self.assertEqual(proof['inventory'],proof['raw_inventory'])
                self.assertEqual(proof['scope_barriers_before'],{})
                self.assertTrue(all(p['profile_status']=='unchanged_native' for p in proof['pages']))
                self.assertEqual([row[1] for row in proof['inventory']],TABLE)

    def test_overlay_has_unique_visible_glyphs_and_preserves_every_original(self):
        raw = source_pdf('overlay'); proof = self.proof(raw)
        self.assertEqual(proof['status'],'observed',proof)
        page = proof['pages'][1]
        self.assertEqual(len(page['hidden_char_indices']),84)
        self.assertEqual(len(page['hidden_counterparts']),84)
        self.assertEqual(page['unmatched_hidden_indices'],[])
        self.assertEqual([cells for _,cells in proof['inventory']],TABLE)
        self.assertNotEqual(proof['inventory'],proof['raw_inventory'])
        self.assertEqual(len(page['chars']),len(page['render_modes']))
        self.assertEqual(set(page['visible_char_indices'])|set(page['hidden_char_indices']),set(range(len(page['chars']))))
        self.assertEqual(proof['row_reasons'],{})
        self.assertEqual(proof['scope_barriers_before'],{})
        self.assertEqual(json.loads(json.dumps(proof)),proof)
        self.assertEqual(self.proof(raw),proof)
        # New partition policy cannot silently loosen strict account replay.
        with pdfplumber.open(__import__('io').BytesIO(raw)) as pdf:
            self.assertEqual(inspect_standard_visible_page(pdf.pages[1])['status'],'unknown')

    def test_unmatched_heading_is_conservative_barrier_and_visible_owner_recovers(self):
        from unison_snapshot.native_partition_rows import interpret_partition_rows
        raw = source_pdf('hidden_scope'); proof = self.proof(raw)
        self.assertEqual(proof['scope_barriers_before'],{'2':True})
        self.assertEqual(proof['rows'][2]['raw_cells'][1],'Investment account #7')
        self.assertEqual(proof['rows'][2]['cells'][1],'')
        for generic in (False,True):
            with self.subTest(generic=generic):
                good,bad,_ = interpret_partition_rows(proof,source_sha256=sha256(raw).hexdigest(),generic=generic)
                self.assertEqual([row['row_number'] for row in good],[2])
                self.assertEqual(good[0]['owner'],'Self')
                self.assertTrue(any(row.get('row_number')==1 for row in bad))
                self.assertFalse(any(row.get('account_title')=='Investment account #7' for row in good+bad))

    def test_hidden_financial_fields_only_affect_their_physical_row(self):
        for kind in ('hidden_asset','hidden_financial_row'):
            with self.subTest(kind=kind):
                proof = self.proof(source_pdf(kind))
                self.assertEqual(proof['scope_barriers_before'],{})
                self.assertEqual(set(proof['row_reasons']),{'3'})
                self.assertIn('native_hidden_financial_field_unresolved',proof['row_reasons']['3'])
                self.assertEqual(proof['rows'][5]['cells'],TABLE[5])
                self.assertEqual(proof['rows'][5]['reasons'],[])

    def test_hidden_mandatory_header_never_uses_financial_fallback(self):
        proof = self.proof(source_pdf('hidden_header'))
        self.assertEqual(proof['status'],'partial')
        self.assertEqual(len(proof['rows']),6)
        self.assertFalse(proof['rows'][0]['financial_slot'])
        self.assertTrue(all('native_table_header_visibility_unknown' in row['reasons'] for row in proof['rows']))
        self.assertEqual(proof['rows'][0]['cells'][1],'')
        self.assertEqual(proof['rows'][0]['raw_cells'][1],'DESCRIPTION')

    def test_unrelated_footer_is_raw_evidence_without_scope_or_cover_block(self):
        for kind in ('footer','cover_footer'):
            with self.subTest(kind=kind):
                proof = self.proof(source_pdf(kind))
                self.assertEqual(proof['status'],'observed',proof)
                self.assertEqual(proof['row_reasons'],{})
                self.assertEqual(proof['scope_barriers_before'],{})
                self.assertIn('Visible native cover',proof['pages'][0]['text'])
                affected = proof['pages'][0 if kind=='cover_footer' else 1]
                self.assertNotIn('Unrelated',affected['text'])
                self.assertIn('Unrelated',affected['raw_text'])
                self.assertTrue(affected['outside_table_hidden_indices'])
        cover = self.proof(source_pdf('hidden_cover'))
        self.assertNotIn('Hidden filer identity',cover['pages'][0]['text'])
        self.assertTrue(cover['cover_unmatched_hidden_regions'])

    def test_unsupported_painting_retains_raw_and_never_exposes_hidden_roles(self):
        for kind in ('unsupported','mode1'):
            with self.subTest(kind=kind):
                proof = self.proof(source_pdf(kind)); page = proof['pages'][1]
                self.assertEqual(proof['status'],'partial')
                self.assertEqual(page['status'],'unknown')
                self.assertEqual(page['text'],'')
                self.assertEqual(page['visible_text'],'')
                self.assertEqual(page['visible_lines'],[])
                self.assertIn('Example Common Stock',page['raw_text'])
                self.assertTrue(page['chars'])
                self.assertTrue(proof['unknown_regions'])
                self.assertEqual(proof['inventory'],[])

    def test_unknown_predecessor_has_barrier_at_next_actual_source_row(self):
        proof = self.proof(source_pdf('unknown_predecessor'))
        self.assertEqual(proof['scope_barriers_before'],{'0':True})
        self.assertEqual([page for page,_ in proof['inventory']],[3,3])
        self.assertTrue(proof['unknown_regions'])
        self.assertEqual(proof['pages'][1]['text'],'')
        self.assertIn('Other Visible Stock',proof['pages'][2]['text'])

    def test_cross_cell_glyphs_are_not_assigned_by_center(self):
        proof = self.proof(source_pdf('cross_cell'))
        self.assertEqual(proof['status'],'partial')
        self.assertTrue(any('native_partition_cell_membership_unresolved' in row['reasons'] for row in proof['rows']))

    def test_unknown_inventory_cannot_claim_mode3_absent_or_zero(self):
        with patch('unison_snapshot.native_text_partition.MAX_TOTAL_CHARACTERS',1):
            plan = self.proof(source_pdf(),True)
            proof = self.proof(source_pdf())
        self.assertIsNone(plan['activated'])
        self.assertFalse(plan['inventory_complete'])
        self.assertEqual(proof['status'],'unknown')
        self.assertFalse(proof['inventory_complete'])
        self.assertTrue(proof['document_reasons'])

    def test_source_and_projection_budgets_are_binding(self):
        raw = source_pdf('overlay')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'source.pdf'; path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError,'SHA256'):
                observe_native_text_partition(path,'0'*64)
            with patch('unison_snapshot.native_text_partition.MAX_PDF_BYTES',10):
                with self.assertRaisesRegex(ValueError,'byte budget'):
                    plan_native_text_partition(path,sha256(raw).hexdigest())
        with patch('unison_snapshot.native_text_partition.MAX_MEMBERSHIP_COMPARISONS',1):
            proof = self.proof(raw)
        self.assertEqual(proof['pages'][1]['status'],'unknown')
        self.assertEqual(proof['pages'][1]['text'],'')
        self.assertTrue(proof['unknown_regions'])
        with patch('unison_snapshot.native_text_partition.MAX_GRID_CELLS',1):
            proof = self.proof(raw)
        self.assertEqual(proof['pages'][1]['status'],'unknown')
        self.assertIn('native_partition_grid_cell_budget_exceeded',proof['pages'][1]['reasons'])
        with patch('unison_snapshot.native_text_partition.MAX_PROOF_BYTES',1):
            proof = self.proof(raw)
        self.assertEqual(proof['status'],'unknown')
        self.assertEqual(proof['document_reasons'],['native_partition_proof_byte_budget_exceeded'])


if __name__ == '__main__':
    unittest.main()
