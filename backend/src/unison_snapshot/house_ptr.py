"""Conservative House PTR extraction into a review queue, never directly into production."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .house import HouseIndexError

SCHEMA = "house-ptr-extraction/v1"
PARSER_VERSION = "house-electronic-ptr-2026-02"
DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
AMOUNT_RE = re.compile(r"^\$(\d[\d,]*)\s*-\s*\$(\d[\d,]*)$")
ASSET_RE = re.compile(r"^(.*?)\s*(?:\(([A-Z][A-Z0-9.\-^/]{0,15})\))?\s*\[([A-Z0-9]{2})\]\s*$")
OWNER_CODES = {"": "Self", "SP": "Spouse", "DC": "Dependent Child", "JT": "Joint"}
TYPE_CODES = {"P": "purchase", "S": "sale", "E": "exchange"}
ASSET_TYPES = {
    "ST": "Stock", "OP": "Option", "CS": "Corporate Security", "CT": "Cryptocurrency",
    "RS": "Restricted Stock Unit", "AB": "Asset-Backed Security", "ET": "Exchange Traded Note",
    "MF": "Mutual Fund", "OT": "Other",
}
REVIEW_SCHEMA = "house-ptr-review/v1"
CORRECTION_FIELDS = {"owner", "asset_name", "ticker", "instrument_type", "transaction_type",
                     "transaction_date", "notification_date", "amount_low", "amount_high"}
REVISION_ACTIONS = {"replace_prior", "standalone_correction"}


def _clean(value: str) -> str:
    return " ".join(value.replace("\x00", "").split())


def _line(words: list[dict], *, x0: float, x1: float, top: float, tolerance: float = 2.0) -> str:
    selected = [word for word in words if x0 <= float(word["x0"]) < x1
                and abs(float(word["top"]) - top) <= tolerance]
    return _clean(" ".join(str(word["text"]) for word in sorted(selected, key=lambda word: float(word["x0"]))))


def parse_word_pages(metadata: dict, source_sha256: str, pages: list[dict], *,
                     copy_allowed: bool | None) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise HouseIndexError("House PTR source hash is invalid")
    document_id = str(metadata.get("document_id", ""))
    if (metadata.get("source_id") != "house_clerk" or metadata.get("filing_type") != "P" or
            not re.fullmatch(r"[0-9]{1,20}", document_id)):
        raise HouseIndexError("House PTR metadata is invalid")
    if not pages:
        raise HouseIndexError("House PTR contains no pages")
    first_text = _clean(" ".join(str(word["text"]) for word in pages[0].get("words", [])))
    large_heading = [_clean(str(word["text"])) for word in pages[0].get("words", [])
                     if float(word.get("size", 0)) >= 18]
    title_matches = "Periodic Transaction Report" in first_text or large_heading[:3] == ["P", "T", "R"]
    if not title_matches or f"#{document_id}" not in first_text:
        raise HouseIndexError("House PTR header does not match its archived identity")

    page_data: list[dict] = []
    anchors: list[dict] = []
    for page_index, page in enumerate(pages):
        width = float(page.get("width", 0))
        height = float(page.get("height", 0))
        words = page.get("words")
        if width <= 0 or height <= 0 or not isinstance(words, list):
            raise HouseIndexError("House PTR page geometry is invalid")
        page_data.append({"width": width, "height": height, "words": words})
        for top in sorted({float(word["top"]) for word in words
                           if 0.53 * width <= float(word["x0"]) < 0.62 * width
                           and DATE_RE.fullmatch(_clean(str(word["text"])))}):
            anchors.append({"page_index": page_index, "top": top})

    extracted: list[dict] = []
    for position, anchor in enumerate(anchors):
        page_index, top = anchor["page_index"], anchor["top"]
        page_number = page_index + 1
        current = page_data[page_index]
        width, height, words = current["width"], current["height"], current["words"]
        following = anchors[position + 1] if position + 1 < len(anchors) else None
        last_page_index = following["page_index"] if following else page_index
        block: list[tuple[int, dict]] = []
        evidence_segments: list[dict] = []
        for segment_page_index in range(page_index, last_page_index + 1):
            segment = page_data[segment_page_index]
            start = top - 2 if segment_page_index == page_index else 0.14 * segment["height"]
            if following and segment_page_index == following["page_index"]:
                end = following["top"] - 2
            elif segment_page_index == page_index and following is None:
                end = min(segment["height"], top + 120)
                following_section = [float(word["top"]) for word in segment["words"]
                                     if top + 10 < float(word["top"]) < end
                                     and float(word.get("size", 0)) >= 11]
                if following_section:
                    end = min(end, min(following_section) - 2)
            else:
                end = segment["height"]
            footer_tops = [float(word["top"]) for word in segment["words"]
                           if "asset-type-codes.aspx" in _clean(str(word["text"]))]
            if footer_tops:
                footer_top = min(footer_tops)
                if start < footer_top < end:
                    end = footer_top - 2
            block.extend((segment_page_index, word) for word in segment["words"]
                         if start <= float(word["top"]) < end)
            evidence_segments.append({"page": segment_page_index + 1,
                                      "bbox_points": [round(0.04 * segment["width"], 2), round(start, 2),
                                                       round(0.90 * segment["width"], 2), round(end, 2)]})
        transaction_date = _line(words, x0=0.53 * width, x1=0.62 * width, top=top)
        notification_date = _line(words, x0=0.62 * width, x1=0.725 * width, top=top)
        raw_type = _line(words, x0=0.42 * width, x1=0.53 * width, top=top)
        reported_transaction_id = _line(words, x0=0.04 * width, x1=0.105 * width, top=top)
        raw_owner = _line(words, x0=0.105 * width, x1=0.165 * width, top=top)
        date_size = max((float(word.get("size", 0)) for word in words
                         if abs(float(word["top"]) - top) <= 2 and
                         0.53 * width <= float(word["x0"]) < 0.62 * width), default=0)
        asset_words = [(segment_page_index, word) for segment_page_index, word in block
                       if 0.165 * page_data[segment_page_index]["width"] <= float(word["x0"]) <
                       0.42 * page_data[segment_page_index]["width"]
                       and float(word.get("size", date_size)) >= date_size - 0.2]
        asset = _clean(" ".join(str(word["text"]) for segment_page_index, word in sorted(
            asset_words, key=lambda item: (item[0], round(float(item[1]["top"]), 1), float(item[1]["x0"])))))
        amount_words = [(segment_page_index, word) for segment_page_index, word in block
                        if 0.725 * page_data[segment_page_index]["width"] <= float(word["x0"]) <
                        0.90 * page_data[segment_page_index]["width"]
                        and float(word.get("size", date_size)) >= date_size - 0.2]
        amount = _clean(" ".join(str(word["text"]) for segment_page_index, word in sorted(
            amount_words, key=lambda item: (item[0], round(float(item[1]["top"]), 1), float(item[1]["x0"])))))
        note_words = [(segment_page_index, word) for segment_page_index, word in block
                      if 0.165 * page_data[segment_page_index]["width"] <= float(word["x0"]) <
                      0.90 * page_data[segment_page_index]["width"]
                      and float(word.get("size", date_size)) < date_size - 0.2]
        note_lines: dict[tuple[int, float], list[dict]] = {}
        for segment_page_index, word in note_words:
            note_lines.setdefault((segment_page_index, round(float(word["top"]), 1)), []).append(word)
        details: dict[str, str] = {}
        last_detail: str | None = None
        patterns = (("filing_status", re.compile(r"^(?:F|Filing)\s+(?:S|Status):\s*(.*)$", re.I)),
                    ("subholding_of", re.compile(r"^(?:S|Subholding)\s+(?:O|Of):\s*(.*)$", re.I)),
                    ("location", re.compile(r"^(?:L|Location):\s*(.*)$", re.I)),
                    ("description", re.compile(r"^(?:D|Description):\s*(.*)$", re.I)))
        for line_words in (note_lines[key] for key in sorted(note_lines)):
            line = _clean(" ".join(str(word["text"]) for word in sorted(line_words, key=lambda word: float(word["x0"]))))
            if re.match(r"^\*\s*For the complete list of asset type abbreviations", line, re.I):
                last_detail = None
                continue
            matched = False
            for name, pattern in patterns:
                match = pattern.match(line)
                if match:
                    details[name] = match.group(1).strip()
                    last_detail = name
                    matched = True
                    break
            if not matched and last_detail and line:
                details[last_detail] = (details[last_detail] + " " + line).strip()
        try:
            transaction_iso = datetime.strptime(transaction_date, "%m/%d/%Y").date().isoformat()
            notification_iso = datetime.strptime(notification_date, "%m/%d/%Y").date().isoformat()
        except ValueError:
            raise HouseIndexError("House PTR row has an invalid date") from None
        amount_match = AMOUNT_RE.fullmatch(amount)
        asset_match = ASSET_RE.fullmatch(asset)
        type_code = raw_type[:1]
        if not amount_match or not asset_match or type_code not in TYPE_CODES:
            failures = ",".join(name for name, valid in (("amount", amount_match is not None),
                ("asset", asset_match is not None), ("transaction_type", type_code in TYPE_CODES)) if not valid)
            raise HouseIndexError(
                f"House PTR page {page_number} row {position + 1} has unsupported {failures} layout")
        asset_name, ticker, asset_type_code = asset_match.groups()
        reasons = ["manual_row_review_required"]
        if raw_owner not in OWNER_CODES:
            reasons.append("unknown_owner_code")
        if asset_type_code not in ASSET_TYPES:
            reasons.append("unknown_asset_type_code")
        if ticker is None:
            reasons.append("ticker_not_explicitly_reported")
        if reported_transaction_id and not re.fullmatch(r"[0-9]{1,30}", reported_transaction_id):
            reasons.append("reported_transaction_id_invalid")
        if not details.get("filing_status"):
            reasons.append("filing_status_missing")
        elif details["filing_status"].lower() != "new":
            reasons.append("non_new_filing_requires_revision_resolution")
        stable = "|".join((source_sha256, str(page_number), f"{top:.2f}", asset,
                           transaction_date, raw_type, raw_owner, amount,
                           reported_transaction_id, details.get("filing_status", "")))
        extracted.append({
            "extraction_id": "house-ptr:" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24],
            "reported_transaction_id": reported_transaction_id or None,
            "filing_status": details.get("filing_status"),
            "owner_code": raw_owner or None,
            "owner": OWNER_CODES.get(raw_owner),
            "asset_name": asset_name.strip(),
            "ticker": ticker,
            "ticker_mapping_basis": "filing_explicit" if ticker else None,
            "asset_type_code": asset_type_code,
            "instrument_type": ASSET_TYPES.get(asset_type_code),
            "transaction_type_raw": raw_type,
            "transaction_type": TYPE_CODES[type_code],
            "transaction_date": transaction_iso,
            "notification_date": notification_iso,
            "amount_low": int(amount_match.group(1).replace(",", "")),
            "amount_high": int(amount_match.group(2).replace(",", "")),
            "subholding_of": details.get("subholding_of"),
            "location": details.get("location"),
            "description": details.get("description"),
            "evidence": {"page": page_number, "pages": [segment["page"] for segment in evidence_segments],
                         "bbox_points": evidence_segments[0]["bbox_points"], "segments": evidence_segments},
            "verification_status": "awaiting_manual_review",
            "review_reasons": reasons,
        })
    if not extracted:
        raise HouseIndexError("House PTR contains no recognized transaction rows")
    if len({row["extraction_id"] for row in extracted}) != len(extracted):
        raise HouseIndexError("House PTR produced duplicate extraction IDs")
    review_reasons = ["manual_row_review_required", "source_use_clearance_required"]
    if copy_allowed is False:
        review_reasons.append("source_pdf_copy_permission_disabled")
    return {
        "schema_version": SCHEMA,
        "parser_version": PARSER_VERSION,
        "source": {key: metadata.get(key) for key in ("source_id", "source_url", "document_id",
            "filer_name", "state_district", "filing_year", "filed_date", "archive_path")},
        "source_sha256": source_sha256,
        "source_pdf_copy_allowed": copy_allowed,
        "transactions": extracted,
        "review": {"status": "awaiting_manual_review", "production_eligible": False,
                   "reasons": review_reasons},
    }


def parse_archived_pdf(archive_root: Path, metadata_path: Path) -> dict:
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise HouseIndexError("House PTR metadata file is invalid") from None
    relative = metadata.get("archive_path")
    if not isinstance(relative, str) or "\\" in relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise HouseIndexError("House PTR archive path is invalid")
    root = archive_root.resolve()
    pdf_path = (root / relative).resolve()
    if root not in pdf_path.parents:
        raise HouseIndexError("House PTR archive path escapes its root")
    try:
        content = pdf_path.read_bytes()
    except OSError:
        raise HouseIndexError("Archived House PTR is unavailable") from None
    source_sha = hashlib.sha256(content).hexdigest()
    if source_sha != metadata.get("sha256"):
        raise HouseIndexError("Archived House PTR hash does not match its metadata")
    try:
        import pdfplumber
    except ImportError:
        raise HouseIndexError("PTR extraction requires the optional pdfplumber package") from None
    try:
        with pdfplumber.open(pdf_path) as document:
            copy_allowed = getattr(document.doc, "is_extractable", None)
            pages = [{"width": page.width, "height": page.height,
                      "words": page.extract_words(x_tolerance=2, y_tolerance=2, extra_attrs=["size"])}
                     for page in document.pages]
    except Exception as exc:
        raise HouseIndexError(f"House PTR extraction failed: {type(exc).__name__}") from None
    return parse_word_pages(metadata, source_sha, pages, copy_allowed=copy_allowed)


def make_review_template(extraction: dict) -> dict:
    if extraction.get("schema_version") != SCHEMA or extraction.get("review", {}).get("production_eligible") is not False:
        raise HouseIndexError("House PTR extraction is not a review-queue document")
    return {
        "schema_version": REVIEW_SCHEMA,
        "source_sha256": extraction.get("source_sha256"),
        "parser_version": extraction.get("parser_version"),
        "document_id": extraction.get("source", {}).get("document_id"),
        "identity": {"person_id": None, "evidence_url": None},
        "filing": {"filed_at": None},
        "source_use_clearance": {"status": "pending", "reference": None},
        "review": {"decision": "pending", "reviewed_by": None, "reviewed_at": None, "note": None},
        "rows": [{"extraction_id": row["extraction_id"], "decision": "pending",
                  "corrections": {}, "note": None,
                  "revision": {
                      "action": "pending" if row.get("reported_transaction_id") or
                      str(row.get("filing_status", "")).lower() != "new" else "none",
                      "prior_record_id": None,
                  }}
                 for row in extraction.get("transactions", [])],
    }


def _review_time(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise HouseIndexError(f"House PTR review requires {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HouseIndexError(f"House PTR review has invalid {field}") from None
    if parsed.tzinfo is None:
        raise HouseIndexError(f"House PTR review requires a timezone for {field}")
    return parsed


def promote_review(extraction: dict, review: dict) -> dict:
    if review.get("schema_version") != REVIEW_SCHEMA or extraction.get("schema_version") != SCHEMA:
        raise HouseIndexError("House PTR review schema is invalid")
    for field, expected in (("source_sha256", extraction.get("source_sha256")),
                            ("parser_version", extraction.get("parser_version")),
                            ("document_id", extraction.get("source", {}).get("document_id"))):
        if review.get(field) != expected:
            raise HouseIndexError(f"House PTR review {field} does not match its extraction")
    identity = review.get("identity") or {}
    person_id, evidence_url = identity.get("person_id"), identity.get("evidence_url")
    if not isinstance(person_id, str) or not re.fullmatch(r"house:[A-Za-z0-9._-]{2,80}", person_id):
        raise HouseIndexError("House PTR review requires a stable House person ID")
    parsed_url = urlsplit(evidence_url) if isinstance(evidence_url, str) else None
    identity_hosts = {"bioguide.congress.gov", "clerk.house.gov", "www.house.gov", "house.gov", "congress.gov", "www.congress.gov"}
    if not parsed_url or parsed_url.scheme != "https" or parsed_url.hostname not in identity_hosts:
        raise HouseIndexError("House PTR review requires an official identity evidence URL")
    clearance = review.get("source_use_clearance") or {}
    if clearance.get("status") != "approved" or not isinstance(clearance.get("reference"), str) \
            or not clearance["reference"].strip():
        raise HouseIndexError("House PTR review requires recorded source-use clearance")
    decision = review.get("review") or {}
    if decision.get("decision") != "approved" or not isinstance(decision.get("reviewed_by"), str) \
            or not decision["reviewed_by"].strip():
        raise HouseIndexError("House PTR review is not approved by a named reviewer")
    reviewed_at = _review_time(decision.get("reviewed_at"), "reviewed_at")
    filed_at_text = (review.get("filing") or {}).get("filed_at")
    filed_at = _review_time(filed_at_text, "filed_at")
    if filed_at.date().isoformat() != extraction.get("source", {}).get("filed_date"):
        raise HouseIndexError("House PTR reviewed filed_at must preserve the official filing date")
    extracted = {row.get("extraction_id"): row for row in extraction.get("transactions", [])}
    review_rows = review.get("rows")
    if not isinstance(review_rows, list) or len(review_rows) != len(extracted):
        raise HouseIndexError("House PTR review must decide every extracted row")
    indexed_reviews = {row.get("extraction_id"): row for row in review_rows if isinstance(row, dict)}
    if set(indexed_reviews) != set(extracted) or len(indexed_reviews) != len(review_rows):
        raise HouseIndexError("House PTR review row identities do not match the extraction")
    transactions = []
    revisions = []
    for extraction_id, source_row in extracted.items():
        row_review = indexed_reviews[extraction_id]
        row_decision = row_review.get("decision")
        note = row_review.get("note")
        if row_decision == "rejected":
            if not isinstance(note, str) or not note.strip():
                raise HouseIndexError("Rejected House PTR rows require a review note")
            continue
        if row_decision != "accepted":
            raise HouseIndexError("Every House PTR row must be accepted or rejected")
        corrections = row_review.get("corrections") or {}
        if not isinstance(corrections, dict) or set(corrections) - CORRECTION_FIELDS:
            raise HouseIndexError("House PTR row contains unsupported corrections")
        if corrections and (not isinstance(note, str) or not note.strip()):
            raise HouseIndexError("Corrected House PTR rows require a review note")
        row = {**source_row, **corrections}
        filing_status = row.get("filing_status")
        if not isinstance(filing_status, str) or not filing_status.strip():
            raise HouseIndexError("Accepted House PTR rows require a filing status")
        revision = row_review.get("revision")
        if not isinstance(revision, dict):
            raise HouseIndexError("Accepted House PTR rows require a revision decision")
        revision_action = revision.get("action")
        prior_record_id = revision.get("prior_record_id")
        requires_revision = bool(row.get("reported_transaction_id")) or filing_status.lower() != "new"
        if requires_revision:
            if revision_action not in REVISION_ACTIONS:
                raise HouseIndexError("Amended House PTR rows require a resolved revision action")
            if revision_action == "replace_prior":
                if not isinstance(prior_record_id, str) or not re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9:._/-]{2,199}", prior_record_id):
                    raise HouseIndexError("House PTR replacement revisions require a prior record ID")
            elif prior_record_id is not None:
                raise HouseIndexError("Standalone House PTR corrections cannot name a prior record ID")
            if revision_action == "standalone_correction" and (not isinstance(note, str) or not note.strip()):
                raise HouseIndexError("Standalone House PTR corrections require a review note")
            revisions.append({
                "extraction_id": extraction_id,
                "reported_transaction_id": row.get("reported_transaction_id"),
                "filing_status": filing_status,
                "action": revision_action,
                "prior_record_id": prior_record_id,
            })
        elif revision_action != "none" or prior_record_id is not None:
            raise HouseIndexError("New House PTR rows require revision action none")
        if row.get("owner") not in set(OWNER_CODES.values()) or row.get("transaction_type") not in set(TYPE_CODES.values()):
            raise HouseIndexError("House PTR reviewed row has an invalid owner or transaction type")
        ticker = row.get("ticker")
        if ticker is not None and (not isinstance(ticker, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-^/]{0,31}", ticker)):
            raise HouseIndexError("House PTR reviewed row has an invalid ticker")
        for date_field in ("transaction_date", "notification_date"):
            try:
                datetime.strptime(row[date_field], "%Y-%m-%d")
            except (KeyError, TypeError, ValueError):
                raise HouseIndexError(f"House PTR reviewed row has an invalid {date_field}") from None
        low, high = row.get("amount_low"), row.get("amount_high")
        if type(low) is not int or type(high) is not int or not 0 <= low <= high:
            raise HouseIndexError("House PTR reviewed row has an invalid amount range")
        transactions.append({
            "id": extraction_id, "filing_id": review["document_id"], "person_id": person_id,
            "owner": row["owner"], "asset_name": row["asset_name"], "ticker": ticker,
            "ticker_mapping_basis": "filing_explicit" if ticker else None,
            "instrument_type": row.get("instrument_type"), "transaction_type": row["transaction_type"],
            "transaction_date": row["transaction_date"], "filed_at": filed_at_text,
            "amount_low": low, "amount_high": high, "position_effect": "unknown",
            "position_effect_basis": None, "source_id": "house_clerk", "source": "U.S. House Clerk",
            "source_url": extraction["source"]["source_url"], "verification_status": "official_matched",
        })
    if not transactions:
        raise HouseIndexError("House PTR review accepted no transaction rows")
    audit = {"schema_version": "house-ptr-reviewed/v1", "source_sha256": review["source_sha256"],
             "parser_version": review["parser_version"], "document_id": review["document_id"],
             "person_id": person_id, "identity_evidence_url": evidence_url,
             "source_use_clearance_reference": clearance["reference"],
             "reviewed_by": decision["reviewed_by"], "reviewed_at": reviewed_at.isoformat(),
             "accepted_count": len(transactions), "rejected_count": len(extracted) - len(transactions),
             "revision_count": len(revisions)}
    return {"audit": audit, "transactions": transactions, "revisions": revisions}
