"""House annual Schedule A/B status from version-bound saved qualifications.

This observes existing artifacts; no collection, PDF parsing or OCR is performed.
Section counts are deliberately separate from report-wide completeness.
"""
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from .house import document_url
from .disclosure_status_house import _read, _time, _stamp, _version_path


def is_annual_transaction(row: dict) -> bool:
    return row.get("source_id") == "house_clerk" and (
        str(row.get("id", "")).startswith("house-annual-b:") or
        str(row.get("source_url", "")).startswith(
            "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/"))


def _source(value: dict, key: tuple) -> None:
    year, docid, sha = key
    source = value.get("source", {})
    if (value.get("source_sha256") != sha or source.get("source_id") != "house_clerk" or
            source.get("filing_year") != year or source.get("document_id") != docid or
            source.get("filing_type") != "O" or source.get("report_type") != "Annual Report" or
            source.get("source_url") != document_url(year, "O", docid)):
        raise ValueError("House annual artifact source/version binding mismatch")


def _selected_version(versions: dict) -> str | None:
    if len(versions) == 1:
        return next(iter(versions))
    ranks = [(_time(meta.get("retrieved_at")), sha) for sha, meta in versions.items()]
    if ranks and all(time is not None for time, _ in ranks):
        ranks.sort()
        if sum(time == ranks[-1][0] for time, _ in ranks) == 1:
            return ranks[-1][1]
    return None


def build_house_annual_status(evidence: Path, review: Path,
                             transactions: list[dict], holdings: list[dict]) -> list[dict]:
    archives = defaultdict(dict)
    observed = []
    base = evidence / "house_clerk/financial-reports"
    for path in sorted(base.glob("*/*/*.json")):
        year, docid, sha = _version_path(path, base)
        meta = _read(path)
        if meta.get("filing_type") != "O":
            continue
        if (meta.get("report_type") != "Annual Report" or meta.get("source_id") != "house_clerk" or
                meta.get("filing_year") != year or meta.get("document_id") != docid or
                meta.get("sha256") != sha or meta.get("source_url") != document_url(year, "O", docid) or
                meta.get("archive_path") != f"house_clerk/financial-reports/{year}/{docid}/{sha}.pdf"):
            raise ValueError("House annual archive receipt binding mismatch")
        archives[year, docid][sha] = meta
        if _time(meta.get("retrieved_at")):
            observed.append(_time(meta["retrieved_at"]))

    artifacts = {}
    keys = set(archives)
    for kind in ("holding_extractions", "holding_qualifications", "holding_section_observations", "holding_identities"):
        base = review / "house_clerk" / kind
        artifacts[kind] = {}
        for path in sorted(base.glob("*/*/*.json")):
            key = _version_path(path, base)
            value = _read(path)
            if kind != "holding_identities":
                _source(value, key)
            elif value.get("document_id") != key[1]:
                raise ValueError("House annual identity binding mismatch")
            artifacts[kind][key] = value
            keys.add(key[:2])

    # Historical caches are observations, not an implicit chain of published
    # predecessors. Select versions before the stateful candidate consumer.
    selected_versions = {key: _selected_version(versions) for key, versions in archives.items()}
    inputs, input_keys = [], []
    for key, obs in artifacts["holding_section_observations"].items():
        if selected_versions.get(key[:2]) != key[2]:
            continue
        payload = {"observation": obs,
                   "extraction": artifacts["holding_extractions"].get(key, {}),
                   "identity": artifacts["holding_identities"].get(key, {})}
        if "source_word_pages" in obs:
            pages = obs["source_word_pages"]
            digest = hashlib.sha256(json.dumps(pages, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":")).encode()).hexdigest()
            if digest != obs.get("source_word_pages_sha256"):
                raise ValueError("House annual saved word pages digest differs")
            payload["pages"] = pages
        inputs.append(payload)
        input_keys.append(key)
    from .house_annual_sections import consume_schedule_b_inputs
    selected_ids = {row.get("observation_id") for payload in inputs
                    for row in payload["observation"].get("schedule_b_rows", [])}
    # Supplied rows outside the selected source remain real retained facts.
    # Never turn an unsubmitted historical cache into such a predecessor, or
    # silently replace an actual old-version publication with fresh observations.
    retained = [row for row in transactions if row.get("source_id") == "house_clerk"
                and str(row.get("id", "")).startswith("house-annual-b:")
                and row["id"] not in selected_ids]
    audits, tx_facts = {}, {}
    # The annual consumer's filing key is document_id. Status identities also
    # include year, so a reused Clerk ID must not borrow another year's facts.
    for year in sorted({key[0] for key in input_keys}):
        positions = [i for i,key in enumerate(input_keys) if key[0] == year]
        selected_inputs = [inputs[i] for i in positions]
        urls = {payload["observation"]["source"]["source_url"] for payload in selected_inputs}
        audit = consume_schedule_b_inputs(selected_inputs, transactions=transactions, reported_holdings=holdings,
            existing_annual_transactions=[row for row in retained if row.get("source_url") in urls])
        audits.update(zip([input_keys[i] for i in positions], audit["reports"], strict=True))
        for row in audit["transactions"]:
            if row["id"] in tx_facts and tx_facts[row["id"]] != row:
                raise ValueError("House annual source row ID conflicts across report years")
            tx_facts[row["id"]] = row
    holding_facts = {}
    reports = []
    for year, docid in sorted(keys):
        versions = archives.get((year, docid), {})
        selected = selected_versions.get((year, docid))
        reasons = set()
        if len(versions) > 1:
            reasons.add("multiple_source_versions")
            if selected is None:
                reasons.add("source_version_order_unknown")
        record = {"record_key": f"house_annual:{year}:{docid}",
                  "report_id": f"house_annual:{year}:{docid}", "document_id": docid,
                  "document_type": "Annual Report", "source_url": document_url(year,"O",docid),
                  "catalog_state": "unknown", "acquisition_state": "archived" if versions else "unknown",
                  "extraction_state": "unknown", "qualification_state": "not_evaluated",
                  "qualified_transaction_count": None, "qualified_holding_count": None,
                  "quarantined_row_count": None, "source_versions": sorted(versions),
                  "selected_source_version": selected, "publication_binding": "verified",
                  "publication_transaction_ids": [], "publication_holding_ids": [],
                  "holding_coverage_status": "unknown", "transaction_coverage_status": "unknown"}
        if selected:
            key = year, docid, selected
            extraction = artifacts["holding_extractions"].get(key)
            qualification = artifacts["holding_qualifications"].get(key)
            for kind in ("holding_extractions", "holding_qualifications", "holding_section_observations"):
                value = artifacts[kind].get(key)
                if value is not None and any(value["source"].get(field) != versions[selected].get(field)
                        for field in ("filed_date", "filing_type", "report_type")):
                    raise ValueError("House annual selected archive context differs from review source")
            record["extraction_state"] = "extracted" if extraction else "not_attempted"
            filed = versions[selected].get("filed_date")
            if filed:
                record.update(filed_date=filed, filed_date_basis="archive_receipt_filing_date")
            any_qualified = False
            partial = False
            if qualification is not None and extraction is not None:
                from .house_holdings import qualify_financial_report
                if qualify_financial_report(extraction, qualification.get("identity", {})) != qualification:
                    raise ValueError("House annual holding qualification differs from saved extraction")
                # Partial-value permission requires the same source replay as the candidate.
                if "holding_coverage" in qualification:
                    from .house_annual_sections import observe_annual_sections
                    from .house_holdings import extract_schedule_a_pages
                    obs = artifacts["holding_section_observations"].get(key, {})
                    pages = obs.get("source_word_pages", [])
                    if (not pages or extract_schedule_a_pages(pages, source_sha256=selected)[0] != extraction.get("rows") or
                            observe_annual_sections(pages, extraction) != obs or
                            extraction.get("source_text_coverage") != obs.get("source_text_coverage")):
                        raise ValueError("House annual partial holding source observations differ")
                eligible = qualification.get("production_eligible") is True
                rows = qualification["holdings"] if eligible else []
                record["qualified_holding_count"] = len(rows)
                for row in rows:
                    holding_facts[row["id"]] = (key, row)
                coverage = qualification.get("holding_coverage")
                record["holding_coverage_status"] = "partial_official_value_unknown" if coverage else (
                    "complete" if eligible and qualification.get("report_complete") is True else "unknown")
                if coverage:
                    record["holding_coverage"] = coverage
                    record["official_value_unknown_holding_count"] = len(coverage["unresolved_rows"])
                record["quarantined_holding_row_count"] = len(qualification["quarantined"])
                reasons.update(qualification.get("qualification_reasons", []))
                any_qualified |= bool(rows)
                partial |= bool(qualification["quarantined"]) or not eligible
            elif qualification is not None:
                reasons.add("qualification_without_extraction")
            annual = audits.get(key)
            if annual:
                ids = [row["formal_transaction_id"] for row in annual["rows"]
                       if row.get("candidate_disposition") in {"promoted", "retained_existing"}]
                if len(ids) != len(set(ids)):
                    raise ValueError("House annual transaction qualification IDs duplicated")
                observed_b = bool(annual["rows"]) or any(section.get("section") == "B"
                    for page in (annual.get("source_text_coverage") or {}).get("pages", [])
                    for section in page.get("sections", []))
                record["qualified_transaction_count"] = len(ids) if observed_b else None
                record["qualified_transaction_ids"] = sorted(ids)
                record["transaction_coverage_status"] = annual["coverage_status"] if observed_b else "unknown"
                if not observed_b:
                    reasons.add("schedule_b_section_not_observed")
                record["observed_transaction_row_count"] = annual["observed_row_count"]
                reasons.update(annual["report_blocking_reasons"])
                for row in annual["rows"]:
                    reasons.update(row.get("candidate_blocking_reasons", []))
                record["quarantined_transaction_row_count"] = sum(row["candidate_disposition"] in {
                    "quarantined", "blocked_pending_relationship"} for row in annual["rows"])
                any_qualified |= bool(ids)
                partial |= (annual["coverage_status"] != "complete" or
                            bool(record["quarantined_transaction_row_count"]) or bool(annual["report_blocking_reasons"]))
            if any_qualified:
                record["qualification_state"] = "partial" if partial or qualification is None or annual is None else "qualified"
            elif qualification is not None or annual is not None:
                rejected = (record.get("quarantined_holding_row_count", 0) or
                            record.get("quarantined_transaction_row_count", 0) or
                            bool((annual or {}).get("report_blocking_reasons")))
                record["qualification_state"] = "quarantined" if rejected else "unknown"
            # A scoped Schedule A/B result cannot attest that the entire report has zero transactions.
            if not any_qualified:
                reasons.add("annual_empty_qualified_rows_not_zero_transaction_attestation")
        elif not versions:
            record["qualification_state"] = "unknown"
            reasons.add("review_without_archive_receipt")
        else:
            record["qualification_state"] = "unknown"
        record["reason_codes"] = sorted(reasons)
        reports.append(record)

    by_url = {row["source_url"]: row for row in reports}
    unmatched = []
    seen = set()
    for row in transactions:
        if not is_annual_transaction(row):
            continue
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError("House annual publication transaction ID missing or duplicated")
        seen.add(identifier)
        report = by_url.get(row.get("source_url"))
        if report is None or row.get("filing_id") != report["document_id"]:
            unmatched.append(identifier)
            continue
        report["publication_transaction_ids"].append(identifier)
        if identifier not in report.get("qualified_transaction_ids", []):
            report["reason_codes"] = sorted(set(report["reason_codes"]) | {"publication_not_qualified_at_selected_version"})
        elif row != tx_facts.get(identifier):
            raise ValueError("House annual published transaction differs from selected source qualification")
    seen.clear()
    unmatched_holdings = []
    for row in holdings:
        if row.get("source_id") != "house_clerk":
            continue
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError("House annual publication holding ID missing or duplicated")
        seen.add(identifier)
        report = by_url.get(row.get("source_url"))
        if report is None or row.get("filing_id") != report["document_id"]:
            unmatched_holdings.append(identifier)
            continue
        report["publication_holding_ids"].append(identifier)
        expected = holding_facts.get(identifier)
        if expected is None:
            report["reason_codes"] = sorted(set(report["reason_codes"]) | {"publication_not_qualified_at_selected_version"})
        elif row != expected[1]:
            raise ValueError("House annual published holding differs from selected source qualification")
    if not reports and not unmatched and not unmatched_holdings:
        return []
    return [{"scope_id": "house_annual", "source_id": "house_clerk",
             "scope": {"document_type": "Annual Report", "filing_types": ["O"],
                       "sections": ["Schedule A", "Schedule B"],
                       "source_version_selection": "latest_archive_retrieved_at; ambiguous chronology remains unknown",
                       "universe_complete": False},
             "observed_at": _stamp(max(observed) if observed else None),
             "limitations": ["Annual scope is limited to archived or saved O/Annual Report artifacts; no catalog completeness claim.",
                             "Section observations do not prove whole-report completeness or zero transactions.",
                             "Holding and transaction quarantine units may overlap; combined total remains unknown."],
             "reports": reports, "unmatched_publication_transaction_ids": sorted(unmatched),
             "unmatched_publication_holding_ids": sorted(unmatched_holdings)}]
