"""Real source glyph/character composition; a microfont is not a table proof."""
from copy import deepcopy
from hashlib import sha256
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pdfplumber
from fontTools.ttLib import TTFont

from test_house_id_source_paint import FONT, source_outline
import unison_snapshot.house_id_source_fonts as fonts
from unison_snapshot.house_id_source_characters import observe_house_id_source_characters
from unison_snapshot.house_id_source_paint import inspect_house_id_source_paint
from unison_snapshot.house_revision_source_scope import (
    _Unknown, _validate_source_character_bindings, inspect_native_house_revision_source,
)


def micro_reference():
    tables = fonts._directory(FONT)
    with TTFont(io.BytesIO(FONT)) as font:
        glyphs = []
        for gid, meaning in ((1, 48), (2, 49)):
            observed = fonts._glyph_observation(font, gid, tables, fonts.MAX_TOTAL_POINTS)
            glyphs.append({'fingerprint': observed['fingerprint'], 'cmap_unicode': [meaning],
                           'smallcaps_nonnumeric': False, 'smallcaps_origin_unicode': [],
                           'trusted_GSUB_features': []})
        return {'schema_version': 'private-micro-reference', 'families': [{
            'profile_id': 'private-micro-reference',
            'global_profile': fonts._global_profile(font, tables), 'glyphs': glyphs}]}


def micro_pdf(*, cmap=(48, 49), font_number=5, cid_map=b'/Identity', body=None, cmap_comment=b''):
    def stream(data, extra=b''):
        return (b'<< /Length ' + str(len(data)).encode() + b' ' + extra +
                b' >>\nstream\n' + data + b'\nendstream')
    content = body or b'BT /F 12 Tf 1 0 0 1 40 400 Tm <00010002> Tj ET'
    cmap_data = cmap_comment + (f'begincmap 1 begincodespacerange <0000> <ffff> endcodespacerange '
                 f'2 beginbfchar <0001> <{cmap[0]:04x}> <0002> <{cmap[1]:04x}> '
                 'endbfchar endcmap').encode()
    objects = {
        1: b'<< /Type /Catalog /Pages 2 0 R >>',
        2: b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        3: (b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
            b'/Resources << /Font << /F ' + str(font_number).encode() + b' 0 R >> >> /Contents 4 0 R >>'),
        4: stream(content),
        font_number: (b'<< /Type /Font /Subtype /Type0 /BaseFont /SourceProof /Encoding /Identity-H '
                      b'/DescendantFonts [6 0 R] /ToUnicode 9 0 R >>'),
        6: (b'<< /Type /Font /Subtype /CIDFontType2 /BaseFont /SourceProof /FontDescriptor 7 0 R '
            b'/CIDToGIDMap ' + cid_map + b' /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) '
            b'/Supplement 0 >> /W [1 [600 600]] /DW 600 >>'),
        7: (b'<< /Type /FontDescriptor /FontName /SourceProof /Flags 4 /ItalicAngle 0 '
            b'/FontBBox [0 0 1000 1400] /Ascent 800 /Descent -200 /CapHeight 700 '
            b'/StemV 80 /FontFile2 8 0 R >>'),
        8: stream(FONT, b'/Length1 ' + str(len(FONT)).encode()),
        9: stream(cmap_data),
        10: stream(b'\x00\x00\x00\x02\x00\x01'),
    }
    raw = bytearray(b'%PDF-1.4\n'); positions = {}
    for number, data in sorted(objects.items()):
        positions[number] = len(raw)
        raw.extend(f'{number} 0 obj\n'.encode() + data + b'\nendobj\n')
    start = len(raw); size = max(objects) + 1
    raw.extend(f'xref\n0 {size}\n0000000000 65535 f \n'.encode())
    for number in range(1, size):
        raw.extend((f'{positions[number]:010} 00000 n \n' if number in positions
                    else '0000000000 00000 f \n').encode())
    raw.extend(f'trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode())
    return bytes(raw)


class HouseRevisionSourceCompositionTests(unittest.TestCase):
    def components(self, raw):
        with pdfplumber.open(io.BytesIO(raw)) as document:
            page = document.pages[0]
            binding = observe_house_id_source_characters(page)
            observed_fonts = {}

            def callback(pointer):
                observed = fonts.observe_house_id_font(document.doc.getobj(pointer.source_object_id),
                                                      pointer.program_bytes, pointer.used_cids)
                observed_fonts[pointer.font_ref] = (pointer, observed)

            with patch.object(fonts, '_references', return_value=micro_reference()):
                paint = inspect_house_id_source_paint(raw, source_sha256=sha256(raw).hexdigest(),
                    font_program_loader=source_outline, font_source_observer=callback)
            return binding, observed_fonts, paint

    def test_real_embedded_TTF_character_and_paint_composition_is_not_scope_authority(self):
        raw = micro_pdf(); binding, observed_fonts, paint = self.components(raw)
        self.assertEqual(binding['status'], 'observed', binding)
        self.assertEqual(paint['status'], 'observed', paint)
        self.assertTrue(all(result['status'] == 'observed' for _, result in observed_fonts.values()))
        _validate_source_character_bindings(paint['occurrences'], binding, observed_fonts)
        self.assertEqual([b['cid'] for b in binding['bindings']], [1, 2])
        self.assertEqual([c['text'] for c in binding['chars']], ['0', '1'])
        self.assertFalse(binding['scope_proven'])
        # A real microfont without private test trust remains unsupported.
        with pdfplumber.open(io.BytesIO(raw)) as document:
            result = fonts.observe_house_id_font(document.doc.getobj(5), FONT, [1, 2])
        self.assertEqual(result['status'], 'unknown')

    def test_actual_source_ToUnicode_cannot_relabel_or_conceal_a_numeric_glyph(self):
        for cmap in ((49, 48), (0, 49), (65, 49)):
            with self.subTest(cmap=cmap):
                binding, observed_fonts, paint = self.components(micro_pdf(cmap=cmap))
                self.assertEqual(binding['status'], 'observed')
                self.assertEqual(paint['status'], 'observed')
                self.assertTrue(observed_fonts)
                self.assertTrue(all(result['status'] == 'unknown' for _, result in observed_fonts.values()))

    def test_actual_nonidentity_CIDToGID_is_not_laundered_by_matching_unicode(self):
        binding, observed_fonts, paint = self.components(micro_pdf(cid_map=b'10 0 R'))
        self.assertEqual(binding['status'], 'observed')
        self.assertEqual(paint['status'], 'unknown')
        self.assertEqual(observed_fonts, {})

    def test_identical_fontnames_text_and_count_do_not_accept_other_source_font_pointer(self):
        binding, _, _ = self.components(micro_pdf())
        other_binding, other_fonts, other_paint = self.components(micro_pdf(font_number=11))
        self.assertEqual(binding['chars'], other_binding['chars'])
        with self.assertRaisesRegex(_Unknown, 'font_membership_differs'):
            _validate_source_character_bindings(other_paint['occurrences'], binding, other_fonts)

    def test_same_text_source_CMap_bytes_are_bound_not_only_derived_unicode(self):
        binding, observed_fonts, paint = self.components(micro_pdf())
        # A source can encode identical Unicode with a byte-distinct valid CMap.
        other = micro_pdf(cmap_comment=b'% same mappings, distinct actual source bytes\n')
        other_binding, _, _ = self.components(other)
        self.assertEqual(binding['chars'], other_binding['chars'])
        self.assertNotEqual(binding['bindings'][0]['ToUnicode_sha256'],
                            other_binding['bindings'][0]['ToUnicode_sha256'])
        with self.assertRaisesRegex(_Unknown, 'ToUnicode_program_differs'):
            _validate_source_character_bindings(paint['occurrences'], other_binding, observed_fonts)

    def test_composition_requires_all_program_hash_GID_and_source_ordinal_equalities(self):
        binding, observed_fonts, paint = self.components(micro_pdf())
        for field, value, reason in (
                ('font_program_sha256', '0' * 64, 'font_program_differs'),
                ('gid', 2, 'selected_glyph_differs'),
                ('occurrence', 1, 'font_membership_differs')):
            with self.subTest(field=field):
                changed = deepcopy(paint['occurrences']); changed[0][field] = value
                with self.assertRaisesRegex(_Unknown, reason):
                    _validate_source_character_bindings(changed, binding, observed_fonts)

    def test_wrong_original_hash_or_selected_cache_cannot_be_scope_evidence(self):
        raw = micro_pdf()
        source = SimpleNamespace(document_id='1', source_sha256='0' * 64, pdf_bytes=raw)
        result = inspect_native_house_revision_source(source)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reasons'], ['source_original_hash_mismatch'])
        import pymupdf
        from test_house_ptr import fixture_pages, META
        with pymupdf.open() as document:
            for inventory in fixture_pages():
                page = document.new_page(width=inventory['width'], height=inventory['height'])
                for word in inventory['words']:
                    page.insert_text((word['x0'], word['top'] + word['size']), word['text'], fontsize=word['size'])
            raw = document.tobytes()
        source.pdf_bytes = raw
        source.source_sha256 = sha256(raw).hexdigest()
        source.document_id = META['document_id']
        source.metadata = {**META, 'sha256': source.source_sha256}
        source.extraction = {}; source.planned_ocr_pages = []
        result = inspect_native_house_revision_source(source)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reasons'], ['selected_extraction_differs_from_original_native_projection'])
        self.assertFalse(result['scope_complete'])


if __name__ == '__main__':
    unittest.main()
