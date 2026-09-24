"""Fail-closed release audit for Donald Trump's public White House 278-T rows.

This gate proves that a source-qualified review overlay is transaction-only and
append-only.  When prepared frontend readbacks are supplied, it also proves
that dashboard, search, person, and one affected ticker view expose the same
immutable candidate facts.  It never fetches, publishes, or updates Git refs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from unison_snapshot.builder import ARRAYS
from unison_snapshot.codec import encode
from unison_snapshot.whitehouse_278t_candidate import SCHEMA as CONSERVATION_SCHEMA
from unison_snapshot.whitehouse_278t_audit import (
    TRUMP_TARGET_DOCUMENT_ID as TARGET_DOCUMENT_ID,
    TRUMP_TARGET_FILED_AT,
    TRUMP_TARGET_SOURCE_SHA256 as TARGET_SOURCE_SHA256,
    TRUMP_TARGET_SOURCE_URL as TARGET_SOURCE_URL,
)
from unison_snapshot.whitehouse_annual_tickers import is_allowed_ticker_upgrade
from unison_snapshot.whitehouse_2026_tickers import is_allowed_2026_ticker_change
from unison_snapshot.whitehouse_278t import TRUMP_2026_PROFILES


SCHEMA = "whitehouse-trump-278t-release-audit/v1"
TRUMP_PERSON_ID = "oge:076544f8ba0638cf"
TARGET_FILED_AT = TRUMP_TARGET_FILED_AT + "T00:00:00Z"
ALLOWED_REPORTS = {
    (TARGET_DOCUMENT_ID, TARGET_SOURCE_URL, TARGET_SOURCE_SHA256): TRUMP_TARGET_FILED_AT,
    **{(profile["document_id"], profile["source_url"], profile["source_sha256"]):
       profile["report_date"] for profile in TRUMP_2026_PROFILES},
}


def _object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _rows(value: dict, name: str) -> list[dict]:
    rows = value.get(name)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Candidate {name} array is invalid")
    return rows


def _identified(value: dict, name: str, field: str = "id") -> dict[str, dict]:
    rows = _rows(value, name)
    identities = [row.get(field) for row in rows]
    if (any(not isinstance(identity, str) or not identity for identity in identities) or
            len(set(identities)) != len(identities)):
        raise ValueError(f"Candidate {name} identities are missing or duplicated")
    return dict(zip(identities, rows, strict=True))


def _candidate(value: dict) -> None:
    if value.get("meta", {}).get("is_demo") is not False:
        raise ValueError("Trump 278-T release audit requires non-demo candidates")
    for name in (*ARRAYS, "source_health"):
        _rows(value, name)
    if value["security_market_data"]:
        raise ValueError("Disclosure candidates must not contain market data")


def _official_whitehouse_pdf(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (parsed.scheme == "https" and
            parsed.hostname in {"whitehouse.gov", "www.whitehouse.gov"} and
            parsed.username is None and parsed.password is None and
            parsed.query == "" and parsed.fragment == "" and
            parsed.path.startswith("/wp-content/uploads/") and
            parsed.path.casefold().endswith(".pdf"))


def _promoted(conservation: dict) -> tuple[set[str], dict[str, dict], int]:
    if (conservation.get("schema_version") != CONSERVATION_SCHEMA or
            conservation.get("candidate_is_append_only") is not True or
            conservation.get("source_row_conservation_complete") is not True or
            conservation.get("existing_transaction_mutation_count") != 0 or
            not isinstance(conservation.get("reports"), list)):
        raise ValueError("Trump 278-T conservation audit is invalid or incomplete")
    promoted: set[str] = set()
    evidence: dict[str, dict] = {}
    source_rows = qualified = quarantined = annual_overlaps = 0
    for report in conservation["reports"]:
        if (not isinstance(report, dict) or
                not isinstance(report.get("rows"), list) or
                not _official_whitehouse_pdf(report.get("source_url")) or
                not isinstance(report.get("document_id"), str) or
                not isinstance(report.get("source_sha256"), str)):
            raise ValueError("Trump 278-T conservation report is invalid")
        rows = report["rows"]
        if report.get("source_row_count") != len(rows):
            raise ValueError("Trump 278-T file rows are not conserved")
        report_promoted = 0
        for row in rows:
            if not isinstance(row, dict) or row.get("status") not in {"promoted", "quarantined"}:
                raise ValueError("Trump 278-T row disposition is invalid")
            if row["status"] == "promoted":
                identity = row.get("extraction_id")
                if not isinstance(identity, str) or not identity or identity in promoted:
                    raise ValueError("Trump 278-T promoted row identity is missing or duplicated")
                promoted.add(identity)
                evidence[identity] = report
                report_promoted += 1
            else:
                quarantined += 1
            if row.get("annual_part7_overlap") is True:
                annual_overlaps += 1
        if (report.get("promoted_count") != report_promoted or
                report.get("quarantined_count") != len(rows) - report_promoted):
            raise ValueError("Trump 278-T per-file disposition counts do not close")
        source_rows += len(rows)
        qualified += report_promoted
    if (source_rows != conservation.get("source_row_count") or
            qualified != conservation.get("promoted_transaction_count") or
            quarantined != conservation.get("quarantined_row_count") or
            sorted(promoted) != conservation.get("promoted_transaction_ids") or
            annual_overlaps != conservation.get("annual_part7_overlap_count", 0)):
        raise ValueError("Trump 278-T batch disposition counts do not close")
    target = [report for report in conservation["reports"]
              if report.get("document_id") == TARGET_DOCUMENT_ID]
    if (len(target) != 1 or target[0].get("source_url") != TARGET_SOURCE_URL or
            target[0].get("source_sha256") != TARGET_SOURCE_SHA256 or
            target[0].get("filed_at") != TARGET_FILED_AT[:10] or
            target[0].get("source_row_count") != 507 or
            sorted(row.get("row_number") for row in target[0]["rows"]
                   if type(row.get("row_number")) is int) != list(range(1, 508))):
        raise ValueError("Trump 278-T fixed source report is not fully conserved")
    indexed = {(report.get("document_id"), report.get("source_url"),
                report.get("source_sha256")): report
               for report in conservation["reports"]}
    expected_2026 = [key for key in ALLOWED_REPORTS if key[0] != TARGET_DOCUMENT_ID]
    present_2026 = [key for key in expected_2026 if key in indexed]
    missing_2026 = [key for key in expected_2026 if key not in indexed]
    if present_2026 and missing_2026:
        raise ValueError("Trump 2026 278-T source reports are not fully conserved")
    for key, filed_at in ALLOWED_REPORTS.items():
        report = indexed.get(key)
        if report is not None and report.get("filed_at") != filed_at:
            raise ValueError("Trump 278-T source report date binding changed")
    return promoted, evidence, annual_overlaps


def _frontend_gate(frontend: dict[str, dict], after: dict,
                   added: set[str], added_rows: dict[str, dict]) -> dict:
    if set(frontend) != {"dashboard", "search", "person", "ticker"}:
        raise ValueError("Frontend audit requires dashboard/search/person/ticker readbacks")
    expected_people = _identified(after, "people")
    expected_transactions = _identified(after, "transactions")
    expected_holdings = _identified(after, "reported_holdings")
    for mode in ("dashboard", "search"):
        value = frontend[mode]
        if (value.get("meta", {}).get("is_demo") is not False or
                value.get("meta", {}).get("selection_scope", {}).get("mode") != mode):
            raise ValueError(f"Prepared {mode} readback metadata is invalid")
        if (_identified(value, "people") != expected_people or
                _identified(value, "transactions") != expected_transactions or
                _identified(value, "reported_holdings") != expected_holdings):
            raise ValueError(f"Prepared {mode} readback differs from the candidate")
        if [row.get("source_id") for row in _rows(value, "source_health")] != [
                row.get("source_id") for row in after["source_health"]]:
            raise ValueError(f"Prepared {mode} source health differs from the candidate")

    person = frontend["person"]
    if (person.get("meta", {}).get("is_demo") is not False or
            person.get("meta", {}).get("selection_scope", {}).get("mode") != "person" or
            person.get("meta", {}).get("selection_scope", {}).get("key") != TRUMP_PERSON_ID):
        raise ValueError("Prepared Trump person readback metadata is invalid")
    person_people = _identified(person, "people")
    person_transactions = _identified(person, "transactions")
    person_holdings = _identified(person, "reported_holdings")
    expected_person_transactions = {
        key: row for key, row in expected_transactions.items()
        if row.get("person_id") == TRUMP_PERSON_ID}
    expected_person_holdings = {
        key: row for key, row in expected_holdings.items()
        if row.get("person_id") == TRUMP_PERSON_ID}
    if (person_people != {TRUMP_PERSON_ID: expected_people[TRUMP_PERSON_ID]} or
            person_transactions != expected_person_transactions or
            person_holdings != expected_person_holdings or
            not added <= person_transactions.keys()):
        raise ValueError("Prepared Trump person readback is incomplete")

    ticker = frontend["ticker"]
    selected = ticker.get("meta", {}).get("selection_scope", {}).get("key")
    if (ticker.get("meta", {}).get("is_demo") is not False or
            ticker.get("meta", {}).get("selection_scope", {}).get("mode") != "ticker" or
            not isinstance(selected, str) or not selected):
        raise ValueError("Prepared affected ticker readback metadata is invalid")
    expected_ticker_rows = {
        key: row for key, row in added_rows.items() if row.get("ticker") == selected}
    ticker_transactions = _identified(ticker, "transactions")
    ticker_people = _identified(ticker, "people")
    if (not expected_ticker_rows or
            not expected_ticker_rows.keys() <= ticker_transactions.keys() or
            any(ticker_transactions[key] != row for key, row in expected_ticker_rows.items()) or
            TRUMP_PERSON_ID not in ticker_people):
        raise ValueError("Prepared affected ticker readback omits Trump 278-T facts")
    return {
        "dashboard_transaction_count": len(expected_transactions),
        "search_transaction_count": len(expected_transactions),
        "person_transaction_count": len(expected_person_transactions),
        "person_holding_count": len(expected_person_holdings),
        "ticker": selected,
        "ticker_added_transaction_count": len(expected_ticker_rows),
    }


def audit_release(before: dict, after: dict, before_oge: dict, after_oge: dict,
                  conservation: dict,
                  frontend: dict[str, dict] | None = None) -> dict:
    """Audit one candidate transition and optional prepared frontend readbacks."""
    for value in (before, after, before_oge, after_oge):
        _candidate(value)
    promoted, evidence, annual_overlaps = _promoted(conservation)

    before_people = _identified(before, "people")
    after_people = _identified(after, "people")
    before_transactions = _identified(before, "transactions")
    after_transactions = _identified(after, "transactions")
    before_holdings = _identified(before, "reported_holdings")
    after_holdings = _identified(after, "reported_holdings")
    before_oge_transactions = _identified(before_oge, "transactions")
    after_oge_transactions = _identified(after_oge, "transactions")
    before_oge_holdings = _identified(before_oge, "reported_holdings")
    after_oge_holdings = _identified(after_oge, "reported_holdings")

    if any(after_people.get(key) != row for key, row in before_people.items()):
        raise ValueError("Trump 278-T release changed or removed an existing person")
    ticker_upgrades = sorted(
        key for key, row in before_transactions.items()
        if after_transactions.get(key) != row and after_transactions.get(key) is not None and
        (is_allowed_ticker_upgrade(row, after_transactions[key]) or
         is_allowed_2026_ticker_change(row, after_transactions[key])))
    changed_transactions = sorted(
        key for key, row in before_transactions.items()
        if after_transactions.get(key) != row and key not in ticker_upgrades)
    if changed_transactions:
        raise ValueError("Trump 278-T release changed or removed an existing transaction")
    oge_ticker_upgrades = sorted(
        key for key, row in before_oge_transactions.items()
        if after_oge_transactions.get(key) != row and
        after_oge_transactions.get(key) is not None and
        (is_allowed_ticker_upgrade(row, after_oge_transactions[key]) or
         is_allowed_2026_ticker_change(row, after_oge_transactions[key])))
    changed_oge_transactions = sorted(
        key for key, row in before_oge_transactions.items()
        if after_oge_transactions.get(key) != row and key not in oge_ticker_upgrades)
    if changed_oge_transactions:
        raise ValueError("Trump 278-T release changed or removed an existing OGE transaction")
    if ticker_upgrades != oge_ticker_upgrades or any(
            after_transactions[key] != after_oge_transactions[key]
            for key in ticker_upgrades):
        raise ValueError("Ticker changes differ between unified and OGE candidates")
    if before_holdings != after_holdings or before_oge_holdings != after_oge_holdings:
        raise ValueError("Trump 278-T release changed reported holdings")
    if before["security_market_data"] != after["security_market_data"] or \
            before_oge["security_market_data"] != after_oge["security_market_data"]:
        raise ValueError("Trump 278-T candidate changed market data")

    added = set(after_transactions) - set(before_transactions)
    added_oge = set(after_oge_transactions) - set(before_oge_transactions)
    expected_added = promoted - set(before_transactions)
    if added != expected_added or added_oge != expected_added:
        raise ValueError("Trump 278-T promoted rows do not equal both candidate deltas")
    if not promoted <= after_transactions.keys() or not promoted <= after_oge_transactions.keys():
        raise ValueError("Trump 278-T candidate omitted a source-qualified row")
    added_rows = {key: after_transactions[key] for key in added}
    if any(after_oge_transactions[key] != row for key, row in added_rows.items()):
        raise ValueError("Unified Trump 278-T rows differ from the OGE source rows")
    for identity, row in added_rows.items():
        report = evidence[identity]
        source_key = (report.get("document_id"), report.get("source_url"),
                      report.get("source_sha256"))
        expected_date = ALLOWED_REPORTS.get(source_key)
        if (expected_date is None or report.get("filed_at") != expected_date or
                row.get("person_id") != TRUMP_PERSON_ID or row.get("source_id") != "oge" or
                row.get("verification_status") != "official_matched" or
                row.get("filing_id") != report["document_id"] or
                row.get("source_url") != report["source_url"] or
                row.get("filed_at") != expected_date + "T00:00:00Z" or
                not _official_whitehouse_pdf(row.get("source_url"))):
            raise ValueError("Trump 278-T release added a non-Trump or unbound transaction")
    added_people = set(after_people) - set(before_people)
    if added_people - {TRUMP_PERSON_ID}:
        raise ValueError("Trump 278-T release added an unrelated person")
    if TRUMP_PERSON_ID not in after_people:
        raise ValueError("Trump is missing from the release candidate")
    if set(after_transactions) & set(after_holdings):
        raise ValueError("Trump 278-T release has a cross-kind fact identity collision")

    result = {
        "schema_version": SCHEMA,
        "before_candidate_sha256": hashlib.sha256(encode(before)).hexdigest(),
        "after_candidate_sha256": hashlib.sha256(encode(after)).hexdigest(),
        "conservation_sha256": hashlib.sha256(encode(conservation)).hexdigest(),
        "source_report_count": conservation["report_count"],
        "source_row_count": conservation["source_row_count"],
        "promoted_transaction_count": len(added),
        "promoted_transaction_ids": sorted(added),
        "quarantined_row_count": conservation["quarantined_row_count"],
        "annual_part7_overlap_count": annual_overlaps,
        "annual_part7_incomparable_row_count":
            conservation.get("annual_part7_incomparable_row_count", 0),
        "candidate_transaction_delta_count": len(added),
        "oge_transaction_delta_count": len(added_oge),
        "reported_holding_delta_count": 0,
        "removed_person_count": 0,
        "removed_transaction_count": 0,
        "existing_transaction_mutation_count": 0,
        "annual_ticker_upgrade_count": sum(is_allowed_ticker_upgrade(
            before_transactions[key], after_transactions[key]) for key in ticker_upgrades),
        "annual_ticker_upgrade_ids": [key for key in ticker_upgrades if
                                      is_allowed_ticker_upgrade(
                                          before_transactions[key], after_transactions[key])],
        "trump_2026_ticker_change_count": sum(is_allowed_2026_ticker_change(
            before_transactions[key], after_transactions[key]) for key in ticker_upgrades),
        "trump_2026_ticker_change_ids": [key for key in ticker_upgrades if
                                         is_allowed_2026_ticker_change(
                                             before_transactions[key], after_transactions[key])],
        "duplicate_person_id_count": 0,
        "duplicate_transaction_id_count": 0,
        "duplicate_holding_id_count": 0,
        "cross_kind_fact_id_collision_count": 0,
        "candidate_is_append_only": True,
        "target_document_id": TARGET_DOCUMENT_ID,
        "target_source_sha256": TARGET_SOURCE_SHA256,
        "target_filed_at": TARGET_FILED_AT,
        "target_2026_document_ids": sorted(
            profile["document_id"] for profile in TRUMP_2026_PROFILES),
        "frontend_verified": frontend is not None,
    }
    if frontend is not None:
        result["frontend"] = _frontend_gate(frontend, after, added, added_rows)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--before-oge", type=Path, required=True)
    parser.add_argument("--after-oge", type=Path, required=True)
    parser.add_argument("--conservation", type=Path, required=True)
    parser.add_argument("--frontend-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    frontend = None
    if args.frontend_dir is not None:
        frontend = {name: _object(args.frontend_dir / f"{name}.json")
                    for name in ("dashboard", "search", "person", "ticker")}
    result = audit_release(
        _object(args.before), _object(args.after),
        _object(args.before_oge), _object(args.after_oge),
        _object(args.conservation), frontend)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encode(result))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
