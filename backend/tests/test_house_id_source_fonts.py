"""Real synthetic TTF mechanics; reference injection is private and test-only."""
from copy import deepcopy
import io
import struct
import unittest
from unittest.mock import patch
import zlib

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables.ttProgram import Program
from pdfminer.pdftypes import PDFStream
from pdfminer.psparser import LIT

import unison_snapshot.house_id_source_fonts as fonts


def ttf(*, change=None, order=None):
    names = order or ['.notdef', 'digit', 'letter', 'smallcap', 'unreferenced']
    fb = FontBuilder(2048, isTTF=True); fb.setupGlyphOrder(names)
    fb.setupCharacterMap({49: 'digit', 65: 'letter'})
    glyphs = {}
    shapes = {'digit': [(0, 0), (400, 0), (400, 1200), (0, 1200)],
              'letter': [(0, 0), (500, 1200), (1000, 0)],
              'smallcap': [(0, 0), (300, 700), (600, 0)],
              'unreferenced': [(0, 0), (123, 456), (400, 0)]}
    for name in names:
        pen = TTGlyphPen(None)
        points = shapes.get(name)
        if points:
            if name == 'digit' and change == 'outline':
                points = [(x+1, y) for x, y in points]
            pen.moveTo(points[0])
            for point in points[1:]: pen.lineTo(point)
            pen.closePath()
        glyph = pen.glyph()
        if points:
            glyph.program = Program(); glyph.program.fromBytecode([1 if name == 'digit' and change == 'instruction' else 0])
        glyphs[name] = glyph
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({name: (1001 if name == 'digit' and change == 'metric' else 1000, 0) for name in names})
    fb.setupHorizontalHeader(ascent=1600, descent=-400)
    fb.setupNameTable({'familyName': 'Synthetic', 'styleName': 'Regular', 'psName': 'Synthetic-Regular'})
    fb.setupOS2(sTypoAscender=1600, sTypoDescender=-400, usWinAscent=1600, usWinDescent=400)
    fb.setupPost(); fb.setupMaxp()
    if change == 'variable':
        variation = newTable('fvar'); variation.axes = []; variation.instances = []
        fb.font['fvar'] = variation
    if change == 'composite':
        glyph = TTGlyphPen(glyphs); glyph.addComponent('letter', (1, 0, 0, 1, 0, 0))
        fb.font['glyf']['digit'] = glyph.glyph()
    result = io.BytesIO(); fb.save(result); return result.getvalue()


def unicode_stream(mapping, *, duplicate=False):
    pairs = [f'<{cid:04x}> <{text.encode("utf-16-be").hex()}>' for cid, text in mapping.items()]
    if duplicate: pairs.append(pairs[0])
    data = ('begincmap\n1 begincodespacerange\n<0000> <ffff>\nendcodespacerange\n'
            + str(len(pairs)) + ' beginbfchar\n' + '\n'.join(pairs) + '\nendbfchar\nendcmap\n').encode()
    return PDFStream({}, data)


def spec(data, mapping=None, *, cid_map=None):
    return {'Type': LIT('Font'), 'Subtype': LIT('Type0'), 'BaseFont': LIT('Georgia'),
            'Encoding': LIT('Identity-H'), 'ToUnicode': unicode_stream({1: '1', 2: 'A'} if mapping is None else mapping),
            'DescendantFonts': [{'Subtype': LIT('CIDFontType2'),
                'FontDescriptor': {'FontFile2': PDFStream({}, data)},
                'CIDToGIDMap': LIT('Identity') if cid_map is None else PDFStream({}, cid_map)}]}


def reference(data):
    tables = fonts._directory(data)
    with TTFont(io.BytesIO(data)) as font:
        records = []
        for gid, meaning, smallcap in ((1, [49], False), (2, [65], False), (3, [], True)):
            result = fonts._glyph_observation(font, gid, tables, fonts.MAX_TOTAL_POINTS)
            records.append({'fingerprint': result['fingerprint'], 'cmap_unicode': meaning,
                            'smallcaps_nonnumeric': smallcap, 'smallcaps_origin_unicode': [65, 97] if smallcap else [],
                            'trusted_GSUB_features': ['c2sc', 'smcp'] if smallcap else []})
        return {'schema_version': 'synthetic-reference-only', 'families': [{
            'profile_id': 'test-only-reference', 'global_profile': fonts._global_profile(font, tables), 'glyphs': records}]}


class HouseIdSourceFontTests(unittest.TestCase):
    def setUp(self):
        self.data = ttf()
        self.reference = reference(self.data)

    def observe(self, source=None, data=None, cids=(1, 2), *, reference=None):
        with patch.object(fonts, '_references', return_value=reference or self.reference):
            return fonts.observe_house_id_font(source or spec(self.data), data or self.data, cids)

    def test_source_program_points_and_Unicode_match_the_private_test_reference(self):
        result = self.observe()
        self.assertEqual(result['status'], 'observed', result)
        self.assertEqual(result['glyphs'][1]['semantic_kind'], 'numeric')
        self.assertEqual(result['glyphs'][1]['source_unicode'], '1')
        self.assertEqual(result['glyphs'][1]['points'], [[0, 0], [400, 0], [400, 1200], [0, 1200]])
        self.assertFalse(result['glyphs'][2]['numeric'])
        self.assertFalse(result['visibility_proven']); self.assertFalse(result['ID_scope_proven'])

    def test_missing_or_literal_NUL_only_accepts_unique_proven_nonnumeric_smallcaps(self):
        for mapping in ({}, {3: '\x00'}):
            result = self.observe(spec(self.data, mapping), cids=[3])
            self.assertEqual(result['status'], 'observed', result)
            glyph = result['glyphs'][3]
            self.assertEqual(glyph['semantic_kind'], 'known_nonnumeric_missing_unicode')
            self.assertEqual(glyph['source_unicode'], mapping.get(3))
        for code in ('\ufffd', '\x01', '1'):
            self.assertEqual(self.observe(spec(self.data, {3: code}), cids=[3])['status'], 'unknown')
        for mapping in ({}, {1: '\x00'}):
            self.assertEqual(self.observe(spec(self.data, mapping), cids=[1])['status'], 'unknown')
        bad = deepcopy(self.reference); bad['families'][0]['glyphs'][2]['trusted_GSUB_features'] = []
        self.assertEqual(self.observe(spec(self.data, {}), cids=[3], reference=bad)['status'], 'unknown')

    def test_ToUnicode_cannot_turn_a_letter_into_number_or_hide_a_number(self):
        for mapping in ({1: 'A'}, {1: '12'}, {1: '\ufffd'}, {2: '1'}):
            with self.subTest(mapping=mapping):
                cid = next(iter(mapping))
                result = self.observe(spec(self.data, mapping), cids=[cid])
                self.assertEqual(result['status'], 'unknown')
                self.assertIn('Unicode_conflict', result['reason'])

    def test_actual_CID_to_GID_and_reordered_glyphs_are_used_instead_of_names_or_ids(self):
        reordered = ttf(order=['.notdef', 'letter', 'digit', 'smallcap', 'unreferenced'])
        mapping = b'\x00\x00' + b'\x00\x02'
        result = self.observe(spec(reordered, {1: '1'}, cid_map=mapping), reordered, [1])
        self.assertEqual(result['status'], 'observed', result)
        self.assertEqual(result['glyphs'][1]['gid'], 2)
        self.assertTrue(result['glyphs'][1]['numeric'])
        wrong = self.observe(spec(reordered, {1: '1'}), reordered, [1])
        self.assertEqual(wrong['status'], 'unknown')

    def test_outline_metric_and_instruction_changes_are_independently_rejected(self):
        for change in ('outline', 'metric', 'instruction'):
            with self.subTest(change=change):
                changed = ttf(change=change)
                result = self.observe(spec(changed), changed, [1])
                self.assertEqual(result['status'], 'unknown')
        wrong_source = self.observe(spec(self.data), ttf(change='outline'), [1])
        self.assertEqual(wrong_source['reason'], 'font_program_differs_from_source_FontFile2')

    def test_global_hint_context_and_scale_cannot_hide_behind_matching_outlines(self):
        for change in ('hint', 'scale', 'storage'):
            with self.subTest(change=change):
                font = TTFont(io.BytesIO(self.data))
                if change == 'hint':
                    table = newTable('prep'); table.program = Program(); table.program.fromBytecode([0])
                    font['prep'] = table
                elif change == 'scale': font['head'].unitsPerEm = 1024
                else: font['maxp'].maxStorage += 1
                output = io.BytesIO(); font.save(output); font.close(); changed = output.getvalue()
                result = self.observe(spec(changed), changed, [1])
                self.assertEqual(result['status'], 'unknown')
                self.assertEqual(result['reason'], 'trusted_font_global_program_unmatched')

    def test_untrusted_point_count_is_checked_before_glyph_coordinate_expansion(self):
        font = TTFont(io.BytesIO(self.data))
        offset = font['loca'].locations[1]
        table_offset = font.reader.tables['glyf'].offset
        font.close()
        changed = bytearray(self.data)
        # Source simple-glyph endpoint declares 65536 points while the trusted
        # maxp/hint context is unchanged. Refuse before fontTools expands it.
        struct.pack_into('>H', changed, table_offset + offset + 10, 65535)
        changed = bytes(changed)
        result = self.observe(spec(changed), changed, [1])
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reason'], 'glyph_point_or_instruction_budget_exceeded')

    def test_unknown_used_glyph_is_not_skipped_and_complex_or_variable_fonts_fail(self):
        self.assertEqual(self.observe(spec(self.data, {4: 'B'}), cids=[1, 4])['status'], 'unknown')
        for change in ('composite', 'variable'):
            changed = ttf(change=change)
            result = self.observe(spec(changed), changed, [1])
            self.assertEqual(result['status'], 'unknown')
        self.assertEqual(self.observe(cids=[])['status'], 'unknown')
        self.assertEqual(self.observe(cids=[65535])['status'], 'unknown')

    def test_ToUnicode_duplicate_ranges_and_stream_bombs_are_bounded(self):
        source = spec(self.data); source['ToUnicode'] = unicode_stream({1: '1'}, duplicate=True)
        self.assertIn('duplicate', self.observe(source, cids=[1])['reason'])
        source = spec(self.data)
        source['ToUnicode'] = PDFStream({}, b'begincmap\n1 beginbfrange\n<0000> <ffff> <0000>\nendbfrange\nendcmap')
        self.assertIn('mapping_budget', self.observe(source, cids=[1])['reason'])
        source = spec(self.data)
        source['ToUnicode'] = PDFStream({'Filter': LIT('FlateDecode')}, zlib.compress(b'x' * 100000))
        self.assertIn('byte_budget', self.observe(source, cids=[1])['reason'])

    def test_production_reference_has_only_fingerprints_and_rejects_synthetic_font(self):
        reference = fonts._references()
        self.assertEqual(reference['schema_version'], 'house-id-trusted-georgia-glyphs/v1')
        self.assertEqual(len(reference['families']), 2)
        for family in reference['families']:
            for glyph in family['glyphs']:
                self.assertNotIn('points', glyph); self.assertNotIn('coordinates', glyph)
                self.assertNotIn('instructions', glyph); self.assertNotIn('program', glyph)
        result = fonts.observe_house_id_font(spec(self.data), self.data, [1])
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reason'], 'trusted_font_global_program_unmatched')


if __name__ == '__main__':
    unittest.main()
