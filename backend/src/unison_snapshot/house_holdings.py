"""House annual-report holdings: official archive, strict extraction and qualification."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .house import (MAX_PDF_BYTES, HouseFilingCandidate, HouseIndexError, document_url,
                    parse_index, validate_pdf)
from .house_ptr import DETERMINISTIC_IDENTITY_BASES, OWNER_CODES

EXTRACTION_SCHEMA = "house-holding-extraction/v1"
QUALIFICATION_SCHEMA = "house-holding-qualification/v1"
PARSER_VERSION = "house-annual-holdings-2026-01"
SUPPORTED_REPORT_TYPES = {"O": "Annual Report"}
ASSET_RE = re.compile(
    r"^(.*?)\s*(?:\(([A-Z][A-Z0-9.\-^/]{0,31})\))?\s*\[([A-Z0-9]{2})\]\s*$")
VALUE_RANGE_RE = re.compile(r"^\$(\d[\d,]*)\s*-\s*\$(\d[\d,]*)$")
VALUE_EXACT_RE = re.compile(r"^\$(\d[\d,]*)(?:\.00)?$")
ASSET_TYPES = {
    "ST": "Stock", "EF": "Exchange Traded Fund", "MF": "Mutual Fund",
    "PE": "Pension", "BA": "Bank Account", "CS": "Corporate Security",
    "CT": "Cryptocurrency", "RS": "Restricted Stock Unit", "OP": "Option",
    "AB": "Asset-Backed Security", "ET": "Exchange Traded Note", "RP": "Real Property",
    "OL": "Other", "OT": "Other", "FN": "Annuity",
}
REPORT_PERIOD_BASIS = "annual_member_pdf_filing_year_end"


def _clean(value: object) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())


class HouseFinancialDocumentClient:
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def download(self, candidate: HouseFilingCandidate) -> tuple[bytes, dict[str, str]]:
        if (candidate.filing_type not in SUPPORTED_REPORT_TYPES or
                candidate.document_url != document_url(candidate.filing_year,
                                                        candidate.filing_type,
                                                        candidate.document_id)):
            raise HouseIndexError("House holding report is not a supported index-confirmed filing")
        request = urllib.request.Request(candidate.document_url, headers={
            "Accept": "application/pdf", "User-Agent": "unison-house-holdings/0.1"}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status != 200 or response.geturl() != candidate.document_url:
                    raise HouseIndexError("House holding report returned an unexpected response")
                content = response.read(MAX_PDF_BYTES + 1)
                headers = {name.lower(): value for name, value in response.headers.items()
                           if name.lower() in {"etag", "last-modified", "content-type"}}
        except urllib.error.HTTPError as exc:
            raise HouseIndexError(f"House holding report returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise HouseIndexError("House holding report is unavailable") from None
        validate_pdf(content)
        return content, headers


def archive_financial_document(root: Path, candidate: HouseFilingCandidate, content: bytes,
                               headers: dict[str, str], retrieved_at: str | None = None) -> dict:
    if (candidate.filing_type not in SUPPORTED_REPORT_TYPES or
            candidate.document_url != document_url(candidate.filing_year,
                                                    candidate.filing_type,
                                                    candidate.document_id)):
        raise HouseIndexError("House holding report is not a supported index-confirmed filing")
    validate_pdf(content)
    root = root.resolve()
    sha = hashlib.sha256(content).hexdigest()
    folder = root / "house_clerk" / "financial-reports" / str(candidate.filing_year) / candidate.document_id
    target = folder / f"{sha}.pdf"
    folder.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_bytes() != content:
        raise HouseIndexError("Archived House holding report hash collision")
    if not target.exists():
        with tempfile.NamedTemporaryFile(dir=folder, prefix=f".{sha}.", suffix=".tmp",
                                         delete=False) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        temporary.replace(target)
    metadata = {
        "source_id": "house_clerk", "source_url": candidate.document_url,
        "document_id": candidate.document_id, "filing_type": candidate.filing_type,
        "report_type": SUPPORTED_REPORT_TYPES[candidate.filing_type],
        "filer_name": candidate.filer_name, "state_district": candidate.state_district,
        "filing_year": candidate.filing_year, "filed_date": candidate.filed_date,
        "retrieved_at": retrieved_at or datetime.now(timezone.utc).isoformat(),
        "sha256": sha, "byte_length": len(content), "headers": headers,
        "archive_path": target.relative_to(root).as_posix(),
        "verification_status": "official_raw_unparsed",
    }
    meta_path = target.with_suffix(".json")
    if meta_path.exists():
        try:
            existing = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise HouseIndexError("Archived House holding metadata is invalid") from None
        stable = ("source_id", "source_url", "document_id", "filing_type", "report_type",
                  "filing_year", "sha256", "byte_length", "archive_path", "verification_status")
        if any(existing.get(field) != metadata[field] for field in stable):
            raise HouseIndexError("Archived House holding metadata conflicts with its content")
        return existing
    encoded = json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=folder, prefix=f".{sha}.", suffix=".tmp",
                                     delete=False) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(meta_path)
    return metadata


def archive_indexed_financial_report(root: Path, year: int, index_sha: str, document_id: str,
                                     *, client: HouseFinancialDocumentClient | None = None) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", index_sha) or not re.fullmatch(r"[0-9]{1,20}", document_id):
        raise HouseIndexError("Invalid House index hash or holding report document ID")
    index_path = root.resolve() / "house_clerk" / "index" / str(year) / f"{index_sha}.zip"
    try:
        content = index_path.read_bytes()
    except OSError:
        raise HouseIndexError("Archived House index is unavailable") from None
    if hashlib.sha256(content).hexdigest() != index_sha:
        raise HouseIndexError("Archived House index hash does not match its path")
    matches = [row for row in parse_index(content, year) if row.document_id == document_id]
    if len(matches) != 1 or matches[0].filing_type not in SUPPORTED_REPORT_TYPES:
        raise HouseIndexError("Document is not a supported full report in the archived House index")
    pdf, headers = (client or HouseFinancialDocumentClient()).download(matches[0])
    return archive_financial_document(root, matches[0], pdf, headers)


def _value(value: str) -> tuple[int, int] | None:
    normalized = _clean(value)
    match = VALUE_RANGE_RE.fullmatch(normalized)
    if match:
        return tuple(int(part.replace(",", "")) for part in match.groups())  # type: ignore[return-value]
    match = VALUE_EXACT_RE.fullmatch(normalized)
    if match:
        exact = int(match.group(1).replace(",", ""))
        return exact, exact
    return None


def annual_report_period_from_pdf(full_text: str, metadata: dict) -> tuple[str, str]:
    """Return a period only when the PDF itself closes the annual-member semantics.

    House Ethics guidance defines the reporting period for annual filers as the preceding
    calendar year and Schedule A values at December 31.  This initial parser therefore requires
    the PDF to say Member, Annual Report, a filing year, and a filing date in the next calendar
    year.  The index is only a cross-check; it is not the source of the period end.
    """
    text = _clean(full_text)
    patterns = {
        "document_id": r"Filing ID\s*#\s*([0-9]{1,20})\b",
        "status": r"Status:\s*([^:]+?)\s+State/District:",
        "report_type": r"Filing Type:\s*([^:]+?)\s+Filing Year:",
        "filing_year": r"Filing Year:\s*([0-9]{4})\b",
        "filed_date": r"Filing Date:\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})\b",
    }
    values = {}
    for name, pattern in patterns.items():
        match = re.search(pattern, text)
        if match is None:
            raise HouseIndexError(f"House annual report PDF does not expose {name}")
        values[name] = _clean(match.group(1))
    try:
        pdf_filed = datetime.strptime(values["filed_date"], "%m/%d/%Y").date()
        metadata_filed = datetime.strptime(str(metadata.get("filed_date")), "%Y-%m-%d").date()
        pdf_year = int(values["filing_year"])
    except (TypeError, ValueError):
        raise HouseIndexError("House annual report PDF has invalid filing fields") from None
    if (values["document_id"] != str(metadata.get("document_id")) or
            values["status"] != "Member" or values["report_type"] != "Annual Report" or
            pdf_year != metadata.get("filing_year") or pdf_filed != metadata_filed):
        raise HouseIndexError("House annual report PDF fields conflict with official index metadata")
    if pdf_filed.year != pdf_year + 1:
        raise HouseIndexError("House annual report PDF does not establish a preceding-calendar-year period")
    return f"{pdf_year}-12-31", REPORT_PERIOD_BASIS


def _lines(words: list[dict]) -> list[tuple[float, list[dict]]]:
    result: list[tuple[float, list[dict]]] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        if not result or abs(float(word["top"]) - result[-1][0]) > 2.5:
            result.append((float(word["top"]), [word]))
        else:
            result[-1][1].append(word)
    return result


def extract_schedule_a_pages(pages: list[dict], *, source_sha256: str) -> tuple[list[dict], bool]:
    """Extract Schedule A rows from the Clerk's native-text electronic annual layout."""
    extracted: list[dict] = []
    found_header = False
    explicit_none = False
    for page_index, page in enumerate(pages):
        width, height, words = float(page.get("width", 0)), float(page.get("height", 0)), page.get("words")
        if width <= 0 or height <= 0 or not isinstance(words, list):
            raise HouseIndexError("House annual report page geometry is invalid")
        lines = _lines(words)
        header_top = None
        asset_x = owner_x = value_x = income_x = None
        for top, line_words in lines:
            by_token: dict[str, float] = {}
            for word in line_words:
                token = _clean(word.get("text"))
                by_token[token] = min(by_token.get(token, float("inf")), float(word["x0"]))
            tokens = set(by_token)
            if {"Asset", "Owner", "Value"}.issubset(tokens):
                header_top = top
                asset_x, owner_x, value_x = by_token["Asset"], by_token["Owner"], by_token["Value"]
                income_positions = [float(word["x0"]) for word in line_words
                                    if _clean(word.get("text")) == "Income"]
                income_x = min(income_positions) if income_positions else 0.665 * width
                found_header = True
                break
        if header_top is None:
            continue
        anchors: list[tuple[float, list[dict]]] = []
        previous_value = ""
        previous_top = -100.0
        for top, line_words in lines:
            if top <= header_top + 10:
                continue
            asset_words = [word for word in line_words if float(asset_x) - 4 <= float(word["x0"]) < float(owner_x) - 2]
            value_words = [word for word in line_words if float(value_x) - 4 <= float(word["x0"]) < float(income_x) - 2]
            value_text = _clean(" ".join(str(word.get("text", "")) for word in value_words))
            if asset_words and (value_text.startswith("$") or value_text in {"None", "Undetermined"}):
                continuation = (previous_value.endswith("-") and top - previous_top < 16
                                and VALUE_EXACT_RE.fullmatch(value_text) is not None)
                if not continuation:
                    anchors.append((top, line_words))
            if value_text:
                previous_value, previous_top = value_text, top
        if not anchors:
            first_body_line = next((_clean(" ".join(str(word.get("text", "")) for word in line_words))
                                    for top, line_words in lines if top > header_top + 10), "")
            if first_body_line == "None disclosed.":
                explicit_none = True
                continue
        for position, (top, line_words) in enumerate(anchors):
            end = anchors[position + 1][0] - 1 if position + 1 < len(anchors) else height
            footer = [float(word["top"]) for word in words if top < float(word["top"]) < end
                      and "asset-type-codes.aspx" in _clean(word.get("text"))]
            if footer:
                end = min(end, min(footer) - 1)
            anchor_sizes = [float(word.get("size", 0)) for word in line_words
                            if float(word["x0"]) < float(owner_x) - 2]
            base_size = max(anchor_sizes, default=0)
            asset_words = [word for word in words if top - 1 <= float(word["top"]) < end
                           and float(asset_x) - 4 <= float(word["x0"]) < float(owner_x) - 2
                           and float(word.get("size") or base_size) >= base_size - 0.2]
            owner_words = [word for word in line_words
                           if float(owner_x) - 4 <= float(word["x0"]) < float(value_x) - 2]
            value_lines = []
            for value_top, candidate_words in lines:
                if not top - 1 <= value_top < end:
                    continue
                cells = [word for word in candidate_words
                         if float(value_x) - 4 <= float(word["x0"]) < float(income_x) - 2]
                cell = _clean(" ".join(str(word.get("text", "")) for word in cells))
                if cell and re.fullmatch(r"[$0-9,.+\- ]+|None|Undetermined", cell):
                    value_lines.append(cell)
            asset = _clean(" ".join(str(word.get("text", "")) for word in sorted(
                asset_words, key=lambda word: (round(float(word["top"]), 1), float(word["x0"])))))
            owner_code = _clean(" ".join(str(word.get("text", "")) for word in owner_words))
            raw_value = _clean(" ".join(value_lines))
            asset_match = ASSET_RE.fullmatch(asset)
            parsed_value = _value(raw_value)
            reasons: list[str] = []
            if asset_match is None:
                reasons.append("asset_layout_unsupported")
                asset_name, ticker, asset_type_code = asset, None, None
            else:
                asset_name, ticker, asset_type_code = asset_match.groups()
            if owner_code not in OWNER_CODES:
                reasons.append("unknown_owner_code")
            if asset_type_code not in ASSET_TYPES:
                reasons.append("unknown_asset_type_code")
            excluded = raw_value == "None"
            if not excluded and parsed_value is None:
                reasons.append("value_not_representable")
            stable = "|".join((source_sha256, str(page_index + 1), f"{top:.2f}", asset,
                               owner_code, raw_value))
            extracted.append({
                "extraction_id": "house-holding:" + hashlib.sha256(stable.encode()).hexdigest()[:24],
                "owner_code": owner_code or None, "owner": OWNER_CODES.get(owner_code),
                "asset_name": asset_name.strip(), "ticker": ticker,
                "ticker_mapping_basis": "filing_explicit" if ticker else None,
                "asset_type_code": asset_type_code,
                "instrument_type": ASSET_TYPES.get(asset_type_code),
                "value_low": parsed_value[0] if parsed_value else None,
                "value_high": parsed_value[1] if parsed_value else None,
                "value_raw": raw_value, "excluded_no_reportable_value": excluded,
                "evidence": {"page": page_index + 1, "top_points": round(top, 2)},
                "review_reasons": reasons,
            })
    if not found_header:
        raise HouseIndexError("House annual report has no recognized Schedule A header")
    if not extracted and not explicit_none:
        raise HouseIndexError("House annual report has no recognized Schedule A disposition")
    if len({row["extraction_id"] for row in extracted}) != len(extracted):
        raise HouseIndexError("House annual report produced duplicate holding extraction IDs")
    return extracted, explicit_none and not extracted


def parse_archived_financial_report(root: Path, metadata: dict) -> dict:
    if (metadata.get("source_id") != "house_clerk" or metadata.get("filing_type") != "O" or
            metadata.get("report_type") != "Annual Report"):
        raise HouseIndexError("House holding metadata is not a supported annual report")
    year, document_id, sha = metadata.get("filing_year"), metadata.get("document_id"), metadata.get("sha256")
    expected = f"house_clerk/financial-reports/{year}/{document_id}/{sha}.pdf"
    if (type(year) is not int or not re.fullmatch(r"[0-9]{1,20}", str(document_id)) or
            not re.fullmatch(r"[0-9a-f]{64}", str(sha)) or metadata.get("archive_path") != expected):
        raise HouseIndexError("House holding metadata is incomplete")
    path = root.resolve() / expected
    try:
        content = path.read_bytes()
    except OSError:
        raise HouseIndexError("Archived House holding PDF is unavailable") from None
    if hashlib.sha256(content).hexdigest() != sha:
        raise HouseIndexError("Archived House holding PDF hash does not match metadata")
    validate_pdf(content)
    try:
        import pdfplumber
    except ImportError:
        raise HouseIndexError("House holding parser requires pdfplumber") from None
    try:
        with pdfplumber.open(path) as document:
            page_objects = []
            texts = []
            for page in document.pages:
                words = page.extract_words(keep_blank_chars=False, extra_attrs=["size"])
                text = page.extract_text() or ""
                page_objects.append({"width": page.width, "height": page.height,
                                     "words": words, "text": text})
                texts.append(_clean(text))
    except Exception as exc:
        raise HouseIndexError(f"House holding PDF could not be read: {type(exc).__name__}") from None
    full_text = " ".join(texts)
    report_period_end, report_period_basis = annual_report_period_from_pdf(full_text, metadata)
    rows, explicit_none = extract_schedule_a_pages(page_objects, source_sha256=sha)
    return {
        "schema_version": EXTRACTION_SCHEMA, "parser_version": PARSER_VERSION,
        "source": {key: metadata.get(key) for key in ("source_id", "source_url", "document_id",
            "filing_type", "report_type", "filer_name", "state_district", "filing_year",
            "filed_date", "archive_path")},
        "source_sha256": sha, "report_period_end": report_period_end,
        "report_period_basis": report_period_basis,
        "extraction_method": "native_pdf_geometry", "schedule_a_complete": True,
        "explicit_no_holdings": explicit_none, "rows": rows,
    }


def qualify_financial_report(extraction: dict, identity: dict) -> dict:
    if extraction.get("schema_version") != EXTRACTION_SCHEMA or not extraction.get("schedule_a_complete"):
        raise HouseIndexError("House holding extraction is not complete")
    if extraction.get("report_period_basis") != REPORT_PERIOD_BASIS:
        raise HouseIndexError("House holding extraction has no supported report-period evidence")
    source = extraction.get("source") or {}
    document_id = source.get("document_id")
    if identity.get("document_id") != document_id:
        raise HouseIndexError("House holding identity does not match its extraction")
    identity_valid = (identity.get("status") in {"matched_automatically", "suggested_requires_review"}
                      and identity.get("match_basis") in DETERMINISTIC_IDENTITY_BASES
                      and isinstance(identity.get("roster_sha256"), str)
                      and re.fullmatch(r"[0-9a-f]{64}", identity["roster_sha256"]))
    person_id = identity.get("person_id")
    evidence_url = identity.get("evidence_url")
    parsed_url = urlsplit(evidence_url) if isinstance(evidence_url, str) else None
    identity_valid = bool(identity_valid and re.fullmatch(r"house:[A-Z][0-9]{6}", str(person_id))
                          and parsed_url and parsed_url.scheme == "https"
                          and parsed_url.hostname == "bioguide.congress.gov")
    try:
        filed = datetime.strptime(source.get("filed_date"), "%Y-%m-%d").date()
        period = datetime.strptime(extraction.get("report_period_end"), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise HouseIndexError("House annual report has invalid filing or period dates") from None
    if filed < period:
        raise HouseIndexError("House annual report filing predates its report period end")
    report_reasons = [] if identity_valid else ["identity_not_deterministic"]
    for row in extraction.get("rows", []):
        if not isinstance(row, dict):
            raise HouseIndexError("House holding extraction row is invalid")
        if row.get("review_reasons"):
            report_reasons.extend(str(reason) for reason in row["review_reasons"])
    eligible = not report_reasons
    holdings: list[dict] = []
    excluded: list[dict] = []
    quarantined: list[dict] = []
    if eligible:
        for row in extraction["rows"]:
            if row.get("excluded_no_reportable_value"):
                excluded.append({"extraction_id": row["extraction_id"],
                                 "reason": "no_reportable_asset_value", "evidence": row.get("evidence")})
                continue
            holdings.append({
                "id": row["extraction_id"], "filing_id": document_id, "person_id": person_id,
                "owner": row["owner"], "asset_name": row["asset_name"], "ticker": row.get("ticker"),
                "ticker_mapping_basis": row.get("ticker_mapping_basis"),
                "instrument_type": row["instrument_type"],
                "report_period_end": extraction["report_period_end"],
                "filed_at": f"{source['filed_date']}T00:00:00Z",
                "value_low": row["value_low"], "value_high": row["value_high"],
                "change_from_prior": "unknown", "source_id": "house_clerk",
                "source": "U.S. House Clerk", "source_url": source["source_url"],
                "verification_status": "official_matched",
            })
    else:
        for row in extraction.get("rows", []):
            quarantined.append({"extraction_id": row.get("extraction_id"),
                                "reasons": sorted(set(report_reasons + row.get("review_reasons", []))),
                                "evidence": row.get("evidence")})
    return {
        "schema_version": QUALIFICATION_SCHEMA,
        "source": source, "source_sha256": extraction.get("source_sha256"),
        "report_period_end": extraction["report_period_end"],
        "report_period_basis": extraction["report_period_basis"],
        "identity": identity, "report_complete": True, "production_eligible": eligible,
        "holdings": holdings, "excluded": excluded, "quarantined": quarantined,
        "qualification_reasons": sorted(set(report_reasons)),
    }
