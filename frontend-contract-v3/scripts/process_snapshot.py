"""Extend the frozen v2 consumer with approved, separately reported open amounts.

v1/v2 remain immutable for old snapshot reading and recovery. This module loads
its own private v2 instance; no shared consumer module is modified.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "frontend-contract-v2/scripts/process_snapshot.py"
_SHA = "746e60177d65dc40504cb0bbd752a28c510627be6039e724b112ed6d46495c15"
if hashlib.sha256(_PATH.read_bytes()).hexdigest() != _SHA:
    raise ValueError("Frozen v2 amount consumer changed")
_SPEC = importlib.util.spec_from_file_location("_private_v2_for_open_amounts", _PATH)
_BASE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BASE)
ProcessingError = _BASE.ProcessingError
_PREPARE = _BASE.prepare_disclosure_rows
_POLICY = "bounded-and-open-separate/v1"


def _is_open(row, prefix="amount"):
    return prefix + "_high" in row and row[prefix + "_high"] is None and type(row.get(prefix + "_low_exclusive")) is bool


def _prepare(rows, people_by_id, *, kind, demo):
    prefix = "amount" if kind == "transaction" else "value"
    envelopes, opened_flags = [], []
    for source in rows:
        opened = _is_open(source, prefix)
        row = deepcopy(source)
        if opened:
            low = row.get(prefix + "_low")
            if type(low) is not int or low < 0:
                raise ProcessingError("Open amount requires an explicit nonnegative threshold")
            # Only the inherited identity/date/source validation sees a finite
            # validation envelope. The output always restores the absent bound.
            row[prefix + "_high"] = low
        elif prefix + "_low_exclusive" in row:
            raise ProcessingError("Open threshold marker requires an absent upper bound")
        if not opened:
            low, high = row.get(prefix + "_low"), row.get(prefix + "_high")
            if type(low) is not int or type(high) is not int or not 0 <= low <= high:
                raise ProcessingError("Missing or invalid bounded disclosure values")
        envelopes.append(row)
        opened_flags.append(opened)
    prepared = _PREPARE(envelopes, people_by_id, kind=kind, demo=demo)
    for normalized, opened in zip(prepared, opened_flags, strict=True):
        if opened:
            normalized[prefix + "_high"] = None
    return prepared


_BASE.prepare_disclosure_rows = _prepare


def _amount_summary(rows, prefix="amount"):
    bounded = [r for r in rows if not _is_open(r, prefix)]
    opened = [r for r in rows if _is_open(r, prefix)]
    result = {prefix + "_low": sum(r[prefix + "_low"] for r in bounded),
              prefix + "_high": sum(r[prefix + "_high"] for r in bounded)}
    if opened:
        result.update({prefix + "_bounded_count": len(bounded), prefix + "_open_count": len(opened),
                       prefix + "_open_records": [{"id": r["id"], "minimum": r[prefix + "_low"],
                                                   "minimum_exclusive": r[prefix + "_low_exclusive"]} for r in opened]})
    return result


def build_snapshot(payload):
    result = _BASE.build_snapshot(payload)
    rows = result["transactions"]
    if not (any(_is_open(r) for r in rows) or
            any(_is_open(r, "value") for r in result["reported_holdings"])):
        return result
    cutoff = _BASE.cutoff_date(result["meta"], rows, result["security_market_data"])
    thirty = _BASE.rows_in_window(rows, cutoff, 30, "transaction_date")
    summary = _amount_summary(thirty)
    result["summary"].update({key + "_30d": value for key, value in summary.items()})
    for group in result["top_tickers"]:
        group.update(_amount_summary([r for r in thirty if r.get("ticker") == group["ticker"]]))
    for days, groups in result["market_moves"].items():
        window = _BASE.rows_in_window(rows, cutoff, int(days), "transaction_date")
        for group in groups:
            group.update(_amount_summary([r for r in window if r.get("ticker") == group["ticker"]]))
    for person in result["priority_people"]:
        latest = [r for r in result["reported_holdings"]
                  if r["person_id"] == person["person"]["id"]
                  and r["report_period_end"] == person["latest_report_period_end"]]
        if any(_is_open(r, "value") for r in latest):
            values = _amount_summary(latest, "value")
            person.update({"reported_" + key: value for key, value in values.items()})
            person["top_holdings"] = sorted(
                [r for r in latest if not _is_open(r, "value")],
                key=lambda r: (r["value_high"], r["value_low"], r["id"]), reverse=True)[:5]
            person["open_holdings"] = [r for r in latest if _is_open(r, "value")]
    result["meta"]["amount_policy"] = _POLICY
    result["processing"]["amount_policy"] = _POLICY
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = build_snapshot(json.loads(args.input.read_text(encoding="utf-8")))
    text = json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
