"""Synthetic geometry regressions; not official-source accuracy fixtures."""

from copy import deepcopy
import unittest

from unison_snapshot.oge_278t_field_shadow import _amount_shape, observe_field_shapes


def observation(words, raw="raw word order", indices=None):
    columns = {name: {"raw_ocr_text": value} for name, value in {
        "printed_number": "1", "description": raw, "type": "Purchase",
        "transaction_date": "01/02/2026", "notification": "No",
        "amount": "$15,001 - $50,000"}.items()}
    if words is not None:
        columns["description"]["word_indices"] = list(range(len(words))) if indices is None else indices
    slot = {"slot_index": 1, "column_observation": columns}
    if words is not None:
        slot["words"] = words
    return {"schema_version": "oge-278t-raster-slot-observation/v2",
            "source_sha256": "a" * 64, "pages": [{"page_number": 2,
            "column_geometry": "oge_2024_six_column_grid", "slots": [slot]}]}


def word(text, x, top, bottom, width=15):
    return {"text": text, "bbox_pdf_points": [x, top, x + width, bottom]}


class OGEFieldGeometryTests(unittest.TestCase):
    def row(self, value):
        return observe_field_shapes([value])["pages"][0]["slots"][0]

    def test_taller_glyph_does_not_move_a_word_before_its_left_neighbor(self):
        value = observation([word("BOND", 40, 96, 104), word("ALPHA", 10, 98, 104)],
                            raw="BOND ALPHA")
        original = deepcopy(value)
        row = self.row(value)
        self.assertEqual(row["field_shapes"]["description"]["value"], "ALPHA BOND")
        self.assertEqual(row["description_reading"]["line_word_indices"], [[1, 0]])
        self.assertEqual(row["raw_columns"]["description"], "BOND ALPHA")
        self.assertEqual(value, original)
        self.assertEqual(row["qualification"], "not_assessed")

    def test_wrap_stays_on_its_line_even_when_the_next_line_starts_further_left(self):
        row = self.row(observation([
            word("BOND", 60, 98, 104), word("CLASS", 5, 110, 116),
            word("ALPHA", 30, 99, 105), word("A", 50, 111, 117)]))
        self.assertEqual(row["field_shapes"]["description"]["value"], "ALPHA BOND CLASS A")
        self.assertEqual(row["description_reading"]["line_word_indices"], [[2, 0], [1, 3]])

    def test_no_geometry_keeps_old_observations_compatible(self):
        row = self.row(observation(None, raw="Original asset"))
        self.assertEqual(row["field_shapes"]["description"]["value"], "Original asset")
        self.assertEqual(row["description_reading"]["reason"], "word_geometry_unavailable")

    def test_word_indices_cannot_lose_or_duplicate_geometry_silently(self):
        for indices in ([0, 0], [1], [-1], [True]):
            with self.subTest(indices=indices), self.assertRaisesRegex(ValueError, "indices"):
                self.row(observation([word("A", 10, 10, 15)], indices=indices))

    def test_invalid_word_boxes_do_not_become_asset_text(self):
        for box in ([10, 10, 5, 15], [10, 15, 20, 10], [10, 10, 20, float("nan")]):
            with self.subTest(box=box), self.assertRaisesRegex(ValueError, "geometry"):
                self.row(observation([{"text": "A", "bbox_pdf_points": box}]))

    def test_amount_decimal_ocr_artifact_cannot_become_a_smaller_valid_range(self):
        for raw in ("$15.001 - $50.000", "$1,000.001 - $5,000,000", "$1,00 - $15,000"):
            with self.subTest(raw=raw):
                self.assertIsNone(_amount_shape(raw)["values"])
        self.assertEqual(_amount_shape("$15,001 - $50,000.")["values"], [15001, 50000])
