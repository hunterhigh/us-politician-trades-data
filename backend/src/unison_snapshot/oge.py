"""Strict, offline parsing for the official OGE disclosure catalog.

The catalog is discovery metadata, not a financial disclosure report.  In
particular, its ``docDate`` field is the date an entry was added to the
catalog.  It must never be treated as a filing date.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import parse_qs, unquote, urlsplit


API_URL = "https://extapps2.oge.gov/201/Presiden.nsf/API.xsp/v2/rest"
CATALOG_URL = (
    "https://www.oge.gov/web/OGE.nsf/"
    "Officials%20Individual%20Disclosures%20Search%20Collection?OpenForm"
)
SCHEMA = "oge-disclosure-catalog/v1"
_RESPONSE_FIELDS = {"draw", "recordsTotal", "recordsFiltered", "data"}
_ROW_FIELDS = {"type", "name", "agency", "title", "level", "docDate", "amended"}
_ALLOWED_HOSTS = {"oge.gov", "www.oge.gov", "extapps2.oge.gov"}


class OgeCatalogError(RuntimeError):
    """The official catalog response did not satisfy the expected contract."""


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


def _classify_278(type_markup: str) -> tuple[str, str, bool] | None:
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
        if (parsed.hostname != "extapps2.oge.gov" or "/PAS+Index/" not in decoded_path or
                "/$FILE/" not in decoded_path or not decoded_path.lower().endswith(".pdf") or
                parsed.query):
            raise OgeCatalogError("OGE direct 278-T link has an unexpected shape")
        return "direct_pdf", url, pending
    if label == "Request this Document":
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (parsed.hostname != "extapps2.oge.gov" or not decoded_path.endswith("/201 Request") or
                set(query) != {"OpenForm", "Filer"} or query["OpenForm"] != [""] or
                len(query["Filer"]) != 1 or not query["Filer"][0].strip()):
            raise OgeCatalogError("OGE request 278-T link has an unexpected shape")
        return "request_required", url, pending
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
        access_method, document_url, pending = classification
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
