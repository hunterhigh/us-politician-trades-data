"""Discover and archive PDFs linked by the official White House disclosures page.

The public page is discovery evidence, not a filing register. Link labels may
identify a report family, but dates and report identities require the PDF.
No request form or OGE Form 201 URL is fetched by this module.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.request
from urllib.parse import unquote, urlsplit

PAGE_URL = "https://www.whitehouse.gov/disclosures/"
INDEX_SCHEMA = "whitehouse-public-disclosures-index/v1"
PAGE_ARCHIVE_SCHEMA = "whitehouse-public-disclosures-page/v1"
PDF_ARCHIVE_SCHEMA = "whitehouse-public-disclosures-pdf/v1"
MAX_PAGE_BYTES = 4 * 1024 * 1024
MAX_PDF_BYTES = 50 * 1024 * 1024
_UPLOAD_PATH = re.compile(r"/wp-content/uploads/20\d{2}/(?:0[1-9]|1[0-2])/.+\.pdf", re.I)
_ANNUAL = re.compile(r"\((20\d{2}) Annual\)$", re.I)
_PRESIDENT_ANNUAL = re.compile(r"\s+(20\d{2}) Annual Report$", re.I)
_PTR = re.compile(r"\s*Periodic Transaction Report\b", re.I)


class WhiteHouseDisclosureError(ValueError):
    """Official source changed shape or evidence failed validation."""


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _write_once(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise WhiteHouseDisclosureError("content-addressed White House evidence conflicts")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                     suffix=".tmp", delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def _pdf_url(url: str) -> str:
    if not isinstance(url, str):
        raise WhiteHouseDisclosureError("PDF URL is invalid")
    parsed = urlsplit(url)
    path = unquote(parsed.path)
    if (not url.isascii() or parsed.scheme != "https" or
            parsed.hostname != "www.whitehouse.gov" or
            parsed.username is not None or parsed.password is not None or
            parsed.port is not None or parsed.query or parsed.fragment or
            not _UPLOAD_PATH.fullmatch(path) or ".." in path.split("/")):
        raise WhiteHouseDisclosureError("PDF URL is outside the official upload allowlist")
    return url


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[dict] = []
        self._details: list[dict] = []
        self._summary = False
        self._anchor: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "details":
            section = {"title": "", "links": []}
            self._details.append(section)
            self.sections.append(section)
        elif tag == "summary" and self._details:
            self._summary = True
        elif tag == "a" and self._details:
            self._anchor = {"url": dict(attrs).get("href"), "label": ""}

    def handle_data(self, data: str) -> None:
        if self._summary and self._details:
            self._details[-1]["title"] += data
        if self._anchor is not None:
            self._anchor["label"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._anchor is not None:
            self._details[-1]["links"].append(self._anchor)
            self._anchor = None
        elif tag == "summary":
            self._summary = False
        elif tag == "details" and self._details:
            self._details.pop()


def _label_kind(section: str, label: str) -> tuple[str, int | None, str]:
    label = " ".join(label.split())
    if section == "Transaction Reports":
        match = _PTR.search(label)
        if match is None:
            return "278t", None, "label_ambiguous"
        name = label[:match.start()].strip(" ,;:–—-\ufffd")
        return "278t", None, name or "label_ambiguous"
    annual = _ANNUAL.search(label)
    if annual:
        return "278e_annual", int(annual.group(1)), label[:annual.start()].strip()
    if _PRESIDENT_ANNUAL.search(label):
        match = _PRESIDENT_ANNUAL.search(label)
        assert match is not None
        return "278e_annual", int(match.group(1)), label[:match.start()].strip()
    for marker in ("Termination Report", "Termination"):
        if label.endswith(f"({marker})"):
            return "278e_termination", None, label[:-(len(marker) + 2)].strip()
    if label.endswith("(New Entrant)"):
        return "278e_new_entrant", None, label[:-13].strip()
    # A bare name is deliberately not called New Entrant from its upload date.
    return "278e_unspecified", None, label


def parse_disclosure_index(html: bytes) -> dict:
    """Deterministically enumerate linked PDFs; ambiguous labels remain explicit."""
    if not isinstance(html, bytes) or len(html) > MAX_PAGE_BYTES:
        raise WhiteHouseDisclosureError("disclosures page is missing or oversized")
    try:
        markup = html.decode("utf-8")
    except UnicodeDecodeError:
        raise WhiteHouseDisclosureError("disclosures page is not UTF-8") from None
    parser = _Links()
    parser.feed(markup)
    sections = {" ".join(item["title"].split()): item["links"] for item in parser.sections}
    if "Financial Disclosure Reports" not in sections or "Transaction Reports" not in sections:
        raise WhiteHouseDisclosureError("disclosures report sections are missing")
    reports = []
    quarantine = []
    seen: set[str] = set()
    for section in ("Financial Disclosure Reports", "Transaction Reports"):
        for item in sections[section]:
            label = " ".join(item["label"].split())
            url = item["url"]
            try:
                _pdf_url(url)
                if not label:
                    raise WhiteHouseDisclosureError("PDF link label is empty")
                if url in seen:
                    raise WhiteHouseDisclosureError("PDF URL is listed more than once")
            except WhiteHouseDisclosureError as exc:
                quarantine.append({"section": section, "label": label,
                                   "url": url, "reason": str(exc)})
                continue
            seen.add(url)
            kind, year, name = _label_kind(section, label)
            status = "classified_from_label" if kind not in {"278e_unspecified"} and name != "label_ambiguous" else "requires_pdf_inspection"
            reports.append({
                "source_id": "whitehouse_public_disclosures",
                "source_document_id": "wh-url:" + _sha(url.encode("utf-8"))[:24],
                "document_url": url,
                "source_section": section,
                "link_label": label,
                "filer_name_from_label": name if name != "label_ambiguous" else None,
                "document_type_from_label": kind,
                "report_year_from_label": year,
                "classification_status": status,
            })
    if not reports:
        raise WhiteHouseDisclosureError("disclosures page has no report PDF links")
    if len({item["source_document_id"] for item in reports}) != len(reports):
        raise WhiteHouseDisclosureError("public disclosure URL identifiers collide")
    reports.sort(key=lambda item: item["document_url"])
    quarantine.sort(key=lambda item: (item["section"], item["label"], item["url"] or ""))
    return {"schema_version": INDEX_SCHEMA, "source_id": "whitehouse_public_disclosures",
            "page_url": PAGE_URL, "page_sha256": _sha(html), "reports": reports,
            "quarantine": quarantine, "report_link_count": len(reports),
            "quarantine_count": len(quarantine)}


class WhiteHouseDisclosureClient:
    def __init__(self, timeout: float = 45.0, opener=None):
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener()

    def _get(self, url: str, maximum: int, accept: str) -> tuple[bytes, dict[str, str]]:
        request = urllib.request.Request(url, headers={
            "Accept": accept,
            "User-Agent": "unison-whitehouse-public-evidence/0.1 (+https://github.com/hunterhigh/us-politician-trades-data)",
        }, method="GET")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status != 200 or response.geturl() != url:
                    raise WhiteHouseDisclosureError("White House source redirected or returned non-200")
                content = response.read(maximum + 1)
                headers = {name.lower(): value for name, value in response.headers.items()
                           if name.lower() in {"content-type", "etag", "last-modified"}}
        except urllib.error.HTTPError as exc:
            raise WhiteHouseDisclosureError(f"White House source returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise WhiteHouseDisclosureError("White House source is unavailable") from None
        if len(content) > maximum:
            raise WhiteHouseDisclosureError("White House source exceeds its size limit")
        return content, headers

    def fetch_index(self) -> tuple[bytes, dict[str, str]]:
        content, headers = self._get(PAGE_URL, MAX_PAGE_BYTES, "text/html")
        if "html" not in headers.get("content-type", "").lower():
            raise WhiteHouseDisclosureError("disclosures page has non-HTML content type")
        return content, headers

    def download_pdf(self, url: str) -> tuple[bytes, dict[str, str]]:
        _pdf_url(url)
        content, headers = self._get(url, MAX_PDF_BYTES, "application/pdf")
        if "pdf" not in headers.get("content-type", "").lower():
            raise WhiteHouseDisclosureError("disclosure report has non-PDF content type")
        if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-4096:]:
            raise WhiteHouseDisclosureError("disclosure report PDF envelope is incomplete")
        return content, headers


def archive_public_index(root: Path, *, client: WhiteHouseDisclosureClient | None = None,
                         retrieved_at: str | None = None) -> dict:
    """Archive the source HTML before returning its deterministic index."""
    content, headers = (client or WhiteHouseDisclosureClient()).fetch_index()
    index = parse_disclosure_index(content)
    base = Path(root).resolve()
    sha = index["page_sha256"]
    page_path = base / "whitehouse/disclosures/pages" / f"{sha}.html"
    metadata_path = page_path.with_suffix(".json")
    _write_once(page_path, content)
    metadata = {"schema_version": PAGE_ARCHIVE_SCHEMA,
                "source_id": "whitehouse_public_disclosures", "page_url": PAGE_URL,
                "retrieved_at": retrieved_at or datetime.now(timezone.utc).isoformat(),
                "sha256": sha, "byte_length": len(content), "headers": headers,
                "archive_path": page_path.relative_to(base).as_posix(),
                "report_link_count": index["report_link_count"],
                "quarantine_count": index["quarantine_count"]}
    if metadata_path.exists():
        old = json.loads(metadata_path.read_text(encoding="utf-8"))
        stable = ("schema_version", "source_id", "page_url", "sha256", "byte_length",
                  "archive_path", "report_link_count", "quarantine_count")
        if any(old.get(key) != metadata[key] for key in stable):
            raise WhiteHouseDisclosureError("archived page metadata conflicts")
        metadata = old
    else:
        _write_once(metadata_path, _canonical(metadata))
    index["page_archive"] = metadata
    return index


def _existing_pdfs(folder: Path, source_id: str, url: str) -> list[dict]:
    result = []
    for path in sorted(folder.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        sha = record.get("sha256")
        pdf = folder / f"{sha}.pdf"
        if (record.get("schema_version") != PDF_ARCHIVE_SCHEMA or
                record.get("document_id") != source_id or record.get("document_url") != url or
                not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha) or
                path.name != f"{sha}.json" or not pdf.is_file() or
                _sha(pdf.read_bytes()) != sha or pdf.stat().st_size != record.get("byte_length")):
            raise WhiteHouseDisclosureError("archived White House PDF or metadata is invalid")
        result.append(record)
    return result


def archive_public_batch(root: Path, index: dict, *, limit: int,
                         client: WhiteHouseDisclosureClient | None = None,
                         refresh_existing: bool = False,
                         start_after_id: str | None = None,
                         retrieved_at: str | None = None) -> dict:
    """Download new URLs at most once per run; preserve same-URL PDF revisions."""
    if (not isinstance(index, dict) or index.get("schema_version") != INDEX_SCHEMA or
            index.get("source_id") != "whitehouse_public_disclosures" or
            not isinstance(index.get("page_sha256"), str) or
            not re.fullmatch(r"[0-9a-f]{64}", index["page_sha256"]) or
            type(limit) is not int or limit < 0 or not isinstance(index.get("reports"), list) or
            not isinstance(index.get("quarantine"), list)):
        raise WhiteHouseDisclosureError("public disclosure index is invalid")
    base = Path(root).resolve()
    archived = []
    failures = []
    attempted = 0
    last_attempted_id = start_after_id
    rows = index["reports"]
    if start_after_id:
        for position, item in enumerate(rows):
            if item.get("source_document_id") == start_after_id:
                rows = rows[position + 1:] + rows[:position + 1]
                break
    for row in rows:
        url = _pdf_url(row.get("document_url"))
        document_id = "wh-url:" + _sha(url.encode("utf-8"))[:24]
        if row.get("source_document_id") != document_id:
            raise WhiteHouseDisclosureError("public disclosure document ID differs")
        folder = base / "whitehouse/disclosures/reports" / document_id.split(":", 1)[1]
        existing = _existing_pdfs(folder, document_id, url)
        if existing and not refresh_existing:
            archived.extend(existing)
            continue
        if attempted >= limit:
            archived.extend(existing)
            continue
        attempted += 1
        last_attempted_id = document_id
        try:
            content, headers = (client or WhiteHouseDisclosureClient()).download_pdf(url)
            sha = _sha(content)
            pdf_path = folder / f"{sha}.pdf"
            metadata_path = folder / f"{sha}.json"
            _write_once(pdf_path, content)
            metadata = {"schema_version": PDF_ARCHIVE_SCHEMA,
                        "source_id": "whitehouse_public_disclosures",
                        "document_id": document_id, "document_url": url,
                        "page_sha256": index["page_sha256"],
                        "link_label": row["link_label"],
                        "document_type_from_label": row["document_type_from_label"],
                        "filer_name_from_label": row["filer_name_from_label"],
                        "report_year_from_label": row["report_year_from_label"],
                        "retrieved_at": retrieved_at or datetime.now(timezone.utc).isoformat(),
                        "sha256": sha, "byte_length": len(content), "headers": headers,
                        "archive_path": pdf_path.relative_to(base).as_posix()}
            if metadata_path.exists():
                old = json.loads(metadata_path.read_text(encoding="utf-8"))
                # The same PDF can be relisted after page and label changes.
                if any(old.get(key) != metadata[key] for key in
                       ("schema_version", "source_id", "document_id", "document_url",
                        "sha256", "byte_length", "archive_path")):
                    raise WhiteHouseDisclosureError("archived PDF metadata conflicts")
                metadata = old
            else:
                _write_once(metadata_path, _canonical(metadata))
            archived.extend(record for record in existing if record["sha256"] != sha)
            archived.append(metadata)
        except (WhiteHouseDisclosureError, OSError) as exc:
            archived.extend(existing)
            failures.append({"document_id": document_id, "document_url": url,
                             "reason": str(exc), "retryable": True})
    return {"schema_version": "whitehouse-public-disclosures-batch/v1",
            "source_id": "whitehouse_public_disclosures",
            "indexed_count": len(index["reports"]), "attempted_count": attempted,
            "last_attempted_id": last_attempted_id,
            "archived_version_count": len(archived),
            "pending_url_count": len(index["reports"]) -
            len({record["document_id"] for record in archived}),
            "failures": failures, "reports": archived,
            "index_quarantine": index["quarantine"]}
