"""Fail-closed download, archive, and offline parsing for Senate eFD PTRs.

The search catalog remains the authority for which report URLs may be
downloaded.  Network access is guarded by the same two explicit eFD flags as
catalog collection.  Original bytes are archived before any parser result is
trusted, so parser changes can always be replayed offline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Iterable
from urllib.parse import urlsplit
import urllib.request
import uuid

from .senate import (
    SEARCH_PAGE_URL,
    SenateEfdClient,
    SenateEfdError,
    SenateSourceConfig,
    normalize_transaction_type,
    require_collection_enabled,
)


REPORT_ARCHIVE_SCHEMA = "senate-efd-report-entrypoint-archive/v1"
REPORT_BATCH_SCHEMA = "senate-efd-report-entrypoint-batch/v1"
REPORT_EXTRACTION_BATCH_SCHEMA = "senate-efd-report-extraction-batch/v1"
ELECTRONIC_EXTRACTION_SCHEMA = "senate-efd-electronic-ptr-extraction/v1"
PAPER_INSPECTION_SCHEMA = "senate-efd-paper-ptr-inspection/v1"
PARSER_VERSION = "senate-efd-report-parser-2026-09"
MAX_ENTRYPOINT_BYTES = 25 * 1024 * 1024
_OFFICIAL_HOST = "efdsearch.senate.gov"
_DISCOVERY_FIELDS = {
    "access_method", "catalog_index", "document_id", "document_url", "filer_name",
    "office", "portal_listed_date", "report_amendment_number", "report_label_date",
    "report_type", "source_id",
}
_EXPECTED_COLUMNS = (
    "#", "Transaction Date", "Owner", "Ticker", "Asset Name", "Asset Type",
    "Type", "Amount", "Comment",
)


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise SenateEfdError("Archived Senate eFD report conflicts with immutable content")
        return
    with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def _canonical_discovery(value: object) -> dict:
    """Validate that one catalog row identifies exactly one official PTR URL."""

    if not isinstance(value, dict) or set(value) != _DISCOVERY_FIELDS:
        raise SenateEfdError("Senate eFD report discovery fields changed")
    if value.get("source_id") != "senate_efd" or value.get("report_type") != "periodic_transaction_report":
        raise SenateEfdError("Senate eFD report discovery is not an official PTR")
    method = value.get("access_method")
    path_kind = {"electronic_ptr": "ptr", "paper_ptr": "paper"}.get(method)
    if path_kind is None:
        raise SenateEfdError("Senate eFD report access method is unsupported")
    raw_id = value.get("document_id")
    if not isinstance(raw_id, str):
        raise SenateEfdError("Senate eFD report document id is invalid")
    try:
        document_id = str(uuid.UUID(raw_id))
    except ValueError:
        raise SenateEfdError("Senate eFD report document id is invalid") from None
    if raw_id != document_id:
        raise SenateEfdError("Senate eFD report document id is not canonical")
    expected_path = f"/search/view/{path_kind}/{document_id}/"
    raw_url = value.get("document_url")
    if not isinstance(raw_url, str):
        raise SenateEfdError("Senate eFD report URL is invalid")
    parsed = urlsplit(raw_url)
    if (parsed.scheme != "https" or parsed.hostname != _OFFICIAL_HOST or parsed.port is not None or
            parsed.username is not None or parsed.password is not None or parsed.path != expected_path or
            parsed.query or parsed.fragment):
        raise SenateEfdError("Senate eFD report URL is not an allowed official URL")
    for field in ("filer_name", "office", "portal_listed_date"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise SenateEfdError(f"Senate eFD report discovery has invalid {field}")
    return dict(value)


def _content_type(headers: dict[str, str]) -> str:
    value = headers.get("content-type", "")
    return value.split(";", 1)[0].strip().lower()


def inspect_report_content(discovery: dict, content: bytes, headers: dict[str, str]) -> dict:
    """Classify downloaded bytes without interpreting report facts."""

    source = _canonical_discovery(discovery)
    if not isinstance(content, bytes) or not content or len(content) > MAX_ENTRYPOINT_BYTES:
        raise SenateEfdError("Senate eFD report content is empty or exceeds its size limit")
    if not isinstance(headers, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in headers.items()):
        raise SenateEfdError("Senate eFD report response headers are invalid")
    media_type = _content_type({key.lower(): value for key, value in headers.items()})
    if content.startswith(b"%PDF-"):
        if source["access_method"] != "paper_ptr":
            raise SenateEfdError("Electronic Senate eFD PTR unexpectedly returned a PDF")
        if media_type not in {"application/pdf", "application/octet-stream"}:
            raise SenateEfdError("Senate eFD paper PTR has an unexpected content type")
        if b"%%EOF" not in content[-4096:]:
            raise SenateEfdError("Senate eFD paper PTR is an incomplete PDF")
        media_kind, extension = "pdf", "pdf"
    else:
        if media_type not in {"text/html", "application/xhtml+xml"}:
            raise SenateEfdError("Senate eFD report has an unexpected content type")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise SenateEfdError("Senate eFD report HTML is not UTF-8") from None
        prefix = text.lstrip("\ufeff\t\r\n ").lower()[:256]
        if not (prefix.startswith("<!doctype html") or prefix.startswith("<html")):
            raise SenateEfdError("Senate eFD report response is not recognizable HTML")
        media_kind, extension = "html", "html"
    return {
        "access_method": source["access_method"],
        "media_kind": media_kind,
        "media_type": media_type,
        "extension": extension,
        "byte_length": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


class SenateReportClient:
    """Authorized session wrapper for one catalog-approved report download."""

    def __init__(self, config: SenateSourceConfig | None = None, *, client=None):
        self.config = config or SenateSourceConfig()
        self.client = client or SenateEfdClient()

    def download(self, discovery: dict) -> tuple[bytes, dict[str, str]]:
        require_collection_enabled(self.config)
        source = _canonical_discovery(discovery)
        if getattr(self.client, "csrf_token", None) is None:
            self.client.begin_authorized_session()
        request = urllib.request.Request(source["document_url"], headers={
            "Accept": "text/html,application/xhtml+xml,application/pdf",
            "Referer": SEARCH_PAGE_URL,
            "User-Agent": (
                "unison-senate-evidence/0.1 "
                "(+https://github.com/hunterhigh/us-politician-trades-data)"
            ),
        }, method="GET")
        content, headers = self.client._open(
            request, expected_urls={source["document_url"]}, maximum=MAX_ENTRYPOINT_BYTES)
        inspect_report_content(source, content, headers)
        return content, headers


def archive_report(root: Path, discovery: dict, content: bytes, headers: dict[str, str],
                   retrieved_at: str | None = None) -> dict:
    """Archive the exact response from a catalog-approved report entrypoint.

    An electronic entrypoint normally contains the report itself.  A paper
    entrypoint can be only an HTML viewer whose page assets still need a
    separate, bounded collection.  This archive therefore does not claim that
    report evidence is complete.
    """

    source = _canonical_discovery(discovery)
    inspection = inspect_report_content(source, content, headers)
    timestamp = retrieved_at or datetime.now(timezone.utc).isoformat()
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise SenateEfdError("Senate eFD report retrieval timestamp is invalid") from None
    if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
        raise SenateEfdError("Senate eFD report retrieval timestamp must include a timezone")
    sha = inspection["sha256"]
    folder = root.resolve() / "senate_efd" / "reports" / source["document_id"]
    document_path = folder / f"{sha}.{inspection['extension']}"
    metadata_path = folder / f"{sha}.metadata.json"
    _write_once(document_path, content)
    normalized_headers = {
        key.lower(): value for key, value in headers.items()
        if key.lower() in {"content-type", "etag", "last-modified"}
    }
    metadata = {
        "schema_version": REPORT_ARCHIVE_SCHEMA,
        "source_id": "senate_efd",
        "document_id": source["document_id"],
        "document_url": source["document_url"],
        "access_method": source["access_method"],
        "filer_name": source["filer_name"],
        "office": source["office"],
        "portal_listed_date": source["portal_listed_date"],
        "report_label_date": source["report_label_date"],
        "report_amendment_number": source["report_amendment_number"],
        "retrieved_at": timestamp,
        "sha256": sha,
        "byte_length": len(content),
        "media_kind": inspection["media_kind"],
        "media_type": inspection["media_type"],
        "artifact_role": "entrypoint_response",
        "evidence_complete": False,
        "headers": normalized_headers,
        "archive_path": document_path.relative_to(root.resolve()).as_posix(),
        "parser_version": PARSER_VERSION,
        "verification_status": (
            "official_viewer_unresolved"
            if source["access_method"] == "paper_ptr" and inspection["media_kind"] == "html"
            else "official_entrypoint_unparsed"
        ),
    }
    encoded = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if metadata_path.exists():
        old = json.loads(metadata_path.read_text(encoding="utf-8"))
        stable = set(metadata) - {"retrieved_at", "headers"}
        if any(old.get(key) != metadata[key] for key in stable):
            raise SenateEfdError("Archived Senate eFD report metadata conflicts with its hash")
    else:
        _write_once(metadata_path, encoded)
    return metadata


def _archived_entrypoints(root: Path) -> list[dict]:
    reports_root = root.resolve() / "senate_efd" / "reports"
    if not reports_root.exists():
        return []
    archived: list[dict] = []
    seen: set[str] = set()
    for path in reports_root.glob("*/*.metadata.json"):
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise SenateEfdError("Archived Senate eFD report metadata is unreadable") from None
        document_id = path.parent.name
        if (metadata.get("schema_version") != REPORT_ARCHIVE_SCHEMA or
                metadata.get("document_id") != document_id or
                metadata.get("artifact_role") != "entrypoint_response"):
            raise SenateEfdError("Archived Senate eFD report metadata is inconsistent")
        if document_id in seen:
            raise SenateEfdError("Multiple Senate eFD entrypoints exist for one catalog document")
        archived.append({
            "document_id": document_id,
            "access_method": metadata.get("access_method"),
            "sha256": metadata.get("sha256"),
            "media_kind": metadata.get("media_kind"),
            "archive_path": metadata.get("archive_path"),
            "evidence_complete": False,
        })
        seen.add(document_id)
    archived.sort(key=lambda item: item["document_id"])
    return archived


def archive_catalog_report_entrypoints(
        root: Path, discovery: object, *, limit: int,
        config: SenateSourceConfig | None = None, client=None) -> dict:
    """Archive a bounded, resumable batch of catalog-approved entrypoints."""

    selected_config = config or SenateSourceConfig()
    require_collection_enabled(selected_config)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise SenateEfdError("Senate eFD report batch limit must be between 1 and 100")
    if (not isinstance(discovery, dict) or
            discovery.get("schema_version") != "senate-efd-discovery/v1" or
            not isinstance(discovery.get("reports"), list)):
        raise SenateEfdError("Senate eFD report discovery batch is invalid")
    discovery_metadata = discovery.get("metadata")
    catalog_sha = discovery_metadata.get("sha256") if isinstance(discovery_metadata, dict) else None
    if not isinstance(catalog_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", catalog_sha):
        raise SenateEfdError("Senate eFD report discovery batch has no valid catalog hash")

    reports: list[dict] = []
    seen: set[str] = set()
    for value in discovery["reports"]:
        report = _canonical_discovery(value)
        if report["document_id"] in seen:
            raise SenateEfdError("Senate eFD report discovery batch contains a duplicate document")
        seen.add(report["document_id"])
        reports.append(report)
    reports.sort(key=lambda item: (item["catalog_index"], item["document_id"]))

    archived_before_items = _archived_entrypoints(root)
    archived_before = {item["document_id"] for item in archived_before_items}
    pending = [report for report in reports if report["document_id"] not in archived_before]
    wrapper = SenateReportClient(selected_config, client=client)
    archived = []
    failures = []
    for report in pending[:limit]:
        try:
            content, headers = wrapper.download(report)
            metadata = archive_report(root, report, content, headers)
            archived.append({
                "document_id": report["document_id"],
                "access_method": report["access_method"],
                "sha256": metadata["sha256"],
                "media_kind": metadata["media_kind"],
                "archive_path": metadata["archive_path"],
                "evidence_complete": False,
            })
        except SenateEfdError as exc:
            failures.append({
                "document_id": report["document_id"],
                "access_method": report["access_method"],
                "reason": str(exc),
            })
    replay = _archived_entrypoints(root)
    archived_ids = {item["document_id"] for item in replay}
    return {
        "schema_version": REPORT_BATCH_SCHEMA,
        "source_id": "senate_efd",
        "catalog_sha256": catalog_sha,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "catalog_record_count": len(reports),
        "batch_limit": limit,
        "attempted_count": len(archived) + len(failures),
        "archived_count": len(archived),
        "failure_count": len(failures),
        "archived_total": len(archived_ids),
        "pending_count": len(reports) - len(archived_ids),
        "archived": archived,
        "replay_count": len(replay),
        "replay": replay,
        "failures": failures,
    }


@dataclass(frozen=True)
class _HtmlTable:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[_HtmlTable] = []
        self._depth = 0
        self._headers: list[str] = []
        self._rows: list[tuple[str, ...]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._cell_kind: str | None = None
        self._capture_tag: str | None = None
        self._capture_text: list[str] = []
        self.headings: list[tuple[str, str]] = []
        self.text_segments: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "table":
            self._depth += 1
            if self._depth > 1:
                raise SenateEfdError("Senate eFD report contains nested tables")
        elif self._depth == 1 and tag == "tr":
            if self._row is not None:
                raise SenateEfdError("Senate eFD report contains nested rows")
            self._row = []
        elif self._depth == 1 and tag in {"th", "td"}:
            if self._row is None or self._cell is not None:
                raise SenateEfdError("Senate eFD report contains malformed cells")
            self._cell, self._cell_kind = [], tag
        elif self._cell is not None and tag == "br":
            self._cell.append(" ")
        if tag in {"h1", "h2", "p"} and self._capture_tag is None:
            self._capture_tag = tag
            self._capture_text = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        if self._capture_tag is not None:
            self._capture_text.append(data)
        normalized = " ".join(data.replace("\xa0", " ").split())
        if normalized:
            self.text_segments.append(normalized)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._depth == 1 and tag in {"th", "td"}:
            if self._cell is None or self._cell_kind != tag or self._row is None:
                raise SenateEfdError("Senate eFD report contains malformed cells")
            text = " ".join("".join(self._cell).replace("\xa0", " ").split())
            self._row.append(text)
            self._cell, self._cell_kind = None, None
        elif self._depth == 1 and tag == "tr":
            if self._row is None or self._cell is not None:
                raise SenateEfdError("Senate eFD report contains malformed rows")
            if self._row:
                if not self._headers:
                    self._headers = self._row
                else:
                    self._rows.append(tuple(self._row))
            self._row = None
        elif tag == "table":
            if self._depth != 1 or self._row is not None:
                raise SenateEfdError("Senate eFD report contains malformed tables")
            self.tables.append(_HtmlTable(tuple(self._headers), tuple(self._rows)))
            self._headers, self._rows = [], []
            self._depth = 0
        if tag == self._capture_tag:
            captured = " ".join("".join(self._capture_text).replace("\xa0", " ").split())
            if tag in {"h1", "h2"}:
                self.headings.append((tag, captured))
            self._capture_tag = None
            self._capture_text = []

    def close(self) -> None:
        super().close()
        if (self._depth or self._row is not None or self._cell is not None or
                self._capture_tag is not None):
            raise SenateEfdError("Senate eFD report HTML is incomplete")


def _iso_date(raw: str) -> str:
    try:
        return datetime.strptime(raw, "%m/%d/%Y").date().isoformat()
    except ValueError:
        raise SenateEfdError(f"Senate eFD transaction date is invalid: {raw!r}") from None


def _report_parser(content: bytes) -> _TableParser:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise SenateEfdError("Senate eFD report HTML is not UTF-8") from None
    parser = _TableParser()
    try:
        parser.feed(text)
        parser.close()
    except SenateEfdError:
        raise
    except Exception:
        raise SenateEfdError("Senate eFD report HTML is malformed") from None
    return parser


def _transaction_rows(parser: _TableParser) -> Iterable[tuple[str, ...]]:
    matches = [table for table in parser.tables if table.headers == _EXPECTED_COLUMNS]
    if len(matches) != 1:
        observed = [list(table.headers) for table in parser.tables]
        raise SenateEfdError(
            f"Senate eFD transaction table changed; expected one exact header, observed={observed!r}")
    return matches[0].rows


def parse_electronic_ptr(metadata: dict, content: bytes) -> dict:
    """Parse an archived electronic PTR without performing network access."""

    if metadata.get("schema_version") != REPORT_ARCHIVE_SCHEMA:
        raise SenateEfdError("Senate eFD report archive metadata is invalid")
    if metadata.get("access_method") != "electronic_ptr" or metadata.get("media_kind") != "html":
        raise SenateEfdError("Senate eFD report is not an archived electronic PTR")
    source_sha = hashlib.sha256(content).hexdigest()
    if metadata.get("sha256") != source_sha or metadata.get("byte_length") != len(content):
        raise SenateEfdError("Senate eFD report bytes do not match archive metadata")
    parser = _report_parser(content)
    titles = [text for tag, text in parser.headings if tag == "h1"]
    if len(titles) != 1:
        raise SenateEfdError("Senate eFD electronic PTR has no unique report title")
    title_match = re.fullmatch(r"Periodic Transaction Report for (\d{2}/\d{2}/\d{4})", titles[0])
    if title_match is None:
        raise SenateEfdError("Senate eFD electronic PTR title changed")
    title_date = _iso_date(title_match.group(1))
    if title_date != metadata.get("report_label_date"):
        raise SenateEfdError("Senate eFD report title date does not match its catalog")
    totals = []
    for text in parser.text_segments:
        match = re.fullmatch(r"\(([0-9]+) transactions? total\)", text)
        if match:
            totals.append(int(match.group(1)))
    if len(totals) != 1:
        raise SenateEfdError("Senate eFD electronic PTR has no unique transaction count")
    filed_values = [text for text in parser.text_segments
                    if re.fullmatch(r"Filed \d{2}/\d{2}/\d{4} @ .+", text)]
    if len(filed_values) > 1:
        raise SenateEfdError("Senate eFD electronic PTR has multiple filing timestamps")
    transactions = []
    seen_numbers: set[int] = set()
    for position, cells in enumerate(_transaction_rows(parser), start=1):
        if len(cells) != len(_EXPECTED_COLUMNS):
            raise SenateEfdError("Senate eFD transaction row width changed")
        (raw_number, raw_date, owner, ticker, asset_name, asset_type, raw_type,
         amount, comment) = cells
        if not re.fullmatch(r"[1-9][0-9]*", raw_number):
            raise SenateEfdError("Senate eFD transaction row number is invalid")
        row_number = int(raw_number)
        if row_number in seen_numbers:
            raise SenateEfdError("Senate eFD transaction row number is duplicated")
        seen_numbers.add(row_number)
        if not asset_name or not asset_type or not raw_type or not amount:
            raise SenateEfdError("Senate eFD transaction row is missing a required value")
        normalized_type = normalize_transaction_type(raw_type)
        stable = "|".join((source_sha, str(row_number), raw_date, owner, ticker, asset_name,
                           asset_type, raw_type, amount, comment))
        transactions.append({
            "extraction_id": "senate-ptr:" + hashlib.sha256(stable.encode()).hexdigest()[:24],
            "row_number": row_number,
            "transaction_date": _iso_date(raw_date),
            "owner_raw": owner or None,
            "ticker_raw": None if ticker in {"", "--"} else ticker,
            "asset_name_raw": asset_name,
            "asset_type_raw": asset_type,
            "transaction_type_raw": raw_type,
            **normalized_type,
            "amount_raw": amount,
            "comment_raw": None if comment in {"", "--"} else comment,
        })
    if totals[0] != len(transactions):
        raise SenateEfdError("Senate eFD transaction count does not match the table")
    disposition = "transactions_parsed" if transactions else "empty_table_requires_review"
    return {
        "schema_version": ELECTRONIC_EXTRACTION_SCHEMA,
        "parser_version": PARSER_VERSION,
        "source_id": "senate_efd",
        "document_id": metadata["document_id"],
        "source_url": metadata["document_url"],
        "source_sha256": source_sha,
        "filer_name": metadata["filer_name"],
        "portal_listed_date": metadata["portal_listed_date"],
        "report_label_date": metadata.get("report_label_date"),
        "report_amendment_number": metadata.get("report_amendment_number"),
        "filed_at_raw": filed_values[0] if filed_values else None,
        "document_disposition": disposition,
        "evidence_complete": bool(transactions),
        "transactions": transactions,
    }


def inspect_paper_entrypoint(metadata: dict, content: bytes) -> dict:
    """Classify a paper PTR entrypoint without assuming that it is the report."""

    if metadata.get("schema_version") != REPORT_ARCHIVE_SCHEMA:
        raise SenateEfdError("Senate eFD report archive metadata is invalid")
    if metadata.get("access_method") != "paper_ptr":
        raise SenateEfdError("Senate eFD report is not an archived paper PTR")
    source_sha = hashlib.sha256(content).hexdigest()
    if metadata.get("sha256") != source_sha or metadata.get("byte_length") != len(content):
        raise SenateEfdError("Senate eFD report bytes do not match archive metadata")
    media_kind = metadata.get("media_kind")
    if media_kind == "html":
        disposition = "paper_viewer_requires_page_collection"
    elif media_kind == "pdf":
        if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-4096:]:
            raise SenateEfdError("Senate eFD paper PTR PDF envelope is incomplete")
        disposition = "paper_pdf_requires_bounded_parser"
    else:
        raise SenateEfdError("Senate eFD paper PTR entrypoint has an unsupported media kind")
    return {
        "schema_version": PAPER_INSPECTION_SCHEMA,
        "parser_version": PARSER_VERSION,
        "source_id": "senate_efd",
        "document_id": metadata["document_id"],
        "source_url": metadata["document_url"],
        "source_sha256": source_sha,
        "document_disposition": disposition,
        "evidence_complete": False,
        "transactions": [],
    }


# Backward-compatible internal name for callers added with the initial
# foundation.  It now has entrypoint semantics and never implies that viewer
# page evidence is complete.
inspect_paper_ptr = inspect_paper_entrypoint


def extract_archived_report_batch(root: Path, batch: object) -> dict:
    """Parse a collected entrypoint batch entirely from archived bytes."""

    if (not isinstance(batch, dict) or batch.get("schema_version") != REPORT_BATCH_SCHEMA or
            batch.get("source_id") != "senate_efd" or
            not isinstance(batch.get("archived"), list) or
            not isinstance(batch.get("replay", batch.get("archived")), list)):
        raise SenateEfdError("Senate eFD report entrypoint batch is invalid")
    catalog_sha = batch.get("catalog_sha256")
    if not isinstance(catalog_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", catalog_sha):
        raise SenateEfdError("Senate eFD report entrypoint batch has no valid catalog hash")

    root = root.resolve()
    extractions = []
    inspections = []
    failures = []
    seen: set[str] = set()
    replay = batch.get("replay", batch["archived"])
    for item in replay:
        if not isinstance(item, dict):
            raise SenateEfdError("Senate eFD report entrypoint batch contains an invalid item")
        document_id = item.get("document_id")
        source_sha = item.get("sha256")
        extension = {"html": "html", "pdf": "pdf"}.get(item.get("media_kind"))
        expected = (f"senate_efd/reports/{document_id}/{source_sha}.{extension}"
                    if extension else None)
        if (not isinstance(document_id, str) or document_id in seen or
                not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha) or
                item.get("archive_path") != expected):
            raise SenateEfdError("Senate eFD report entrypoint batch item is inconsistent")
        seen.add(document_id)
        content_path = root / expected
        metadata_path = content_path.with_name(content_path.stem + ".metadata.json")
        try:
            content = content_path.read_bytes()
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("document_id") != document_id or metadata.get("sha256") != source_sha:
                raise SenateEfdError("Senate eFD report entrypoint metadata does not match its batch")
            if item.get("access_method") == "electronic_ptr":
                extractions.append(parse_electronic_ptr(metadata, content))
            elif item.get("access_method") == "paper_ptr":
                inspections.append(inspect_paper_entrypoint(metadata, content))
            else:
                raise SenateEfdError("Senate eFD report entrypoint method is unsupported")
        except (OSError, json.JSONDecodeError, SenateEfdError) as exc:
            failures.append({
                "document_id": document_id,
                "source_sha256": source_sha,
                "reason": str(exc),
            })
    return {
        "schema_version": REPORT_EXTRACTION_BATCH_SCHEMA,
        "source_id": "senate_efd",
        "catalog_sha256": catalog_sha,
        "parser_version": PARSER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entrypoint_count": len(replay),
        "extraction_count": len(extractions),
        "inspection_count": len(inspections),
        "failure_count": len(failures),
        "transaction_count": sum(len(item["transactions"]) for item in extractions),
        "extractions": extractions,
        "inspections": inspections,
        "failures": failures,
    }
