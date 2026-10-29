"""Finite engine placement, arithmetic enclosures, and refusal boundaries."""
from fractions import Fraction as F
import struct
import unittest

from unison_snapshot.house_id_source_numbers import (
    HorizontalTextState, SourceNumberError, SourceNumbers,
)


METRICS = {'F': {'widths': {19: '613.76953', 20: '429.6875', 21: '558.59375',
                            23: '564.9414', 25: '565.91797'}, 'default_width': 1000}}


def cursor(numbers=None, *, page=False):
    numbers = numbers or SourceNumbers()
    ctm = numbers.matrix([1, 0, 0, -1, 0, 792]) if page else numbers.identity()
    return numbers, HorizontalTextState(numbers, ctm=ctm, font_metrics=METRICS)


class HouseIdSourceNumbersTests(unittest.TestCase):
    def test_documented_integer_metric_and_float_constant_are_not_normative_reals(self):
        n = SourceNumbers()
        self.assertEqual(n.engine_width('558.59375'), 559)
        self.assertEqual(n.engine_width('564.9414'), 565)
        self.assertEqual(n.engine_width(8388609), 8388609)
        # A real-object cast with a width on a rounding discontinuity is not
        # conflated with the same PDF integer object or chosen from a trace.
        with self.assertRaisesRegex(SourceNumberError, 'rounding_ambiguous'):
            n.engine_width('8388609.0')
        with self.assertRaisesRegex(SourceNumberError, 'literal_kind'):
            n.engine_width(F(55859375, 100000))
        self.assertEqual(n.number('0.001').point, F.from_float(
            struct.unpack('>f', bytes.fromhex('3a83126f'))[0]))
        self.assertNotEqual(n.number('0.001').point, F(1, 1000))

    def test_source_decimal_has_exact_directed_bounds_and_even_tie_rounding(self):
        n = SourceNumbers()
        value = n.number('0.23999999')
        self.assertLessEqual(value.lower, F('0.23999999'))
        self.assertGreaterEqual(value.upper, F('0.23999999'))
        self.assertEqual(value.point, F.from_float(struct.unpack('>f', bytes.fromhex('3e75c28f'))[0]))
        tie = n.number(F(1) + F(1, 1 << 24))
        self.assertEqual(tie.point, 1)
        self.assertEqual(tie.lower, 1)
        self.assertEqual(tie.upper, F(1) + F(1, 1 << 23))

    def test_enclosure_keeps_distinct_fused_and_separately_rounded_candidates(self):
        n = SourceNumbers()
        a = n.number(4097)
        value = n.add(n.multiply(a, a), n.number(-16785406))
        # Exact product is 16785409. Separate binary32 multiplication gives
        # 16785408; fused multiply-add produces 3, separate operations give 2.
        self.assertEqual(value.point, 2)
        self.assertLessEqual(value.lower, F(2))
        self.assertGreaterEqual(value.upper, F(3))

    def test_real_declarative_vector_matches_independent_pinned_engine_origins(self):
        n, state = cursor(page=True)
        state.apply('cm', ['0.23999999', 0, 0, '-0.23999999', 0, 792])
        state.apply('cm', ['3.125', 0, 0, '3.125', '90.625', 300])
        state.apply('BT', [])
        state.apply('Tf', ['F', 12])
        state.apply('Tm', [1, 0, 0, -1, '4.59375', 348])
        positions = state.apply('Tj', [[21, 19, 19, 19, 20, 23, 19, 23, 23, 25]])
        # Values independently recorded by MuPDF 1.28.2 in the bounded source
        # method experiment; this test does not claim a complete PDF profile.
        expected_x = [25.1953125, 30.2263126373291, 35.75231170654297,
            41.27831268310547, 46.80431365966797, 50.674312591552734,
            55.75931167602539, 61.28531265258789, 66.37031555175781, 71.45531463623047]
        for index, (position, x) in enumerate(zip(positions, expected_x, strict=True)):
            with self.subTest(glyph=index):
                self.assertEqual(position.origin[0].point, F.from_float(x))
                self.assertEqual(position.origin[1].point, 333)
                for axis, point in zip(position.origin, (x, 333), strict=True):
                    self.assertLessEqual(axis.lower, F.from_float(float(point)))
                    self.assertGreaterEqual(axis.upper, F.from_float(float(point)))
        # The original real-width result is deliberately a different model.
        self.assertNotEqual(positions[1].origin[0].point,
                            F('25.1953114501953125') + F('558.59375') * 9 / 1000)
        state.apply('ET', []); state.finish()

    def test_Td_moves_line_matrix_instead_of_accumulating_previous_glyph_advance(self):
        n, state = cursor()
        state.apply('q', []); state.apply('cm', [1, 0, 0, 1, 90, 20]); state.apply('Q', [])
        state.apply('BT', []); state.apply('Tf', ['F', 10])
        state.apply('Tm', [1, 0, 0, 1, 3, 4])
        first = state.apply('Tj', [[21]])
        state.apply('Td', [2, 0])
        second = state.apply('Tj', [[21]])
        self.assertEqual(first[0].origin[0].point, 3)
        self.assertEqual(second[0].origin[0].point, 5)
        self.assertEqual(second[0].origin[1].point, 4)
        self.assertEqual([first[0].glyph_index, second[0].glyph_index], [0, 1])
        state.apply('ET', []); state.finish()

    def test_unsupported_operators_wrong_arity_and_unclosed_state_never_fallback(self):
        _, state = cursor()
        for operator, args in [('TJ', [[]]), ('Tz', [0]), ('gs', ['unknown']),
                               ('Tm', [1, 0, 0, 1, 0]), ('Q', [])]:
            with self.subTest(operator=operator), self.assertRaises(SourceNumberError):
                state.apply(operator, args)
        state.apply('BT', [])
        with self.assertRaisesRegex(SourceNumberError, 'unclosed'):
            state.finish()
        with self.assertRaisesRegex(SourceNumberError, 'inside_text'):
            state.apply('q', [])

    def test_overflow_subnormal_nonfinite_and_unbounded_inputs_are_explicit_errors(self):
        n = SourceNumbers()
        for value in ('nan', 'inf', '1e20', '1' * 129, 1.0, True, 1 << 300):
            with self.subTest(value=str(value)[:30]), self.assertRaises(SourceNumberError):
                n.number(value)
        with self.assertRaisesRegex(SourceNumberError, 'overflow'):
            n.multiply(n.number(1 << 127), n.number(4))
        with self.assertRaisesRegex(SourceNumberError, 'subnormal'):
            n.multiply(n.number(F(1, 1 << 126)), n.number('0.5'))
        with self.assertRaisesRegex(SourceNumberError, 'subnormal'):
            n.number(F(1, 1 << 149))
        with self.assertRaisesRegex(SourceNumberError, 'size'):
            n.engine_width('1' * 129)
        with self.assertRaisesRegex(SourceNumberError, 'context'):
            n.add(n.one, SourceNumbers().one)

    def test_budget_failure_does_not_expose_or_commit_a_half_Tj(self):
        n = SourceNumbers(max_operations=100)
        _, state = cursor(n)
        state.apply('BT', []); state.apply('Tf', ['F', 12])
        before = state.tm, state.glyph_count, state.show_count
        with self.assertRaisesRegex(SourceNumberError, 'budget'):
            state.apply('Tj', [[21, 19, 21]])
        self.assertEqual((state.tm, state.glyph_count, state.show_count), before)
        self.assertLessEqual(n.operations, n.max_operations)

    def test_empty_shows_preserve_numbering_and_inventory_limit_rejects_up_front(self):
        n, state = cursor()
        state.apply('BT', []); state.apply('Tf', ['F', 12])
        matrix = state.tm
        self.assertEqual(state.apply('Tj', [[]]), [])
        self.assertEqual((state.tm, state.glyph_count, state.show_count), (matrix, 0, 1))
        with self.assertRaisesRegex(SourceNumberError, 'over_budget'):
            state.apply('Tj', [[21] * 25001])
        self.assertEqual((state.tm, state.glyph_count, state.show_count), (matrix, 0, 1))


if __name__ == '__main__':
    unittest.main()
