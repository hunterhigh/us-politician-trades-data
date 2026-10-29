"""Real-PDF positive profile and bounded validation contracts, without OCR."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pdfplumber
import pymupdf

from unison_snapshot.native_source_visibility import (
    inspect_standard_visible_document, inspect_standard_visible_page)
from unison_snapshot.pdf_native_observer import observe_source_characters
from whitehouse_account_display_fixture import fixture


def simple_pdf(*, origin=20, appended=None, pages=1):
    document = pymupdf.open()
    for _ in range(pages):
        page = document.new_page(width=700, height=792)
        page.insert_text((origin, 100), 'j retirement account', fontsize=8)
        if appended:
            xref = document.get_new_xref(); document.update_object(xref, '<< >>')
            document.update_stream(xref, appended)
            document.xref_set_key(page.xref, 'Contents', '[' + ' '.join(
                f'{ref} 0 R' for ref in [*page.get_contents(), xref]) + ']')
    raw = document.tobytes(); document.close()
    return raw


class NativeSourceVisibilityTests(unittest.TestCase):
    def inspect(self, raw, *, document=True, **kwargs):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'source.pdf'; path.write_bytes(raw)
            with pdfplumber.open(path) as pdf:
                return (inspect_standard_visible_document(pdf, **kwargs) if document else
                        inspect_standard_visible_page(pdf.pages[0], **kwargs))

    def test_whole_original_account_sources_have_positive_visible_profiles(self):
        for extra in (None, 'two_accounts', 'unrelated'):
            with self.subTest(extra=extra):
                _, _, _, evidence, _ = fixture(extra=extra)
                raw = next(raw for path, raw in evidence.items() if path.endswith('.pdf'))
                result = self.inspect(raw)
                self.assertEqual(result['status'], 'observed', result)
                self.assertEqual([row['page_number'] for row in result['pages']], [1, 2])
                self.assertTrue(all(row['comparison_count'] < 200_000 for row in result['pages']))

    def test_actual_ink_domain_is_distinct_from_advance_box(self):
        self.assertEqual(self.inspect(simple_pdf())['status'], 'observed')
        result = self.inspect(simple_pdf(origin=0))
        self.assertEqual(result['reason'], 'native_glyph_ink_outside_visible_page')

    def test_all_pages_are_checked_and_stateful_operators_are_closed(self):
        for program in (b'Q', b'q', b'BT', b'BT BT ET ET', b'1 2 Tm', b'true w',
                        b'0 w', b'3 w', b'0 0 m 1 1 l 2 2 l S', b'1 cm',
                        b'0 0 1 1 re W n', b'1 J', b'1 0 0 rg 0 0 m 20 0 l S',
                        b'BT /helv 8 Tf (\x80) Tj ET'):
            with self.subTest(program=program):
                self.assertEqual(self.inspect(simple_pdf(appended=program))['status'], 'unknown')
        raw = simple_pdf(pages=2)
        document = pymupdf.open(stream=raw, filetype='pdf')
        document[1].insert_text((20, 200), 'Hidden scope barrier', render_mode=3)
        changed = document.tobytes(); document.close()
        result = self.inspect(changed)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['pages'][0]['status'], 'observed')
        self.assertEqual(result['pages'][1]['status'], 'unknown')

    def test_bounded_default_and_rgb_strokes_are_checked_by_their_ink_footprints(self):
        for program in (b'0 G 20 650 m 200 650 l S',
                        b'0 0 0 RG 2 w 20 650 m 200 650 l S',
                        b'q 2 w 20 650 m 200 650 l S Q 20 640 m 200 640 l S'):
            with self.subTest(program=program):
                self.assertEqual(self.inspect(simple_pdf(appended=program))['status'], 'observed')
        crossed = self.inspect(simple_pdf(appended=b'1 w 20 694 m 200 694 l S'))
        self.assertEqual(crossed['reason'], 'native_glyph_or_stroke_overlap')
        # cm changes stroke width even when no glyph matrix changes.
        result = self.inspect(simple_pdf(appended=b'q 20 0 0 20 0 0 cm 1 w 1 32 m 10 32 l S Q'))
        self.assertEqual(result['status'], 'unknown')

    def test_page_and_document_paint_state_cannot_hide_outside_resources(self):
        raw = simple_pdf()
        for where, key, value in (('page', 'Group', '<< /S /Transparency /CS /DeviceRGB >>'),
                                  ('page', 'TrimBox', '[0 0 10 10]'),
                                  ('catalog', 'OCProperties', '<< >>'),
                                  ('catalog', 'AcroForm', '<< /XFA () >>'),
                                  ('catalog', 'NeedsRendering', 'true')):
            with self.subTest(key=key):
                document = pymupdf.open(stream=raw, filetype='pdf')
                xref = document[0].xref if where == 'page' else document.pdf_catalog()
                document.xref_set_key(xref, key, value)
                changed = document.tobytes(); document.close()
                self.assertEqual(self.inspect(changed)['status'], 'unknown')

    def test_pair_page_character_and_token_budgets_fail_before_partial_acceptance(self):
        raw = simple_pdf(pages=2)
        self.assertEqual(self.inspect(raw, max_pages=1)['reason'], 'native_page_budget_exceeded')
        document = pymupdf.open(stream=raw, filetype='pdf')
        document[0].insert_text((20, 200), 'Another account', fontsize=8)
        paired = document.tobytes(); document.close()
        self.assertEqual(self.inspect(paired, max_comparisons=0)['reason'], 'native_intersection_budget_exceeded')
        for arguments in ({'max_comparisons':True}, {'max_comparisons':200_001}, {'max_pages':33}):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.inspect(raw, **arguments)['status'], 'unknown')
        with patch('unison_snapshot.native_source_visibility.MAX_TOKENS', 1):
            self.assertEqual(self.inspect(raw)['reason'], 'native_content_budget_exceeded')
        with patch('unison_snapshot.native_source_visibility.MAX_CHARACTERS', 1):
            self.assertEqual(self.inspect(raw)['reason'], 'source_visibility_character_budget_exceeded')

    def test_shared_observer_wrapper_preserves_source_projection_and_budget_errors(self):
        from unison_snapshot import house_native_visibility as house
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'source.pdf'; path.write_bytes(simple_pdf())
            with pdfplumber.open(path) as pdf:
                page = pdf.pages[0]
                before = deepcopy(page.chars)
                self.assertEqual(house._source_characters(page), observe_source_characters(page))
                self.assertEqual(page.chars, before)
                with patch.object(house, 'MAX_CHARACTERS', 1), self.assertRaisesRegex(
                        ValueError, '^source_visibility_character_budget_exceeded$'):
                    house._source_characters(page)
                with patch.object(house, 'MAX_TOKENS', 1), self.assertRaisesRegex(
                        ValueError, '^source_visibility_token_budget_exceeded$'):
                    house._source_characters(page)

    def test_source_limits_precede_default_or_controlled_glyph_materialization(self):
        from pdfplumber.page import PDFPageAggregatorWithMarkedContent
        original = PDFPageAggregatorWithMarkedContent.render_char
        calls = []
        def counted(device, *args, **kwargs):
            calls.append(1)
            return original(device, *args, **kwargs)
        # A small budget distinguishes the evaluation order without creating
        # a dangerous source or mocking the page/object inventory.
        with patch.object(PDFPageAggregatorWithMarkedContent, 'render_char', counted), \
                patch('unison_snapshot.native_source_visibility.MAX_CHARACTERS', 10):
            result = self.inspect(simple_pdf())
        self.assertEqual(result['reason'], 'source_visibility_character_budget_exceeded')
        self.assertEqual(calls, [])
        # One long real Tj and nested TJ numeric tokens must be bounded before
        # the ordinary pdfplumber layout parser expands any glyphs.
        programs = [(b'BT /helv 8 Tf ('+b'A'*25_001+b') Tj ET', None,
                     'source_visibility_character_budget_exceeded'),
                    (b'BT /helv 8 Tf ['+b'0 '*100+b'(X)] TJ ET', 30,
                     'native_content_budget_exceeded')]
        for program, tokens, reason in programs:
            calls.clear()
            with self.subTest(reason=reason), \
                    patch.object(PDFPageAggregatorWithMarkedContent, 'render_char', counted), \
                    patch('unison_snapshot.native_source_visibility.MAX_TOKENS', tokens or 100_000):
                self.assertEqual(self.inspect(simple_pdf(appended=program))['reason'], reason)
            self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
