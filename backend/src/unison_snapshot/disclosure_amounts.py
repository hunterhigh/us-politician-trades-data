"""User-approved separation of bounded amounts and disclosed open thresholds."""
from collections import Counter

POLICY = "bounded-and-open-separate/v1"


def is_open_amount(row: dict, prefix: str = "amount") -> bool:
    return prefix + "_high" in row and row[prefix + "_high"] is None and type(row.get(prefix + "_low_exclusive")) is bool


def validate_amount(row: dict, prefix: str = "amount", *, allow_open: bool = True) -> None:
    low, high = row.get(prefix + "_low"), row.get(prefix + "_high")
    if type(low) is not int or low < 0:
        raise ValueError("Disclosure amount lower value must be an explicit nonnegative integer")
    if is_open_amount(row, prefix):
        if not allow_open:
            raise ValueError("Open holding values require their own approved policy")
        return
    if type(high) is not int or high < low or prefix + "_low_exclusive" in row:
        raise ValueError("Disclosure amount must be bounded or have an explicit open threshold")


def summarize_amounts(rows: list[dict], prefix: str = "amount") -> dict:
    """Finite sums exclude open amounts, while counts and thresholds retain them."""
    bounded, opened = [], []
    for row in rows:
        validate_amount(row, prefix)
        (opened if is_open_amount(row, prefix) else bounded).append(row)
    result = {prefix + "_low": sum(row[prefix + "_low"] for row in bounded),
              prefix + "_high": sum(row[prefix + "_high"] for row in bounded)}
    if opened:
        groups = Counter((row[prefix + "_low"], row[prefix + "_low_exclusive"]) for row in opened)
        result.update({prefix + "_bounded_count": len(bounded), prefix + "_open_count": len(opened),
                       prefix + "_open_thresholds": [
                           {"minimum": floor, "minimum_exclusive": exclusive, "count": count}
                           for (floor, exclusive), count in sorted(groups.items())]})
    return result
