"""Fail-closed collection and parsing for the official OGE disclosure catalog.

The catalog is discovery metadata, not a financial disclosure report.  In
particular, its ``docDate`` field is the date an entry was added to the
catalog.  It must never be treated as a filing date.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Iterable
import urllib.error
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
import urllib.request


API_URL = "https://extapps2.oge.gov/201/Presiden.nsf/API.xsp/v2/rest"
CATALOG_URL = (
    "https://www.oge.gov/web/OGE.nsf/"
    "Officials%20Individual%20Disclosures%20Search%20Collection?OpenForm"
)
SCHEMA = "oge-disclosure-catalog/v1"
GATE_SCHEMA = "oge-source-gate/v1"
_RESPONSE_FIELDS = {"draw", "recordsTotal", "recordsFiltered", "data"}
_ROW_FIELDS = {"type", "name", "agency", "title", "level", "docDate", "amended"}
_ALLOWED_HOSTS = {"oge.gov", "www.oge.gov", "extapps2.oge.gov"}
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_CATALOG_ROWS = 25_000
MAX_CATALOG_PAGE_SIZE = MAX_CATALOG_ROWS
MAX_CATALOG_ATTEMPTS = 3


class OgeCatalogError(RuntimeError):
    """The official catalog response did not satisfy the expected contract."""


@dataclass(frozen=True)
class OgeSourceConfig:
    """Collection gate; absent settings can never access the official catalog."""

    enabled: bool = False
    terms_acknowledged: bool = False


def _environment_flag(value: object, name: str) -> bool:
    if value in {None, ""}:
        return False
    if value == "true":
        return True
    if value == "false":
        return False
    raise OgeCatalogError(f"{name} must be exactly true or false")


def source_config_from_environment(
        environment: dict[str, str] | None = None, *,
        enabled_name: str = "OGE_COLLECTION_ENABLED",
        terms_name: str = "OGE_TERMS_ACKNOWLEDGED") -> OgeSourceConfig:
    values = os.environ if environment is None else environment
    return OgeSourceConfig(
        enabled=_environment_flag(values.get(enabled_name), enabled_name),
        terms_acknowledged=_environment_flag(values.get(terms_name), terms_name),
    )


def require_collection_enabled(config: OgeSourceConfig | None = None) -> None:
    selected = config or OgeSourceConfig()
    if not isinstance(selected, OgeSourceConfig):
        raise OgeCatalogError("OGE source configuration is invalid")
    if not selected.enabled:
        raise OgeCatalogError("OGE collection is disabled")
    if not selected.terms_acknowledged:
        raise OgeCatalogError("OGE terms acknowledgement is required")


def collection_gate_status(config: OgeSourceConfig) -> dict:
    if not isinstance(config, OgeSourceConfig):
        raise OgeCatalogError("OGE source configuration is invalid")
    status = ("enabled" if config.enabled and config.terms_acknowledged else
              "blocked" if config.enabled else "disabled")
    reasons = ([] if status == "enabled" else
               ["terms_acknowledgement_missing"] if status == "blocked" else
               ["collection_not_enabled"])
    return {
        "schema_version": GATE_SCHEMA,
        "source_id": "oge",
        "status": status,
        "collection_enabled": config.enabled,
        "terms_acknowledged": config.terms_acknowledged,
        "reasons": reasons,
    }


@dataclass(frozen=True)
class OgeCatalogRecord:
    """One visible 278-T catalog row; this deliberately has no filing ID."""

    catalog_index: int
    catalog_added_date: str
    document_type: str
    filer_name: str
    agency: str
    position_title: str
    level: str
    amended_label: str | None
    pending_final_oge_disposition: bool
    access_method: str
    source_document_id: str | None
    document_url: str
    source_id: str = "oge"


@dataclass(frozen=True)
class OgeCatalogPage:
    start: int
    requested_length: int
    records_total: int
    row_count: int
    transactions: tuple[OgeCatalogRecord, ...]


class _TypeMarkupParser(HTMLParser):
    """Accept the catalog's small anchor-only HTML fragment."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.anchors: list[tuple[str, str]] = []
        self._href: str | None = None
        self._anchor_text: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self._anchor_text is not None:
            raise OgeCatalogError("OGE catalog type contains unexpected markup")
        values = dict(attrs)
        if set(values) != {"href"} or not isinstance(values["href"], str):
            raise OgeCatalogError("OGE catalog link has unexpected attributes")
        self._href = values["href"]
        self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._anchor_text is None or self._href is None:
            raise OgeCatalogError("OGE catalog type contains malformed markup")
        label = " ".join("".join(self._anchor_text).split())
        self.anchors.append((self._href, label))
        self._href = None
        self._anchor_text = None

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self._anchor_text is not None:
            self._anchor_text.append(data)

    def close(self) -> None:
        super().close()
        if self._anchor_text is not None:
            raise OgeCatalogError("OGE catalog type contains an unclosed link")


def _string(row: dict, field: str, *, allow_empty: bool = False) -> str:
    value = row.get(field)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise OgeCatalogError(f"OGE catalog row has invalid {field}")
    if value != value.strip() and not allow_empty:
        raise OgeCatalogError(f"OGE catalog row has padded {field}")
    return value


def _catalog_date(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise OgeCatalogError("OGE catalog row has invalid docDate") from None
    return parsed.date().isoformat()


def _official_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS or
            parsed.username is not None or parsed.password is not None or
            parsed.port is not None or parsed.fragment):
        raise OgeCatalogError("OGE catalog link is not an allowed official URL")
    return value


def _type_fragment(value: str) -> tuple[str, list[tuple[str, str]]]:
    parser = _TypeMarkupParser()
    try:
        parser.feed(value)
        parser.close()
    except OgeCatalogError:
        raise
    except Exception:
        raise OgeCatalogError("OGE catalog type markup is invalid") from None
    if len(parser.anchors) > 1:
        raise OgeCatalogError("OGE catalog type contains multiple links")
    anchors = [(_official_url(url), label) for url, label in parser.anchors]
    return " ".join("".join(parser.text).split()), anchors


def _classify_278(type_markup: str) -> tuple[str, str, str | None, bool] | None:
    # The live catalog contains legacy markup defects in unrelated document
    # types (for example, an unescaped apostrophe inside a request URL).  Only
    # 278-T rows are inputs to this source, so out-of-scope markup must not
    # block the entire catalog while every candidate 278-T link stays strict.
    if "278 Transaction" not in type_markup:
        return None
    text, anchors = _type_fragment(type_markup)
    if not text.startswith("278 Transaction"):
        return None
    if len(anchors) != 1:
        raise OgeCatalogError("OGE 278 Transaction row requires one official link")
    url, label = anchors[0]
    parsed = urlsplit(url)
    decoded_path = unquote(parsed.path)
    pending = "Pending Final OGE Disposition" in text
    if label == "278 Transaction":
        match = re.fullmatch(
            r"/201/Presiden\.nsf/PAS\+Index/([0-9A-F]{32})/\$FILE/([^/]+\.pdf)",
            decoded_path, flags=re.IGNORECASE)
        if parsed.hostname != "extapps2.oge.gov" or match is None or parsed.query:
            raise OgeCatalogError("OGE direct 278-T link has an unexpected shape")
        return "direct_pdf", url, match.group(1).lower(), pending
    if label == "Request this Document":
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (parsed.hostname != "extapps2.oge.gov" or not decoded_path.endswith("/201 Request") or
                set(query) != {"OpenForm", "Filer"} or query["OpenForm"] != [""] or
                len(query["Filer"]) != 1 or not query["Filer"][0].strip()):
            raise OgeCatalogError("OGE request 278-T link has an unexpected shape")
        return "request_required", url, None, pending
    raise OgeCatalogError("OGE 278 Transaction link has an unexpected label")


def parse_catalog_page(payload: object, *, start: int, length: int) -> OgeCatalogPage:
    """Validate one unfiltered official API page and retain all 278-T rows."""

    if type(start) is not int or start < 0 or type(length) is not int or length <= 0:
        raise OgeCatalogError("OGE catalog page bounds are invalid")
    if not isinstance(payload, dict) or set(payload) != _RESPONSE_FIELDS:
        raise OgeCatalogError("OGE catalog response fields changed")
    draw = payload.get("draw")
    total = payload.get("recordsTotal")
    filtered = payload.get("recordsFiltered")
    rows = payload.get("data")
    if type(draw) is not int or draw < 0:
        raise OgeCatalogError("OGE catalog response has invalid draw")
    if type(total) is not int or total < 0 or filtered != total:
        raise OgeCatalogError("OGE catalog response has invalid recordsTotal")
    if not isinstance(rows, list) or len(rows) > length or start + len(rows) > total:
        raise OgeCatalogError("OGE catalog response has invalid page data")

    transactions: list[OgeCatalogRecord] = []
    for offset, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != _ROW_FIELDS:
            raise OgeCatalogError("OGE catalog row fields changed")
        type_markup = _string(row, "type")
        classification = _classify_278(type_markup)
        # Validate every row's catalog link even when it is not a 278-T.
        if classification is None:
            continue
        access_method, document_url, source_document_id, pending = classification
        amended = _string(row, "amended", allow_empty=True).strip() or None
        transactions.append(OgeCatalogRecord(
            catalog_index=start + offset,
            catalog_added_date=_catalog_date(_string(row, "docDate")),
            document_type="278_transaction",
            filer_name=_string(row, "name"),
            agency=_string(row, "agency"),
            position_title=_string(row, "title"),
            level=_string(row, "level"),
            amended_label=amended,
            pending_final_oge_disposition=pending,
            access_method=access_method,
            source_document_id=source_document_id,
            document_url=document_url,
        ))
    return OgeCatalogPage(start, length, total, len(rows), tuple(transactions))


def build_catalog(pages: Iterable[OgeCatalogPage]) -> dict:
    """Join complete pages without deduplicating visible catalog rows."""

    ordered = sorted(pages, key=lambda page: page.start)
    if not ordered:
        raise OgeCatalogError("OGE catalog requires at least one page")
    total = ordered[0].records_total
    expected_start = 0
    transactions: list[dict] = []
    for page in ordered:
        if page.records_total != total:
            raise OgeCatalogError("OGE catalog recordsTotal changed between pages")
        if page.start != expected_start:
            raise OgeCatalogError("OGE catalog pages contain a gap or overlap")
        expected_count = min(page.requested_length, total - page.start)
        if expected_count < 0 or page.row_count != expected_count:
            raise OgeCatalogError("OGE catalog page is incomplete")
        transactions.extend(asdict(row) for row in page.transactions)
        expected_start += page.row_count
    if expected_start != total:
        raise OgeCatalogError("OGE catalog pagination is incomplete")
    return {
        "schema_version": SCHEMA,
        "source_id": "oge",
        "source_url": CATALOG_URL,
        "records_total": total,
        "catalog_rows_covered": expected_start,
        "transactions": transactions,
    }


class OgeCatalogClient:
    """Bounded client for the public DataTables endpoint.

    It never submits Form 201.  A caller must explicitly satisfy both source
    gates before this client is reachable.
    """

    _COLUMNS = ("docDate", "title", "type", "name", "agency", "level")

    def __init__(self, timeout: float = 30.0, opener=None, *,
                 max_attempts: int = MAX_CATALOG_ATTEMPTS, sleeper=time.sleep):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
            raise OgeCatalogError("OGE catalog attempts are invalid")
        if not callable(sleeper):
            raise OgeCatalogError("OGE catalog sleeper is invalid")
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener()
        self.max_attempts = max_attempts
        self.sleeper = sleeper

    def download_page(self, *, start: int, length: int, draw: int) -> tuple[bytes, dict[str, str]]:
        if (type(start) is not int or start < 0 or type(length) is not int or
                not 1 <= length <= MAX_CATALOG_PAGE_SIZE):
            raise OgeCatalogError("OGE catalog request bounds are invalid")
        if type(draw) is not int or draw <= 0:
            raise OgeCatalogError("OGE catalog request draw is invalid")
        parameters: list[tuple[str, str]] = [
            ("draw", str(draw)), ("start", str(start)), ("length", str(length)),
            ("search[value]", ""), ("search[regex]", "false"),
        ]
        # Production reads the bounded catalog data in one response after a count
        # probe. Keeping the official newest-first ordering is therefore safe from
        # page-boundary ties.
        parameters.extend([
            ("order[0][column]", "0"),
            ("order[0][dir]", "desc"),
        ])
        for index, name in enumerate(self._COLUMNS):
            parameters.extend([
                (f"columns[{index}][data]", name),
                (f"columns[{index}][name]", ""),
                (f"columns[{index}][searchable]", "true"),
                (f"columns[{index}][orderable]", "true"),
                (f"columns[{index}][search][value]", ""),
                (f"columns[{index}][search][regex]", "false"),
            ])
        url = f"{API_URL}?{urlencode(parameters)}"
        request = urllib.request.Request(url, headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": CATALOG_URL,
            "User-Agent": (
                "unison-oge-evidence/0.1 "
                "(+https://github.com/hunterhigh/us-politician-trades-data)"
            ),
            "X-Requested-With": "XMLHttpRequest",
        }, method="GET")
        content = b""
        headers: dict[str, str] = {}
        for attempt in range(self.max_attempts):
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    if response.status != 200 or response.geturl() != url:
                        raise OgeCatalogError("OGE catalog returned an unexpected response")
                    content = response.read(MAX_RESPONSE_BYTES + 1)
                    headers = {name.lower(): value for name, value in response.headers.items()
                               if name.lower() in {"etag", "last-modified", "content-type"}}
                break
            except urllib.error.HTTPError as exc:
                # The official DataTables endpoint intermittently returns 400
                # for an unchanged request and then succeeds on retry. Keep the
                # retry bounded; response validation below still fails closed.
                if exc.code not in {400, 429, 500, 502, 503, 504} or attempt + 1 == self.max_attempts:
                    raise OgeCatalogError(f"OGE catalog returned HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError,
                    http.client.HTTPException):
                if attempt + 1 == self.max_attempts:
                    raise OgeCatalogError("OGE catalog is unavailable") from None
            self.sleeper(2 ** attempt)
        if len(content) > MAX_RESPONSE_BYTES:
            raise OgeCatalogError("OGE catalog response exceeds its size limit")
        if "json" not in headers.get("content-type", "").lower():
            raise OgeCatalogError("OGE catalog returned a non-JSON response")
        return content, headers


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise OgeCatalogError("Archived OGE content conflicts with its hash")
        return
    with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def archive_catalog(root: Path, raw_pages: list[tuple[int, int, bytes, dict[str, str]]],
                    catalog: dict, retrieved_at: str | None = None) -> dict:
    if not raw_pages or catalog.get("schema_version") != SCHEMA:
        raise OgeCatalogError("OGE catalog archive input is invalid")
    base = root.resolve()
    folder = base / "oge" / "catalog"
    page_metadata = []
    for start, length, content, headers in raw_pages:
        sha = hashlib.sha256(content).hexdigest()
        path = folder / "pages" / f"{sha}.json"
        _write_once(path, content)
        page_metadata.append({
            "start": start, "length": length, "sha256": sha, "byte_length": len(content),
            "headers": headers, "archive_path": path.relative_to(base).as_posix(),
        })
    normalized = json.dumps(catalog, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")).encode("utf-8")
    sha = hashlib.sha256(normalized).hexdigest()
    normalized_path = folder / "normalized" / f"{sha}.json"
    _write_once(normalized_path, normalized)
    path = folder / f"{sha}.json"
    metadata = {
        "schema_version": "oge-catalog-archive/v1",
        "source_id": "oge", "source_url": API_URL,
        "retrieved_at": retrieved_at or datetime.now(timezone.utc).isoformat(),
        "sha256": sha, "record_count": catalog["records_total"],
        "transaction_report_count": len(catalog["transactions"]),
        "direct_pdf_count": sum(item["access_method"] == "direct_pdf"
                                for item in catalog["transactions"]),
        "request_required_count": sum(item["access_method"] == "request_required"
                                      for item in catalog["transactions"]),
        "page_count": len(page_metadata), "pages": page_metadata,
        "normalized_archive_path": normalized_path.relative_to(base).as_posix(),
        "archive_path": path.relative_to(base).as_posix(),
    }
    encoded = json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise OgeCatalogError("Archived OGE catalog metadata is invalid") from None
        stable = set(metadata) - {"retrieved_at"}
        if any(existing.get(field) != metadata[field] for field in stable):
            raise OgeCatalogError("Archived OGE catalog metadata conflicts with its content")
        return existing
    _write_once(path, encoded)
    return metadata


def discover_catalog(root: Path, config: OgeSourceConfig, *, page_size: int = MAX_CATALOG_PAGE_SIZE,
                     client: OgeCatalogClient | None = None) -> dict:
    require_collection_enabled(config)
    if type(page_size) is not int or not 1 <= page_size <= MAX_CATALOG_PAGE_SIZE:
        raise OgeCatalogError(
            f"OGE catalog page size must be between 1 and {MAX_CATALOG_PAGE_SIZE}")
    selected = client or OgeCatalogClient()
    pages: list[OgeCatalogPage] = []
    raw_pages: list[tuple[int, int, bytes, dict[str, str]]] = []
    if page_size == MAX_CATALOG_PAGE_SIZE:
        probe_content, probe_headers = selected.download_page(start=0, length=1, draw=1)
        try:
            probe_payload = json.loads(probe_content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise OgeCatalogError("OGE catalog response is invalid JSON") from None
        probe = parse_catalog_page(probe_payload, start=0, length=1)
        total = probe.records_total
        if total > MAX_CATALOG_ROWS:
            raise OgeCatalogError("OGE catalog exceeds its safety limit")
        if total == 0:
            pages.append(probe)
            raw_pages.append((0, 1, probe_content, probe_headers))
        else:
            content, headers = selected.download_page(start=0, length=total, draw=2)
            try:
                payload = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise OgeCatalogError("OGE catalog response is invalid JSON") from None
            page = parse_catalog_page(payload, start=0, length=total)
            if page.records_total != total:
                raise OgeCatalogError("OGE catalog changed during collection")
            pages.append(page)
            raw_pages.append((0, total, content, headers))
        catalog = build_catalog(pages)
        metadata = archive_catalog(root, raw_pages, catalog)
        return {"metadata": metadata, **catalog}

    start = 0
    draw = 1
    while True:
        content, headers = selected.download_page(start=start, length=page_size, draw=draw)
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise OgeCatalogError("OGE catalog response is invalid JSON") from None
        page = parse_catalog_page(payload, start=start, length=page_size)
        if page.records_total > MAX_CATALOG_ROWS:
            raise OgeCatalogError("OGE catalog exceeds its safety limit")
        pages.append(page)
        raw_pages.append((start, page_size, content, headers))
        start += page.row_count
        if start == page.records_total:
            break
        if page.row_count == 0:
            raise OgeCatalogError("OGE catalog pagination made no progress")
        draw += 1
    catalog = build_catalog(pages)
    metadata = archive_catalog(root, raw_pages, catalog)
    return {"metadata": metadata, **catalog}
