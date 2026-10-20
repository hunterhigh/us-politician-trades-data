"""Extend the frozen v2 consumer with approved, separately reported open amounts.

v1/v2 remain immutable for old snapshot reading and recovery. This module loads
its own private v2 instance; no shared consumer module is modified.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
import math
import re
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


def _transaction_relation_identity(relation: dict) -> tuple[str, str]:
    """Validate the original locator without inventing printed row numbers."""
    common = {'transaction_id','source_id','filing_id','source_url','source_sha256',
              'section','status','possible_ptr_transaction_ids'}
    sha = relation.get('source_sha256')
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha):
        raise ProcessingError('Transaction disclosure relation source hash differs')
    if relation.get('source_id') == 'senate_efd':
        number = relation.get('printed_row_number')
        if (set(relation) != common | {'printed_row_number'} or relation.get('section') != 'Part 4b' or
                not isinstance(number, str) or not re.fullmatch(r'[1-9]\d*', number)):
            raise ProcessingError('Senate annual disclosure relation locator differs')
        return 'senate-annual-part4b:' + hashlib.sha256(f'{sha}:part4b:{number}'.encode()).hexdigest()[:24], 'senate-ptr:'
    if relation.get('source_id') == 'house_clerk':
        page, top = relation.get('page_number'), relation.get('top_points')
        if (set(relation) != common | {'page_number','top_points'} or relation.get('section') != 'Schedule B' or
                type(page) is not int or not 1 <= page <= 1200 or
                type(top) not in (int, float) or not math.isfinite(top) or
                not 0 <= top <= 20000 or round(top, 2) != top):
            raise ProcessingError('House annual disclosure relation locator differs')
        return 'house-annual-b:' + hashlib.sha256(f'{sha}:{page}:{top:.2f}'.encode()).hexdigest()[:24], 'house-ptr:'
    raise ProcessingError('Unsupported annual disclosure relation source')


def build_snapshot(payload):
    result = _BASE.build_snapshot(payload)
    relations=result['meta'].get('transaction_disclosure_relations')
    annual_ids={r['id'] for r in result['transactions'] if str(r['id']).startswith(('senate-annual-part4b:','house-annual-b:'))}
    if annual_ids and relations is None:raise ProcessingError('Annual disclosure facts require complete relationship metadata')
    if relations is not None:
        if not isinstance(relations,list):raise ProcessingError('Transaction disclosure relations must be a list')
        facts={r['id']:r for r in result['transactions']};checked=[];seen=set()
        subset=result['meta'].get('selection_scope',{}).get('mode') in {'person','ticker','people','tickers'}
        for relation in relations:
            if not isinstance(relation,dict):raise ProcessingError('Transaction disclosure relation must be an object')
            expected, ptr_prefix = _transaction_relation_identity(relation)
            key=relation['transaction_id']
            if key != expected or key in seen:
                raise ProcessingError('Transaction disclosure relation source identity differs')
            seen.add(key);fact=facts.get(key)
            possible=relation['possible_ptr_transaction_ids']
            if (not isinstance(possible,list) or any(not isinstance(i,str) for i in possible) or len(possible)!=len(set(possible)) or
                    relation['status']!=('possible_ptr_overlap' if possible else 'cross_report_relationship_unconfirmed')):
                raise ProcessingError('Transaction disclosure relation status differs')
            if fact is None:
                if subset:continue
                raise ProcessingError('Transaction disclosure relation references missing fact')
            if any(fact.get(k)!=relation[k] for k in ('source_id','filing_id','source_url')):
                raise ProcessingError('Transaction disclosure relation references another source')
            for ptr in possible:
                other=facts.get(ptr)
                if not ptr.startswith(ptr_prefix):raise ProcessingError('Transaction disclosure relation possible PTR ID differs')
                if other is None and subset:continue
                if (other is None or not ptr.startswith(ptr_prefix) or other.get('source_id')!=relation['source_id'] or
                        other.get('person_id')!=fact['person_id']):
                    raise ProcessingError('Transaction disclosure relation possible PTR differs')
            checked.append(deepcopy(relation))
        result['meta']['transaction_disclosure_relations']=checked
        if {r['transaction_id'] for r in checked}!=annual_ids:
            raise ProcessingError('Annual disclosure relationship metadata does not cover loaded annual facts')
    coverages = result["meta"].get("holding_report_coverage")
    if coverages is not None:
        if not isinstance(coverages, list):
            raise ProcessingError("Holding report coverage must be a list")
        visible = {p["id"] for p in result["people"]}
        checked, seen = [], set()
        for coverage in coverages:
            if not isinstance(coverage, dict):
                raise ProcessingError("Holding report coverage must be an object")
            person = coverage.get("person_id")
            if person in seen:
                raise ProcessingError("Duplicate holding report coverage person")
            seen.add(person)
            if person not in visible:
                continue
            # A ticker selection contains only a subset of this person's
            # holdings. Never label its subtotal as the whole report's total.
            filtered = result["meta"].get("selection_scope", {}).get("mode") == "ticker"
            rows = [r for r in result["reported_holdings"] if r["person_id"] == person]
            ids = coverage.get("published_holding_ids")
            excluded = coverage.get("excluded_row_ids")
            unknown = coverage.get("unresolved_rows")
            if (coverage.get("schema_version") != "house-approved-partial-holdings/v1" or
                    coverage.get("source_id") != "house_clerk" or
                    not re.fullmatch(r"[0-9a-f]{64}",str(coverage.get("source_sha256", ""))) or
                    coverage.get("selection_basis") != "complete_schedule_a_explicit_undetermined_value/v1" or
                    coverage.get("scope") != "partial_report" or coverage.get("complete_total_assets") is not False or
                    not isinstance(ids,list) or not isinstance(excluded,list) or not isinstance(unknown,list) or not unknown or
                    any(not isinstance(i,str) for i in ids+excluded) or len(ids)!=len(set(ids)) or
                    (not set(r["id"] for r in rows) <= set(ids) if filtered else
                     sorted(ids) != sorted(r["id"] for r in rows) or not rows) or
                    any(r["filing_id"] != coverage.get("filing_id") or
                        r["source_id"] != coverage.get("source_id") or
                        r["source_url"] != coverage.get("source_url") or
                        r["report_period_end"] != coverage.get("report_period_end") for r in rows)):
                raise ProcessingError("Partial holding coverage differs from published report")
            unknown_ids = []
            for row in unknown:
                if (not isinstance(row,dict) or not isinstance(row.get("extraction_id"),str) or
                        not isinstance(row.get("asset_name"),str) or not row["asset_name"].strip() or
                        not isinstance(row.get("value_raw"),str) or row["value_raw"].strip().casefold() != "undetermined" or
                        not row.get("reasons") or not set(row["reasons"]) <= {"value_not_representable","value_bounds_unresolved"}):
                    raise ProcessingError("Partial holding unresolved value has no explicit source literal")
                unknown_ids.append(row["extraction_id"])
            all_ids = ids + excluded + unknown_ids
            bounded = [r for r in rows if not _is_open(r,"value")]
            if (len(all_ids)!=len(set(all_ids)) or type(coverage.get("observed_row_count")) is not int or
                    coverage.get("observed_row_count")!=len(all_ids) or
                    type(coverage.get("finite_value_low")) is not int or
                    type(coverage.get("finite_value_high")) is not int or
                    not 0 <= coverage["finite_value_low"] <= coverage["finite_value_high"] or
                    (not filtered and (coverage["finite_value_low"] != sum(r["value_low"] for r in bounded) or
                     coverage["finite_value_high"] != sum(r["value_high"] for r in bounded)))):
                raise ProcessingError("Partial holding row partition or finite totals differ")
            if not filtered:
                checked.append(deepcopy(coverage))
        result["meta"]["holding_report_coverage"] = checked
        for person in result["priority_people"]:
            coverage = next((c for c in checked if c["person_id"]==person["person"]["id"]),None)
            if coverage is not None:
                person["holding_report_coverage"] = deepcopy(coverage)
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
