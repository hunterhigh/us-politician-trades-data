"""CID and source font pointers survive where text/count comparisons cannot."""
from copy import deepcopy
import io
import unittest
from unittest.mock import patch

import pdfplumber
from unison_snapshot.house_id_source_characters import observe_house_id_source_characters


FONT = (b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica '
        b'/FirstChar 65 /LastChar 66 /Widths [600 600] '
        b'/Encoding << /BaseEncoding /WinAnsiEncoding /Differences [65 /A /A] >> >>')


def stream(content, extra=b''):
    return b'<< /Length ' + str(len(content)).encode() + b' ' + extra + b' >>\nstream\n' + content + b'\nendstream'


def source(content=None, *, font_generation=0, form=False, cycle=False, direct=False,
           font_body=FONT):
    content = content or b'BT /F1 10 Tf 1 0 0 1 20 100 Tm (AB) Tj ET'
    font_ref = f'4 {font_generation} R'.encode()
    resources = b'<< /Font << /F1 ' + (FONT if direct else font_ref) + b' /F2 5 0 R >>'
    if form or cycle: resources += b' /XObject << /X 7 0 R >>'
    resources += b' >>'
    objects = {
        1: (0, b'<< /Type /Catalog /Pages 2 0 R >>'),
        2: (0, b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>'),
        3: (0, b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources ' + resources + b' /Contents 6 0 R >>'),
        4: (font_generation, font_body), 5: (0, FONT), 6: (0, stream(content)),
    }
    if form or cycle:
        nested = b'/X Do' if cycle else b'BT /F1 10 Tf 1 0 0 1 20 100 Tm (A) Tj ET'
        objects[7] = (0, stream(nested, b'/Type /XObject /Subtype /Form /BBox [0 0 200 200] '
            b'/Matrix [1 0 0 1 0 0] /Resources ' + resources))
    raw = bytearray(b'%PDF-1.7\n'); offsets = {}
    for number, (generation, data) in objects.items():
        offsets[number] = len(raw)
        raw.extend(f'{number} {generation} obj\n'.encode() + data + b'\nendobj\n')
    start = len(raw); raw.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for number, (generation, _) in objects.items():
        raw.extend(f'{offsets[number]:010} {generation:05} n \n'.encode())
    raw.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode())
    return bytes(raw)


class HouseIdSourceCharacterTests(unittest.TestCase):
    def observe(self, raw, **kwargs):
        with pdfplumber.open(io.BytesIO(raw)) as document:
            result = observe_house_id_source_characters(document.pages[0], **kwargs)
            if result['status'] == 'observed':
                self.assertEqual(result['chars'], document.pages[0].chars)
            return result

    def test_equal_unicode_arrays_and_counts_do_not_hide_different_CID_orders(self):
        first = self.observe(source())
        second = self.observe(source(b'BT /F1 10 Tf 1 0 0 1 20 100 Tm (BA) Tj ET'))
        self.assertEqual(first['status'], 'observed', first)
        self.assertEqual(second['status'], 'observed', second)
        self.assertEqual(first['chars'], second['chars'])
        self.assertEqual([b['text'] for b in first['bindings']], ['A', 'A'])
        self.assertEqual([b['cid'] for b in first['bindings']], [65, 66])
        self.assertEqual([b['cid'] for b in second['bindings']], [66, 65])
        self.assertFalse(first['scope_proven']); self.assertFalse(first['paint_proven'])

    def test_same_fontname_and_unicode_still_bind_two_distinct_source_font_objects(self):
        raw = source(b'BT /F1 10 Tf 1 0 0 1 20 100 Tm (A) Tj '
                     b'/F2 10 Tf 1 0 0 1 30 100 Tm (A) Tj ET')
        result = self.observe(raw)
        self.assertEqual(result['status'], 'observed', result)
        self.assertEqual([c['fontname'] for c in result['chars']], ['Helvetica', 'Helvetica'])
        self.assertEqual([b['source_object_id'] for b in result['bindings']], [4, 5])
        self.assertEqual([b['cid'] for b in result['bindings']], [65, 65])
        self.assertEqual([b['source_generation'] for b in result['bindings']], [0, 0])

    def test_nonzero_source_object_generation_is_not_invented_from_PDFObjRef(self):
        result = self.observe(source(font_generation=7))
        self.assertEqual(result['status'], 'observed', result)
        self.assertEqual([b['source_generation'] for b in result['bindings']], [7, 7])

    def test_Form_and_parent_glyphs_keep_source_order_and_real_resource_pointers(self):
        raw = source(b'q /X Do Q BT /F2 10 Tf 1 0 0 1 30 100 Tm (B) Tj ET', form=True)
        result = self.observe(raw)
        self.assertEqual(result['status'], 'observed', result)
        self.assertEqual([b['source_object_id'] for b in result['bindings']], [4, 5])
        self.assertEqual([b['render_ordinal'] for b in result['bindings']], [0, 1])
        self.assertEqual([b['char_index'] for b in result['bindings']], [0, 1])
        self.assertEqual([b['cid'] for b in result['bindings']], [65, 66])

    def test_projection_mismatch_rejects_instead_of_adopting_cached_same_count(self):
        with pdfplumber.open(io.BytesIO(source())) as document:
            page = document.pages[0]
            cached = deepcopy(page.chars); cached[0]['text'] = 'Z'
            page._objects['char'] = cached
            result = observe_house_id_source_characters(page)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reason'], 'source_character_projection_differs')
        self.assertEqual(result['bindings'], [])

    def test_character_and_token_limits_fail_before_any_character_projection(self):
        for limits in ({'max_characters': 0}, {'max_tokens': 1}):
            with self.subTest(limits=limits), pdfplumber.open(io.BytesIO(source())) as document:
                page = document.pages[0]
                with patch.object(page, 'process_object', side_effect=AssertionError('projection must not run')):
                    result = observe_house_id_source_characters(page, **limits)
                self.assertEqual(result['status'], 'unknown')
                self.assertIn('budget', result['reason'])
                self.assertFalse(hasattr(page, '_objects'))
                self.assertEqual(result['chars'], [])

    def test_direct_font_and_circular_Form_never_become_unbound_or_zero_proof(self):
        result = self.observe(source(direct=True))
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reason'], 'source_character_font_pointer_unproved')
        result = self.observe(source(b'/X Do', cycle=True))
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reason'], 'source_content_cycle')
        self.assertFalse(result['scope_proven'])

    def test_marked_content_projection_matches_default_pdfplumber_attributes(self):
        result = self.observe(source(b'/P << /MCID 2 >> BDC BT /F1 10 Tf '
                                     b'1 0 0 1 20 100 Tm (AB) Tj ET EMC'))
        self.assertEqual(result['status'], 'observed', result)
        self.assertEqual([c['mcid'] for c in result['chars']], [2, 2])

    def test_cyclic_font_resources_and_enormous_width_ranges_fail_before_projection(self):
        fonts = [
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding 4 0 R >>',
            b'<< /Type /Font /Subtype /CIDFontType2 /Encoding /Identity-H '
            b'/BaseFont /Helvetica /W [0 1000000000 600] >>',
        ]
        for font in fonts:
            with self.subTest(font=font), pdfplumber.open(io.BytesIO(source(font_body=font))) as document:
                page = document.pages[0]
                with patch.object(page, 'process_object', side_effect=AssertionError('projection must not run')):
                    result = observe_house_id_source_characters(page)
                self.assertEqual(result['status'], 'unknown')
                self.assertIn(result['reason'], {
                    'source_resource_tree_cycle', 'source_width_declaration_invalid'})
                self.assertFalse(hasattr(page, '_objects'))


if __name__ == '__main__':
    unittest.main()
