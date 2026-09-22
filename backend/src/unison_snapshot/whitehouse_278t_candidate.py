"""Append qualified public White House 278-T rows to an OGE review candidate.

This is a pure, source-specific projection.  It neither publishes the five
arrays nor converts annual 278/278e Part 7 assets into holdings.  The input
extractions must already be bound to archived PDF byte hashes by the caller.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, time
import re
from urllib.parse import urlsplit

from .builder import ARRAYS, FIELDS, normalize, timestamp
from .oge import OgeCatalogError
from .oge_candidate import _identity_key, _instrument, _person_id, _short_name
from .oge_reports import _iso_date
from .whitehouse_278t import _ELECTRONIC_SIGNATURE, _first_last
from .whitehouse_278t_audit import AUDIT_SCHEMA, audit_whitehouse_278t


SCHEMA = "whitehouse-278t-candidate-conservation/v1"
_ID = re.compile(r"oge-278t:[0-9a-f]{24}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-^/]{0,31}")


def _official_pdf_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (parsed.scheme == "https" and parsed.netloc.casefold() in
            {"whitehouse.gov", "www.whitehouse.gov"} and not parsed.fragment and
            parsed.path.startswith("/wp-content/uploads/") and
            parsed.path.casefold().endswith(".pdf"))


def _signature_date(report: dict) -> str | None:
    """Use only the filer's electronic signature text, never an index date."""
    evidence = report.get("filer_signature_evidence")
    signer = report.get("filer_signature_name")
    if report.get("signature_method") != "electronic" or not isinstance(evidence, str) \
            or not isinstance(signer, str):
        return None
    match = _ELECTRONIC_SIGNATURE.fullmatch(evidence)
    if (match is None or _first_last(signer) is None or
            _first_last(match.group("signature")) != _first_last(signer) or
            _first_last(match.group("signer")) != _first_last(signer) or
            _first_last(report.get("pdf_filer_name") or "") != _first_last(signer)):
        return None
    try:
        return _iso_date(match.group("date"))
    except OgeCatalogError:
        return None


def _person(identity: dict) -> dict:
    key = _identity_key(identity)
    person = {
        "id": _person_id(key), "display_name": identity["filer_name"],
        "short_name": _short_name(identity["filer_name"]),
        "role": identity["position_title"], "office_type": identity["agency"],
        "chamber": None, "party": None, "state": None,
        "disclosure_authority": "oge", "priority": False,
        "priority_reason": None, "portrait_url": None,
    }
    if set(person) != FIELDS["people"]:
        raise OgeCatalogError("White House 278-T person field contract changed")
    return person


def _transaction(report: dict, row: dict, person_id: str) -> dict:
    ticker = row.get("ticker")
    fact = {
        "id": row["extraction_id"], "filing_id": report["document_id"],
        "person_id": person_id, "owner": row["owner"],
        "asset_name": row["asset_name"], "ticker": ticker,
        "ticker_mapping_basis": "filing_explicit" if ticker else None,
        "instrument_type": _instrument(row["asset_name"], ticker),
        "option_type": None, "strike_price": None, "expiration_date": None,
        "transaction_type": row["transaction_type"],
        "transaction_date": row["transaction_date"],
        "filed_at": report["filed_at"] + "T00:00:00Z",
        "amount_low": row["amount_low"], "amount_high": row["amount_high"],
        "position_effect": "unknown", "position_effect_basis": None,
        "source_id": "oge", "source": "U.S. Office of Government Ethics",
        "source_url": report["source_url"],
        "verification_status": "official_matched",
    }
    if set(fact) != FIELDS["transactions"]:
        raise OgeCatalogError("White House 278-T transaction field contract changed")
    return fact


def build_whitehouse_278t_review_candidate(
        base_oge_candidate: dict, extractions: list[dict], eligibility_audit: dict,
        oge_whitehouse_coverage: dict, *, data_cutoff_at: str) -> tuple[dict, dict]:
    """Return an append-only OGE review candidate and every source row's fate.

    The supplied audit is recomputed from these exact inputs to reject stale or
    edited qualifications.  A row can be promoted once or quarantined once;
    conflicts never overwrite an existing OGE fact.
    """
    try:
        cutoff = timestamp(data_cutoff_at)
        base_cutoff = timestamp(base_oge_candidate["meta"]["data_cutoff_at"])
    except (TypeError, ValueError, KeyError, AttributeError):
        raise OgeCatalogError("White House 278-T candidate cutoff is invalid") from None
    if cutoff < base_cutoff:
        raise OgeCatalogError("White House 278-T cutoff cannot precede the existing OGE cutoff")
    if not isinstance(base_oge_candidate, dict) or base_oge_candidate["meta"].get("is_demo") is not False:
        raise OgeCatalogError("White House 278-T candidate base must be non-demo")
    if not isinstance(extractions, list) or not isinstance(eligibility_audit, dict) \
            or eligibility_audit.get("schema_version") != AUDIT_SCHEMA:
        raise OgeCatalogError("White House 278-T candidate inputs are invalid")
    for name in (*ARRAYS, "source_health"):
        rows = base_oge_candidate.get(name)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise OgeCatalogError(f"Existing OGE {name} is invalid")
    if any(row.get("source_id") != "oge" for name in ("transactions", "reported_holdings")
           for row in base_oge_candidate[name]):
        raise OgeCatalogError("White House 278-T requires an OGE-only disclosure candidate")
    # Validate the existing OGE source independently.  The result is discarded
    # so original people, transactions, holdings, market facts and health stay
    # byte-for-byte unchanged in the returned candidate.
    try:
        normalize(base_oge_candidate, allow_production=True,
                  allow_empty_production=True,
                  allow_market=bool(base_oge_candidate["security_market_data"]))
    except ValueError as exc:
        raise OgeCatalogError(f"Existing OGE candidate is invalid: {exc}") from None
    expected_audit = audit_whitehouse_278t(
        extractions, oge_whitehouse_coverage, base_oge_candidate)
    if eligibility_audit != expected_audit:
        raise OgeCatalogError("White House 278-T qualification audit is stale or altered")
    if len(extractions) != expected_audit["report_count"]:
        raise OgeCatalogError("White House 278-T report count changed")

    candidate = deepcopy(base_oge_candidate)
    candidate["meta"]["data_cutoff_at"] = data_cutoff_at
    existing_people = {person["id"]: person for person in candidate["people"]}
    existing_ids = {row["id"] for row in candidate["transactions"]}
    source_id_counts = Counter(
        row.get("extraction_id") for report in extractions
        for row in [*report["transactions"], *report["quarantined"]]
        if isinstance(row.get("extraction_id"), str) and row["extraction_id"])
    seen_documents: set[str] = set()
    audit_reports = []
    added_transactions = []
    added_people = {}
    for report, qualified in zip(extractions, expected_audit["reports"], strict=True):
        document_id = report["document_id"]
        if document_id in seen_documents:
            raise OgeCatalogError("White House 278-T document ID is duplicated")
        seen_documents.add(document_id)
        source_rows = [*report["transactions"], *report["quarantined"]]
        if len(source_rows) != len(qualified["rows"]):
            raise OgeCatalogError("White House 278-T source rows are not conserved")
        doc_reasons = list(qualified["document_reasons"])
        if not _official_pdf_url(report.get("source_url")):
            doc_reasons.append("source_url_invalid")
        if (not isinstance(report.get("source_sha256"), str) or
                not _SHA256.fullmatch(report["source_sha256"])):
            doc_reasons.append("source_sha256_invalid")
        identity = qualified["matched_catalog_identity"]
        person = _person(identity) if identity is not None else None
        if person is not None and person["id"] != identity["person_id"]:
            raise OgeCatalogError("White House 278-T official identity changed")
        if person is not None and person["id"] in existing_people:
            prior = existing_people[person["id"]]
            for field in ("display_name", "role", "office_type", "disclosure_authority"):
                if prior.get(field) != person[field]:
                    raise OgeCatalogError("White House 278-T identity conflicts with existing OGE person")
        if _signature_date(report) != report.get("filed_at"):
            doc_reasons.append("filer_signature_date_not_verified")
        try:
            filing_at = datetime.combine(datetime.strptime(report["filed_at"], "%Y-%m-%d").date(),
                                         time.min, cutoff.tzinfo)
            if filing_at > cutoff:
                doc_reasons.append("filing_exceeds_cutoff")
        except (TypeError, ValueError, KeyError):
            doc_reasons.append("filed_at_invalid")
        row_results = []
        for source_row, decision in zip(source_rows, qualified["rows"], strict=True):
            reasons = sorted(set([*decision["reasons"], *doc_reasons]))
            row_id = source_row.get("extraction_id")
            if (row_id != decision["extraction_id"] or
                    source_row.get("row_number") != decision["row_number"]):
                raise OgeCatalogError("White House 278-T audit row is misaligned")
            if decision["status"] == "eligible" and not reasons:
                if not isinstance(row_id, str) or not _ID.fullmatch(row_id):
                    reasons.append("transaction_id_invalid")
                elif row_id in existing_ids:
                    reasons.append("transaction_id_conflict_existing")
                elif source_id_counts[row_id] > 1:
                    reasons.append("transaction_id_conflict_source")
                if person is None:
                    reasons.append("catalog_identity_missing")
                if not isinstance(source_row.get("ticker"), (str, type(None))) or (
                        isinstance(source_row.get("ticker"), str) and
                        not _TICKER.fullmatch(source_row["ticker"])):
                    reasons.append("transaction_ticker_invalid")
            if decision["status"] == "eligible" and not reasons:
                fact = _transaction(report, source_row, person["id"])
                added_transactions.append(fact)
                if person["id"] not in existing_people:
                    added_people[person["id"]] = person
            row_results.append({
                "extraction_id": row_id, "row_number": source_row.get("row_number"),
                "status": "promoted" if decision["status"] == "eligible" and not reasons
                          else "quarantined",
                "reasons": sorted(set(reasons)),
            })
        promoted_count = sum(row["status"] == "promoted" for row in row_results)
        quarantined_count = len(row_results) - promoted_count
        audit_reports.append({
            "document_id": document_id, "source_url": report["source_url"],
            "source_sha256": report["source_sha256"],
            "matched_person_id": person["id"] if person else None,
            "source_row_count": len(source_rows), "promoted_count": promoted_count,
            "quarantined_count": quarantined_count,
            "document_reasons": sorted(set(doc_reasons)), "rows": row_results,
        })
    if len(added_transactions) != sum(row["promoted_count"] for row in audit_reports):
        raise OgeCatalogError("White House 278-T promoted row count is inconsistent")
    candidate["people"].extend(added_people.values())
    candidate["transactions"].extend(added_transactions)
    candidate["people"].sort(key=lambda row: row["id"])
    candidate["transactions"].sort(key=lambda row: row["id"])
    total_rows = sum(row["source_row_count"] for row in audit_reports)
    promoted = sum(row["promoted_count"] for row in audit_reports)
    quarantined = sum(row["quarantined_count"] for row in audit_reports)
    if promoted + quarantined != total_rows:
        raise OgeCatalogError("White House 278-T row conservation failed")
    return candidate, {
        "schema_version": SCHEMA, "source_id": "oge",
        "catalog_sha256": expected_audit["catalog_sha256"],
        "data_cutoff_at": data_cutoff_at,
        "report_count": len(audit_reports), "source_row_count": total_rows,
        "promoted_report_count": sum(row["promoted_count"] > 0 for row in audit_reports),
        "promoted_transaction_count": promoted,
        "quarantined_row_count": quarantined,
        "added_person_count": len(added_people),
        "base_transaction_count": len(base_oge_candidate["transactions"]),
        "candidate_transaction_count": len(candidate["transactions"]),
        "reports": audit_reports,
    }
