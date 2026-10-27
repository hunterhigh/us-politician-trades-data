"""Finite numeric replay for the horizontal native-ID source profile.

These numbers describe an engine-specific placement, not normative PDF real
arithmetic, visible ink, font semantics, or a qualified transaction. The caller
must verify the engine pin, source grammar, font metrics and paint context.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
import math
import re
import struct


class SourceNumberError(ValueError):
    pass


_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z")
_INTEGER = re.compile(r"[+-]?[0-9]+\Z")
_MIN_NORMAL = Fraction(1, 1 << 126)
_MAX_FINITE = Fraction((1 << 24) - 1) * (1 << 104)


def _bits(value: float) -> int:
    return struct.unpack(">I", struct.pack(">f", value))[0]


def _from_bits(value: int) -> float:
    return struct.unpack(">f", struct.pack(">I", value))[0]


def _next(value: float, up: bool) -> float:
    if value == 0:
        return _from_bits(1 if up else 0x80000001)
    return _from_bits(_bits(value) + (1 if (value > 0) == up else -1))


@dataclass(frozen=True)
class Float32Interval:
    point: Fraction
    lower: Fraction
    upper: Fraction
    _owner: object = field(repr=False, compare=False)

    def as_dict(self) -> dict:
        return {"point": str(self.point), "lower": str(self.lower), "upper": str(self.upper)}


class SourceNumbers:
    """Bounded binary32 replay with exact directed rational enclosures.

    Each conversion, arithmetic primitive or integer-metric decision consumes
    one operation. Each arithmetic primitive performs at most three bounded
    binary32 brackets; those brackets are counted separately for diagnostics.
    No interval width depends on a reference reader or an acceptance epsilon.
    """

    def __init__(self, *, max_operations: int = 200_000):
        if type(max_operations) is not int or not 1 <= max_operations <= 1_000_000:
            raise SourceNumberError("numeric_operation_budget_invalid")
        self.max_operations = max_operations
        self.operations = 0
        self.rounding_brackets = 0
        self._owner = object()
        self.zero = Float32Interval(Fraction(0), Fraction(0), Fraction(0), self._owner)
        self.one = Float32Interval(Fraction(1), Fraction(1), Fraction(1), self._owner)

    def _charge(self) -> None:
        if self.operations >= self.max_operations:
            raise SourceNumberError("numeric_operation_budget_exceeded")
        self.operations += 1

    def _literal(self, value) -> Fraction:
        if type(value) is int:
            if value.bit_length() > 256:
                raise SourceNumberError("numeric_literal_size_exceeded")
            return Fraction(value)
        if isinstance(value, Fraction):
            if max(value.numerator.bit_length(), value.denominator.bit_length()) > 512:
                raise SourceNumberError("numeric_fraction_size_exceeded")
            return value
        if isinstance(value, str) and len(value) <= 128 and _DECIMAL.fullmatch(value):
            return Fraction(value)
        raise SourceNumberError("exact_source_number_required")

    @staticmethod
    def _normal(value: Fraction) -> None:
        if abs(value) > _MAX_FINITE:
            raise SourceNumberError("binary32_overflow")
        if value and abs(value) < _MIN_NORMAL:
            raise SourceNumberError("binary32_subnormal_unsupported")

    def _bracket(self, exact: Fraction) -> tuple[Fraction, Fraction]:
        self.rounding_brackets += 1
        if self.rounding_brackets > 3 * self.max_operations:
            raise SourceNumberError("numeric_rounding_budget_exceeded")
        self._normal(exact)
        try:
            anchor = struct.unpack(">f", struct.pack(">f", float(exact)))[0]
        except (OverflowError, struct.error) as exc:
            raise SourceNumberError("binary32_overflow") from exc
        if not math.isfinite(anchor):
            raise SourceNumberError("binary32_overflow")
        represented = Fraction.from_float(anchor)
        if represented == exact:
            return represented, represented
        low, high = ((anchor, _next(anchor, True)) if represented < exact else
                     (_next(anchor, False), anchor))
        if not math.isfinite(low) or not math.isfinite(high):
            raise SourceNumberError("binary32_overflow")
        lower, upper = Fraction.from_float(low), Fraction.from_float(high)
        # The rational check makes a double-rounded float64 anchor harmless:
        # its adjacent binary32 endpoints must really enclose the exact value.
        if not lower <= exact <= upper:
            raise SourceNumberError("binary32_bracket_unproved")
        self._normal(lower)
        self._normal(upper)
        return lower, upper

    @staticmethod
    def _nearest(exact: Fraction, lower: Fraction, upper: Fraction) -> Fraction:
        if lower == upper:
            return lower
        left, right = exact - lower, upper - exact
        if left < right:
            return lower
        if right < left:
            return upper
        return lower if _bits(float(lower)) % 2 == 0 else upper

    def _interval(self, point, lower, upper) -> Float32Interval:
        if lower > upper or not lower <= point <= upper:
            raise SourceNumberError("binary32_interval_invalid")
        # A continuous uncertain interval that reaches zero can also contain
        # subnormal outcomes. Do not silently flush those candidates to zero.
        if lower != upper and lower <= 0 <= upper:
            raise SourceNumberError("binary32_subnormal_candidates_unsupported")
        return Float32Interval(point, lower, upper, self._owner)

    def number(self, value) -> Float32Interval:
        if isinstance(value, Float32Interval):
            return self._value(value)
        self._charge()
        exact = self._literal(value)
        lower, upper = self._bracket(exact)
        return self._interval(self._nearest(exact, lower, upper), lower, upper)

    def _value(self, value: Float32Interval) -> Float32Interval:
        if not isinstance(value, Float32Interval) or value._owner is not self._owner:
            raise SourceNumberError("numeric_context_mismatch")
        return value

    def _result(self, exact, lower, upper) -> Float32Interval:
        a, b = self._bracket(exact)
        lo = self._bracket(lower)[0]
        hi = self._bracket(upper)[1]
        return self._interval(self._nearest(exact, a, b), lo, hi)

    def add(self, left, right) -> Float32Interval:
        left, right = self._value(left), self._value(right)
        self._charge()
        return self._result(left.point + right.point, left.lower + right.lower,
                            left.upper + right.upper)

    def multiply(self, left, right) -> Float32Interval:
        left, right = self._value(left), self._value(right)
        self._charge()
        candidates = (left.lower * right.lower, left.lower * right.upper,
                      left.upper * right.lower, left.upper * right.upper)
        return self._result(left.point * right.point, min(candidates), max(candidates))

    def identity(self) -> tuple[Float32Interval, ...]:
        return (self.one, self.zero, self.zero, self.one, self.zero, self.zero)

    def matrix(self, values) -> tuple[Float32Interval, ...]:
        if not isinstance(values, (list, tuple)) or len(values) != 6:
            raise SourceNumberError("matrix_arity_invalid")
        return tuple(self.number(value) for value in values)

    def _matrix(self, value):
        if not isinstance(value, tuple) or len(value) != 6:
            raise SourceNumberError("matrix_arity_invalid")
        for number in value:
            self._value(number)
        return value

    def compose(self, outer, inner) -> tuple[Float32Interval, ...]:
        """Apply inner then outer; source cm becomes compose(current, new)."""
        a, b, c, d, e, f = self._matrix(outer)
        A, B, C, D, E, G = self._matrix(inner)
        mul, add = self.multiply, self.add
        pair = lambda x, y, z, w: add(mul(x, y), mul(z, w))
        return (pair(a, A, c, B), pair(b, A, d, B), pair(a, C, c, D),
                pair(b, C, d, D), add(pair(a, E, c, G), e), add(pair(b, E, d, G), f))

    def pre_translate(self, matrix, x, y) -> tuple[Float32Interval, ...]:
        a, b, c, d, e, f = self._matrix(matrix)
        x, y = self._value(x), self._value(y)
        return (a, b, c, d,
                self.add(e, self.add(self.multiply(x, a), self.multiply(y, c))),
                self.add(f, self.add(self.multiply(x, b), self.multiply(y, d))))

    def transform_point(self, matrix, x, y) -> tuple[Float32Interval, Float32Interval]:
        a, b, c, d, e, f = self._matrix(matrix)
        x, y = self._value(x), self._value(y)
        return (self.add(self.add(self.multiply(x, a), self.multiply(y, c)), e),
                self.add(self.add(self.multiply(x, b), self.multiply(y, d)), f))

    def engine_width(self, literal: int | str) -> int:
        """Preserve PDF integer/real kinds for MuPDF 1.28.2 CID metrics."""
        # https://raw.githubusercontent.com/ArtifexSoftware/mupdf/1.28.2/source/pdf/pdf-object.c
        self._charge()
        if isinstance(literal, str) and len(literal) > 128:
            raise SourceNumberError("numeric_literal_size_exceeded")
        if type(literal) is int or isinstance(literal, str) and _INTEGER.fullmatch(literal):
            value = self._literal(literal)
            if not -(1 << 31) <= value < (1 << 31):
                raise SourceNumberError("integer_metric_overflow")
            return int(value)
        if not isinstance(literal, str):
            raise SourceNumberError("PDF_width_literal_kind_required")
        source = self.number(literal)
        shifted = self.add(source, self.number("0.5"))
        low, high = math.floor(shifted.lower), math.floor(shifted.upper)
        if low != high:
            raise SourceNumberError("integer_metric_rounding_ambiguous")
        if not -(1 << 31) <= low < (1 << 31):
            raise SourceNumberError("integer_metric_overflow")
        return low


@dataclass(frozen=True)
class GlyphPosition:
    glyph_index: int
    show_index: int
    cid: int
    font_resource: str
    origin: tuple[Float32Interval, Float32Interval]
    text_advance: Float32Interval
    text_matrix: tuple[Float32Interval, ...]
    render_matrix: tuple[Float32Interval, ...]
    ctm: tuple[Float32Interval, ...]


class HorizontalTextState:
    """Pure finite q/Q/cm/BT/ET/Tf/Tm/Td/Tj placement dispatcher.

    Tj operands are one already-decoded CID sequence: ``[[cid, ...]]``.
    Font metrics are ``{resource: {"widths": {cid: raw_int_or_decimal_string},
    "default_width": raw_int_or_decimal_string}}``. Encoding, Unicode, font
    semantics, all other operators and graphics-state effects belong to the
    caller's closed source profile. Unsupported text-state changes cannot be
    ignored through this dispatcher; horizontal scale=1, rise/spacing=0.
    """

    def __init__(self, numbers: SourceNumbers, *, ctm=None, font_metrics=None):
        if not isinstance(numbers, SourceNumbers):
            raise SourceNumberError("numeric_context_required")
        self.numbers = numbers
        self.ctm = numbers.identity() if ctm is None else numbers._matrix(ctm)
        self.tm = self.tlm = numbers.identity()
        self.font_resource = None
        self.font_size = numbers.zero
        self.inside_text = False
        self._stack = []
        self.glyph_count = self.show_count = 0
        if not isinstance(font_metrics, dict) or not 1 <= len(font_metrics) <= 32:
            raise SourceNumberError("font_metric_inventory_invalid")
        self.font_metrics = {}
        for name, metrics in font_metrics.items():
            numbers._charge()
            if (not isinstance(name, str) or not 1 <= len(name) <= 128 or
                    not isinstance(metrics, dict) or set(metrics) - {"widths", "default_width"} or
                    not isinstance(metrics.get("widths"), dict) or len(metrics["widths"]) > 65536):
                raise SourceNumberError("font_metric_inventory_invalid")
            for cid in metrics["widths"]:
                numbers._charge()
                if type(cid) is not int or not 0 <= cid <= 65535:
                    raise SourceNumberError("CID_metric_invalid")
            self.font_metrics[name] = {"widths": dict(metrics["widths"]),
                                       "default_width": metrics.get("default_width", 1000)}

    def apply(self, operator: str, operands) -> list[GlyphPosition]:
        n = self.numbers
        n._charge()
        arity = {"q": 0, "Q": 0, "cm": 6, "BT": 0, "ET": 0,
                 "Tf": 2, "Tm": 6, "Td": 2, "Tj": 1}
        if not isinstance(operator, str) or operator not in arity:
            raise SourceNumberError("unsupported_position_operator")
        if not isinstance(operands, (list, tuple)) or len(operands) != arity[operator]:
            raise SourceNumberError("position_operator_arity_invalid")
        if operator in {"q", "Q", "cm"} and self.inside_text:
            raise SourceNumberError("graphics_operator_inside_text_unsupported")
        if operator == "q":
            if len(self._stack) >= 32:
                raise SourceNumberError("position_stack_budget_exceeded")
            self._stack.append((self.ctm, self.font_resource, self.font_size))
        elif operator == "Q":
            if not self._stack:
                raise SourceNumberError("position_stack_underflow")
            self.ctm, self.font_resource, self.font_size = self._stack.pop()
        elif operator == "cm":
            self.ctm = n.compose(self.ctm, n.matrix(operands))
        elif operator == "BT":
            if self.inside_text:
                raise SourceNumberError("nested_text_object")
            self.tm = self.tlm = n.identity()
            self.inside_text = True
        elif operator == "ET":
            if not self.inside_text:
                raise SourceNumberError("text_object_underflow")
            self.inside_text = False
        else:
            if not self.inside_text:
                raise SourceNumberError("position_operator_outside_text")
            if operator == "Tf":
                name, size = operands
                if not isinstance(name, str) or name not in self.font_metrics:
                    raise SourceNumberError("font_resource_unavailable")
                size = n.number(size)
                if size.lower <= 0:
                    raise SourceNumberError("font_size_unsupported")
                self.font_resource, self.font_size = name, size
            elif operator == "Tm":
                self.tm = self.tlm = n.matrix(operands)
            elif operator == "Td":
                matrix = n.pre_translate(self.tlm, n.number(operands[0]), n.number(operands[1]))
                self.tm = self.tlm = matrix
            elif operator == "Tj":
                return self._show(operands[0])
        return []

    def _show(self, cids) -> list[GlyphPosition]:
        n = self.numbers
        if not isinstance(cids, (list, tuple)) or self.glyph_count + len(cids) > 25_000:
            raise SourceNumberError("CID_show_inventory_invalid_or_over_budget")
        for cid in cids:
            n._charge()
            if type(cid) is not int or not 0 <= cid <= 65535:
                raise SourceNumberError("CID_show_inventory_invalid_or_over_budget")
        if self.font_resource is None:
            raise SourceNumberError("font_resource_unavailable")
        metrics = self.font_metrics[self.font_resource]
        result, matrix = [], self.tm
        for cid in cids:
            width = n.engine_width(metrics["widths"].get(cid, metrics["default_width"]))
            w0 = n.multiply(n.number(width), n.number("0.001"))
            advance = n.multiply(n.add(n.multiply(w0, self.font_size), n.zero), n.one)
            scale = (n.multiply(self.font_size, n.one), n.zero, n.zero,
                     self.font_size, n.zero, n.zero)
            render = n.compose(matrix, scale)
            origin = n.transform_point(self.ctm, render[4], render[5])
            result.append(GlyphPosition(self.glyph_count + len(result), self.show_count + 1,
                cid, self.font_resource, origin, advance, matrix, render, self.ctm))
            matrix = n.pre_translate(matrix, advance, n.zero)
        # Never expose a half-show or update a cursor on numeric failure.
        self.tm = matrix
        self.glyph_count += len(cids)
        self.show_count += 1
        return result

    def finish(self) -> None:
        if self.inside_text or self._stack:
            raise SourceNumberError("unclosed_position_state")
