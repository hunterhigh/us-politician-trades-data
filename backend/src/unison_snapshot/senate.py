"""Fail-closed, offline models for Senate eFD PTR discovery.

This module deliberately contains no HTTP client and cannot accept the eFD
portal notice.  A caller may feed it an already obtained search response, but
production collection remains behind an explicit configuration and terms
gate.  The date exposed by the search table is retained as portal metadata;
it is not evidence of the report's filing date.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Iterable
import urllib.error
from urllib.parse import urlencode, urljoin, urlsplit
import urllib.request
import uuid


HOME_URL = "https://efdsearch.senate.gov/search/home/"
SEARCH_URL = "https://efdsearch.senate.gov/search/report/data/"
SEARCH_PAGE_URL = "https://efdsearch.senate.gov/search/"
SCHEMA = "senate-efd-discovery/v1"
GATE_SCHEMA = "senate-efd-source-gate/v1"
_ALLOWED_HOST = "efdsearch.senate.gov"
_RESPONSE_FIELDS = {"draw", "recordsTotal", "recordsFiltered", "data", "result"}
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_REPORTS = 10_000


class SenateEfdError(RuntimeError):
    """The supplied eFD data or access configuration is unsafe to use."""


@dataclass(frozen=True)
class SenateSourceConfig:
    """Collection gate; the default cannot perform a production collection."""

    enabled: bool = False
    terms_acknowledged: bool = False


def require_collection_enabled(config: SenateSourceConfig | None = None) -> None:
    """Fail unless a production owner explicitly enables both access gates."""

    selected = config or SenateSourceConfig()
    if not isinstance(selected, SenateSourceConfig):
        raise SenateEfdError("Senate eFD source configuration is invalid")
    if not selected.enabled:
        raise SenateEfdError("Senate eFD collection is disabled")
    if not selected.terms_acknowledged:
        raise SenateEfdError("Senate eFD terms acknowledgement is required")


def _environment_flag(value: object, name: str) -> bool:
    if value in {None, ""}:
        return False
    if value == "true":
        return True
    if value == "false":
        return False
    raise SenateEfdError(f"{name} must be exactly true or false")


def source_config_from_environment(
        environment: dict[str, str] | None = None, *,
        enabled_name: str = "SENATE_EFD_COLLECTION_ENABLED",
        terms_name: str = "SENATE_EFD_TERMS_ACKNOWLEDGED") -> SenateSourceConfig:
    """Read exact, auditable flags; an absent flag never enables collection."""

    values = os.environ if environment is None else environment
    return SenateSourceConfig(
        enabled=_environment_flag(values.get(enabled_name), enabled_name),
        terms_acknowledged=_environment_flag(values.get(terms_name), terms_name),
    )


def collection_gate_status(config: SenateSourceConfig) -> dict:
    if not isinstance(config, SenateSourceConfig):
        raise SenateEfdError("Senate eFD source configuration is invalid")
    status = ("enabled" if config.enabled and config.terms_acknowledged else
              "blocked" if config.enabled else "disabled")
    reasons = ([] if status == "enabled" else
               ["terms_acknowledgement_missing"] if status == "blocked" else
               ["collection_not_enabled"])
    return {
        "schema_version": GATE_SCHEMA,
        "source_id": "senate_efd",
        "status": status,
        "collection_enabled": config.enabled,
        "terms_acknowledged": config.terms_acknowledged,
        "reasons": reasons,
    }


@dataclass(frozen=True)
class SenatePtrDiscovery:
    catalog_index: int
    filer_name: str
    office: str
    report_type: str
    access_method: str
    document_id: str
    document_url: str
    portal_listed_date: str
    source_id: str = "senate_efd"


@dataclass(frozen=True)
class SenateSearchPage:
    start: int
    requested_length: int
    records_total: int
    row_count: int
    reports: tuple[SenatePtrDiscovery, ...]


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.href: str | None = None
        self.label: list[str] | None = None
        self.links: list[tuple[str, str]] = []
        self.outside_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self.label is not None:
            raise SenateEfdError("Senate eFD report cell contains unexpected markup")
        values = dict(attrs)
        if set(values) != {"href"} or not isinstance(values["href"], str):
            raise SenateEfdError(
                f"Senate eFD report link has unexpected attributes: {sorted(values)}")
        self.href = values["href"]
        self.label = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self.label is None or self.href is None:
            raise SenateEfdError("Senate eFD report cell contains malformed markup")
        self.links.append((self.href, " ".join("".join(self.label).split())))
        self.href = None
        self.label = None

    def handle_data(self, data: str) -> None:
        if self.label is None:
            self.outside_text.append(data)
        else:
            self.label.append(data)

    def close(self) -> None:
        super().close()
        if self.label is not None:
            raise SenateEfdError("Senate eFD report cell contains an unclosed link")


class _CsrfParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "input":
            return
        values = dict(attrs)
        if values.get("name") == "csrfmiddlewaretoken" and isinstance(values.get("value"), str):
            self.tokens.append(values["value"])


def _csrf_token(content: bytes) -> str:
    try:
        text = content.decode("utf-8")
        parser = _CsrfParser()
        parser.feed(text)
        parser.close()
    except UnicodeDecodeError:
        raise SenateEfdError("Senate eFD agreement page is invalid") from None
    if len(parser.tokens) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", parser.tokens[0]):
        raise SenateEfdError("Senate eFD agreement page has no unique CSRF token")
    return parser.tokens[0]


class SenateEfdClient:
    """Session client whose agreement POST is reachable only after the external gate."""

    def __init__(self, timeout: float = 30.0, opener=None):
        self.timeout = timeout
        self.cookies = http.cookiejar.CookieJar()
        self.opener = opener or urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies))
        self.csrf_token: str | None = None

    def _open(self, request: urllib.request.Request, *, expected_urls: set[str],
              maximum: int = MAX_RESPONSE_BYTES) -> tuple[bytes, dict[str, str]]:
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status != 200 or response.geturl() not in expected_urls:
                    raise SenateEfdError("Senate eFD returned an unexpected response")
                content = response.read(maximum + 1)
                headers = {name.lower(): value for name, value in response.headers.items()
                           if name.lower() in {"etag", "last-modified", "content-type"}}
        except urllib.error.HTTPError as exc:
            raise SenateEfdError(f"Senate eFD returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise SenateEfdError("Senate eFD is unavailable") from None
        if len(content) > maximum:
            raise SenateEfdError("Senate eFD response exceeds its size limit")
        return content, headers

    def begin_authorized_session(self) -> None:
        request = urllib.request.Request(HOME_URL, headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "unison-senate-evidence/0.1 (+https://github.com/hunterhigh/us-politician-trades-data)",
        }, method="GET")
        content, _ = self._open(request, expected_urls={HOME_URL})
        form_token = _csrf_token(content)
        payload = urlencode({"csrfmiddlewaretoken": form_token,
                             "prohibition_agreement": "1"}).encode("ascii")
        agreement = urllib.request.Request(HOME_URL, data=payload, headers={
            "Accept": "text/html,application/xhtml+xml",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": HOME_URL,
            "User-Agent": "unison-senate-evidence/0.1 (+https://github.com/hunterhigh/us-politician-trades-data)",
        }, method="POST")
        response, _ = self._open(agreement, expected_urls={HOME_URL, SEARCH_PAGE_URL})
        cookie_token = next((cookie.value for cookie in self.cookies
                             if cookie.name in {"csrftoken", "csrf"}), None)
        self.csrf_token = cookie_token or _csrf_token(response)

    def download_search_page(self, *, start: int, length: int, draw: int,
                             submitted_start_date: str) -> tuple[bytes, dict[str, str]]:
        if self.csrf_token is None:
            raise SenateEfdError("Senate eFD authorized session is not initialized")
        payload = urlencode({
            "draw": str(draw), "start": str(start), "length": str(length),
            "report_types": "[11]", "submitted_start_date": submitted_start_date,
            "csrfmiddlewaretoken": self.csrf_token,
        }).encode("ascii")
        request = urllib.request.Request(SEARCH_URL, data=payload, headers={
            "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded",
            "Referer": SEARCH_PAGE_URL, "X-CSRFToken": self.csrf_token,
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "unison-senate-evidence/0.1 (+https://github.com/hunterhigh/us-politician-trades-data)",
        }, method="POST")
        content, headers = self._open(request, expected_urls={SEARCH_URL})
        content_type = headers.get("content-type", "").lower()
        if "json" not in content_type:
            raise SenateEfdError("Senate eFD search returned a non-JSON response")
        return content, headers


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise SenateEfdError(f"Senate eFD row has invalid {field}")
    return value


def _portal_date(value: str) -> str:
    for pattern in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            pass
    raise SenateEfdError("Senate eFD row has invalid portal date")


def _canonical_report_link(markup: str) -> tuple[str, str, str]:
    parser = _AnchorParser()
    try:
        parser.feed(markup)
        parser.close()
    except SenateEfdError:
        raise
    except Exception:
        raise SenateEfdError("Senate eFD report markup is invalid") from None
    if len(parser.links) != 1 or "".join(parser.outside_text).strip():
        raise SenateEfdError("Senate eFD report cell must contain exactly one link")
    href, label = parser.links[0]
    if label != "Periodic Transaction Report":
        raise SenateEfdError("Senate eFD report link is not a PTR")

    # Only an absolute official HTTPS URL or a root-relative portal path is
    # accepted.  urljoin would otherwise turn network-path references into an
    # external origin.
    if href.startswith("//") or (not href.startswith("/") and not href.startswith("https://")):
        raise SenateEfdError("Senate eFD report link is not an allowed official URL")
    url = urljoin(HOME_URL, href)
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != _ALLOWED_HOST or
            parsed.username is not None or parsed.password is not None or
            parsed.port is not None or parsed.query or parsed.fragment):
        raise SenateEfdError("Senate eFD report link is not an allowed official URL")

    match = re.fullmatch(r"/search/view/(ptr|paper)/([^/]+)/", parsed.path)
    if not match:
        raise SenateEfdError("Senate eFD report link has an unexpected shape")
    access_kind, raw_id = match.groups()
    try:
        document_id = str(uuid.UUID(raw_id))
    except ValueError:
        raise SenateEfdError("Senate eFD report link has an invalid document id") from None
    if raw_id.lower() != document_id:
        raise SenateEfdError("Senate eFD report document id is not canonical")
    return ("electronic_ptr" if access_kind == "ptr" else "paper_ptr", document_id, url)


def parse_search_page(payload: object, *, start: int, length: int) -> SenateSearchPage:
    """Validate one unfiltered eFD DataTables page.

    eFD returns five columns: first name, last name, office, a linked report
    type, and a date displayed by the portal.  That last value is intentionally
    named ``portal_listed_date`` rather than ``filed_at``.
    """

    if type(start) is not int or start < 0 or type(length) is not int or length <= 0:
        raise SenateEfdError("Senate eFD search page bounds are invalid")
    if not isinstance(payload, dict):
        raise SenateEfdError("Senate eFD search response is not an object")
    if set(payload) != _RESPONSE_FIELDS:
        missing = sorted(_RESPONSE_FIELDS - set(payload))
        unexpected = sorted(set(payload) - _RESPONSE_FIELDS)
        scalar_details = {
            name: payload[name] for name in unexpected
            if isinstance(payload[name], (str, int, float, bool, type(None)))
        }
        raise SenateEfdError(
            f"Senate eFD search response fields changed; missing={missing}; "
            f"unexpected={unexpected}; scalar_details={scalar_details!r}")
    draw = payload.get("draw")
    result = payload.get("result")
    total = payload.get("recordsTotal")
    filtered = payload.get("recordsFiltered")
    rows = payload.get("data")
    if result != "ok":
        raise SenateEfdError("Senate eFD search response result is not ok")
    if type(draw) is not int or draw < 0:
        raise SenateEfdError("Senate eFD search response has invalid draw")
    if type(total) is not int or total < 0 or filtered != total:
        raise SenateEfdError("Senate eFD search response has invalid recordsTotal")
    if not isinstance(rows, list) or len(rows) > length or start + len(rows) > total:
        raise SenateEfdError("Senate eFD search response has invalid page data")

    reports: list[SenatePtrDiscovery] = []
    for offset, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != 5:
            raise SenateEfdError("Senate eFD search row fields changed")
        first = _nonempty_string(row[0], "first_name")
        last = _nonempty_string(row[1], "last_name")
        office = _nonempty_string(row[2], "office")
        report_markup = _nonempty_string(row[3], "report_type")
        portal_date = _portal_date(_nonempty_string(row[4], "portal_date"))
        access_method, document_id, document_url = _canonical_report_link(report_markup)
        reports.append(SenatePtrDiscovery(
            catalog_index=start + offset,
            filer_name=f"{first} {last}",
            office=office,
            report_type="periodic_transaction_report",
            access_method=access_method,
            document_id=document_id,
            document_url=document_url,
            portal_listed_date=portal_date,
        ))
    return SenateSearchPage(start, length, total, len(rows), tuple(reports))


def build_discovery(pages: Iterable[SenateSearchPage]) -> dict:
    """Join a complete result set without inventing report-level facts."""

    ordered = sorted(pages, key=lambda page: page.start)
    if not ordered:
        raise SenateEfdError("Senate eFD discovery requires at least one page")
    total = ordered[0].records_total
    expected_start = 0
    reports: list[dict] = []
    for page in ordered:
        if page.records_total != total:
            raise SenateEfdError("Senate eFD recordsTotal changed between pages")
        if page.start != expected_start:
            raise SenateEfdError("Senate eFD pages contain a gap or overlap")
        expected_count = min(page.requested_length, total - page.start)
        if expected_count < 0 or page.row_count != expected_count:
            raise SenateEfdError("Senate eFD search page is incomplete")
        reports.extend(asdict(report) for report in page.reports)
        expected_start += page.row_count
    if expected_start != total:
        raise SenateEfdError("Senate eFD pagination is incomplete")
    return {
        "schema_version": SCHEMA,
        "source_id": "senate_efd",
        "source_url": HOME_URL,
        "records_total": total,
        "catalog_rows_covered": expected_start,
        "reports": reports,
    }


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise SenateEfdError("Archived Senate eFD catalog content conflicts with its hash")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
                                     delete=False) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def archive_discovery(root: Path, raw_pages: list[tuple[int, int, bytes, dict[str, str]]],
                      discovery: dict, retrieved_at: str | None = None) -> dict:
    """Archive raw catalog responses and their deterministic normalized result."""

    if not raw_pages or discovery.get("schema_version") != SCHEMA:
        raise SenateEfdError("Senate eFD discovery archive input is invalid")
    folder = root.resolve() / "senate_efd" / "catalog"
    page_metadata = []
    for start, length, content, headers in raw_pages:
        sha = hashlib.sha256(content).hexdigest()
        path = folder / "pages" / f"{sha}.json"
        _write_once(path, content)
        page_metadata.append({
            "start": start, "length": length, "sha256": sha, "byte_length": len(content),
            "headers": headers, "archive_path": path.relative_to(root.resolve()).as_posix(),
        })
    normalized = json.dumps(discovery, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")).encode("utf-8")
    sha = hashlib.sha256(normalized).hexdigest()
    metadata_path = folder / f"{sha}.json"
    metadata = {
        "schema_version": "senate-efd-catalog-archive/v1",
        "source_id": "senate_efd", "source_url": SEARCH_URL,
        "retrieved_at": retrieved_at or datetime.now(timezone.utc).isoformat(),
        "sha256": sha, "record_count": discovery["records_total"],
        "page_count": len(page_metadata), "pages": page_metadata,
        "archive_path": metadata_path.relative_to(root.resolve()).as_posix(),
    }
    if metadata_path.exists():
        try:
            existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise SenateEfdError("Archived Senate eFD catalog metadata is invalid") from None
        stable = ("schema_version", "source_id", "source_url", "sha256", "record_count",
                  "page_count", "pages", "archive_path")
        if any(existing.get(field) != metadata[field] for field in stable):
            raise SenateEfdError("Archived Senate eFD catalog metadata conflicts with its content")
        return existing
    _write_once(metadata_path, json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":")).encode("utf-8"))
    return metadata


def discover_ptrs(root: Path, config: SenateSourceConfig, *, submitted_start_date: str,
                  page_size: int = 100, client: SenateEfdClient | None = None) -> dict:
    """Collect the PTR catalog after, and only after, both production gates pass."""

    require_collection_enabled(config)
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise SenateEfdError("Senate eFD page size must be between 1 and 100")
    try:
        start_day = datetime.strptime(submitted_start_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise SenateEfdError("Senate eFD submitted start date must be YYYY-MM-DD") from None
    if not datetime(2012, 1, 1).date() <= start_day <= datetime.now(timezone.utc).date():
        raise SenateEfdError("Senate eFD submitted start date is outside the supported range")
    selected = client or SenateEfdClient()
    selected.begin_authorized_session()
    pages: list[SenateSearchPage] = []
    raw_pages: list[tuple[int, int, bytes, dict[str, str]]] = []
    start = 0
    draw = 1
    portal_date = start_day.strftime("%m/%d/%Y") + " 00:00:00"
    while True:
        content, headers = selected.download_search_page(
            start=start, length=page_size, draw=draw, submitted_start_date=portal_date)
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SenateEfdError("Senate eFD search response is invalid JSON") from None
        page = parse_search_page(payload, start=start, length=page_size)
        if page.records_total > MAX_REPORTS:
            raise SenateEfdError("Senate eFD search result exceeds its safety limit")
        pages.append(page)
        raw_pages.append((start, page_size, content, headers))
        start += page.row_count
        if start == page.records_total:
            break
        if page.row_count == 0:
            raise SenateEfdError("Senate eFD pagination made no progress")
        draw += 1
    discovery = build_discovery(pages)
    metadata = archive_discovery(root, raw_pages, discovery)
    return {"metadata": metadata, **discovery}


def normalize_transaction_type(value: object) -> dict[str, object]:
    """Preserve an eFD transaction direction and quarantine exchanges.

    An exchange is not a sale.  The two-sided asset relationship needs a
    richer contract than the current frontend transaction row, so the record
    remains visible to the qualification layer but cannot auto-promote.
    """

    if not isinstance(value, str):
        raise SenateEfdError("Senate eFD transaction type is invalid")
    key = " ".join(value.strip().lower().split())
    mapping = {
        "purchase": "purchase",
        "sale (full)": "sale",
        "sale (partial)": "sale",
        "sale": "sale",
        "exchange": "exchange",
    }
    if key not in mapping:
        raise SenateEfdError("Senate eFD transaction type is unsupported")
    normalized = mapping[key]
    if normalized == "exchange":
        return {
            "transaction_type": "exchange",
            "qualification_status": "quarantined",
            "quarantine_reasons": ["exchange_requires_contract_resolution"],
        }
    return {
        "transaction_type": normalized,
        "qualification_status": "eligible",
        "quarantine_reasons": [],
    }
