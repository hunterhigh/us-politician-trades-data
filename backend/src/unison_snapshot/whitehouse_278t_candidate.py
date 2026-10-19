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
import hashlib
import json
from urllib.parse import urlsplit

from .builder import ARRAYS, FIELDS, normalize, timestamp
from .oge import OgeCatalogError
from .oge_candidate import _identity_key, _instrument, _person_id, _short_name
from .oge_reports import _iso_date
from .whitehouse_278t import (_ELECTRONIC_SIGNATURE, _first_last, GENERIC_PARSER_VERSION,
    generic_existing_fact, generic_whitehouse_filing_date)
from .whitehouse_278t_audit import (
    AUDIT_SCHEMA, TRUMP_PERSON_ID, audit_whitehouse_278t, source_bound_filing_date,
    resolve_exact_source_reports,
)


SCHEMA = "whitehouse-278t-candidate-conservation/v1"
_ID = re.compile(r"oge-278t:[0-9a-f]{24}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-^/]{0,31}")


EXACT_COPY_SCHEMA = "whitehouse-278t-exact-source-candidate/v1"


def _cross_source_bindings(extractions, bindings):
    """Validate archived same-byte receipts; missing old observations are not conflicts."""
    if bindings is None:
        return {}
    if bindings.get("schema_version") != "whitehouse-278t-exact-source-bindings/v1":
        raise OgeCatalogError("White House cross-source binding schema is unsupported")
    reports = {report["document_id"]: report for report in extractions}
    result = {}
    for binding in bindings.get("reports", []):
        wh, oge, parsed = (binding[key] for key in ("whitehouse_archive", "oge_archive", "oge_extraction"))
        report = reports.get(wh.get("document_id"))
        direct_url = urlsplit(oge.get("document_url") or "")
        if (report is None or report["document_id"] in result or
                wh.get("document_url") != report["source_url"] or
                wh.get("sha256") != report["source_sha256"] or oge.get("sha256") != wh.get("sha256") or
                direct_url.scheme != "https" or direct_url.netloc != "extapps2.oge.gov" or
                direct_url.query or direct_url.fragment or not direct_url.path.lower().endswith(".pdf") or
                not re.search(r"/" + re.escape(oge.get("document_id") or "INVALID") + r"/\$file/", direct_url.path, re.I) or
                parsed.get("schema_version") != "oge-278t-extraction/v1" or
                parsed.get("source_sha256") != oge.get("sha256") or
                parsed.get("source_url") != oge.get("document_url") or
                parsed.get("document_id") != oge.get("document_id") or
                _first_last(parsed.get("catalog_filer_name") or "") is None or
                _first_last(parsed.get("catalog_filer_name") or "") != _first_last(report.get("pdf_filer_name") or "")):
            raise OgeCatalogError("White House cross-source alias is not bound to the same PDF and filer")
        slots = {}
        for row in [*parsed["transactions"], *parsed["quarantined"]]:
            page, number = row.get("page_number"), row.get("row_number")
            if type(page) is int and page > 0 and type(number) is int and number > 0:
                slots.setdefault((page, number), []).append(row)
        result[report["document_id"]] = {"archive": oge, "slots": slots,
            "receipt": {"canonical_document_id": report["document_id"],
                "source_sha256": report["source_sha256"], "oge_document_id": oge["document_id"],
                "oge_source_url": oge["document_url"], "whitehouse_source_url": wh["document_url"],
                "binding_sha256": hashlib.sha256(json.dumps(binding, sort_keys=True,
                    ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
                "old_observed_transaction_count": len(parsed["transactions"]),
                "old_quarantined_observation_count": len(parsed["quarantined"]), "rows": []}}
    return result


def _exact_source_inputs(base: dict, extractions: list[dict], coverage: dict,
                         annual_extractions=None, directory_index=None, annual_identity_people=None,
                         source_bindings=None):
    base = deepcopy(base)
    removed_duplicates = []
    cross = _cross_source_bindings(extractions, source_bindings)
    canonical, aliases = resolve_exact_source_reports(extractions, coverage,
                                                     existing_transactions=base["transactions"])
    complete = deepcopy(canonical)
    groups = {group["canonical_document_id"]: group for group in aliases}
    retained, receipts = [], []
    for report in canonical:
        group = groups.get(report["document_id"])
        relation = cross.get(report["document_id"])
        if group is None and relation is not None:
            group = {"sources": [{"document_id": report["document_id"], "source_url": report["source_url"],
                "source_sha256": report["source_sha256"], "rows": [
                    {"row_number": row["row_number"], "extraction_id": row["extraction_id"]}
                    for row in [*report["transactions"], *report["quarantined"]]]}]}
        if group is None:
            continue
        # Independently assess the original rows, ignoring only existing facts
        # whose URL is proven to refer to these exact bytes. No other original
        # qualification reason is removed.
        urls = {source["source_url"] for source in group["sources"]}
        if relation:
            urls.add(relation["archive"]["document_url"])
        comparison_base = deepcopy(base)
        comparison_base["transactions"] = [row for row in base["transactions"]
                                            if row.get("source_url") not in urls]
        checked = audit_whitehouse_278t([report], coverage, comparison_base,
                                        annual_extractions, directory_index, annual_identity_people)
        identity = checked["reports"][0]["matched_catalog_identity"]
        remaining = []
        for row, decision in zip(report["transactions"], checked["reports"][0]["rows"]):
            matches = []
            bound_old_ids = set()
            if relation:
                observed = relation["slots"].get((row.get("page_number"), row.get("row_number")), [])
                if len(observed) == 1:
                    bound_old_ids.add(observed[0]["extraction_id"])
            if identity is not None and decision["status"] == "eligible" and _signature_date(report) == report.get("filed_at"):
                proposed = _transaction(report, row, identity["person_id"])
                core = {"person_id", "owner", "asset_name", "transaction_type", "transaction_date",
                        "filed_at", "amount_low", "amount_high"}
                matches = [old for old in base["transactions"]
                           if old.get("source_url") in urls and
                           (old.get("source_url") != (relation["archive"]["document_url"] if relation else None) or
                            old.get("id") in bound_old_ids) and
                           (row.get("ticker") is None or old.get("ticker") == row.get("ticker")) and
                           all(old.get(key) == proposed[key] for key in core)]
                direct_matches = [old for old in matches if old["id"] in bound_old_ids]
                wh_row_ids = {entry["extraction_id"] for source in group["sources"]
                              for entry in source["rows"] if entry["row_number"] == row["row_number"]}
                if (len(matches) > 1 and len(direct_matches) == 1 and
                        all(old is direct_matches[0] or old["id"] in wh_row_ids for old in matches)):
                    keep = direct_matches[0]
                    duplicate_ids = {old["id"] for old in matches if old is not keep}
                    for old in matches:
                        if old["id"] in duplicate_ids:
                            removed_duplicates.append({"old_transaction_id": old["id"],
                                "canonical_transaction_id": keep["id"], "source_sha256": report["source_sha256"],
                                "page_number": row.get("page_number"), "row_number": row["row_number"],
                                "old_fact_sha256": hashlib.sha256(json.dumps(old, sort_keys=True,
                                    ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()})
                    base["transactions"] = [old for old in base["transactions"] if old["id"] not in duplicate_ids]
                    matches = [keep]
            if len(matches) == 1:
                retained.append({"document_id": report["document_id"],
                                 "extraction_id": row["extraction_id"],
                                 "row_number": row["row_number"], "formal_transaction_id": matches[0]["id"],
                                 "formal_source_url": matches[0]["source_url"],
                                 "status": "retained_existing_exact_source"})
            else:
                existing_bound = [old for old in base["transactions"] if old["id"] in bound_old_ids]
                if existing_bound:
                    blocked = deepcopy(row)
                    blocked["reasons"] = sorted(set(blocked.get("reasons", []) +
                                                     ["existing_exact_source_row_interpretation_conflict"]))
                    report["quarantined"].append(blocked)
                else:
                    remaining.append(row)
            if relation:
                existing = [old for old in base["transactions"] if old["id"] in bound_old_ids]
                relation["receipt"]["rows"].append({"extraction_id": row["extraction_id"],
                    "page_number": row.get("page_number"), "row_number": row["row_number"],
                    "old_bound_extraction_ids": sorted(bound_old_ids),
                    "status": "retained_existing_exact_source" if len(matches) == 1 else
                        "old_formal_row_interpretation_differs" if existing else "no_old_formal_fact",
                    "formal_transaction_id": matches[0]["id"] if len(matches) == 1 else None})
                relation["receipt"]["oge_formal_transaction_count"] = sum(
                    old.get("source_url") == relation["archive"]["document_url"] for old in base["transactions"])
        report["transactions"] = remaining
        receipts.append({"canonical_document_id": report["document_id"],
                         "source_sha256": report["source_sha256"],
                         "canonical_row_count": checked["source_row_count"],
                         "aliases": group["sources"]})
    # A fully retained source has no new facts to append. Its source rows are
    # still recorded above; calling an empty extraction a new report is false.
    retained_documents = {row["document_id"] for row in retained}
    selected = [report for report in canonical if report["transactions"] or report["quarantined"] or
                report["document_id"] not in retained_documents]
    audit = audit_whitehouse_278t(selected, coverage, base,
                                  annual_extractions, directory_index, annual_identity_people,
                                  source_complete_extractions=complete)
    return selected, {"schema_version": EXACT_COPY_SCHEMA, "exact_source_aliases": receipts,
                      "retained_rows": retained, "retained_existing_transaction_count": len(retained),
                      "canonical_eligibility": audit,
                      **({"cross_source_relations": [value["receipt"] for value in cross.values()]} if cross else {}),
                      **({"removed_duplicate_formal_rows": removed_duplicates} if removed_duplicates else {})}, complete, base


def audit_whitehouse_278t_exact_source_candidate(base: dict, extractions: list[dict], coverage: dict,
                                                *, annual_extractions=None, directory_index=None,
                                                annual_identity_people=None, source_bindings=None) -> dict:
    """Explicit development entry; the existing production default is unchanged."""
    return _exact_source_inputs(base, extractions, coverage, annual_extractions,
                                directory_index, annual_identity_people, source_bindings)[1]


def build_whitehouse_278t_exact_source_candidate(base: dict, extractions: list[dict], audit: dict,
                                                coverage: dict, *, data_cutoff_at: str,
                                                annual_extractions=None, directory_index=None,
                                                annual_identity_people=None, source_bindings=None) -> tuple[dict, dict]:
    """Consume exact PDF copies once and retain their source aliases and old IDs."""
    selected, expected, complete, consumer_base = _exact_source_inputs(base, extractions, coverage, annual_extractions,
                                              directory_index, annual_identity_people, source_bindings)
    if audit != expected:
        raise OgeCatalogError("White House exact-source qualification is stale or altered")
    candidate, result = build_whitehouse_278t_review_candidate(
        consumer_base, selected, expected["canonical_eligibility"], coverage, data_cutoff_at=data_cutoff_at,
        annual_extractions=annual_extractions, directory_index=directory_index,
        annual_identity_people=annual_identity_people, source_complete_extractions=complete)
    outcomes = {(report["document_id"], row["row_number"]): row
                for report in result["reports"] for row in report["rows"]}
    retained = {(row["document_id"], row["row_number"]): row for row in expected["retained_rows"]}
    aliases = deepcopy(expected["exact_source_aliases"])
    alias_row_count = 0
    for group in aliases:
        canonical = group["canonical_document_id"]
        for source in group["aliases"]:
            for row in source["rows"]:
                old = retained.get((canonical, row["row_number"]))
                outcome = outcomes.get((canonical, row["row_number"]))
                row["formal_transaction_id"] = (old["formal_transaction_id"] if old else
                    outcome["extraction_id"] if outcome and outcome["status"] in {"promoted", "retained_existing"} else None)
                row["canonical_status"] = old["status"] if old else outcome["status"] if outcome else "unresolved"
                row["reasons"] = outcome["reasons"] if outcome else []
                if source["document_id"] != canonical:
                    alias_row_count += 1
    total = sum(len(report["transactions"]) + len(report["quarantined"]) for report in extractions)
    if (result["promoted_transaction_count"] + result["quarantined_row_count"] +
            len(expected["retained_rows"]) + len(result.get("retained_existing_transaction_ids", [])) + alias_row_count != total):
        raise OgeCatalogError("White House exact-source row dispositions do not conserve")
    result.update(schema_version=EXACT_COPY_SCHEMA,
                  source_report_count=len(extractions), source_row_count=total,
                  exact_source_alias_row_count=alias_row_count,
                  exact_source_aliases=aliases,
                  retained_rows=expected["retained_rows"],
                  retained_existing_transaction_count=len(expected["retained_rows"])+len(result.get("retained_existing_transaction_ids", [])))
    if "cross_source_relations" in expected:
        result["cross_source_relations"] = expected["cross_source_relations"]
    if "removed_duplicate_formal_rows" in expected:
        result["removed_duplicate_formal_rows"] = expected["removed_duplicate_formal_rows"]
        result["candidate_is_append_only"] = False
        result["original_base_transaction_count"] = len(base["transactions"])
    return candidate, result


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
    """Use validated exact-source filing or official disclosure-date evidence."""
    if report.get("parser_version") == GENERIC_PARSER_VERSION:
        return generic_whitehouse_filing_date(report)
    source_bound = source_bound_filing_date(report)
    if source_bound is not None:
        return source_bound
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
    computed_id = _person_id(key)
    person_id = identity.get("person_id", computed_id)
    source_bound_new = identity.get("identity_basis") == "whitehouse_directory_new_oge_person"
    if person_id not in {computed_id, TRUMP_PERSON_ID} and not (
            source_bound_new and re.fullmatch(r"oge:[0-9a-f]{16}", person_id or "")):
        raise OgeCatalogError("White House 278-T person identity is unsupported")
    is_trump = person_id == TRUMP_PERSON_ID
    person = {
        "id": person_id,
        "display_name": "Donald Trump" if is_trump else identity["filer_name"],
        "short_name": "Trump" if is_trump else _short_name(identity["filer_name"]),
        "role": ("President of the United States of America" if is_trump else
                 identity["position_title"]),
        "office_type": identity["agency"],
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
    if report.get("parser_version") == GENERIC_PARSER_VERSION:
        title = row.get("account_title")
        if title:
            fact["asset_name"] = f"{row['asset_name']} — {title}"
        if "amount_low_exclusive" in row:
            fact["amount_low_exclusive"] = row["amount_low_exclusive"]
    if set(fact) != FIELDS["transactions"] | ({"amount_low_exclusive"} if "amount_low_exclusive" in fact else set()):
        raise OgeCatalogError("White House 278-T transaction field contract changed")
    return fact


def build_whitehouse_278t_review_candidate(
        base_oge_candidate: dict, extractions: list[dict], eligibility_audit: dict,
        oge_whitehouse_coverage: dict, *, data_cutoff_at: str,
        annual_extractions: list[dict] | None = None,
        directory_index: dict | None = None,
        annual_identity_people: list[dict] | None = None,
        source_complete_extractions: list[dict] | None = None) -> tuple[dict, dict]:
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
    # so original people, transactions, holdings and market facts stay
    # unchanged. Only the OGE health detail is expanded with exact new counts.
    try:
        normalize(base_oge_candidate, allow_production=True,
                  allow_empty_production=True,
                  allow_market=bool(base_oge_candidate["security_market_data"]))
    except ValueError as exc:
        raise OgeCatalogError(f"Existing OGE candidate is invalid: {exc}") from None
    expected_audit = audit_whitehouse_278t(
        extractions, oge_whitehouse_coverage, base_oge_candidate,
        annual_extractions, directory_index, annual_identity_people,
        source_complete_extractions=source_complete_extractions)
    if eligibility_audit != expected_audit:
        raise OgeCatalogError("White House 278-T qualification audit is stale or altered")
    if len(extractions) != expected_audit["report_count"]:
        raise OgeCatalogError("White House 278-T report count changed")

    candidate = deepcopy(base_oge_candidate)
    candidate["meta"]["data_cutoff_at"] = data_cutoff_at
    existing_people = {person["id"]: person for person in candidate["people"]}
    existing_people_by_name: dict[tuple[str, str], list[dict]] = {}
    for existing_person in candidate["people"]:
        name_key = _first_last(existing_person.get("display_name") or "")
        if name_key is not None:
            existing_people_by_name.setdefault(name_key, []).append(existing_person)
    existing_ids = {row["id"] for row in candidate["transactions"]}
    source_id_counts = Counter(
        row.get("extraction_id") for report in extractions
        for row in [*report["transactions"], *report["quarantined"]]
        if isinstance(row.get("extraction_id"), str) and row["extraction_id"])
    seen_documents: set[str] = set()
    audit_reports = []
    added_transactions = []
    corrected_existing = {}
    retained_existing = []
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
        directory_reuse = bool(identity and identity.get("identity_basis") ==
                               "whitehouse_directory_existing_oge_person")
        if directory_reuse:
            proposed_person = existing_people.get(identity["person_id"])
            if proposed_person is None or (
                    _first_last(proposed_person["display_name"]) !=
                    _first_last(identity["filer_name"]) or
                    proposed_person["office_type"] != identity["agency"] or
                    proposed_person["disclosure_authority"] != "oge"):
                raise OgeCatalogError("White House directory person reuse is no longer valid")
        else:
            proposed_person = _person(identity) if identity is not None else None
        if proposed_person is not None and proposed_person["id"] != identity["person_id"]:
            raise OgeCatalogError("White House 278-T official identity changed")
        person = proposed_person
        if identity is not None:
            name_key = _first_last(identity["filer_name"])
            matches = existing_people_by_name.get(name_key, []) if name_key else []
            compatible = [row for row in matches
                          if row.get("disclosure_authority") == "oge" and
                          row.get("office_type", "").casefold() ==
                          identity["agency"].casefold()]
            if len(compatible) > 1:
                exact = [row for row in compatible
                         if row.get("id") == identity["person_id"]]
                if len(exact) == 1:
                    person = exact[0]
                else:
                    doc_reasons.append("existing_oge_person_ambiguous")
                    person = None
            elif len(compatible) == 1:
                person = compatible[0]
            elif matches:
                raise OgeCatalogError(
                    "White House 278-T identity conflicts with an existing person")
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
            retained = generic_existing_fact(report, source_row, candidate["transactions"])
            if decision["status"] == "eligible" and not reasons:
                if not isinstance(row_id, str) or not _ID.fullmatch(row_id):
                    reasons.append("transaction_id_invalid")
                elif row_id in existing_ids and retained is None:
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
                if retained is not None:
                    retained_existing.append(row_id)
                    inheritance = source_row.get("legacy_inheritance", {})
                    corrections = [value for value in (inheritance.get("owner_correction"), inheritance.get("account_display_correction")) if value]
                    if corrections:
                        replacement = deepcopy(retained)
                        for correction in corrections:
                            replacement[correction["field"]] = correction["new_value"]
                        corrected_existing[row_id] = {"fact": replacement, "audit": [{**correction,
                            "formal_id": row_id, "source_sha256": report["source_sha256"],
                            "page_number": source_row["page_number"], "cells": source_row["cells"]} for correction in corrections]}
                else:
                    fact = _transaction(report, source_row, person["id"])
                    added_transactions.append(fact)
                    if person["id"] not in existing_people:
                        added_people[person["id"]] = person
            row_results.append({
                "extraction_id": row_id, "row_number": source_row.get("row_number"),
                "status": ("retained_existing" if retained is not None else "promoted")
                          if decision["status"] == "eligible" and not reasons else "quarantined",
                "annual_part7_overlap": decision.get("annual_part7_overlap") is True,
                "reasons": sorted(set(reasons)),
            })
        promoted_count = sum(row["status"] == "promoted" for row in row_results)
        retained_count = sum(row["status"] == "retained_existing" for row in row_results)
        quarantined_count = len(row_results) - promoted_count - retained_count
        audit_reports.append({
            "document_id": document_id, "source_url": report["source_url"],
            "source_sha256": report["source_sha256"],
            "filed_at": report.get("filed_at"),
            "matched_person_id": person["id"] if person else None,
            "identity_basis": identity.get("identity_basis") if identity else None,
            "source_row_count": len(source_rows), "promoted_count": promoted_count,
            "quarantined_count": quarantined_count,
            **({"parser_version": GENERIC_PARSER_VERSION, "retained_count": retained_count,
                "retained_ids": [row["extraction_id"] for row in row_results if row["status"] == "retained_existing"],
                "retained_source_bindings": {"schema_version": "whitehouse-generic-retained-source/v1",
                    "source_sha256": report["source_sha256"],
                    "legacy_extraction_canonical_sha256": report.get("legacy_source_binding",{}).get("legacy_extraction_sha256"),
                    "legacy_cache_hash_is_reference_only": True,
                    "rows": [{"formal_id": row["extraction_id"], "page_number": source["page_number"],
                        "source_rows_index": source.get("source_rows_index"),
                        "raw_cells_canonical_sha256": hashlib.sha256(json.dumps(source.get("cells"),ensure_ascii=False,
                            sort_keys=True,separators=(",", ":")).encode()).hexdigest(),
                        "basis": "same_source_raw_cells_stable_id_replayed_and_formal_projection_bound"}
                        for row,source in zip(row_results,source_rows,strict=True) if row["status"] == "retained_existing"]}}
               if report.get("parser_version") == GENERIC_PARSER_VERSION else {}),
            "document_reasons": sorted(set(doc_reasons)), "rows": row_results,
        })
    if len(added_transactions) != sum(row["promoted_count"] for row in audit_reports):
        raise OgeCatalogError("White House 278-T promoted row count is inconsistent")
    candidate["people"].extend(added_people.values())
    candidate["transactions"] = [corrected_existing[row["id"]]["fact"] if row["id"] in corrected_existing else row
                                 for row in candidate["transactions"]]
    candidate["transactions"].extend(added_transactions)
    candidate["people"].sort(key=lambda row: row["id"])
    candidate["transactions"].sort(key=lambda row: row["id"])
    candidate["source_health"].sort(key=lambda row: row["source_id"])
    total_rows = sum(row["source_row_count"] for row in audit_reports)
    promoted = sum(row["promoted_count"] for row in audit_reports)
    quarantined = sum(row["quarantined_count"] for row in audit_reports)
    if promoted + quarantined + len(retained_existing) != total_rows:
        raise OgeCatalogError("White House 278-T row conservation failed")
    base_people = {row["id"]: row for row in base_oge_candidate["people"]}
    candidate_people = {row["id"]: row for row in candidate["people"]}
    base_transactions = {row["id"]: row for row in base_oge_candidate["transactions"]}
    candidate_transactions = {row["id"]: row for row in candidate["transactions"]}
    if (len(base_people) != len(base_oge_candidate["people"]) or
            len(candidate_people) != len(candidate["people"]) or
            any(candidate_people.get(key) != value for key, value in base_people.items())):
        raise OgeCatalogError("White House 278-T candidate changed an existing person")
    if (len(base_transactions) != len(base_oge_candidate["transactions"]) or
            len(candidate_transactions) != len(candidate["transactions"]) or
            any(candidate_transactions.get(key) != corrected_existing.get(key, {}).get("fact", value)
                for key, value in base_transactions.items())):
        raise OgeCatalogError("White House 278-T candidate changed an existing transaction")
    if (candidate["reported_holdings"] != base_oge_candidate["reported_holdings"] or
            candidate["security_market_data"] != base_oge_candidate["security_market_data"]):
        raise OgeCatalogError("White House 278-T candidate changed a non-transaction array")
    base_other_health = [row for row in base_oge_candidate["source_health"]
                         if row.get("source_id") != "oge"]
    candidate_other_health = [row for row in candidate["source_health"]
                              if row.get("source_id") != "oge"]
    if sorted(base_other_health, key=lambda row: row.get("source_id", "")) != \
            sorted(candidate_other_health, key=lambda row: row.get("source_id", "")):
        raise OgeCatalogError("White House 278-T candidate changed another source health record")
    oge_health = [row for row in candidate["source_health"] if row.get("source_id") == "oge"]
    if len(oge_health) != 1 or not isinstance(oge_health[0].get("detail"), str):
        raise OgeCatalogError("White House 278-T requires one existing OGE source health record")
    prior_detail = oge_health[0]["detail"]
    oge_health[0]["detail"] = (
        f"Prior direct OGE status: {prior_detail}; "
        f"White House public 278-T: {len(audit_reports)} PDFs audited, "
        f"{promoted} new transactions qualified, {quarantined} rows quarantined; "
        f"{len(candidate['transactions'])} total OGE candidate transactions."
    )
    return candidate, {
        "schema_version": SCHEMA, "source_id": "oge",
        "catalog_sha256": expected_audit["catalog_sha256"],
        "data_cutoff_at": data_cutoff_at,
        "report_count": len(audit_reports), "source_row_count": total_rows,
        "promoted_report_count": sum(row["promoted_count"] > 0 for row in audit_reports),
        "promoted_transaction_count": promoted,
        "promoted_transaction_ids": sorted(row["id"] for row in added_transactions),
        "quarantined_row_count": quarantined,
        "added_person_count": len(added_people),
        "base_transaction_count": len(base_oge_candidate["transactions"]),
        "candidate_transaction_count": len(candidate["transactions"]),
        "annual_part7_overlap_count": expected_audit["annual_part7_overlap_count"],
        "annual_part7_incomparable_row_count":
            expected_audit["annual_part7_incomparable_row_count"],
        "existing_transaction_mutation_count": len(corrected_existing),
        "candidate_is_append_only": not corrected_existing,
        **({"retained_existing_transaction_ids": retained_existing,
            "source_field_corrections": [correction for row in corrected_existing.values() for correction in row["audit"]],
            "owner_field_corrections": [correction for row in corrected_existing.values() for correction in row["audit"] if correction["field"] == "owner"]} if retained_existing else {}),
        "source_row_conservation_complete": True,
        "reports": audit_reports,
    }
