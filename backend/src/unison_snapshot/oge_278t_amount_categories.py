"""Amount labels on the OGE 278-T February 2024 Excel form.

Source: https://www.oge.gov/Web/OGE.nsf/0/C1D046290312C7C7852585B6005A204F/
        $FILE/OGE%20Form%20278-T%20Dec%202023.xlsx
SHA-256: 024564b6133c5ffee365b41b66c19f1948fbc64e3098908b7eee489a5c36b417
Workbook name `Amount` points to `Drop-Down Lists!F2:F12`. These labels are a
format clue, never an OCR correction or proof of the amount a filer selected.
"""

from __future__ import annotations

import re

from .oge_278t_field_shadow import _amount_shape


_BOUNDED = {
    (1001, 15000), (15001, 50000), (50001, 100000),
    (100001, 250000), (250001, 500000), (500001, 1000000),
    (1000001, 5000000), (5000001, 25000000),
    (25000001, 50000000),
}
_OPEN = re.compile(r"^\s*(?:yes|no)?\s*[|:]?\s*over\s+"
                   r"\$\s*(1,000,000|50,000,000)\s*$", re.I)


def observe_template_amount(raw: str) -> dict:
    """Classify an OCR string against template choices without inventing values."""
    open_match = _OPEN.fullmatch(raw)
    if open_match:
        floor = int(open_match[1].replace(",", ""))
        return {"status": "open_upper_template_shape", "minimum_exclusive": floor,
                "lower": None, "upper": None}
    shape = _amount_shape(raw)
    values = shape["values"]
    if values is None:
        return {"status": "unknown", "lower": None, "upper": None,
                "minimum_exclusive": None}
    return {"status": ("bounded_template_numeric_shape" if tuple(values) in _BOUNDED
                       else "non_template_numeric_shape"),
            "lower": values[0], "upper": values[1],
            "minimum_exclusive": None}
