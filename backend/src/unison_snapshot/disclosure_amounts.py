"""User-approved separation of bounded amounts and disclosed open thresholds."""

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


