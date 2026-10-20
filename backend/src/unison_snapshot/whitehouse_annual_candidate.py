"""Overlay complete White House annual filer-reported holdings on OGE data."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .builder import FIELDS, normalize
from .oge import OgeCatalogError
from .oge_candidate import _identity_key, _instrument, _person_id, _short_name
from .whitehouse_278t import _first_last
from .whitehouse_annual_review import LEGACY_SCHEMA, SCHEMA, _holding_row_id


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


def annual_identity_people(annual: dict) -> list[dict]:
    """Expose only source-qualified annual filers before the annual fact overlay."""
    if not isinstance(annual, dict):
        raise OgeCatalogError("White House annual identity source is invalid")
    reports = annual.get("reports")
    if (annual.get("schema_version") not in {LEGACY_SCHEMA, SCHEMA} or
            annual.get("production_status") !=
            "review_only_complete_or_source_bound_partial_rows" or
            not isinstance(reports, list) or
            annual.get("report_count") != len(reports) or
            not isinstance(annual.get("holdings"), list) or
            annual.get("holding_count") != len(annual["holdings"]) or
            not isinstance(annual.get("transactions", []), list) or
            annual.get("transaction_count", 0) != len(annual.get("transactions", []))):
        raise OgeCatalogError("White House annual identity source is invalid")
    qualified = [row for row in reports if row.get("source_candidate_eligible") is True or
                 row.get("source_candidate_transaction_eligible") is True]
    by_filer: dict[tuple[str, str], list[dict]] = {}
    for report in qualified:
        key = _first_last(report.get("filer_reported_name") or "")
        source_url = report.get("source_url")
        source_sha = report.get("source_sha256")
        if (key is None or not isinstance(source_url, str) or
                urlsplit(source_url).hostname != "www.whitehouse.gov" or
                report.get("document_id") != "wh-url:" + hashlib.sha256(
                    source_url.encode()).hexdigest()[:24] or
                not isinstance(source_sha, str) or
                re.fullmatch(r"[0-9a-f]{64}", source_sha) is None):
            raise OgeCatalogError("White House annual identity report is invalid")
        by_filer.setdefault(key, []).append(report)
    if any(len(rows) != 1 for rows in by_filer.values()):
        raise OgeCatalogError("White House annual identity versions require resolution")
    return [_person(rows[0]) for _, rows in sorted(by_filer.items())]


def _overlay_whitehouse_annual_candidate(candidate: dict, review_root: Path) -> tuple[dict, dict]:
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
    unlinked_report_ids = []
    for key, report_rows in by_filer.items():
        matches = existing_by_name.get(key, [])
        proposed = _person(report_rows[0])
        if len(matches) > 1:
            exact = [person for person in matches if person["id"] == proposed["id"]]
            if len(exact) == 1:
                person = exact[0]
            else:
                # Retain the annual report's own source identity without
                # guessing which same-name OGE person it represents.
                person = proposed
                unlinked_report_ids.append(report_rows[0]["document_id"])
        else:
            person = matches[0] if matches else proposed
        if person.get("disclosure_authority") != "oge":
            raise OgeCatalogError("White House annual filer conflicts with another authority")
        if person["id"] in people and people[person["id"]] != person:
            raise OgeCatalogError("White House annual person ID conflicts with OGE")
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
        "annual_identity_unlinked_report_ids": sorted(unlinked_report_ids),
    }


def overlay_oge_annual_candidate(candidate: dict, inputs: dict) -> tuple[dict, dict]:
    """Consume qualified OGE annual rows in the existing annual overlay.

    Partial-report display remains a pending product choice. Part 7 relations
    are retained for reconciliation; similarity never adds or removes facts.
    """
    from .oge_annual_ledger import build_oge_annual_shadow_ledger
    from .builder import timestamp
    if not isinstance(candidate, dict) or candidate.get("meta", {}).get("is_demo") is not False:
        raise OgeCatalogError("OGE annual overlay requires a production candidate")
    extractions = inputs.get("extractions") if isinstance(inputs, dict) else None
    metadata = inputs.get("archive_metadata_by_document", {}) if isinstance(inputs, dict) else None
    dates = inputs.get("scan_date_observations_by_document", {}) if isinstance(inputs, dict) else None
    saved = inputs.get("saved_scan_observations_by_document", {}) if isinstance(inputs, dict) else None
    if not isinstance(extractions, list) or not isinstance(metadata, dict) or not isinstance(dates, dict) or not isinstance(saved, dict):
        raise OgeCatalogError("OGE annual overlay input must contain extractions and source metadata")
    extractions = list(extractions)
    if saved:
        from .oge_annual import materialize_saved_annual_scan
        for document, bundle in saved.items():
            if document not in metadata or any(row.get("source_document_id") == document for row in extractions):
                raise OgeCatalogError("saved annual scan input is duplicated or lacks source metadata")
            extractions.append(materialize_saved_annual_scan(bundle["cover_observation"], bundle["section_observation"],
                archive_metadata=metadata[document], holding_page_observations=bundle.get("holding_page_observations"),
                archived_pdf_path=bundle.get("archived_pdf_path")))
    result = deepcopy(candidate)
    people = {row["id"]: row for row in result["people"]}
    prior = {row["id"]: row for row in result["reported_holdings"]}
    reports, seen = [], set()
    versions = {}
    for item in extractions:
        archive = metadata.get(item.get("source_document_id"), {})
        if isinstance(archive, dict) and all(isinstance(archive.get(key), str) and archive[key].strip()
                                              for key in ("filer_name", "agency", "position_title")):
            versions.setdefault((_identity_key(archive), item.get("report_period_end")), []).append(item["source_document_id"])
    for extraction in extractions:
        document_id = extraction.get("source_document_id") if isinstance(extraction, dict) else None
        if not isinstance(document_id, str) or document_id in seen:
            raise OgeCatalogError("OGE annual overlay document ID is missing or duplicated")
        seen.add(document_id)
        archive = metadata.get(document_id)
        if (not isinstance(archive, dict) or archive.get("schema_version") != "oge-278e-annual-archive/v1" or
                any(archive.get(a) != extraction.get(b) for a, b in
                    (("document_id", "source_document_id"), ("sha256", "source_sha256"),
                     ("document_url", "source_url"), ("filer_name", "filer_name"),
                     ("archive_path", "evidence_archive_path")))):
            raise OgeCatalogError("OGE annual overlay metadata differs from extraction")
        ledger = build_oge_annual_shadow_ledger(extraction, run_id="oge-annual-candidate",
            code_commit=inputs["code_commit"], started_at=inputs["started_at"], qualified=True,
            scan_date_observation=dates.get(document_id))
        report_reasons = []
        version_refs = versions.get((_identity_key(archive), extraction.get("report_period_end")), [])
        if len(version_refs) > 1:
            report_reasons.append("annual_report_version_relationship_unresolved")
        filing_day = ledger["filing_date_evidence"]["date"]
        if filing_day and timestamp(filing_day + "T00:00:00Z") > timestamp(candidate["meta"]["data_cutoff_at"]):
            report_reasons.append("filing_exceeds_cutoff")
        if not ledger["holding_coverage_complete"]:
            report_reasons.append("partial_report_policy_pending")
        if archive.get("amended_label"):
            report_reasons.append("annual_amendment_relationship_unresolved")
        if archive.get("pending_final_oge_disposition") is True:
            report_reasons.append("pending_final_oge_disposition")
        identity = {key: archive.get(key) for key in ("filer_name", "agency", "position_title")}
        person_id = _person_id(_identity_key(identity))
        existing_report_rows = [row for row in prior.values() if row.get("filing_id") == document_id]
        if any(row.get("person_id") == person_id and row.get("report_period_end") == extraction.get("report_period_end")
               and row.get("filing_id") != document_id for row in prior.values()):
            report_reasons.append("existing_annual_report_version_relationship_unresolved")
        source = {"id": person_id, "display_name": archive["filer_name"], "short_name": _short_name(archive["filer_name"]),
            "role": archive["position_title"], "office_type": archive["agency"], "chamber": None,
            "party": None, "state": None, "disclosure_authority": "oge", "priority": False,
            "priority_reason": None, "portrait_url": None}
        index_by_object = {id(row): index for index, row in enumerate(extraction["holdings"])}
        prepared = []
        row_audit = []
        for decision, row in zip(ledger["rows"], ledger["detected_source_rows"], strict=True):
            reasons = decision["reasons"][:]
            collection = decision["observations"][0]["conditions"]["extraction_collection"]
            if row["section"] == "part7" or collection != "holdings":
                row_audit.append({"candidate_id": decision["candidate_id"], "section": row["section"],
                                  "disposition": decision["disposition"], "reasons": reasons,
                                  **({"saved_native_reference": row["saved_native_reference"],
                                      "value_disclosure_status": row.get("value_disclosure_status")}
                                     if "saved_native_reference" in row else {}),
                                  "account_context": row.get("account_context"), "hierarchy_evidence": row.get("hierarchy_evidence")})
                continue
            index = index_by_object[id(row)]
            identifier = _holding_row_id(extraction["source_sha256"], row, index)
            reasons.extend(report_reasons)
            if decision["disposition"] == "qualified" and not reasons:
                title = row.get("account_context", {}).get("title")
                fact = {"id": identifier, "filing_id": document_id, "person_id": person_id,
                    "owner": row["owner"], "asset_name": f"{row['asset_name']} — {title}" if title else row["asset_name"],
                    "ticker": None, "ticker_mapping_basis": None, "instrument_type": _instrument(row["asset_name"], None),
                    "report_period_end": row["report_period_end"],
                    "filed_at": ledger["filing_date_evidence"]["date"] + "T00:00:00Z",
                    "value_low": row["value_low"], "value_high": row["value_high"],
                    **({"value_low_exclusive": row["value_low_exclusive"]} if "value_low_exclusive" in row else {}),
                    "change_from_prior": "unknown", "source_id": "oge", "source": "U.S. Office of Government Ethics",
                    "source_url": extraction["source_url"], "verification_status": "official_matched"}
                if set(fact) != FIELDS["reported_holdings"] | ({"value_low_exclusive"} if "value_low_exclusive" in row else set()):
                    raise OgeCatalogError("OGE annual holding projection fields changed")
                prepared.append((fact, reasons))
            row_audit.append({"candidate_id": decision["candidate_id"], "formal_id": identifier,
                              "section": row["section"], "disposition": decision["disposition"],
                              **({"saved_native_reference": row["saved_native_reference"],
                                  "value_disclosure_status": row.get("value_disclosure_status")}
                                 if "saved_native_reference" in row else {}),
                              "reasons": sorted(set(reasons)), "account_context": row.get("account_context"),
                              "hierarchy_evidence": row.get("hierarchy_evidence")})
        # If a previous version's formal IDs cannot be bound, retain them and
        # block additions rather than minting replacements from shifted indices.
        prepared_ids = {fact["id"] for fact, _ in prepared}
        unbound = [row["id"] for row in existing_report_rows if row["id"] not in prepared_ids]
        if unbound:
            report_reasons.append("legacy_annual_row_binding_unresolved")
        added, retained_ids, field_differences = 0, {row["id"] for row in existing_report_rows}, []
        for fact, _ in prepared:
            old = prior.get(fact["id"])
            if old is not None:
                retained_ids.add(old["id"])
                if old != fact:
                    report_reasons.append("existing_annual_fact_diff_requires_source_reconciliation")
                    field_differences.append({"formal_id": fact["id"], "source_sha256": extraction["source_sha256"],
                        "fields": {field: {"old_value": old.get(field), "new_value": fact.get(field)}
                                   for field in sorted(set(old) | set(fact)) if old.get(field) != fact.get(field)}})
                continue
            if unbound:
                report_reasons.append("legacy_annual_row_binding_unresolved")
                continue
            prior[fact["id"]] = fact
            people.setdefault(person_id, source)
            added += 1
        reports.append({"document_id": document_id, "source_sha256": extraction["source_sha256"],
            "added_holding_count": added, "retained_holding_count": len(retained_ids),
            "existing_field_differences": field_differences, "version_document_references": version_refs,
            "unbound_existing_ids": unbound, "report_reasons": sorted(set(report_reasons)),
            "filing_date_evidence": ledger["filing_date_evidence"], "row_qualification": row_audit,
            "ledger_counts": ledger["manifest"]["counts"],
            "unlocated_source_issues": ledger["unlocated_source_issues"],
            "annual_278t_relationship_status": "unresolved_no_similarity_dedup"})
    result["reported_holdings"] = sorted(prior.values(), key=lambda row: row["id"])
    result["people"] = sorted(people.values(), key=lambda row: row["id"])
    return result, {"schema_version": "oge-annual-candidate-audit/v1", "reports": reports,
                    "added_holding_count": sum(row["added_holding_count"] for row in reports),
                    "added_transaction_count": 0}


def overlay_whitehouse_annual_candidate(candidate: dict, review_root: Path, *,
                                        oge_annual_input: dict | None = None) -> tuple[dict, dict]:
    protected = [row for row in candidate.get("reported_holdings", []) if row.get("source_id") == "oge" and
                 isinstance(row.get("filing_id"), str) and re.fullmatch(r"[0-9a-f]{32}", row["filing_id"]) and
                 row.get("verification_status") == "official_matched"]
    result, audit = _overlay_whitehouse_annual_candidate(candidate, review_root)
    if protected:
        holdings = {row["id"]: row for row in result["reported_holdings"]}
        conflicts = [row["id"] for row in protected if row["id"] in holdings and holdings[row["id"]] != row]
        if conflicts:
            audit["annual_oge_existing_id_conflicts_require_reconciliation"] = conflicts
        for row in protected:
            holdings[row["id"]] = deepcopy(row)
        result["reported_holdings"] = sorted(holdings.values(), key=lambda row: row["id"])
        people = {row["id"]: row for row in result["people"]}
        required = {row["person_id"] for row in protected}
        for person in candidate["people"]:
            if person["id"] in required:
                people[person["id"]] = deepcopy(person)
        result["people"] = sorted(people.values(), key=lambda row: row["id"])
    if oge_annual_input is not None:
        result, oge_audit = overlay_oge_annual_candidate(result, oge_annual_input)
        audit["oge_annual"] = oge_audit
    return result, audit
