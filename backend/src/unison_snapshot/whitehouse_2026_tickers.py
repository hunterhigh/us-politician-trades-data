"""Source-bound ticker enrichment for Trump's 2026 White House 278-T trades."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from .alpaca_market import (
    SIP_EXCHANGES, TICKER, _asset_registry, _recover_unique_asset_name_tickers,
)
from .codec import encode
from .whitehouse_278t import TRUMP_2026_PROFILES


SCHEMA = "whitehouse-trump-2026-278t-ticker-mapping/v1"
TRUMP_PERSON_ID = "oge:076544f8ba0638cf"
SEMANTIC_BASIS = "alpaca_source_bound_semantic_alias"
ALLOWED_BASES = {"alpaca_unique_asset_name", "alpaca_unique_classless_asset_name",
                 SEMANTIC_BASIS}
REPORTS = {profile["document_id"]: profile for profile in TRUMP_2026_PROFILES}
ANNUAL_MAPPING_EVIDENCE = (
    "https://github.com/hunterhigh/us-politician-trades-data/blob/"
    "10bd7a05b68ce52b9d8650a2a669639c4d46b314/"
    "whitehouse/annual/ticker-mapping-current.json")
# Exact filing labels only. Each ticker has a prior mapped annual identity or
# issuer-published symbol evidence; the live Alpaca SIP asset must still exist.
SEMANTIC_ALIASES = {
    "ITRON INC EQUITY CLASS EQUITY": ("ITRI", ANNUAL_MAPPING_EVIDENCE),
    "AIRBNB INC CLA": ("ABNB", ANNUAL_MAPPING_EVIDENCE),
    "WORKDAY INC CLASS CLASS A": ("WDAY", ANNUAL_MAPPING_EVIDENCE),
    "META PLATFORMS INC CLASS CLASS A": ("META", ANNUAL_MAPPING_EVIDENCE),
    "AST SPACEMOBILE INC CLA": ("ASTS", ANNUAL_MAPPING_EVIDENCE),
    "[SOUTHERN CO COM": ("SO", ANNUAL_MAPPING_EVIDENCE),
    "CHARTER COMMUNICATIONS INC NEW CLA": ("CHTR", ANNUAL_MAPPING_EVIDENCE),
    "SMURFIT WESTROCK PLC F": ("SW", ANNUAL_MAPPING_EVIDENCE),
    "CONSTELLATION BRANDS INC CLA": ("STZ", ANNUAL_MAPPING_EVIDENCE),
    "VISA INC CLA": ("V", ANNUAL_MAPPING_EVIDENCE),
    "BLACKSTONE INC CLA": ("BX", ANNUAL_MAPPING_EVIDENCE),
    "PALANTIR TECHNOLOGIES INC CLA": ("PLTR", ANNUAL_MAPPING_EVIDENCE),
    "Lowes Cos Inc Com": ("LOW", ANNUAL_MAPPING_EVIDENCE),
    "WILLIAMS COS INC DEL": ("WMB", ANNUAL_MAPPING_EVIDENCE),
    "MERCK & CO INC COM": ("MRK", ANNUAL_MAPPING_EVIDENCE),
    "EMERSON ELECTRIC COM": ("EMR", ANNUAL_MAPPING_EVIDENCE),
    "WORKDAY INC CLA": ("WDAY", ANNUAL_MAPPING_EVIDENCE),
    "Boeing Co Com": ("BA", ANNUAL_MAPPING_EVIDENCE),
    "MEDTRONIC PLC F.": ("MDT", ANNUAL_MAPPING_EVIDENCE),
    "DOORDASH INC CLA": ("DASH", ANNUAL_MAPPING_EVIDENCE),
    "Arista Networks Inc Com New.": ("ANET", ANNUAL_MAPPING_EVIDENCE),
    "APPLOVIN CORP CLA": ("APP", ANNUAL_MAPPING_EVIDENCE),
    "ZOETIS INC CLA": ("ZTS", ANNUAL_MAPPING_EVIDENCE),
    "FOX CORP CLASS A": ("FOXA", ANNUAL_MAPPING_EVIDENCE),
    "BOSTON SCIENTIFIC CORP COM": ("BSX", ANNUAL_MAPPING_EVIDENCE),
    "KINDER MORGAN INC DEL": ("KMI", ANNUAL_MAPPING_EVIDENCE),
    "DOORDASH INC CLASS CLASS A": ("DASH", ANNUAL_MAPPING_EVIDENCE),
    "COPART INC": (
        "CPRT", "https://www.copart.com/content/cprt-01-31-26-earnings-release.pdf"),
    "EXXON MOBIL CORP": (
        "XOM", "https://investor.exxonmobil.com/company-information/"
        "press-releases/detail/1208/exxonmobil-announces-second-quarter-2026-results"),
    "MARSH & MCLENNAN COS INC": (
        "MRSH", "https://www.marsh.com/en/corp/about/news/"
        "marsh-mclennan-to-change-nyse-symbol-to-mrsh.html"),
    "CHESAPEAKE UTILS CORP": ("CPK", "https://www.chpk.com/investors/"),
    "BRIGHT HORIZONS FAMILY S": (
        "BFAM", "https://investors.brighthorizons.com/"),
    "GALLAGHER ARTHUR J & CO": (
        "AJG", "https://investor.ajg.com/news/news-details/2026/"
        "Arthur-J--Gallagher--Co--Announces-Second-Quarter-2026-Financial-Results/"
        "default.aspx"),
    "COGNIZANT TECHNOLOGY SOLUTIONS CORP CLA": (
        "CTSH", "https://investors.cognizant.com/investor-resources/"
        "stock-information/default.aspx"),
    "NIKE INC CLASS CLASS B": (
        "NKE", "https://investors.nike.com/investors/news-events-and-reports/"
        "investor-news/investor-news-details/2026/"
        "NIKE-Inc--Declares-0-41-Quarterly-Dividend-f1997578c/default.aspx"),
    "UNIVERSAL CORP VA": (
        "UVV", "https://investor.universalcorp.com/news/news-details/2026/"
        "Universal-Corporation-Reports-Fiscal-Year-and-Fourth-Quarter-2026-Results/"),
    "BLUE OWL CAPITAL INC CLA": (
        "OWL", "https://www.sec.gov/Archives/edgar/data/1823945/"
        "000182394526000009/owl-20251231.htm"),
    "GODADDY INC CLASS CLASS A": ("GDDY", ANNUAL_MAPPING_EVIDENCE),
    "CBRE GROUP INC CLASS CLASS A": ("CBRE", ANNUAL_MAPPING_EVIDENCE),
    "GLOBAL PMTS INC": ("GPN", ANNUAL_MAPPING_EVIDENCE),
    "CHARTER COMMUNICATIONS | CLASS A": ("CHTR", ANNUAL_MAPPING_EVIDENCE),
    "PALANTIR TECHNOLOGIES IN CLASS A": ("PLTR", ANNUAL_MAPPING_EVIDENCE),
}
FALSE_EXPLICIT = {
    ("wh-url:7e17c4be2b42f563d37df173", "CIGNA GROUP"): "THE",
    ("wh-url:01475dee0bcffa4e79f7f58c", "KROGER CO"): "THE",
    ("wh-url:7e17c4be2b42f563d37df173", "JADOBE INC."): "DELAWARE",
    ("wh-url:7e17c4be2b42f563d37df173", "COCA COLA COMPANY"): "THE",
    ("wh-url:7e17c4be2b42f563d37df173", "HARTFORD INSURANCE GROUP INC"): "THE",
    ("wh-url:7e17c4be2b42f563d37df173", "HERSHEY COMPANY"): "THE",
}
DEBT_LABEL = re.compile(
    r"\b(?:BOND|BONDS|BDS|NOTE|NOTES|NTS|DEBENTURE|TREASURY|MUNICIPAL|"
    r"MUNI|DUE|DTD|YTM|COUPON|REFUNDING|RFDG)\b|\bB/E\b|%|@",
    re.IGNORECASE,
)


def _source_row(row: dict) -> bool:
    profile = REPORTS.get(row.get("filing_id"))
    return (profile is not None and
            str(row.get("id") or "").startswith("oge-278t:") and
            row.get("person_id") == TRUMP_PERSON_ID and
            row.get("source_id") == "oge" and
            row.get("verification_status") == "official_matched" and
            row.get("source_url") == profile["source_url"] and
            row.get("filed_at") == profile["report_date"] + "T00:00:00Z")


def _false_explicit(row: dict) -> bool:
    return (_source_row(row) and
            row.get("ticker") == FALSE_EXPLICIT.get(
                (row.get("filing_id"), row.get("asset_name"))) and
            row.get("ticker_mapping_basis") == "filing_explicit")


def _eligible_name(row: dict) -> bool:
    if not _source_row(row):
        return False
    asset_name = str(row.get("asset_name") or "")
    return (row.get("instrument_type") in {"Stock", "ETF", "Unspecified"} and
            not DEBT_LABEL.search(asset_name))


def _semantic_rule_id(asset_name: str) -> str:
    return "semantic:" + hashlib.sha256(asset_name.encode("utf-8")).hexdigest()[:16]


def _apply_semantic_aliases(proposed: dict, assets: list[dict],
                            ambiguous_ids: set[str]) -> list[dict]:
    registry = _asset_registry(assets)
    recovered = []
    for row in proposed["transactions"]:
        if (not _eligible_name(row) or row.get("ticker") or
                row["id"] in ambiguous_ids):
            continue
        rule = SEMANTIC_ALIASES.get(row["asset_name"])
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


def is_allowed_2026_ticker_change(before: dict, after: dict) -> bool:
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
    if previous.get("schema_version") != SCHEMA or not isinstance(rows, list):
        raise ValueError("Previous Trump 2026 ticker mapping is invalid")
    result = {}
    for row in rows:
        if (not isinstance(row, dict) or
                not isinstance(row.get("record_id"), str) or
                not isinstance(row.get("asset_name"), str) or
                not isinstance(row.get("ticker"), str) or
                not TICKER.fullmatch(row["ticker"]) or
                row.get("mapping_basis") not in ALLOWED_BASES or
                row["record_id"] in result):
            raise ValueError("Previous Trump 2026 ticker mapping row is invalid")
        if row["mapping_basis"] == SEMANTIC_BASIS:
            rule = SEMANTIC_ALIASES.get(row["asset_name"])
            if (rule is None or rule[0] != row["ticker"] or
                    row.get("semantic_rule_id") != _semantic_rule_id(row["asset_name"]) or
                    row.get("semantic_evidence_url") != rule[1]):
                raise ValueError("Previous Trump 2026 semantic rule changed")
        result[row["record_id"]] = row
    return result


def restore_pre_enrichment(candidate: dict, mapping_audit: dict) -> dict:
    """Recover the exact 278-T projection for an idempotent review rebuild."""
    mappings = _previous(mapping_audit)
    correction_ids = mapping_audit.get("correction_ids")
    if (not isinstance(correction_ids, list) or
            len(correction_ids) != len(set(correction_ids)) or
            mapping_audit.get("correction_count") != len(correction_ids)):
        raise ValueError("Prior Trump 2026 ticker corrections are invalid")
    result = deepcopy(candidate)
    rows = {row["id"]: row for row in result["transactions"] if _source_row(row)}
    for record_id, mapping in mappings.items():
        row = rows.get(record_id)
        if (row is None or row["asset_name"] != mapping["asset_name"] or
                row.get("ticker") != mapping["ticker"] or
                row.get("ticker_mapping_basis") != mapping["mapping_basis"]):
            raise ValueError("Prior Trump 2026 ticker mapping differs from candidate")
        row["ticker"] = None
        row["ticker_mapping_basis"] = None
    for record_id in correction_ids:
        row = rows.get(record_id)
        if (row is None or
                (row.get("filing_id"), row.get("asset_name")) not in FALSE_EXPLICIT or
                (record_id not in mappings and
                 (row.get("ticker"), row.get("ticker_mapping_basis")) != (None, None))):
            raise ValueError("Prior Trump 2026 THE correction differs from candidate")
        row["ticker"] = FALSE_EXPLICIT[(row["filing_id"], row["asset_name"])]
        row["ticker_mapping_basis"] = "filing_explicit"
    return result


def enrich_trump_2026_tickers(candidate: dict, assets: object, *,
                              checked_at: str, previous: dict | None = None
                              ) -> tuple[dict, dict]:
    """Enrich fixed 2026 PTR rows without altering identities or other facts."""
    if not isinstance(candidate, dict) or candidate.get("meta", {}).get("is_demo") is not False:
        raise ValueError("Trump 2026 ticker enrichment requires a production candidate")
    if not isinstance(assets, list) or any(not isinstance(row, dict) for row in assets):
        raise ValueError("Trump 2026 ticker enrichment requires an asset array")
    before = deepcopy(candidate)
    corrected = deepcopy(candidate)
    corrections = []
    for row in corrected.get("transactions", []):
        if _false_explicit(row):
            row["ticker"] = None
            row["ticker_mapping_basis"] = None
            corrections.append(row["id"])
    proposed, recovered, current = _recover_unique_asset_name_tickers(
        corrected, assets, eligible=_eligible_name)
    ambiguous_ids = {row["record_id"] for row in current["ambiguous_records"]}
    recovered.extend(_apply_semantic_aliases(proposed, assets, ambiguous_ids))
    proposed_by_id = {row["record_id"]: row for row in recovered}
    prior_by_id = _previous(previous)
    if prior_by_id.keys() & ambiguous_ids:
        raise ValueError("A sticky Trump 2026 ticker mapping is now ambiguous")
    rows_by_id = {row["id"]: row for row in proposed["transactions"] if _source_row(row)}
    mappings = []
    for record_id, old in prior_by_id.items():
        row = rows_by_id.get(record_id)
        if row is None or row["asset_name"] != old["asset_name"]:
            raise ValueError("A sticky Trump 2026 ticker mapping lost its source row")
        current_mapping = proposed_by_id.get(record_id)
        if current_mapping is not None and any(
                current_mapping.get(key) != old.get(key) for key in (
                    "ticker", "mapping_basis", "semantic_rule_id",
                    "semantic_evidence_url")):
            raise ValueError("Alpaca identity conflicts with a sticky Trump 2026 mapping")
        row["ticker"] = old["ticker"]
        row["ticker_mapping_basis"] = old["mapping_basis"]
        mappings.append(dict(old))
    for record_id, current_mapping in proposed_by_id.items():
        if record_id not in prior_by_id:
            mappings.append({key: current_mapping[key] for key in (
                "record_id", "asset_name", "ticker", "mapping_basis",
                "provider_asset_name", "semantic_rule_id",
                "semantic_evidence_url") if key in current_mapping})
    mappings.sort(key=lambda row: row["record_id"])
    for old, new in zip(before["transactions"], proposed["transactions"], strict=True):
        if old["id"] != new["id"]:
            raise ValueError("Trump 2026 ticker enrichment reordered transactions")
        if old != new and not is_allowed_2026_ticker_change(old, new):
            raise ValueError("Trump 2026 ticker enrichment changed a non-ticker fact")
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
        raise ValueError("Trump 2026 ticker counts do not conserve transactions")
    ordered_assets = sorted(assets, key=lambda row: json.dumps(
        row, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    asset_bytes = json.dumps(ordered_assets, ensure_ascii=True, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
    audit = {
        "schema_version": SCHEMA, "checked_at": checked_at,
        "candidate_before_sha256": hashlib.sha256(encode(before)).hexdigest(),
        "asset_master_sha256": hashlib.sha256(asset_bytes).hexdigest(),
        "source_transaction_count": len(source_rows),
        "correction_count": len(corrections),
        "correction_ids": sorted(corrections),
        "mapping_count": len(mappings),
        "semantic_mapping_count": sum(
            row["mapping_basis"] == SEMANTIC_BASIS for row in mappings),
        "retained_mapping_count": len(prior_by_id),
        "new_mapping_count": len(mappings) - len(prior_by_id),
        "ambiguous_record_count": len(ambiguous),
        "ambiguous_records": ambiguous,
        "unmatched_record_count": len(unmatched),
        "explicit_ticker_count": len(explicit),
        "mappings": mappings,
    }
    return proposed, audit
