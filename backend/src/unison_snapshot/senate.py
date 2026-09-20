"""Fail-closed, offline models for Senate eFD PTR discovery.

This module deliberately contains no HTTP client and cannot accept the eFD
portal notice.  A caller may feed it an already obtained search response, but
production collection remains behind an explicit configuration and terms
gate.  The date exposed by the search table is retained as portal metadata;
it is not evidence of the report's filing date.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from html.parser import HTMLParser
import re
from typing import Iterable
from urllib.parse import urljoin, urlsplit
import uuid


HOME_URL = "https://efdsearch.senate.gov/search/home/"
SEARCH_URL = "https://efdsearch.senate.gov/search/report/data/"
SCHEMA = "senate-efd-discovery/v1"
_ALLOWED_HOST = "efdsearch.senate.gov"
_RESPONSE_FIELDS = {"draw", "recordsTotal", "recordsFiltered", "data"}


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
            raise SenateEfdError("Senate eFD report link has unexpected attributes")
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
    if not isinstance(payload, dict) or set(payload) != _RESPONSE_FIELDS:
        raise SenateEfdError("Senate eFD search response fields changed")
    draw = payload.get("draw")
    total = payload.get("recordsTotal")
    filtered = payload.get("recordsFiltered")
    rows = payload.get("data")
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
