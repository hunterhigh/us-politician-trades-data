"""Overlay complete White House annual filer-reported holdings on OGE data."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .builder import FIELDS, normalize
from .oge import OgeCatalogError
from .oge_candidate import _identity_key, _instrument, _person_id, _short_name
from .whitehouse_278t import _first_last
from .whitehouse_annual_review import LEGACY_SCHEMA, SCHEMA


def _person(report: dict) -> dict:
    position = report.get("position_title_raw")
    if not isinstance(position, str) or not position.strip():
        raise OgeCatalogError("White House annual report has no position")
    identity = {"filer_name": report["filer_reported_name"],
                "agency": "White House Office", "position_title": position.strip()}
    person = {
        "id": _person_id(_identity_key(identity)),
        "display_name": identity["filer_name"], "short_name": _short_name(identity["filer_name"]),
        "role": identity["position_title"], "office_type": identity["agency"],
        "chamber": None, "party": None, "state": None,
        "disclosure_authority": "oge", "priority": False,
        "priority_reason": None, "portrait_url": None,
    }
    if set(person) != FIELDS["people"]:
        raise OgeCatalogError("White House annual person contract changed")
    return person


def overlay_whitehouse_annual_candidate(candidate: dict, review_root: Path) -> tuple[dict, dict]:
    """Add complete reports and source-bound partial rows; keep Unknown unchanged."""
    path = review_root / "whitehouse/annual/filer-reported-current.json"
    if not path.is_file():
        return candidate, {"annual_status": "not_available", "annual_holding_count": 0}
    raw = path.read_bytes()
    annual = json.loads(raw)
    reports = annual.get("reports")
    holdings = annual.get("holdings")
    transactions = annual.get("transactions", [])
    if (annual.get("schema_version") not in {LEGACY_SCHEMA, SCHEMA} or
            annual.get("production_status") !=
            "review_only_complete_or_source_bound_partial_rows" or
            not isinstance(reports, list) or not isinstance(holdings, list) or
            not isinstance(transactions, list) or
            annual.get("report_count") != len(reports) or
            annual.get("holding_count") != len(holdings) or
            annual.get("transaction_count", 0) != len(transactions)):
        raise OgeCatalogError("White House annual review artifact is invalid")
    complete_reports = [row for row in reports if row.get("source_holdings_eligible") is True]
    eligible_reports = {row["document_id"]: row for row in reports
                        if row.get("source_candidate_eligible") is True}
    transaction_reports = {row["document_id"]: row for row in reports
                           if row.get("source_candidate_transaction_eligible") is True}
    if (len(complete_reports) != annual.get("source_eligible_report_count") or
            len(eligible_reports) != annual.get("source_candidate_report_count")):
        raise OgeCatalogError("White House annual candidate report count changed")
    if len(transaction_reports) != annual.get(
            "source_candidate_transaction_report_count", 0):
        raise OgeCatalogError("White House annual transaction report count changed")
    eligible_rows = [row for row in holdings if row.get("source_holdings_eligible") is True]
    if len(eligible_rows) != annual.get("source_eligible_holding_count"):
        raise OgeCatalogError("White House annual eligible holding count changed")
    eligible_transactions = [row for row in transactions
                             if row.get("source_transaction_eligible") is True]
    if len(eligible_transactions) != annual.get("source_eligible_transaction_count", 0):
        raise OgeCatalogError("White House annual eligible transaction count changed")
    by_filer: dict[tuple[str, str], list[dict]] = {}
    for report in {**eligible_reports, **transaction_reports}.values():
        key = _first_last(report.get("filer_reported_name") or "")
        period = report.get("report_period_end")
        if key is None or not isinstance(period, str):
            raise OgeCatalogError("White House annual filer or period is invalid")
        by_filer.setdefault(key, []).append(report)
    if any(len(rows) != 1 for rows in by_filer.values()):
        raise OgeCatalogError("White House annual report versions require resolution")

    result = deepcopy(candidate)
    result["transactions"] = [row for row in result["transactions"]
                              if not str(row.get("id", "")).startswith("wh-annual-tx:")]
    result["reported_holdings"] = [row for row in result["reported_holdings"]
                                   if not str(row.get("id", "")).startswith("wh-annual:")]
    retained_people = {row["person_id"] for row in result["transactions"]}
    retained_people.update(row["person_id"] for row in result["reported_holdings"])
    result["people"] = [person for person in result["people"]
                        if person["id"] in retained_people]
    existing_by_name: dict[tuple[str, str], list[dict]] = {}
    for person in result.get("people", []):
        key = _first_last(person.get("display_name") or "")
        if key is not None:
            existing_by_name.setdefault(key, []).append(person)
    people = {person["id"]: person for person in result["people"]}
    person_by_document = {}
    for key, report_rows in by_filer.items():
        matches = existing_by_name.get(key, [])
        if len(matches) > 1:
            raise OgeCatalogError("White House annual filer matches multiple OGE people")
        person = matches[0] if matches else _person(report_rows[0])
        if person.get("disclosure_authority") != "oge":
            raise OgeCatalogError("White House annual filer conflicts with another authority")
        people.setdefault(person["id"], person)
        person_by_document[report_rows[0]["document_id"]] = person["id"]

    prior_ids = {row["id"] for row in result["reported_holdings"]}
    added = []
    for row in eligible_rows:
        report = eligible_reports.get(row.get("document_id"))
        if report is None or row.get("source_sha256") != report.get("source_sha256") or (
                row.get("source_url") != report.get("source_url")):
            raise OgeCatalogError("White House annual holding is not bound to its report")
        fact = {
            "id": row["row_id"], "filing_id": row["document_id"],
            "person_id": person_by_document[row["document_id"]],
            "owner": row["asset_owner"], "asset_name": row["asset_name"],
            "ticker": None, "ticker_mapping_basis": None,
            "instrument_type": _instrument(row["asset_name"], None),
            "report_period_end": row["report_period_end"],
            "filed_at": report["filing_date"] + "T00:00:00Z",
            "value_low": row["value_low"], "value_high": row["value_high"],
            "change_from_prior": "unknown", "source_id": "oge",
            "source": "U.S. Office of Government Ethics",
            "source_url": row["source_url"], "verification_status": "official_matched",
        }
        if set(fact) != FIELDS["reported_holdings"] or fact["id"] in prior_ids:
            raise OgeCatalogError("White House annual holding conflicts with candidate")
        prior_ids.add(fact["id"])
        added.append(fact)
    prior_transaction_ids = {row["id"] for row in result["transactions"]}
    added_transactions = []
    for row in eligible_transactions:
        report = transaction_reports.get(row.get("document_id"))
        if report is None or row.get("source_sha256") != report.get("source_sha256") or (
                row.get("source_url") != report.get("source_url")):
            raise OgeCatalogError("White House annual transaction is not bound to its report")
        fact = {
            "id": row["row_id"], "filing_id": row["document_id"],
            "person_id": person_by_document[row["document_id"]],
            "owner": row["asset_owner"], "asset_name": row["asset_name"],
            "ticker": None, "ticker_mapping_basis": None,
            "instrument_type": _instrument(row["asset_name"], None),
            "option_type": None, "strike_price": None, "expiration_date": None,
            "transaction_type": row["transaction_type"],
            "transaction_date": row["transaction_date"],
            "filed_at": report["filing_date"] + "T00:00:00Z",
            "amount_low": row["amount_low"], "amount_high": row["amount_high"],
            "position_effect": "unknown", "position_effect_basis": None,
            "source_id": "oge", "source": "U.S. Office of Government Ethics",
            "source_url": row["source_url"], "verification_status": "official_matched",
        }
        if set(fact) != FIELDS["transactions"] or fact["id"] in prior_transaction_ids:
            raise OgeCatalogError("White House annual transaction conflicts with candidate")
        prior_transaction_ids.add(fact["id"])
        added_transactions.append(fact)
    result["people"] = sorted(people.values(), key=lambda row: row["id"])
    result["reported_holdings"] = sorted([*result["reported_holdings"], *added],
                                          key=lambda row: row["id"])
    result["transactions"] = sorted([*result["transactions"], *added_transactions],
                                    key=lambda row: row["id"])
    for health in result["source_health"]:
        if health.get("source_id") == "oge":
            health["detail"] = health["detail"].split("; White House annual:", 1)[0]
            partial_count = sum(row.get("holding_coverage_status") == "partial"
                                for row in eligible_reports.values())
            health["detail"] += (f"; White House annual: {len(complete_reports)} complete and "
                                 f"{partial_count} partial filer-reported reports, "
                                 f"{len(added)} holdings and "
                                 f"{len(added_transactions)} transactions")
    normalized = normalize(result, allow_production=True,
                           allow_market=bool(result["security_market_data"]))
    if normalized != result:
        raise OgeCatalogError("White House annual candidate changed during normalization")
    return result, {
        "annual_status": "included",
        "annual_review_sha256": hashlib.sha256(raw).hexdigest(),
        "annual_qualified_report_count": len(eligible_reports),
        "annual_complete_report_count": len(complete_reports),
        "annual_partial_report_count": sum(row.get("holding_coverage_status") == "partial"
                                           for row in eligible_reports.values()),
        "annual_holding_count": len(added),
        "annual_transaction_count": len(added_transactions),
        "annual_unknown_owner_count": sum(row["asset_owner"] == "Unknown" for row in eligible_rows),
    }
