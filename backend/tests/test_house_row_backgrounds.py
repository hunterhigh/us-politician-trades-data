"""Source paint and complete closing grammar regressions, without official PDFs."""
from copy import deepcopy
import unittest

import test_house_revision_source_scope as source
from unison_snapshot.house_revision_source_scope import _group_domain, _Unknown
from test_house_native_coverage import template, geometry, SHA
from unison_snapshot.house_native_coverage import closing_role_pages


class RowBackgroundTests(unittest.TestCase):
    def test_long_native_inventory_still_has_a_hard_page_limit(self):
        import pymupdf
        import pdfplumber
        import io
        from unison_snapshot.house_native_contrast import _inventory
        from unison_snapshot.house import HouseIndexError
        for count in (33,97):
            with pymupdf.open() as document:
                for _ in range(count):document.new_page()
                content = document.tobytes()
            with pdfplumber.open(io.BytesIO(content)) as document:
                if count==97:
                    with self.assertRaisesRegex(HouseIndexError,'page budget'):_inventory(document,'a'*64)
                else:
                    self.assertEqual(len(_inventory(document,'a'*64)[0]),count)

    def paint(self, body, *, required=True):
        fixture = source.HouseRevisionSourceCompositionTests()
        self.addCleanup(fixture.doCleanups)
        _, _, paint = fixture.components(source.micro_pdf(body=body))
        if required:self.assertEqual(paint['status'], 'observed', paint)
        return paint

    def test_light_gray_and_white_underlays_keep_real_source_glyphs_visible(self):
        for shade in (b'0.933333', b'0.960784', b'1'):
            with self.subTest(shade=shade):
                paint = self.paint(b' '.join([shade]*3)+b' rg 35 390 40 40 re f 0 0 0 rg '
                    b'BT /F 12 Tf 1 0 0 1 40 400 Tm <00010002> Tj ET')
                self.assertTrue(_group_domain(paint['occurrences'], [0, 1],
                    paint['paint_inventory'], allow_row_background=True))

    def test_later_covers_white_glyphs_and_hidden_render_mode_stay_blocked(self):
        text = b'BT /F 12 Tf 1 0 0 1 40 400 Tm <00010002> Tj ET'
        for body in (text+b' .933333 .933333 .933333 rg 35 390 40 40 re f',
                     b'1 1 1 rg '+text,
                     b'BT /F 12 Tf 3 Tr 1 0 0 1 40 400 Tm <00010002> Tj ET',
                     b'.5 .5 .5 rg 35 390 40 40 re f 0 0 0 rg '+text):
            with self.subTest(body=body):
                paint = self.paint(body, required=False)
                if paint['status'] != 'observed':
                    self.assertTrue(paint['unresolved'])
                    continue
                with self.assertRaises(_Unknown):
                    _group_domain(paint['occurrences'], [0, 1], paint['paint_inventory'],
                                  allow_row_background=True, allow_status_color=True)

    def test_earlier_partial_rect_is_not_a_full_background_witness(self):
        paint = self.paint(b'.933333 .933333 .933333 rg 40 400 3 3 re f 0 0 0 rg '
            b'BT /F 12 Tf 1 0 0 1 40 400 Tm <00010002> Tj ET')
        with self.assertRaises(_Unknown):
            _group_domain(paint['occurrences'], [0, 1], paint['paint_inventory'],
                          allow_row_background=True)

    def test_closing_tail_can_omit_empty_vehicle_and_continue_signature(self):
        pages = template()
        closing = pages[1]
        closing['words'] = [w for w in closing['words'] if w['top'] not in (104,131,200,220)]
        closing['native_page_geometry']['rects'] = []
        tail = deepcopy(closing)
        tail['native_page_geometry'] = geometry(3)
        tail['words'] = [w for w in closing['words'] if w['top'] >= 335]
        closing['words'] = [w for w in closing['words'] if w['top'] < 335]
        pages.append(tail)
        self.assertEqual(closing_role_pages(SHA, pages), {2,3})
        for mutation in ('missing_line', 'extra_table', 'missing_ipo'):
            bad = deepcopy(pages)
            if mutation == 'missing_line':bad[2]['words'].pop(0)
            if mutation == 'extra_table':bad[2]['native_page_geometry']['rects'].append([20,50,500,100])
            if mutation == 'missing_ipo':bad[1]['words'] = [w for w in bad[1]['words'] if w['top'] != 249]
            self.assertEqual(closing_role_pages(SHA,bad), set())

