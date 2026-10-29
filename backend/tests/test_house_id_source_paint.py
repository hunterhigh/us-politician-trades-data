"""Real source bytes, finite paint boundaries, and refusal before observation."""
from fractions import Fraction as F
import hashlib
import json
import struct
import unittest
import zlib

import pymupdf

from unison_snapshot.house_id_source_paint import (
    GlyphOutline, SourcePaintError, inspect_house_id_source_paint,
)


def _checksum(data):
    data += b"\0" * (-len(data) % 4)
    return sum(struct.unpack(">" + "I" * (len(data) // 4), data)) & 0xffffffff


def _font_program():
    """A tiny valid source TrueType program; no installed/committed font."""
    def glyph(width):
        return (struct.pack(">hhhhh", 1, 0, 0, width, 1400) + struct.pack(">HH", 3, 0) +
                b"\1" * 4 + struct.pack(">hhhh", 0, width, 0, -width) +
                struct.pack(">hhhh", 0, 0, 1400, 0))
    first, second = glyph(1000), glyph(600)
    first += b"\0" * (-len(first) % 4)
    second += b"\0" * (-len(second) % 4)
    head = struct.pack(">IIIIHHQQhhhhHHhhh", 0x10000, 0x10000, 0, 0x5f0f3cf5, 0, 2048,
                       0, 0, 0, 0, 1000, 1400, 0, 8, 2, 1, 0)
    hhea = struct.pack(">IhhhHhhhhhhhhhhhH", 0x10000, 1600, -400, 0, 1200,
                       0, 0, 1000, 1, 0, 0, 0, 0, 0, 0, 0, 3)
    maxp = struct.pack(">I14H", 0x10000, 3, 4, 1, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0)
    cmap4 = struct.pack(">HHHHHHH", 4, 32, 0, 4, 4, 1, 0)
    cmap4 += struct.pack(">2HH2H2h2H", 0x31, 0xffff, 0, 0x30, 0xffff, -47, 1, 0, 0)
    name = "SourceProof".encode("utf-16-be")
    tables = {"head": head, "hhea": hhea, "maxp": maxp,
              "hmtx": struct.pack(">6H", 1200, 0, 1200, 0, 1200, 0),
              "loca": struct.pack(">4I", 0, 0, len(first), len(first) + len(second)),
              "glyf": first + second,
              "cmap": struct.pack(">HHHHI", 0, 1, 3, 1, 12) + cmap4,
              "name": struct.pack(">3H6H", 0, 1, 18, 3, 1, 0x409, 1, len(name), 0) + name,
              "post": struct.pack(">IihhIIIII", 0x30000, 0, 0, 0, 0, 0, 0, 0, 0)}
    tags = sorted(tables)
    count = len(tags)
    maximum = 1 << (count.bit_length() - 1)
    out = bytearray(struct.pack(">I4H", 0x10000, count, maximum * 16,
                                maximum.bit_length() - 1, count * 16 - maximum * 16))
    offset = 12 + count * 16
    data = bytearray()
    head_offset = None
    for tag in tags:
        blob = tables[tag]
        out.extend(tag.encode("ascii") + struct.pack(">3I", _checksum(blob), offset, len(blob)))
        if tag == "head":
            head_offset = offset
        data.extend(blob + b"\0" * (-len(blob) % 4))
        offset += (len(blob) + 3) // 4 * 4
    out.extend(data)
    struct.pack_into(">I", out, head_offset + 8, (0xb1b0afba - _checksum(bytes(out))) & 0xffffffff)
    return bytes(out)


FONT = _font_program()


def source_outline(data, gid):
    """Read the fixture's own source glyf/loca rather than a saved bbox."""
    if data != FONT:
        raise ValueError("fixture source font mismatch")
    count = struct.unpack_from(">H", data, 4)[0]
    tables = {}
    for i in range(count):
        tag, _, offset, length = struct.unpack_from(">4sIII", data, 12 + 16 * i)
        tables[tag] = (offset, length)
    loca, _ = tables[b"loca"]
    start, end = struct.unpack_from(">2I", data, loca + gid * 4)
    if start == end:
        return GlyphOutline(2048, ())
    glyf = tables[b"glyf"][0] + start
    count, _, _, _, _ = struct.unpack_from(">5h", data, glyf)
    if count != 1:
        raise ValueError("fixture simple glyph required")
    endpoint, instructions = struct.unpack_from(">2H", data, glyf + 10)
    if endpoint != 3 or instructions or data[glyf + 14:glyf + 18] != b"\1" * 4:
        raise ValueError("fixture control program unsupported")
    dx = struct.unpack_from(">4h", data, glyf + 18)
    dy = struct.unpack_from(">4h", data, glyf + 26)
    x = y = 0
    points = []
    for a, b in zip(dx, dy):
        x += a; y += b; points.append((x, y))
    return GlyphOutline(2048, tuple(points))


TEXT = b"BT /F 12 Tf 1 0 0 1 40 400 Tm <00010002> Tj ET\n"


def pdf_source(body=TEXT, *, form=True, state=b"/ca 1 /CA 1 /BM /Normal",
               extra_resource=b"", form_extra=b"", page_extra=b"", pages=1,
               descriptor_extra=b"", cid_map=b"/Identity", image=False):
    def stream(blob, extra=b""):
        return b"<< /Length " + str(len(blob)).encode() + b" " + extra + b" >>\nstream\n" + blob + b"\nendstream"
    font = (b"<< /Type /Font /Subtype /Type0 /BaseFont /SourceProof /Encoding /Identity-H "
            b"/DescendantFonts [6 0 R] /ToUnicode 9 0 R >>")
    resources = (b"<< /Font << /F 5 0 R >> /ExtGState << /G 10 0 R >> " +
                 (b"/XObject << /Im 12 0 R >> " if image else b"") + extra_resource + b" >>")
    page_resources = b"<< /XObject << /X 11 0 R >> >>" if form else resources
    kids = b" ".join(str(3 if i == 0 else 13 + i - 1).encode() + b" 0 R" for i in range(pages))
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(pages).encode() + b" >>",
        4: stream(b"/X Do\n" if form else body),
        5: font,
        6: (b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /SourceProof /FontDescriptor 7 0 R "
            b"/CIDToGIDMap " + cid_map + b" /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /W [1 [600 600]] /DW 600 >>"),
        7: (b"<< /Type /FontDescriptor /FontName /SourceProof /Flags 4 /ItalicAngle 0 "
            b"/FontBBox [0 0 500 700] /Ascent 800 /Descent -200 /CapHeight 700 /StemV 80 "
            b"/FontFile2 8 0 R " + descriptor_extra + b" >>"),
        8: stream(FONT, b"/Length1 " + str(len(FONT)).encode()),
        9: stream(b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def /CMapName /Proof def /CMapType 2 def 1 begincodespacerange <0000> <FFFF> endcodespacerange 2 beginbfchar <0001> <0030> <0002> <0031> endbfchar endcmap CMapName currentdict /CMap defineresource pop end end"),
        10: b"<< /Type /ExtGState " + state + b" >>",
        11: stream(body, b"/Type /XObject /Subtype /Form /FormType 1 /BBox [0 0 612 792] /Matrix [1 0 0 1 0 0] /Resources " + resources + form_extra),
        12: stream(zlib.compress(b"\0\0\0"), b"/Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode"),
    }
    for i in range(pages):
        number = 3 if i == 0 else 13 + i - 1
        objects[number] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /CropBox [0 0 612 792] "
                           b"/Resources " + page_resources + b" /Contents 4 0 R " + page_extra + b" >>")
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i in range(1, max(objects) + 1):
        offsets.append(len(output))
        output.extend(str(i).encode() + b" 0 obj\n" + objects[i] + b"\nendobj\n")
    start = len(output)
    output.extend(b"xref\n0 " + str(len(offsets)).encode() + b"\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010} 00000 n \n".encode())
    output.extend(b"trailer\n<< /Size " + str(len(offsets)).encode() + b" /Root 1 0 R >>\nstartxref\n" + str(start).encode() + b"\n%%EOF\n")
    return bytes(output)


def inspect(raw, **kwargs):
    return inspect_house_id_source_paint(raw, source_sha256=hashlib.sha256(raw).hexdigest(),
                                        font_program_loader=source_outline, **kwargs)


class HouseIdSourcePaintTests(unittest.TestCase):
    def test_source_CIDs_and_font_bytes_bind_occurrences_without_digit_meaning(self):
        raw = pdf_source()
        result = inspect(raw)
        self.assertEqual(result["status"], "observed", result["unresolved"])
        self.assertEqual([r["cid"] for r in result["occurrences"]], [1, 2])
        self.assertTrue(all(r["font_ref"].endswith("/F@5:0") for r in result["occurrences"]))
        self.assertEqual(result["fonts"][0]["program_sha256"], hashlib.sha256(FONT).hexdigest())
        self.assertTrue(all("unicode" not in r for r in result["occurrences"]))
        self.assertTrue(all(r["outer_device_pixels"] for r in result["occurrences"]))
        self.assertEqual(result["render_profile"]["actual_glyph_count"], 2)
        self.assertNotIn(FONT, result.values())
        self.assertEqual(raw, pdf_source())

    def test_runtime_font_pointer_is_bound_to_fresh_original_spec_and_not_serialized(self):
        import io
        from pdfminer.pdfdocument import PDFDocument
        from pdfminer.pdfparser import PDFParser
        from pdfminer.pdftypes import resolve1
        raw = pdf_source()
        document = PDFDocument(PDFParser(io.BytesIO(raw)))
        observed = []
        def observer(source):
            spec = document.getobj(source.source_object_id)
            child = resolve1(resolve1(spec['DescendantFonts'])[0])
            program = resolve1(resolve1(child['FontDescriptor'])['FontFile2']).get_data()
            self.assertEqual(source.program_bytes, program)
            self.assertEqual(source.used_cids, (1, 2))
            self.assertIn(b'begincmap', source.to_unicode_bytes)
            observed.append(source.font_ref)
            return {'passed': True, 'unicode': 'fake callback result'}
        result = inspect(raw, font_source_observer=observer)
        self.assertEqual(result['status'], 'observed', result['unresolved'])
        self.assertEqual(len(observed), 1)
        self.assertNotIn('fake callback result', json.dumps(result))

    def test_existing_white_cover_is_not_lost_from_complete_paint_inventory(self):
        control = inspect(pdf_source())
        bounds = control["occurrences"][0]["outer_device_pixels"]
        x, y, X, Y = bounds
        cover = f"1 1 1 rg {x-1} {792-Y-1} {X-x+2} {Y-y+2} re f\n".encode()
        blocked = inspect(pdf_source(TEXT + cover))
        self.assertEqual(blocked["status"], "observed", blocked["unresolved"])
        self.assertEqual(len(blocked["paint_inventory"]), 3)
        self.assertIn(2, blocked["occurrences"][0]["intersecting_paint_events"])

    def test_page_stream_wrappers_and_late_page_paint_are_all_replayed(self):
        raw = pdf_source()
        with pymupdf.open(stream=raw, filetype="pdf") as document:
            document[0].draw_rect(pymupdf.Rect(39, 382, 55, 394), color=None, fill=(1, 1, 1))
            changed = document.tobytes()
        result = inspect(changed)
        self.assertEqual(result["status"], "observed", result["unresolved"])
        self.assertEqual(len(result["paint_inventory"]), 3)
        self.assertEqual(result["paint_inventory"][-1]["kind"], "path")
        self.assertTrue(all(r["source_stream_object"] > 0 for r in result["occurrences"]))
        self.assertTrue(any("nonglyph_paint_domain_intersects" in r["unresolved"] for r in result["occurrences"]))

    def test_existing_empty_password_RC4_source_bytes_are_bound_before_decryption(self):
        raw = pdf_source()
        with pymupdf.open(stream=raw, filetype="pdf") as document:
            encrypted = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_RC4_128, owner_pw="owner", user_pw="")
        result = inspect(encrypted)
        self.assertEqual(result["status"], "observed", result["unresolved"])
        self.assertEqual(result["source_encryption"]["R"], 3)
        self.assertEqual(result["source_sha256"], hashlib.sha256(encrypted).hexdigest())
        self.assertEqual([r["cid"] for r in result["occurrences"]], [1, 2])
        with pymupdf.open(stream=raw, filetype="pdf") as document:
            unsupported = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="")
        rejected = inspect(unsupported)
        self.assertEqual(rejected["status"], "unknown")
        self.assertEqual(rejected["occurrences"], [])

    def test_complex_clip_only_poisons_its_actual_qQ_scope(self):
        path = b"q 0 0 m 600 0 l 600 700 l 0 700 l h W n "
        outside = inspect(pdf_source(path + b"Q\n" + TEXT))
        inside = inspect(pdf_source(path + TEXT + b"Q\n"))
        self.assertEqual(outside["status"], "observed", outside["unresolved"])
        self.assertTrue(all("target_clip_not_proved_interior" not in r["unresolved"] for r in outside["occurrences"]))
        self.assertTrue(all("target_clip_not_proved_interior" in r["unresolved"] for r in inside["occurrences"]))
        self.assertEqual(outside["clips"][0]["activated_before_event"], outside["clips"][0]["retired_before_event"])

    def test_active_rect_clip_is_inward_while_glyph_domain_is_outward(self):
        raw = pdf_source(b"q 41 395 3 10 re W n\n" + TEXT + b"Q")
        result = inspect(raw)
        self.assertEqual(result["status"], "observed", result["unresolved"])
        self.assertTrue(any("target_clip_not_proved_interior" in r["unresolved"] for r in result["occurrences"]))

    def test_image_and_stroke_domains_are_not_omitted_from_occlusion_evidence(self):
        far_image = b"q 5 0 0 5 400 600 cm /Im Do Q\n"
        control = inspect(pdf_source(TEXT + far_image, image=True))
        self.assertEqual(control['status'], 'observed', control['unresolved'])
        self.assertEqual(control['paint_inventory'][-1]['kind'], 'image')
        self.assertFalse(control['occurrences'][0]['intersecting_paint_events'])
        near_image = b"q 20 0 0 20 39 399 cm /Im Do Q\n"
        covered = inspect(pdf_source(TEXT + near_image, image=True))
        self.assertEqual(covered['status'], 'observed', covered['unresolved'])
        self.assertIn(2, covered['occurrences'][0]['intersecting_paint_events'])
        stroked = inspect(pdf_source(TEXT + b"/G gs 40 400 m 60 400 l S\n",
                                     state=b"/LW 2 /ML 4 /LC 0 /LJ 0"))
        self.assertEqual(stroked['status'], 'observed', stroked['unresolved'])
        self.assertTrue(stroked['paint_inventory'][-1]['stroke'])
        self.assertIn(2, stroked['occurrences'][0]['intersecting_paint_events'])
        thin = inspect(pdf_source(TEXT + b"/G gs 200 500 m 220 500 l S\n", state=b"/LW 0.1"))
        self.assertEqual(thin['status'], 'unknown')
        self.assertEqual(thin['occurrences'], [])

    def test_curve_flattening_keeps_all_source_controls_and_finite_budget(self):
        curve = b"200 500 m 210 520 230 520 240 500 c f\n"
        result = inspect(pdf_source(TEXT + curve))
        self.assertEqual(result['status'], 'observed', result['unresolved'])
        event = result['paint_inventory'][-1]
        self.assertIn('c', event['source_ops'])
        self.assertLessEqual(event['outer_device_pixels'][0], 200)
        self.assertGreaterEqual(event['outer_device_pixels'][2], 240)
        self.assertLessEqual(event['outer_device_pixels'][1], 272)
        self.assertGreaterEqual(event['outer_device_pixels'][3], 292)

    def test_sibling_glyph_overlap_is_reported_without_an_invented_group(self):
        # Source widths deliberately place two genuine outline masks together.
        raw = pdf_source(TEXT.replace(b'<00010002>', b'<00010001>').replace(b'12 Tf', b'2 Tf'))
        result = inspect(raw)
        self.assertEqual(result['status'], 'observed', result['unresolved'])
        for row in result['occurrences']:
            self.assertEqual(row['occlusion_status'], 'requires_independent_target_group_check')
            self.assertNotIn('id', row)

    def test_opaque_source_rectangle_background_has_exact_not_thresholded_bytes(self):
        background = b'0.9333 0.9333 0.9333 rg 35 395 30 25 re f 0 0 0 rg\n'
        result = inspect(pdf_source(background + TEXT))
        self.assertEqual(result['status'], 'observed', result['unresolved'])
        metadata = result['paint_inventory'][0]['uniform_background']
        self.assertEqual(metadata['status'], 'observed')
        self.assertTrue(metadata['opaque'])
        self.assertEqual(metadata['display_bytes'], [237, 237, 237])
        self.assertEqual(metadata['display_byte_ranges'], [[237, 237]] * 3)
        self.assertTrue(metadata['engine_source_color_verified'])
        self.assertTrue(metadata['engine_source_shape_verified'])
        self.assertEqual(metadata['engine_fill_index'], 0)
        self.assertTrue(metadata['effective_clips'])
        tiny = inspect(pdf_source(background.replace(b'0.9333', b'0.001') + TEXT))
        # Positive PDF real color is insufficient: the actual uchar values
        # are black and cannot establish a distinct background for black ink.
        self.assertEqual(tiny['paint_inventory'][0]['uniform_background']['display_bytes'], [0, 0, 0])

    def test_text_source_palette_is_explicit_engine_bound_and_does_not_change_black_ID_policy(self):
        result = inspect(pdf_source(b'0.502 0.502 0.502 rg\n' + TEXT))
        self.assertEqual(result['status'], 'observed', result['unresolved'])
        for row in result['occurrences']:
            self.assertEqual(row['display_bytes'], [128, 128, 128])
            self.assertEqual(row['display_byte_ranges'], [[128, 128]] * 3)
            self.assertTrue(row['engine_color_source_verified'])
            self.assertTrue(row['opaque'])
            self.assertIn('target_fill_not_black', row['unresolved'])
        white = inspect(pdf_source(b'1 1 1 rg\n' + TEXT))
        self.assertTrue(all(row['display_bytes'] == [255, 255, 255] for row in white['occurrences']))

    def test_same_background_after_glyphs_is_still_an_external_cover(self):
        background = b'0.9333 0.9333 0.9333 rg 35 395 30 25 re f\n'
        result = inspect(pdf_source(TEXT + background))
        self.assertEqual(result['status'], 'observed', result['unresolved'])
        event = result['paint_inventory'][-1]
        self.assertEqual(event['uniform_background']['display_bytes'], [237, 237, 237])
        self.assertGreater(event['event'], result['occurrences'][-1]['paint_event'])
        self.assertIn(event['event'], result['occurrences'][0]['intersecting_paint_events'])
        self.assertIn('nonglyph_paint_domain_intersects', result['occurrences'][0]['unresolved'])

    def test_rectangular_looking_path_transparency_or_unknown_clip_cannot_self_sign_background(self):
        polygon = b'0.9333 0.9333 0.9333 rg 35 395 m 65 395 l 65 420 l 35 420 l h f 0 0 0 rg\n'
        result = inspect(pdf_source(polygon + TEXT))
        self.assertEqual(result['status'], 'observed', result['unresolved'])
        self.assertNotIn('uniform_background', result['paint_inventory'][0])
        transparent = inspect(pdf_source(b'/G gs 0.9333 0.9333 0.9333 rg 35 395 30 25 re f\n' + TEXT,
                                         state=b'/ca 0.2'))
        self.assertNotIn('uniform_background', transparent['paint_inventory'][0])
        clipped = inspect(pdf_source(b'q 0 0 m 600 0 l 600 700 l 0 700 l h W n\n' +
                                     b'0.9333 0.9333 0.9333 rg 35 395 30 25 re f 0 0 0 rg\n' + TEXT + b'Q'))
        self.assertNotIn('uniform_background', clipped['paint_inventory'][0])

    def test_unknown_operator_resource_and_graphics_state_invalidate_full_scope(self):
        sources = [pdf_source(TEXT + b"3 Tr"), pdf_source(extra_resource=b"/Pattern << >>"),
                   pdf_source(state=b"/Font [5 0 R 100]"), pdf_source(state=b"/SMask /None"),
                   pdf_source(TEXT + b"/Missing gs"), pdf_source(form_extra=b"/Group << /S /Transparency >>"),
                   pdf_source(cid_map=b"8 0 R"), pdf_source(b"40 400 m q 60 400 l S Q")]
        for raw in sources:
            with self.subTest(digest=hashlib.sha256(raw).hexdigest()):
                result = inspect(raw)
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["occurrences"], [])
                self.assertTrue(result["unresolved"])

    def test_unknown_or_nonopaque_target_does_not_become_visible(self):
        result = inspect(pdf_source(b"/G gs\n" + TEXT, state=b"/ca 0.2 /BM /Normal"))
        self.assertEqual(result["status"], "observed", result["unresolved"])
        self.assertTrue(all("target_alpha_not_opaque" in row["unresolved"] for row in result["occurrences"]))
        white = inspect(pdf_source(b"1 1 1 rg\n" + TEXT))
        self.assertTrue(all("target_fill_not_black" in row["unresolved"] for row in white["occurrences"]))

    def test_all_pages_are_enumerated_and_selected_page_is_explicit(self):
        raw = pdf_source(pages=2)
        one = inspect(raw)
        two = inspect(raw, page_number=2)
        self.assertEqual(one["pages"], [1, 2])
        self.assertEqual(one["unprocessed_pages"], [2])
        self.assertEqual(two["processed_pages"], [2])
        self.assertEqual(two["unprocessed_pages"], [1])
        self.assertEqual(inspect(raw, max_pages=1)["status"], "unknown")

    def test_hash_limits_numeric_budget_and_AA_never_reuse_success(self):
        raw = pdf_source()
        self.assertEqual(inspect(raw)["status"], "observed")
        with self.assertRaisesRegex(SourcePaintError, "sha256"):
            inspect_house_id_source_paint(raw + b" ", source_sha256=hashlib.sha256(raw).hexdigest(), font_program_loader=source_outline)
        for kwargs in ({"max_operators": 1}, {"max_glyphs": 1}, {"max_events": 1}, {"max_numeric_operations": 10}):
            result = inspect(raw, **kwargs)
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["occurrences"], [])
        old = pymupdf.TOOLS.show_aa_level()
        try:
            pymupdf.TOOLS.set_aa_level(0)
            result = inspect(raw)
            self.assertEqual(result["status"], "unknown")
        finally:
            pymupdf.TOOLS.set_aa_level(old["text"])

    def test_source_font_loader_unsupported_glyph_and_geometry_fail_closed(self):
        raw = pdf_source()
        bad = lambda data, gid: GlyphOutline(1000, ((0, 0), (1, 1)))
        result = inspect_house_id_source_paint(raw, source_sha256=hashlib.sha256(raw).hexdigest(), font_program_loader=bad)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["occurrences"], [])
        self.assertEqual(inspect(pdf_source(b"q 1 0.1 0 1 0 0 cm\n" + TEXT + b"Q"))["status"], "unknown")
        self.assertEqual(inspect(pdf_source(TEXT, page_extra=b"/Rotate 90"))["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
