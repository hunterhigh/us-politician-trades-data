"""Source-bound security enrichment from reviewed reference data."""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import re

from .alpaca_market import (
    SIP_EXCHANGES, TICKER, _asset_registry, _recover_unique_asset_name_tickers,
)
from .codec import encode


SEMANTIC_BASIS = "alpaca_source_bound_semantic_alias"
PRIOR_EXACT_NAME_BASIS = "prior_trump_exact_asset_name"
SOURCE_DIRECTORY_BASIS = "official_source_row_security_directory"
ALLOWED_BASES = {"alpaca_unique_asset_name", "alpaca_unique_classless_asset_name",
                 SEMANTIC_BASIS, PRIOR_EXACT_NAME_BASIS, SOURCE_DIRECTORY_BASIS}
SOURCE_DIRECTORY_AMBIGUITY_GUARD = (
    "fixed_official_row+single_security_directory_symbol+"
    "single_active_sip_asset+no_debt_or_concatenated_asset")


DEBT_LABEL = re.compile(
    r"\b(?:BOND|BONDS|BDS|NOTE|NOTES|NTS|DEBENTURE|TREASURY|MUNICIPAL|"
    r"MUNI|DUE|DTD|YTM|COUPON|REFUNDING|RFDG)\b|\bB/E\b|%|@",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def _rules():
    path = Path(__file__).resolve().parents[2] / 'data/reference_mappings/security_tickers.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('schema_version') != 'reviewed-security-ticker-rules/v1':
        raise ValueError('Unsupported reviewed security ticker rules')
    if (set(value['source_aliases']) != set(value['source_alias_reports']) or
            set(value['source_alias_reports'].values()) - set(value['reports']) or
            set(value['prior_name_target_reports']) - set(value['reports'])):
        raise ValueError('Reviewed security ticker source scopes are inconsistent')
    return value


def _source_alias_url(record_id):
    return _rules()['reports'][_rules()['source_alias_reports'][record_id]]['source_url']


def _false_explicit_value(document, name):
    return _rules()['false_explicit'].get(document, {}).get(name)


def _source_row(row: dict) -> bool:
    profile = _rules()['reports'].get(row.get("filing_id"))
    return (profile is not None and
            str(row.get("id") or "").startswith("oge-278t:") and
            row.get("person_id") == _rules()['person_id'] and
            row.get("source_id") == "oge" and
            row.get("verification_status") == "official_matched" and
            row.get("source_url") == profile["source_url"] and
            row.get("filed_at") == profile["report_date"] + "T00:00:00Z")


def _false_explicit(row: dict) -> bool:
    return (_source_row(row) and
            row.get("ticker") == _false_explicit_value(row.get('filing_id'), row.get('asset_name')) and
            row.get("ticker_mapping_basis") == "filing_explicit")


def _eligible_name(row: dict) -> bool:
    if not _source_row(row):
        return False
    asset_name = str(row.get("asset_name") or "")
    return (row.get("instrument_type") in {"Stock", "ETF", "Unspecified"} and
            not DEBT_LABEL.search(asset_name))


def _semantic_rule_id(asset_name: str) -> str:
    return "semantic:" + hashlib.sha256(asset_name.encode("utf-8")).hexdigest()[:16]


def _source_directory_rule_id(record_id: str, asset_name: str, ticker: str,
                              page_number: int, row_number: int,
                              transaction_date: str, directory_url: str) -> str:
    evidence = "\x00".join((record_id, asset_name, ticker, str(page_number),
                              str(row_number), transaction_date,
                              _source_alias_url(record_id),
                              directory_url, _rules()['directory_checked_on']))
    return "source-directory:" + hashlib.sha256(
        evidence.encode("utf-8")).hexdigest()[:16]


def _apply_semantic_aliases(proposed: dict, assets: list[dict],
                            ambiguous_ids: set[str]) -> list[dict]:
    registry = _asset_registry(assets)
    recovered = []
    for row in proposed["transactions"]:
        if (not _eligible_name(row) or row.get("ticker") or
                row["id"] in ambiguous_ids):
            continue
        rule = _rules()['semantic_aliases'].get(row["asset_name"])
        if rule is None:
            continue
        ticker, evidence_url = rule
        asset = registry.get(ticker)
        if (asset is None or asset["status"] != "active" or
                asset["exchange"] not in SIP_EXCHANGES):
            continue
        row["ticker"] = ticker
        row["ticker_mapping_basis"] = SEMANTIC_BASIS
        recovered.append({
            "record_id": row["id"], "asset_name": row["asset_name"],
            "ticker": ticker, "mapping_basis": SEMANTIC_BASIS,
            "provider_asset_name": asset["name"],
            "semantic_rule_id": _semantic_rule_id(row["asset_name"]),
            "semantic_evidence_url": evidence_url,
        })
    return recovered


def _unique_active_sip_asset(assets: list[dict], ticker: str) -> dict | None:
    matches = []
    for row in assets:
        if not isinstance(row, dict):
            continue
        if (str(row.get("symbol") or "").strip().upper() == ticker and
                str(row.get("class") or "").strip().casefold() == "us_equity" and
                str(row.get("status") or "").strip().casefold() == "active" and
                str(row.get("exchange") or "").strip().upper() in SIP_EXCHANGES and
                isinstance(row.get("id"), str) and row["id"].strip() and
                isinstance(row.get("name"), str) and row["name"].strip()):
            matches.append(dict(
                row, symbol=ticker,
                status=str(row["status"]).strip().casefold(),
                exchange=str(row["exchange"]).strip().upper(),
            ))
    return matches[0] if len(matches) == 1 else None


def _apply_source_row_aliases(proposed: dict, assets: list[dict]) -> list[dict]:
    """Apply only fixed official-row mappings backed by current symbol directories."""
    registry = _asset_registry(assets)
    recovered = []
    for row in proposed["transactions"]:
        if (row.get("filing_id") not in _rules()['prior_name_target_reports'] or
                not _eligible_name(row) or row.get("ticker")):
            continue
        rule = _rules()['source_aliases'].get(row["id"])
        if rule is None:
            continue
        asset_name, ticker, page_number, row_number, transaction_date = rule
        if row["asset_name"] != asset_name:
            raise ValueError("A reviewed source row changed its asset label")
        if row.get("transaction_date") != transaction_date:
            raise ValueError("A reviewed source row changed its transaction date")
        directory_name, directory_url = _rules()['security_directory'][ticker]
        asset = _unique_active_sip_asset(assets, ticker)
        if asset is None or registry.get(ticker) != asset:
            continue
        row["ticker"] = ticker
        row["ticker_mapping_basis"] = SOURCE_DIRECTORY_BASIS
        recovered.append({
            "record_id": row["id"], "asset_name": row["asset_name"],
            "ticker": ticker, "mapping_basis": SOURCE_DIRECTORY_BASIS,
            "provider_asset_name": asset["name"],
            "provider_asset_id": asset["id"],
            "provider_exchange": asset["exchange"],
            "provider_active_sip_match_count": 1,
            "source_evidence_url": _source_alias_url(row['id']),
            "source_page_number": page_number,
            "source_row_number": row_number,
            "source_transaction_date": transaction_date,
            "security_directory_name": directory_name,
            "security_directory_url": directory_url,
            "security_directory_checked_on": _rules()['directory_checked_on'],
            "ambiguity_exclusion_basis": SOURCE_DIRECTORY_AMBIGUITY_GUARD,
            "source_rule_id": _source_directory_rule_id(
                row["id"], row["asset_name"], ticker, page_number, row_number,
                transaction_date, directory_url),
        })
    return recovered


def _apply_prior_exact_names(proposed: dict, assets: list[dict],
                                   ambiguous_ids: set[str]) -> list[dict]:
    """Reuse only a unique, already qualified reviewed ticker for the selected source reports."""
    registry = _asset_registry(assets)
    prior_by_name: dict[str, dict[str, set[str]]] = {}
    for row in proposed["transactions"]:
        if (row.get("person_id") != _rules()['person_id'] or
                row.get("filing_id") in _rules()['prior_name_target_reports'] or
                row.get("source_id") != "oge" or
                row.get("verification_status") != "official_matched" or
                not isinstance(row.get("ticker"), str) or
                not TICKER.fullmatch(row["ticker"]) or
                row.get("ticker_mapping_basis") not in {
                    "alpaca_unique_asset_name", "alpaca_unique_classless_asset_name",
                    SEMANTIC_BASIS,
                } or
                not isinstance(row.get("asset_name"), str)):
            continue
        prior_by_name.setdefault(row["asset_name"], {}).setdefault(
            row["ticker"], set()).add(row["id"])
    recovered = []
    for row in proposed["transactions"]:
        if (row.get("filing_id") not in _rules()['prior_name_target_reports'] or
                not _eligible_name(row) or row.get("ticker") or
                row["id"] in ambiguous_ids):
            continue
        matches = prior_by_name.get(row["asset_name"], {})
        if len(matches) != 1:
            continue
        ticker, prior_ids = next(iter(matches.items()))
        asset = registry.get(ticker)
        if (asset is None or asset["status"] != "active" or
                asset["exchange"] not in SIP_EXCHANGES):
            continue
        row["ticker"] = ticker
        row["ticker_mapping_basis"] = PRIOR_EXACT_NAME_BASIS
        recovered.append({
            "record_id": row["id"], "asset_name": row["asset_name"],
            "ticker": ticker, "mapping_basis": PRIOR_EXACT_NAME_BASIS,
            "provider_asset_name": asset["name"],
            "prior_record_ids": sorted(prior_ids),
        })
    return recovered


def is_allowed_ticker_change(before: dict, after: dict) -> bool:
    """Accept only a mapped ticker or correction of a known OCR suffix error."""
    if not _source_row(before) or not _source_row(after):
        return False
    old = (before.get("ticker"), before.get("ticker_mapping_basis"))
    new = (after.get("ticker"), after.get("ticker_mapping_basis"))
    if old == new or (old != (None, None) and not _false_explicit(before)):
        return False
    if new != (None, None) and not (
            isinstance(new[0], str) and TICKER.fullmatch(new[0]) and
            new[1] in ALLOWED_BASES and _eligible_name(after)):
        return False
    if new == (None, None) and old == (None, None):
        return False
    return all(before.get(key) == after.get(key) for key in
               (before.keys() | after.keys()) - {"ticker", "ticker_mapping_basis"})


def _previous(previous: dict | None) -> dict[str, dict]:
    if previous is None:
        return {}
    rows = previous.get("mappings")
    if previous.get("schema_version") != _rules()['mapping_schema'] or not isinstance(rows, list):
        raise ValueError("Previous reviewed security ticker mapping is invalid")
    result = {}
    for row in rows:
        if (not isinstance(row, dict) or
                not isinstance(row.get("record_id"), str) or
                not isinstance(row.get("asset_name"), str) or
                not isinstance(row.get("ticker"), str) or
                not TICKER.fullmatch(row["ticker"]) or
                row.get("mapping_basis") not in ALLOWED_BASES or
                row["record_id"] in result):
            raise ValueError("Previous reviewed security ticker mapping row is invalid")
        if row["mapping_basis"] == SEMANTIC_BASIS:
            rule = _rules()['semantic_aliases'].get(row["asset_name"])
            if (rule is None or rule[0] != row["ticker"] or
                    row.get("semantic_rule_id") != _semantic_rule_id(row["asset_name"]) or
                    row.get("semantic_evidence_url") != rule[1]):
                raise ValueError("Previous reviewed security semantic rule changed")
        if row["mapping_basis"] == PRIOR_EXACT_NAME_BASIS and not (
                isinstance(row.get("prior_record_ids"), list) and
                row["prior_record_ids"] and
                all(isinstance(item, str) and item for item in row["prior_record_ids"]) and
                row["prior_record_ids"] == sorted(set(row["prior_record_ids"]))):
            raise ValueError("Previous reviewed exact-name evidence is invalid")
        if row["mapping_basis"] == SOURCE_DIRECTORY_BASIS:
            rule = _rules()['source_aliases'].get(row["record_id"])
            if rule is None:
                raise ValueError("Previous source-directory rule no longer exists")
            if (not isinstance(row.get("provider_asset_id"), str) or
                    not row["provider_asset_id"] or
                    not isinstance(row.get("provider_asset_name"), str) or
                    not row["provider_asset_name"] or
                    not isinstance(row.get("provider_exchange"), str) or
                    row["provider_exchange"] not in SIP_EXCHANGES):
                raise ValueError("Previous source-directory provider evidence is invalid")
            asset_name, ticker, page_number, row_number, transaction_date = rule
            directory_name, directory_url = _rules()['security_directory'][ticker]
            if row.get("source_transaction_date") != transaction_date:
                raise ValueError("Previous source-directory transaction date is invalid")
            expected = {
                "asset_name": asset_name, "ticker": ticker,
                "source_evidence_url": _source_alias_url(row['record_id']),
                "source_page_number": page_number,
                "source_row_number": row_number,
                "source_transaction_date": transaction_date,
                "security_directory_name": directory_name,
                "security_directory_url": directory_url,
                "security_directory_checked_on": _rules()['directory_checked_on'],
                "ambiguity_exclusion_basis": SOURCE_DIRECTORY_AMBIGUITY_GUARD,
                "provider_active_sip_match_count": 1,
                "source_rule_id": _source_directory_rule_id(
                    row["record_id"], asset_name, ticker, page_number, row_number,
                    transaction_date, directory_url),
            }
            if any(row.get(key) != value for key, value in expected.items()):
                raise ValueError("Previous source-directory evidence changed")
        result[row["record_id"]] = row
    return result


def restore_pre_enrichment(candidate: dict, mapping_audit: dict) -> dict:
    """Recover the exact 278-T projection for an idempotent review rebuild."""
    mappings = _previous(mapping_audit)
    correction_ids = mapping_audit.get("correction_ids")
    if (not isinstance(correction_ids, list) or
            len(correction_ids) != len(set(correction_ids)) or
            mapping_audit.get("correction_count") != len(correction_ids)):
        raise ValueError("Prior reviewed security ticker corrections are invalid")
    result = deepcopy(candidate)
    rows = {row["id"]: row for row in result["transactions"] if _source_row(row)}
    for record_id, mapping in mappings.items():
        row = rows.get(record_id)
        if (row is None or row["asset_name"] != mapping["asset_name"] or
                row.get("ticker") != mapping["ticker"] or
                row.get("ticker_mapping_basis") != mapping["mapping_basis"]):
            raise ValueError("Prior reviewed security ticker mapping differs from candidate")
        row["ticker"] = None
        row["ticker_mapping_basis"] = None
    for record_id in correction_ids:
        row = rows.get(record_id)
        if (row is None or
                _false_explicit_value(row.get('filing_id'), row.get('asset_name')) is None or
                (record_id not in mappings and
                 (row.get("ticker"), row.get("ticker_mapping_basis")) != (None, None))):
            raise ValueError("Prior reviewed security explicit-ticker correction differs from candidate")
        row["ticker"] = _false_explicit_value(row['filing_id'], row['asset_name'])
        row["ticker_mapping_basis"] = "filing_explicit"
    return result


def enrich_reviewed_tickers(candidate: dict, assets: object, *,
                              checked_at: str, previous: dict | None = None
                              ) -> tuple[dict, dict]:
    """Enrich reviewed source rows without altering identities or other facts."""
    if not isinstance(candidate, dict) or candidate.get("meta", {}).get("is_demo") is not False:
        raise ValueError("reviewed security ticker enrichment requires a production candidate")
    if not isinstance(assets, list) or any(not isinstance(row, dict) for row in assets):
        raise ValueError("reviewed security ticker enrichment requires an asset array")
    before = deepcopy(candidate)
    corrected = deepcopy(candidate)
    corrections = []
    for row in corrected.get("transactions", []):
        if _false_explicit(row):
            row["ticker"] = None
            row["ticker_mapping_basis"] = None
            corrections.append(row["id"])
    source_recovered = _apply_source_row_aliases(corrected, assets)
    proposed, recovered, current = _recover_unique_asset_name_tickers(
        corrected, assets, eligible=_eligible_name)
    recovered.extend(source_recovered)
    ambiguous_ids = {row["record_id"] for row in current["ambiguous_records"]}
    recovered.extend(_apply_semantic_aliases(proposed, assets, ambiguous_ids))
    recovered.extend(_apply_prior_exact_names(proposed, assets, ambiguous_ids))
    proposed_by_id = {row["record_id"]: row for row in recovered}
    prior_by_id = _previous(previous)
    active_registry = _asset_registry(assets)
    references = {row["id"]: row for row in proposed["transactions"]
                  if isinstance(row.get("id"), str)}
    source_rule_ids = {row["record_id"] for row in recovered
                       if row["mapping_basis"] == SOURCE_DIRECTORY_BASIS}
    if (prior_by_id.keys() & ambiguous_ids) - source_rule_ids:
        raise ValueError("A sticky reviewed security ticker mapping is now ambiguous")
    rows_by_id = {row["id"]: row for row in proposed["transactions"] if _source_row(row)}
    mappings = []
    retained_ticker_conflicts = []
    for record_id, old in prior_by_id.items():
        row = rows_by_id.get(record_id)
        if row is None or row["asset_name"] != old["asset_name"]:
            raise ValueError("A sticky reviewed security ticker mapping lost its source row")
        if old["mapping_basis"] == PRIOR_EXACT_NAME_BASIS:
            asset = active_registry.get(old["ticker"])
            if (asset is None or asset["status"] != "active" or
                    asset["exchange"] not in SIP_EXCHANGES or
                    any((reference := references.get(prior_id)) is None or
                        reference.get("person_id") != _rules()['person_id'] or
                        reference.get("source_id") != "oge" or
                        reference.get("verification_status") != "official_matched" or
                        reference.get("asset_name") != old["asset_name"] or
                        reference.get("ticker") != old["ticker"] or
                        reference.get("ticker_mapping_basis") not in {
                            "alpaca_unique_asset_name", "alpaca_unique_classless_asset_name",
                            SEMANTIC_BASIS,
                        } for prior_id in old["prior_record_ids"])):
                raise ValueError("A sticky reviewed exact-name source is no longer valid")
        if old["mapping_basis"] == SOURCE_DIRECTORY_BASIS:
            asset = _unique_active_sip_asset(assets, old["ticker"])
            if (asset is None or active_registry.get(old["ticker"]) != asset or
                    row.get("transaction_date") != old["source_transaction_date"] or
                    old["provider_asset_id"] != asset["id"] or
                    old["provider_asset_name"] != asset["name"] or
                    old["provider_exchange"] != asset["exchange"]):
                raise ValueError("A sticky source-directory security is no longer active SIP")
        current_mapping = proposed_by_id.get(record_id)
        if current_mapping is not None:
            changed_keys = [key for key in (
                    "ticker", "mapping_basis", "semantic_rule_id",
                    "semantic_evidence_url", "source_rule_id",
                    "source_evidence_url", "source_page_number",
                    "source_row_number", "source_transaction_date",
                    # Alpaca can revise a display name while the same unique
                    # ticker still matches. Retain the historical audit name.
                    "provider_asset_id",
                    "provider_exchange", "provider_active_sip_match_count",
                    "ambiguity_exclusion_basis", "security_directory_name",
                    "security_directory_url", "security_directory_checked_on")
                            if current_mapping.get(key) != old.get(key)]
            if changed_keys:
                if (changed_keys == ['ticker'] and old['mapping_basis'] in {
                        'alpaca_unique_asset_name', 'alpaca_unique_classless_asset_name'}):
                    # A current name match cannot rewrite a historical mapping.
                    # Quarantine the new proposal; do not infer a rename, split,
                    # price-history alias or successor security from its name.
                    retained_ticker_conflicts.append({
                        'record_id': record_id, 'asset_name': old['asset_name'],
                        'retained_ticker': old['ticker'],
                        'proposed_ticker': current_mapping['ticker'],
                        'reason': 'provider_name_match_changed_ticker',
                        'disposition': 'proposal_quarantined_prior_mapping_retained',
                    })
                else:
                    raise ValueError(
                        "Alpaca identity conflicts with a sticky reviewed security mapping: "
                        f"record_id={record_id}, fields={','.join(changed_keys)}"
                    )
        row["ticker"] = old["ticker"]
        row["ticker_mapping_basis"] = old["mapping_basis"]
        mappings.append(dict(old))
    for record_id, current_mapping in proposed_by_id.items():
        if record_id not in prior_by_id:
            mappings.append({key: current_mapping[key] for key in (
                "record_id", "asset_name", "ticker", "mapping_basis",
                "provider_asset_name", "semantic_rule_id",
                "semantic_evidence_url", "prior_record_ids", "source_rule_id",
                "source_evidence_url", "source_page_number", "source_row_number",
                "source_transaction_date", "provider_asset_id", "provider_exchange",
                "provider_active_sip_match_count", "ambiguity_exclusion_basis",
                "security_directory_name", "security_directory_url",
                "security_directory_checked_on") if key in current_mapping})
    mappings.sort(key=lambda row: row["record_id"])
    for old, new in zip(before["transactions"], proposed["transactions"], strict=True):
        if old["id"] != new["id"]:
            raise ValueError("reviewed security ticker enrichment reordered transactions")
        if old != new and not is_allowed_ticker_change(old, new):
            raise ValueError("reviewed security ticker enrichment changed a non-ticker fact")
    source_rows = list(rows_by_id.values())
    mapped_ids = {row["record_id"] for row in mappings}
    ambiguous = [row for row in current["ambiguous_records"]
                 if row["record_id"] not in mapped_ids]
    ambiguous_ids = {row["record_id"] for row in ambiguous}
    unmatched = [row for row in source_rows if not row.get("ticker") and
                 row["id"] not in ambiguous_ids]
    explicit = [row for row in source_rows if row.get("ticker") and
                row["id"] not in mapped_ids]
    if len(mapped_ids) + len(ambiguous_ids) + len(unmatched) + len(explicit) != len(source_rows):
        raise ValueError("reviewed security ticker counts do not conserve transactions")
    ordered_assets = sorted(assets, key=lambda row: json.dumps(
        row, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    asset_bytes = json.dumps(ordered_assets, ensure_ascii=True, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
    audit = {
        "schema_version": _rules()['mapping_schema'], "checked_at": checked_at,
        "candidate_before_sha256": hashlib.sha256(encode(before)).hexdigest(),
        "asset_master_sha256": hashlib.sha256(asset_bytes).hexdigest(),
        "source_transaction_count": len(source_rows),
        "correction_count": len(corrections),
        "correction_ids": sorted(corrections),
        "mapping_count": len(mappings),
        "semantic_mapping_count": sum(
            row["mapping_basis"] == SEMANTIC_BASIS for row in mappings),
        "source_directory_mapping_count": sum(
            row["mapping_basis"] == SOURCE_DIRECTORY_BASIS for row in mappings),
        "retained_mapping_count": len(prior_by_id),
        "retained_ticker_conflict_count": len(retained_ticker_conflicts),
        "retained_ticker_conflicts": sorted(retained_ticker_conflicts,
                                            key=lambda row: row['record_id']),
        "new_mapping_count": len(mappings) - len(prior_by_id),
        "ambiguous_record_count": len(ambiguous),
        "ambiguous_records": ambiguous,
        "unmatched_record_count": len(unmatched),
        "explicit_ticker_count": len(explicit),
        "mappings": mappings,
    }
    return proposed, audit
