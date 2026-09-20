"""Build a frontend-compatible Senate candidate from production review artifacts."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .senate import SenateEfdError
from .senate_reports import ELECTRONIC_EXTRACTION_SCHEMA


CANDIDATE_AUDIT_SCHEMA = "senate-efd-candidate-audit/v2"
CANDIDATE_BUILDER_VERSION = "senate-efd-candidate-2026-09-v3"
AMENDMENT_COMPARE_RULE_VERSION = "senate-amendment-pair-2026-09-v2"
AMENDMENT_SUPPLEMENT_SCHEMA = "senate-amendment-supplement/v1"
AMENDMENT_SUPPLEMENT_POINTER_SCHEMA = "senate-amendment-supplement-pointer/v1"
AMOUNT_RANGES = {
    "$1,001 - $15,000": (1001, 15000),
    "$15,001 - $50,000": (15001, 50000),
    "$50,001 - $100,000": (50001, 100000),
    "$100,001 - $250,000": (100001, 250000),
    "$250,001 - $500,000": (250001, 500000),
    "$500,001 - $1,000,000": (500001, 1000000),
    "$1,000,001 - $5,000,000": (1000001, 5000000),
    "$5,000,001 - $25,000,000": (5000001, 25000000),
}
INSTRUMENT_TYPES = {
    "Stock": "Stock",
    "Stock Option": "Option",
    "Corporate Bond": "Bond",
    "Municipal Security": "Municipal Security",
    "Non-Public Stock": "Non-Public Stock",
    "Other": "Other",
}
_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-^/]{0,31}")
_FILED = re.compile(
    r"Filed (\d{2}/\d{2}/\d{4}) @ (?:0?[1-9]|1[0-2])(?::[0-5][0-9])? (?:AM|PM)"
)
_FILED_TIMESTAMP = re.compile(
    r"Filed (\d{2}/\d{2}/\d{4}) @ (0?[1-9]|1[0-2])(?::([0-5][0-9]))? (AM|PM)"
)
_OPTION = re.compile(
    r"\bOption Type: (Call|Put)\s+Strike price:\s*\$([0-9][0-9,]*(?:\.[0-9]+)?)\s+"
    r"Expires:\s*(\d{4}-\d{2}-\d{2})\b"
)


def _canonical_asset(row: dict) -> tuple[str | None, str] | None:
    name = row.get("asset_name_raw")
    ticker = row.get("ticker_raw")
    if not isinstance(name, str) or not name.strip():
        return None
    normalized_name = " ".join(name.split())
    normalized_ticker = ticker if isinstance(ticker, str) and _TICKER.fullmatch(ticker) else None
    if normalized_ticker:
        prefix = f"{normalized_ticker} - "
        if normalized_name.startswith(prefix):
            normalized_name = normalized_name[len(prefix):].strip()
        return normalized_ticker, normalized_name
    prefix_match = re.fullmatch(r"([A-Z0-9][A-Z0-9.\-^/]{0,31})\s+-\s+(.+)", normalized_name)
    if prefix_match:
        return prefix_match.group(1), prefix_match.group(2).strip()
    return None, normalized_name


def _amendment_pair_changes(previous: dict, amended: dict) -> list[dict] | None:
    previous_rows = previous.get("transactions")
    amended_rows = amended.get("transactions")
    if (not isinstance(previous_rows, list) or not isinstance(amended_rows, list) or
            not previous_rows or len(previous_rows) != len(amended_rows)):
        return None
    expected_rows = list(range(1, len(previous_rows) + 1))
    previous_numbers = [row.get("row_number") for row in previous_rows if isinstance(row, dict)]
    amended_numbers = [row.get("row_number") for row in amended_rows if isinstance(row, dict)]
    if (sorted(previous_numbers) != expected_rows or sorted(amended_numbers) != expected_rows or
            previous_numbers != amended_numbers):
        return None
    changes = []
    stable_fields = (
        "row_number", "transaction_date", "owner_raw", "amount_raw", "asset_type_raw",
        "comment_raw", "transaction_type",
    )
    for old, new in zip(previous_rows, amended_rows):
        if (not isinstance(old, dict) or not isinstance(new, dict) or
                any(old.get(field) != new.get(field) for field in stable_fields) or
                _canonical_asset(old) != _canonical_asset(new)):
            return None
        if (old.get("transaction_type_raw") != new.get("transaction_type_raw") and
                (old.get("transaction_type") != "sale" or
                 {old.get("transaction_type_raw"), new.get("transaction_type_raw")} -
                 {"Sale (Full)", "Sale (Partial)"})):
            return None
        changed_fields = [
            field for field in ("asset_name_raw", "ticker_raw", "transaction_type_raw")
            if old.get(field) != new.get(field)
        ]
        if changed_fields:
            changes.append({"row_number": old.get("row_number"), "fields": changed_fields})
    return changes


def _filed_timestamp(raw: object) -> datetime | None:
    match = _FILED_TIMESTAMP.fullmatch(raw) if isinstance(raw, str) else None
    if match is None:
        return None
    date_value, hour, minute, meridiem = match.groups()
    try:
        return datetime.strptime(
            f"{date_value} {hour}:{minute or '00'} {meridiem}", "%m/%d/%Y %I:%M %p")
    except ValueError:
        return None


def _limited_correction_changes(previous: dict, amended: dict) -> list[dict] | None:
    """Allow one strongly anchored row correction in an otherwise identical report."""

    previous_rows = previous.get("transactions")
    amended_rows = amended.get("transactions")
    if (not isinstance(previous_rows, list) or not isinstance(amended_rows, list) or
            not previous_rows or len(previous_rows) != len(amended_rows)):
        return None
    expected_rows = list(range(1, len(previous_rows) + 1))
    previous_numbers = [row.get("row_number") for row in previous_rows if isinstance(row, dict)]
    amended_numbers = [row.get("row_number") for row in amended_rows if isinstance(row, dict)]
    if (sorted(previous_numbers) != expected_rows or sorted(amended_numbers) != expected_rows or
            previous_numbers != amended_numbers or
            len(previous_numbers) != len(previous_rows) or len(amended_numbers) != len(amended_rows)):
        return None
    changed_rows = []
    raw_fields = (
        "transaction_date", "owner_raw", "ticker_raw", "asset_name_raw", "asset_type_raw",
        "transaction_type_raw", "transaction_type", "amount_raw", "comment_raw",
    )
    for old, new in zip(previous_rows, amended_rows):
        changed_fields = [field for field in raw_fields if old.get(field) != new.get(field)]
        if not changed_fields:
            continue
        anchors = (
            old.get("transaction_date") == new.get("transaction_date"),
            old.get("owner_raw") == new.get("owner_raw"),
            _canonical_asset(old) == _canonical_asset(new),
            old.get("asset_type_raw") == new.get("asset_type_raw"),
            old.get("transaction_type") == new.get("transaction_type"),
            old.get("amount_raw") == new.get("amount_raw"),
            old.get("comment_raw") == new.get("comment_raw"),
        )
        if sum(anchors) < 3:
            return None
        changed_rows.append({"row_number": old["row_number"], "fields": changed_fields})
    return changed_rows if len(changed_rows) == 1 else None


def compare_amendment_pair(previous: dict, amended: dict) -> dict | None:
    """Return the exact allowed differences for one ordered amendment pair."""

    changes = _amendment_pair_changes(previous, amended)
    if changes is None:
        changes = _limited_correction_changes(previous, amended)
    previous_filed_at = _filed_timestamp(previous.get("filed_at_raw"))
    amended_filed_at = _filed_timestamp(amended.get("filed_at_raw"))
    if (changes is None or previous_filed_at is None or amended_filed_at is None or
            amended_filed_at <= previous_filed_at):
        return None
    return {
        "rule_version": AMENDMENT_COMPARE_RULE_VERSION,
        "previous_filed_at_raw": previous.get("filed_at_raw"),
        "current_filed_at_raw": amended.get("filed_at_raw"),
        "changed_rows": changes,
    }


def _resolve_amendments(extractions: list[dict], identity_by_document: dict[str, dict]) -> dict:
    """Resolve only uniquely comparable amendment tails; leave all other groups quarantined."""

    groups: dict[tuple[str, str], list[dict]] = {}
    for extraction in extractions:
        identity = identity_by_document.get(extraction.get("document_id"))
        if (isinstance(identity, dict) and identity.get("match_class") in {"exact", "alias"} and
                extraction.get("report_amendment_number") is not None):
            key = (identity.get("person_id"), extraction.get("report_title_date"))
            groups.setdefault(key, [])
    for extraction in extractions:
        identity = identity_by_document.get(extraction.get("document_id"))
        key = (identity.get("person_id"), extraction.get("report_title_date")) \
            if isinstance(identity, dict) else None
        if key in groups:
            groups[key].append(extraction)

    superseded: set[str] = set()
    unresolved: set[str] = set()
    chains = []
    for (person_id, title_date), reports in sorted(groups.items()):
        amendments = [report for report in reports
                      if isinstance(report.get("report_amendment_number"), int)]
        highest = max((report["report_amendment_number"] for report in amendments), default=None)
        latest = [report for report in amendments
                  if report.get("report_amendment_number") == highest]
        if highest is None or len(latest) != 1:
            unresolved.update(report["document_id"] for report in reports)
            continue
        current = latest[0]
        latest_report = current
        links = []
        chain_failed = False
        while current.get("report_amendment_number") is not None:
            current_number = current["report_amendment_number"]
            previous_number = None if current_number == 1 else current_number - 1
            predecessors = [report for report in reports
                            if report.get("report_amendment_number") == previous_number]
            if not predecessors and current_number == 1 and links:
                # A later amendment can establish that the immediately preceding
                # published amendment was superseded even if the original report
                # predates the bounded catalog window.
                break
            matches = []
            for predecessor in predecessors:
                comparison = compare_amendment_pair(predecessor, current)
                if comparison is not None:
                    matches.append((predecessor, comparison["changed_rows"],
                                    _filed_timestamp(predecessor.get("filed_at_raw"))))
            if len(matches) != 1:
                chain_failed = True
                break
            predecessor, changes, predecessor_filed_at = matches[0]
            unresolved.update(
                item["document_id"] for item in predecessors
                if item["document_id"] != predecessor["document_id"]
            )
            links.append({
                "previous_document_id": predecessor["document_id"],
                "previous_source_sha256": predecessor.get("source_sha256"),
                "previous_filed_at_raw": predecessor.get("filed_at_raw"),
                "current_document_id": current["document_id"],
                "current_source_sha256": current.get("source_sha256"),
                "current_filed_at_raw": current.get("filed_at_raw"),
                "changed_rows": changes,
            })
            current = predecessor
        if chain_failed or not links:
            unresolved.update(report["document_id"] for report in reports)
            continue
        chain_superseded = [link["previous_document_id"] for link in links]
        superseded.update(chain_superseded)
        chains.append({
            "person_id": person_id,
            "report_title_date": title_date,
            "current_document_id": latest_report["document_id"],
            "current_source_sha256": latest_report.get("source_sha256"),
            "current_filed_at_raw": latest_report.get("filed_at_raw"),
            "current_amendment_number": highest,
            "superseded_document_ids": chain_superseded,
            "row_count": len(latest_report["transactions"]),
            "links": links,
        })
    return {"superseded": superseded, "unresolved": unresolved, "chains": chains}


def _read_json(path: Path, description: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise SenateEfdError(f"{description} is invalid") from None
    if not isinstance(value, dict):
        raise SenateEfdError(f"{description} must be an object")
    return value


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _load_active_amendment_supplement(review_root: Path, parser_version: str) -> dict:
    pointer_path = review_root / "senate_efd" / "amendment_supplements" / "current.json"
    if not pointer_path.is_file():
        return {"manifest": None, "extractions": []}
    pointer = _read_json(pointer_path, "Senate amendment supplement pointer")
    supplement_sha = pointer.get("supplement_sha256")
    expected_manifest = (
        f"senate_efd/amendment_supplements/manifests/{supplement_sha}.json"
        if isinstance(supplement_sha, str) else None
    )
    if (set(pointer) != {
            "schema_version", "source_id", "status", "supplement_sha256", "manifest_path",
            "parser_version", "compare_rule_version", "selected_predecessor_count",
            } or
            pointer.get("schema_version") != AMENDMENT_SUPPLEMENT_POINTER_SCHEMA or
            pointer.get("source_id") != "senate_efd" or pointer.get("status") != "active" or
            not isinstance(supplement_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", supplement_sha) or
            pointer.get("manifest_path") != expected_manifest or
            pointer.get("parser_version") != parser_version or
            pointer.get("compare_rule_version") != AMENDMENT_COMPARE_RULE_VERSION):
        raise SenateEfdError("Senate amendment supplement pointer is invalid")
    manifest = _read_json(review_root / expected_manifest, "Senate amendment supplement manifest")
    unhashed = {key: value for key, value in manifest.items() if key != "supplement_sha256"}
    targets = manifest.get("targets")
    selected_ids = manifest.get("selected_document_ids")
    if (manifest.get("schema_version") != AMENDMENT_SUPPLEMENT_SCHEMA or
            manifest.get("source_id") != "senate_efd" or manifest.get("status") != "ready" or
            manifest.get("reasons") != [] or manifest.get("supplement_sha256") != supplement_sha or
            _canonical_sha256(unhashed) != supplement_sha or
            manifest.get("parser_version") != parser_version or
            manifest.get("compare_rule_version") != AMENDMENT_COMPARE_RULE_VERSION or
            not isinstance(targets, list) or not isinstance(selected_ids, list) or
            manifest.get("target_count") != len(targets) or
            manifest.get("selected_predecessor_count") != len(selected_ids) or
            pointer.get("selected_predecessor_count") != len(selected_ids) or
            any(not isinstance(document_id, str) for document_id in selected_ids) or
            len(selected_ids) != len(set(selected_ids))):
        raise SenateEfdError("Senate amendment supplement manifest is invalid")
    extractions = []
    for document_id in selected_ids:
        matches = [target["content_matches"][0] for target in targets
                   if (isinstance(target, dict) and target.get("content_match_count") == 1 and
                       isinstance(target.get("content_matches"), list) and
                       len(target["content_matches"]) == 1 and
                       target["content_matches"][0].get("document_id") == document_id)]
        if len(matches) != 1:
            raise SenateEfdError("Senate amendment supplement selection is not unique")
        source_sha = matches[0].get("source_sha256")
        extraction_key = hashlib.sha256(
            f"{document_id}:{source_sha}:{parser_version}".encode("ascii")).hexdigest()
        path = (review_root / "senate_efd" / "amendment_supplements" /
                "extractions" / f"{extraction_key}.json")
        extraction = _read_json(path, "Senate amendment supplement extraction")
        if (extraction.get("schema_version") != ELECTRONIC_EXTRACTION_SCHEMA or
                extraction.get("source_id") != "senate_efd" or
                extraction.get("document_id") != document_id or
                extraction.get("source_sha256") != source_sha or
                extraction.get("parser_version") != parser_version or
                extraction.get("report_amendment_number") is not None or
                extraction.get("evidence_complete") is not True or
                not isinstance(extraction.get("transactions"), list)):
            raise SenateEfdError("Senate amendment supplement extraction is invalid")
        extractions.append(extraction)
    return {"manifest": manifest, "extractions": extractions}


def _filed_date(raw: object) -> str:
    match = _FILED.fullmatch(raw) if isinstance(raw, str) else None
    if match is None:
        raise SenateEfdError("Senate PTR has no valid official filing timestamp")
    try:
        return datetime.strptime(match.group(1), "%m/%d/%Y").date().isoformat()
    except ValueError:
        raise SenateEfdError("Senate PTR has an invalid official filing date") from None


def _person(identity: dict) -> dict:
    person_id = identity.get("person_id")
    display_name = identity.get("filer_name")
    official_name = identity.get("official_name")
    evidence_url = identity.get("evidence_url")
    parsed = urlsplit(evidence_url) if isinstance(evidence_url, str) else None
    if (not isinstance(person_id, str) or not re.fullmatch(r"senate:[A-Z][0-9]{6}", person_id) or
            not isinstance(display_name, str) or not display_name.strip() or
            not isinstance(official_name, str) or not parsed or parsed.scheme != "https" or
            parsed.hostname != "bioguide.congress.gov"):
        raise SenateEfdError("Senate candidate identity is incomplete")
    short_name = official_name.split(" (", 1)[0].strip()
    return {
        "id": person_id,
        "display_name": display_name,
        "short_name": short_name,
        "role": "U.S. Senator",
        "office_type": "Congress",
        "chamber": "Senate",
        "party": identity.get("party"),
        "state": identity.get("state"),
        "disclosure_authority": "senate_efd",
        "priority": False,
        "priority_reason": None,
        "portrait_url": None,
    }


def _transaction(row: dict, extraction: dict, identity: dict) -> tuple[dict | None, list[str]]:
    reasons: list[str] = []
    qualification_status = row.get("qualification_status")
    raw_reasons = row.get("quarantine_reasons")
    valid_reasons = (isinstance(raw_reasons, list) and
                     all(isinstance(reason, str) and reason for reason in raw_reasons))
    if not valid_reasons:
        reasons.append("qualification_reasons_invalid")
        raw_reasons = []
    if qualification_status == "eligible":
        if raw_reasons:
            reasons.extend(["qualification_state_inconsistent", *raw_reasons])
    elif qualification_status == "quarantined":
        reasons.extend(raw_reasons or ["qualification_state_inconsistent"])
    else:
        reasons.append("qualification_state_inconsistent")
    extraction_id = row.get("extraction_id")
    if not isinstance(extraction_id, str) or not re.fullmatch(r"senate-ptr:[0-9a-f]{24}", extraction_id):
        reasons.append("extraction_id_invalid")
    amount = AMOUNT_RANGES.get(row.get("amount_raw"))
    if amount is None:
        reasons.append("amount_range_not_supported")
    transaction_type = row.get("transaction_type")
    if transaction_type not in {"purchase", "sale"}:
        reasons.append("transaction_type_not_supported")
    owner_raw = row.get("owner_raw")
    if owner_raw not in {"Self", "Joint", "Spouse", "Child"}:
        reasons.append("owner_not_supported")
    owner = "Dependent Child" if owner_raw == "Child" else owner_raw
    asset_name = row.get("asset_name_raw")
    if not isinstance(asset_name, str) or not asset_name.strip():
        reasons.append("asset_name_invalid")
    instrument = INSTRUMENT_TYPES.get(row.get("asset_type_raw"))
    if instrument is None:
        reasons.append("instrument_type_not_supported")
    ticker = row.get("ticker_raw")
    if ticker is not None and (not isinstance(ticker, str) or not _TICKER.fullmatch(ticker)):
        reasons.append("ticker_invalid")
    transaction_date = row.get("transaction_date")
    transaction_day = None
    filed_date = None
    try:
        transaction_day = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        filed_date = _filed_date(extraction.get("filed_at_raw"))
        filed_day = datetime.strptime(filed_date, "%Y-%m-%d").date()
        if transaction_day > filed_day:
            reasons.append("date_sequence_invalid")
    except (TypeError, ValueError):
        reasons.append("transaction_date_invalid")

    option_fields = {}
    if instrument == "Option":
        option = _OPTION.search(asset_name or "")
        if option is None:
            reasons.append("option_details_incomplete")
        else:
            option_type, strike, expiration = option.groups()
            strike_value = float(strike.replace(",", ""))
            try:
                if transaction_day is None or datetime.strptime(expiration, "%Y-%m-%d").date() < transaction_day:
                    reasons.append("option_expiration_invalid")
            except ValueError:
                reasons.append("option_expiration_invalid")
            option_fields = {
                "option_type": option_type,
                "strike_price": int(strike_value) if strike_value.is_integer() else strike_value,
                "expiration_date": expiration,
            }
            asset_name = (asset_name[:option.start()] + asset_name[option.end():]).strip()
    if reasons:
        return None, sorted(set(reasons))

    return {
        "id": extraction_id,
        "filing_id": extraction["document_id"],
        "person_id": identity["person_id"],
        "owner": owner,
        "asset_name": asset_name,
        "ticker": ticker,
        "ticker_mapping_basis": "filing_explicit" if ticker else None,
        "instrument_type": instrument,
        "transaction_type": transaction_type,
        **option_fields,
        "transaction_date": transaction_date,
        "filed_at": f"{filed_date}T00:00:00Z",
        "amount_low": amount[0],
        "amount_high": amount[1],
        "position_effect": "unknown",
        "position_effect_basis": None,
        "source_id": "senate_efd",
        "source": "U.S. Senate eFD",
        "source_url": extraction["source_url"],
        "verification_status": "official_matched",
    }, []


def build_senate_candidate(
        review_root: Path, state_status: dict, base: dict) -> tuple[dict, dict]:
    """Build a conservative Senate source candidate and its machine audit."""

    review_root = review_root.resolve()
    status = _read_json(review_root / "status" / "senate_efd.json", "Senate review status")
    state_catalog = state_status.get("catalog")
    state_reports = state_status.get("reports")
    if (status.get("schema_version") != "senate-review-run/v1" or
            status.get("source_id") != "senate_efd" or
            status.get("status") != "catalog_ready_for_review" or
            state_status.get("schema_version") != "senate-source-run/v1" or
            state_status.get("source_id") != "senate_efd" or
            state_status.get("status") != "catalog_ready_for_review" or
            state_status.get("collection_enabled") is not True or
            state_status.get("terms_acknowledged") is not True or
            not isinstance(state_catalog, dict) or not isinstance(state_reports, dict) or
            status.get("catalog_record_count") != state_catalog.get("record_count") or
            status.get("catalog_sha256") != state_catalog.get("sha256") or
            status.get("report_entrypoint_count") != state_reports.get("entrypoint_count") or
            status.get("report_entrypoint_pending_count") != state_reports.get("pending_count") or
            status.get("report_evidence_count") != state_reports.get("evidence_count") or
            status.get("extracted_transaction_count") != state_reports.get("last_batch_transactions") or
            status.get("report_entrypoint_pending_count") != 0 or
            status.get("report_entrypoint_failure_count") != 0 or
            status.get("report_extraction_failure_count") != 0):
        raise SenateEfdError("Senate review queue is not ready for a candidate")
    catalog_sha = status.get("catalog_sha256")
    roster_sha = status.get("identity_roster_sha256")
    congress_roster_sha = status.get("identity_congress_roster_sha256")
    state_historical_roster = state_status.get("historical_roster")
    if congress_roster_sha is not None and (
            not isinstance(state_historical_roster, dict) or
            state_historical_roster.get("status") != "ok" or
            state_historical_roster.get("source_id") != "congress_gov_members" or
            state_historical_roster.get("sha256") != congress_roster_sha):
        raise SenateEfdError("Senate Congress.gov roster does not match source state")
    parser_version = status.get("report_parser_version")
    identity_binding = hashlib.sha256(
        f"{roster_sha}:{congress_roster_sha or ''}".encode("ascii")).hexdigest()
    identity_path = (review_root / "senate_efd" / "identities" /
                     str(catalog_sha) / f"{identity_binding}.json")
    legacy_identity_path = (review_root / "senate_efd" / "identities" /
                            str(catalog_sha) / f"{roster_sha}.json")
    if congress_roster_sha is None and not identity_path.is_file() and legacy_identity_path.is_file():
        identity_path = legacy_identity_path
    identities_doc = _read_json(
        identity_path,
        "Senate identity batch",
    )
    identities = identities_doc.get("identities")
    if (identities_doc.get("schema_version") != "senate-efd-identities/v1" or
            identities_doc.get("source_id") != "senate_efd" or
            identities_doc.get("catalog_sha256") != catalog_sha or
            identities_doc.get("roster_sha256") != roster_sha or
            identities_doc.get("congress_roster_sha256") != congress_roster_sha or
            (congress_roster_sha is not None and
             (not isinstance(congress_roster_sha, str) or
              not re.fullmatch(r"[0-9a-f]{64}", congress_roster_sha))) or
            not isinstance(identities, list) or
            identities_doc.get("report_count") != status.get("catalog_record_count")):
        raise SenateEfdError("Senate identity batch does not match review status")
    identity_by_document = {item.get("document_id"): item for item in identities
                            if isinstance(item, dict)}
    if len(identity_by_document) != len(identities):
        raise SenateEfdError("Senate identity batch contains duplicate documents")

    extraction_paths = sorted((review_root / "senate_efd" / "extractions").glob(
        f"*/*/{parser_version}.json"))
    if len(extraction_paths) != status.get("report_evidence_count"):
        raise SenateEfdError("Senate extraction count does not match review status")

    extraction_values = []
    for path in extraction_paths:
        extraction = _read_json(path, "Senate extraction artifact")
        document_id = extraction.get("document_id")
        source_sha = extraction.get("source_sha256")
        expected_url = f"https://efdsearch.senate.gov/search/view/ptr/{document_id}/"
        if (extraction.get("source_id") != "senate_efd" or
                path.parent.parent.name != document_id or
                path.parent.name != source_sha or
                path.stem != extraction.get("parser_version") or
                not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha) or
                extraction.get("source_url") != expected_url):
            raise SenateEfdError("Senate extraction artifact does not match its evidence path")
        extraction_values.append(extraction)
    if any(not isinstance(item.get("transactions"), list) for item in extraction_values):
        raise SenateEfdError("Senate extraction artifact has no transaction array")
    extraction_document_ids = [item.get("document_id") for item in extraction_values]
    if (len(set(extraction_document_ids)) != len(extraction_document_ids) or
            any(document_id not in identity_by_document for document_id in extraction_document_ids)):
        raise SenateEfdError("Senate extractions do not map uniquely to the identity batch")
    if sum(len(item["transactions"]) for item in extraction_values) != status.get("extracted_transaction_count"):
        raise SenateEfdError("Senate extracted transaction count does not match review status")
    primary_by_document = {item["document_id"]: item for item in extraction_values}
    supplement = _load_active_amendment_supplement(review_root, parser_version)
    supplement_extractions = supplement["extractions"]
    supplement_manifest = supplement["manifest"]
    supplement_ids = {item["document_id"] for item in supplement_extractions}
    if supplement_ids & set(primary_by_document):
        raise SenateEfdError("Senate amendment supplement duplicates a primary extraction")
    resolution_identities = dict(identity_by_document)
    expected_supplement_links = set()
    if supplement_manifest is not None:
        for target in supplement_manifest["targets"]:
            amendment_id = target.get("amendment_document_id")
            amendment = primary_by_document.get(amendment_id)
            identity = identity_by_document.get(amendment_id)
            match = target["content_matches"][0]
            predecessor = next((item for item in supplement_extractions
                                if item["document_id"] == match.get("document_id")), None)
            if (not isinstance(amendment, dict) or not isinstance(identity, dict) or
                    amendment.get("source_sha256") != target.get("amendment_source_sha256") or
                    amendment.get("report_title_date") != target.get("report_title_date") or
                    identity.get("person_id") != target.get("person_id") or
                    not isinstance(predecessor, dict) or
                    predecessor.get("source_sha256") != match.get("source_sha256") or
                    predecessor.get("report_title_date") != target.get("report_title_date")):
                raise SenateEfdError("Senate amendment supplement no longer matches current review")
            resolution_identities[predecessor["document_id"]] = {
                "document_id": predecessor["document_id"],
                "person_id": target["person_id"],
                "match_class": "exact",
                "status": "matched_automatically",
            }
            expected_supplement_links.add((
                predecessor["document_id"], predecessor["source_sha256"],
                amendment_id, amendment["source_sha256"],
            ))
    amendment_resolution = _resolve_amendments(
        [*extraction_values, *supplement_extractions], resolution_identities)
    superseded_documents = amendment_resolution["superseded"]
    unresolved_amendment_documents = amendment_resolution["unresolved"]
    if supplement_ids & unresolved_amendment_documents or not supplement_ids <= superseded_documents:
        raise SenateEfdError("Senate amendment supplement did not resolve every predecessor")
    actual_supplement_links = {
        (link.get("previous_document_id"), link.get("previous_source_sha256"),
         link.get("current_document_id"), link.get("current_source_sha256"))
        for chain in amendment_resolution["chains"] for link in chain["links"]
        if link.get("previous_document_id") in supplement_ids
    }
    if actual_supplement_links != expected_supplement_links:
        raise SenateEfdError("Senate amendment supplement links differ from the active manifest")
    primary_superseded_documents = superseded_documents - supplement_ids
    if not primary_superseded_documents <= set(primary_by_document):
        raise SenateEfdError("Senate amendment resolver superseded an unknown report")

    transactions = []
    people: dict[str, dict] = {}
    seen_transactions: set[str] = set()
    report_reasons: Counter[str] = Counter()
    row_reasons: Counter[str] = Counter()
    quarantined_reports = []
    quarantined_rows = []
    qualified_rows = []
    report_quarantined_transaction_count = 0
    qualified_reports = 0
    fully_qualified_reports = 0
    partially_qualified_reports = 0
    row_only_quarantined_reports = 0
    empty_eligible_reports = 0
    superseded_report_transaction_count = 0
    for extraction in extraction_values:
        document_id = extraction.get("document_id")
        identity = identity_by_document.get(document_id)
        if (extraction.get("schema_version") != ELECTRONIC_EXTRACTION_SCHEMA or
                extraction.get("parser_version") != parser_version or not extraction.get("evidence_complete")):
            raise SenateEfdError("Senate extraction artifact has an unsupported schema or state")
        if (not isinstance(identity, dict) or
                identity.get("match_class") not in {"exact", "alias"} or
                identity.get("status") != "matched_automatically" or
                identity.get("roster_sha256") != roster_sha or
                (congress_roster_sha is not None and
                 identity.get("congress_roster_sha256") != congress_roster_sha)):
            reason = ("identity_unresolved" if isinstance(identity, dict) and
                      identity.get("match_class") == "unresolved" else
                      "identity_not_automatically_matched")
            report_reasons[reason] += 1
            report_quarantined_transaction_count += len(extraction.get("transactions", []))
            quarantined_reports.append({
                "document_id": document_id, "reasons": [reason],
                "source_sha256": extraction.get("source_sha256"),
            })
            continue
        if document_id in primary_superseded_documents:
            superseded_report_transaction_count += len(extraction.get("transactions", []))
            continue
        if document_id in unresolved_amendment_documents:
            report_reasons["amendment_relationship_pending"] += 1
            report_quarantined_transaction_count += len(extraction.get("transactions", []))
            quarantined_reports.append({
                "document_id": document_id, "reasons": ["amendment_relationship_pending"],
                "source_sha256": extraction.get("source_sha256"),
            })
            continue
        try:
            filed_date = _filed_date(extraction.get("filed_at_raw"))
        except SenateEfdError:
            filed_date = None
        if filed_date != extraction.get("portal_listed_date"):
            report_reasons["filed_date_conflicts_catalog"] += 1
            report_quarantined_transaction_count += len(extraction.get("transactions", []))
            quarantined_reports.append({
                "document_id": document_id, "reasons": ["filed_date_conflicts_catalog"],
                "source_sha256": extraction.get("source_sha256"),
            })
            continue
        person = _person(identity)
        person_id = person["id"]
        if person_id in people and people[person_id] != person:
            raise SenateEfdError("Senate identity changed across report artifacts")
        report_rows = 0
        report_quarantined_rows = 0
        for row in extraction.get("transactions", []):
            if not isinstance(row, dict):
                raise SenateEfdError("Senate extraction contains an invalid transaction")
            transaction, reasons = _transaction(row, extraction, identity)
            if reasons:
                row_reasons.update(reasons)
                quarantined_rows.append({
                    "document_id": document_id,
                    "extraction_id": row.get("extraction_id"),
                    "reasons": reasons,
                    "source_sha256": extraction.get("source_sha256"),
                    "filed_at_raw": extraction.get("filed_at_raw"),
                    "filed_at_precision": "date",
                })
                report_quarantined_rows += 1
                continue
            transaction_id = transaction["id"]
            if transaction_id in seen_transactions:
                raise SenateEfdError("Senate candidate transaction ID is duplicated")
            seen_transactions.add(transaction_id)
            transactions.append(transaction)
            qualified_rows.append({
                "transaction_id": transaction_id,
                "document_id": document_id,
                "source_sha256": extraction.get("source_sha256"),
                "source_url": extraction.get("source_url"),
                "filed_at_raw": extraction.get("filed_at_raw"),
                "filed_at_precision": "date",
            })
            report_rows += 1
        if report_rows:
            people[person_id] = person
            qualified_reports += 1
            if report_quarantined_rows:
                partially_qualified_reports += 1
            else:
                fully_qualified_reports += 1
        elif report_quarantined_rows:
            row_only_quarantined_reports += 1
        else:
            empty_eligible_reports += 1

    cutoff = state_status.get("run_at")
    try:
        parsed_cutoff = datetime.fromisoformat(str(cutoff).replace("Z", "+00:00"))
        if parsed_cutoff.tzinfo is None:
            raise ValueError
    except ValueError:
        raise SenateEfdError("Senate source state has no valid data cutoff") from None
    disposition_total = (len(quarantined_reports) + len(primary_superseded_documents) +
                         fully_qualified_reports +
                         partially_qualified_reports + row_only_quarantined_reports +
                         empty_eligible_reports)
    if disposition_total != len(extraction_paths):
        raise SenateEfdError("Senate report dispositions do not cover every extraction")
    input_transaction_count = sum(len(item["transactions"]) for item in extraction_values)
    transaction_disposition_total = (
        len(transactions) + report_quarantined_transaction_count +
        len(quarantined_rows) + superseded_report_transaction_count
    )
    if transaction_disposition_total != input_transaction_count:
        raise SenateEfdError("Senate transaction dispositions do not cover every extracted row")
    candidate = deepcopy(base)
    if not isinstance(candidate.get("meta"), dict) or candidate["meta"].get("is_demo") is not False:
        raise SenateEfdError("Senate candidate base must be a production input")
    candidate["meta"]["data_cutoff_at"] = cutoff
    candidate["meta"]["subtitle"] = "Senate电子PTR真实候选；纸面报告、其他来源和行情仍在回填"
    candidate["people"] = [people[key] for key in sorted(people)]
    candidate["transactions"] = sorted(transactions, key=lambda item: item["id"])
    candidate["reported_holdings"] = []
    candidate["security_market_data"] = []
    source_health = {
        "source_id": "senate_efd",
        "source": "U.S. Senate eFD",
        "source_type": "official_disclosure",
        "source_url": "https://efdsearch.senate.gov/search/home/",
        "status": "partial",
        "last_checked_at": cutoff,
        "last_successful_sync_at": cutoff,
        "data_cutoff_at": cutoff,
        "detail": (f"{status['catalog_record_count']} PTR catalog records and entrypoints archived; "
                   f"{status['report_evidence_count']} electronic reports parsed; "
                   f"{len(transactions)} transactions automatically qualified; "
                   f"{report_quarantined_transaction_count + len(quarantined_rows)} transactions quarantined "
                   f"across {len(quarantined_reports) + partially_qualified_reports + row_only_quarantined_reports} affected reports; "
                   f"{superseded_report_transaction_count} transactions superseded by verified amendments; "
                   f"{status['catalog_record_count'] - status['report_evidence_count']} paper viewers pending pages."),
    }
    health = candidate.get("source_health")
    if not isinstance(health, list):
        raise SenateEfdError("Senate candidate base has no source health array")
    candidate["source_health"] = [source_health if item.get("source_id") == "senate_efd" else item
                                  for item in health]
    if not any(item.get("source_id") == "senate_efd" for item in health):
        candidate["source_health"].append(source_health)
    audit = {
        "schema_version": CANDIDATE_AUDIT_SCHEMA,
        "builder_version": CANDIDATE_BUILDER_VERSION,
        "amendment_compare_rule_version": AMENDMENT_COMPARE_RULE_VERSION,
        "source_id": "senate_efd",
        "catalog_sha256": catalog_sha,
        "roster_sha256": roster_sha,
        "congress_roster_sha256": congress_roster_sha,
        "identity_binding_sha256": identity_binding,
        "parser_version": parser_version,
        "data_cutoff_at": cutoff,
        "catalog_record_count": status["catalog_record_count"],
        "electronic_report_count": len(extraction_paths),
        "input_transaction_count": input_transaction_count,
        "qualified_report_count": qualified_reports,
        "fully_qualified_report_count": fully_qualified_reports,
        "partially_qualified_report_count": partially_qualified_reports,
        "row_only_quarantined_report_count": row_only_quarantined_reports,
        "empty_eligible_report_count": empty_eligible_reports,
        "resolved_amendment_chain_count": len(amendment_resolution["chains"]),
        "resolved_amendment_chains": amendment_resolution["chains"],
        "superseded_report_count": len(primary_superseded_documents),
        "superseded_report_transaction_count": superseded_report_transaction_count,
        "amendment_supplement_sha256": (
            supplement_manifest.get("supplement_sha256")
            if supplement_manifest is not None else None),
        "amendment_supplement_report_count": len(supplement_extractions),
        "amendment_supplement_transaction_count": sum(
            len(item["transactions"]) for item in supplement_extractions),
        "candidate_person_count": len(people),
        "candidate_transaction_count": len(transactions),
        "qualified_rows": qualified_rows,
        "quarantined_report_count": len(quarantined_reports),
        "quarantined_report_reasons": dict(sorted(report_reasons.items())),
        "quarantined_reports": quarantined_reports,
        "report_quarantined_transaction_count": report_quarantined_transaction_count,
        "quarantined_row_count": len(quarantined_rows),
        "quarantined_row_reasons": dict(sorted(row_reasons.items())),
        "quarantined_rows": quarantined_rows,
        "quarantined_transaction_count": report_quarantined_transaction_count + len(quarantined_rows),
        "paper_viewer_count": status["catalog_record_count"] - status["report_evidence_count"],
    }
    return candidate, audit


def load_senate_candidate(
        review_root: Path, state_status_path: Path, base_path: Path) -> tuple[dict, dict]:
    return build_senate_candidate(
        review_root,
        _read_json(state_status_path, "Senate source state"),
        _read_json(base_path, "Senate candidate base"),
    )
