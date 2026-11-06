"""Keep large partial-holding evidence content-addressed outside the manifest."""
import json
import re
from copy import deepcopy
from .disclosure_amounts import is_open_amount as _is_open
from .codec import encode, digest

REFERENCE_SCHEMA = 'holding-report-coverage-reference/v1'
MAX_BYTES = 4 * 1024 * 1024


def attach(manifest, files):
    coverage = manifest.get('holding_report_coverage')
    if coverage is None or len(encode(manifest)) <= 16 * 1024:
        return
    raw = encode(coverage)
    if not isinstance(coverage, list) or len(raw) > MAX_BYTES:
        raise ValueError('Holding coverage exceeds its public size budget')
    sha = digest(raw)
    files['status/'+sha+'.json'] = raw
    manifest['holding_report_coverage'] = {'schema_version':REFERENCE_SCHEMA,
        'sha256':sha,'byte_length':len(raw)}


def restore(manifest, read):
    ref = manifest.get('holding_report_coverage')
    if not isinstance(ref, dict):
        return
    if (set(ref) != {'schema_version','sha256','byte_length'} or ref['schema_version'] != REFERENCE_SCHEMA or
            not isinstance(ref['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',ref['sha256']) or
            type(ref['byte_length']) is not int or not 0 < ref['byte_length'] <= MAX_BYTES):
        raise ValueError('Invalid holding coverage reference')
    raw = read('status/'+ref['sha256']+'.json',MAX_BYTES)
    if len(raw) != ref['byte_length'] or digest(raw) != ref['sha256']:
        raise ValueError('Holding coverage byte length or hash mismatch')
    coverage = json.loads(raw)
    if not isinstance(coverage,list):
        raise ValueError('Holding coverage must be an array')
    manifest['holding_report_coverage'] = coverage


def validate_metadata(payload):
    coverages = payload["meta"].get("holding_report_coverage")
    if coverages is not None:
        if not isinstance(coverages, list):
            raise ValueError("Holding report coverage must be a list")
        visible = {p["id"] for p in payload["people"]}
        checked, seen = [], set()
        for coverage in coverages:
            if not isinstance(coverage, dict):
                raise ValueError("Holding report coverage must be an object")
            person = coverage.get("person_id")
            if person in seen:
                raise ValueError("Duplicate holding report coverage person")
            seen.add(person)
            if person not in visible:
                continue
            # A ticker selection contains only a subset of this person's
            # holdings. Never label its subtotal as the whole report's total.
            filtered = payload["meta"].get("selection_scope", {}).get("mode") == "ticker"
            rows = [r for r in payload["reported_holdings"] if r["person_id"] == person]
            if coverage.get("schema_version") == "oge-approved-partial-holdings/v1":
                rows = [r for r in rows if r["report_period_end"] == coverage.get("report_period_end")]
            ids = coverage.get("published_holding_ids")
            excluded = coverage.get("excluded_row_ids")
            unknown = coverage.get("unresolved_rows")
            oge = coverage.get("schema_version") == "oge-approved-partial-holdings/v1"
            expected_source = "oge" if oge else "house_clerk"
            expected_basis = ("complete_holding_sections_explicit_not_readily_ascertainable/v1" if oge else
                              "complete_schedule_a_explicit_undetermined_value/v1")
            if (coverage.get("schema_version") not in {"house-approved-partial-holdings/v1", "oge-approved-partial-holdings/v1"} or
                    coverage.get("source_id") != expected_source or
                    not re.fullmatch(r"[0-9a-f]{64}",str(coverage.get("source_sha256", ""))) or
                    coverage.get("selection_basis") != expected_basis or
                    coverage.get("scope") != "partial_report" or coverage.get("complete_total_assets") is not False or
                    not isinstance(ids,list) or not isinstance(excluded,list) or not isinstance(unknown,list) or not unknown or
                    any(not isinstance(i,str) for i in ids+excluded) or len(ids)!=len(set(ids)) or
                    (not set(r["id"] for r in rows) <= set(ids) if filtered else
                     sorted(ids) != sorted(r["id"] for r in rows) or not rows) or
                    any(r["filing_id"] != coverage.get("filing_id") or
                        r["source_id"] != coverage.get("source_id") or
                        r["source_url"] != coverage.get("source_url") or
                        r["report_period_end"] != coverage.get("report_period_end") for r in rows)):
                raise ValueError("Partial holding coverage differs from published report")
            unknown_ids = []
            for row in unknown:
                if (not isinstance(row,dict) or not isinstance(row.get("extraction_id"),str) or
                        not isinstance(row.get("asset_name"),str) or not row["asset_name"].strip() or
                        not isinstance(row.get("value_raw"),str) or row["value_raw"].strip().casefold() !=
                            ("value not readily ascertainable" if oge else "undetermined") or
                        not row.get("reasons") or not set(row["reasons"]) <=
                            ({"holding_value_missing"} if oge else {"value_not_representable","value_bounds_unresolved"}) or
                        (oge and ("value not readily ascertainable" not in row["asset_name"].casefold() or
                                  type(row.get("page_number")) is not int or row["page_number"] < 1 or
                                  not isinstance(row.get("printed_row_number"), str) or not row["printed_row_number"]))):
                    raise ValueError("Partial holding unresolved value has no explicit source literal")
                unknown_ids.append(row["extraction_id"])
            opened = coverage.get('excluded_open_holding_ids', [])
            receipts = payload['meta'].get('known_gaps', {}).get('excluded_open_amounts', {}).get('reported_holdings', [])
            authorized = {i for group in receipts if group['person_id'] == person and
                          group['filing_id'] == coverage['filing_id'] for i in group['record_ids']}
            if not isinstance(opened, list) or any(i not in authorized for i in opened):
                raise ValueError('Excluded open holding coverage lacks its original threshold receipt')
            all_ids = ids + excluded + unknown_ids + opened
            bounded = [r for r in rows if not _is_open(r,"value")]
            if (len(all_ids)!=len(set(all_ids)) or type(coverage.get("observed_row_count")) is not int or
                    coverage.get("observed_row_count")!=len(all_ids) or
                    type(coverage.get("finite_value_low")) is not int or
                    type(coverage.get("finite_value_high")) is not int or
                    not 0 <= coverage["finite_value_low"] <= coverage["finite_value_high"] or
                    (not filtered and (coverage["finite_value_low"] != sum(r["value_low"] for r in bounded) or
                     coverage["finite_value_high"] != sum(r["value_high"] for r in bounded)))):
                raise ValueError("Partial holding row partition or finite totals differ")
            if not filtered:
                checked.append(deepcopy(coverage))
        return checked
