import unittest

import numpy as np

from unison_snapshot.pdf_source_glyphs import interpret_glyph_unicode, cff_cid_to_gid, PARAMETERS


def synthetic_pair(prefix="slash", suffix="2"):
    # Synthetic shape contract, not an archived source or an accuracy gold.
    result = np.zeros((200, 220), dtype="uint8")
    for y in range(200):
        left = int(5 + .30 * y) if prefix == "slash" else 25
        result[y, left:left + 24] = 255
    if suffix == "2":
        result[:25, 90:220] = 255
        for y in range(25, 135):
            left = int(90 + .68 * (y - 25))
            result[y, left:min(220, left + 28)] = 255
        result[135:195, 100:220] = 255
    elif suffix == "7":
        for y in range(180):
            left = int(90 + .5 * y)
            result[y, left:min(220, left + 22)] = 255
        result[180:200, 90:220] = 255
    return result


class SourceGlyphInterpretationTests(unittest.TestCase):
    def test_synthetic_slash_two_requires_both_visible_parts(self):
        result = interpret_glyph_unicode("12", synthetic_pair())
        self.assertEqual(result["status"], "interpreted")
        self.assertEqual(result["interpreted_unicode"], "/2")
        self.assertEqual(result["raw_unicode"], "12")
        self.assertTrue(result["shape_evidence"]["slash_prefix_supported"])
        self.assertTrue(result["shape_evidence"]["two_suffix_supported"])

    def test_true_one_and_wrong_suffix_not_changed(self):
        self.assertEqual(interpret_glyph_unicode("12", synthetic_pair(prefix="one"))["status"], "unsupported")
        self.assertEqual(interpret_glyph_unicode("12", synthetic_pair(suffix="7"))["status"], "unsupported")
        for raw in ("1", "/", "2", "7", "so", "ourchase", "purchaso", "/2"):
            self.assertEqual(interpret_glyph_unicode(raw, synthetic_pair())["status"], "unsupported")

    def test_empty_tiny_and_invalid_arrays_do_not_make_reading(self):
        self.assertEqual(interpret_glyph_unicode("12", np.zeros((200, 220)))["status"], "unsupported")
        self.assertEqual(interpret_glyph_unicode("12", np.ones((3, 3)) * 255)["status"], "unsupported")
        with self.assertRaises(ValueError): interpret_glyph_unicode("12", np.zeros(10))

    def test_receipt_is_json_stable_and_does_not_mutate_rule(self):
        import json
        result = interpret_glyph_unicode("12", synthetic_pair())
        self.assertEqual(result, json.loads(json.dumps(result)))
        negative = interpret_glyph_unicode("12", synthetic_pair(prefix="one"))
        self.assertEqual(negative, json.loads(json.dumps(negative)))
        result["parameters"]["prefix_fit_height_fraction"][0] = 0
        self.assertEqual(PARAMETERS["prefix_fit_height_fraction"], [.20, .80])

    def test_not_a_guessed_cid_equals_gid_mapping(self):
        with self.assertRaises(ValueError): cff_cid_to_gid(b"not a CFF")
