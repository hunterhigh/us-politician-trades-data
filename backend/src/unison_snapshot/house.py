"""Official House Clerk bulk-index discovery; PDF parsing is a separate stage."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

INDEX_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
MAX_ZIP_BYTES = 10 * 1024 * 1024
MAX_INDEX_BYTES = 25 * 1024 * 1024
MAX_PDF_BYTES = 25 * 1024 * 1024


class HouseIndexError(RuntimeError):
    pass


@dataclass(frozen=True)
class HouseFilingCandidate:
    source_id: str
    document_id: str
    filing_type: str
    filer_name: str
    state_district: str | None
    filing_year: int
    filed_date: str | None
    document_url: str
    verification_status: str


def document_url(year: int, filing_type: str, document_id: str) -> str:
    folder = "ptr-pdfs" if filing_type == "P" else "financial-pdfs"
    return f"https://disclosures-clerk.house.gov/public_disc/{folder}/{year}/{document_id}.pdf"


def parse_index(archive: bytes, year: int) -> list[HouseFilingCandidate]:
    if not 2008 <= year <= 2100 or len(archive) > MAX_ZIP_BYTES or not archive.startswith(b"PK"):
        raise HouseIndexError("Invalid House index archive")
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            entries = source.infolist()
            expected = {f"{year}FD.txt", f"{year}FD.xml"}
            names = {entry.filename for entry in entries}
            if (len(entries) != len(expected) or names != expected or
                    any(PurePosixPath(entry.filename).name != entry.filename for entry in entries)):
                raise HouseIndexError("House archive has an unexpected file set")
            xml_entry = next(entry for entry in entries if entry.filename.endswith(".xml"))
            if xml_entry.file_size > MAX_INDEX_BYTES:
                raise HouseIndexError("House XML index exceeds its size limit")
            xml_bytes = source.read(xml_entry)
    except (zipfile.BadZipFile, RuntimeError, KeyError):
        raise HouseIndexError("House index is not a valid ZIP") from None
    if b"<!DOCTYPE" in xml_bytes.upper() or b"<!ENTITY" in xml_bytes.upper():
        raise HouseIndexError("House XML index contains a forbidden declaration")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        raise HouseIndexError("House XML index is invalid") from None
    if root.tag != "FinancialDisclosure":
        raise HouseIndexError("House XML index has an unexpected root")
    rows: list[HouseFilingCandidate] = []
    seen: set[str] = set()
    for member in root.findall("Member"):
        def text(name: str) -> str:
            return (member.findtext(name) or "").strip()
        document_id, filing_type, row_year = text("DocID"), text("FilingType"), text("Year")
        if not re.fullmatch(r"[0-9]{1,20}", document_id) or document_id in seen:
            raise HouseIndexError("House index has an invalid or duplicate document ID")
        if not re.fullmatch(r"[A-Z]", filing_type) or row_year != str(year):
            raise HouseIndexError("House index row has an invalid type or year")
        filed_text = text("FilingDate")
        if filed_text:
            try:
                filed_date = datetime.strptime(filed_text, "%m/%d/%Y").date().isoformat()
            except ValueError:
                raise HouseIndexError("House index row has an invalid filing date") from None
        else:
            filed_date = None
        parts = [text("Prefix"), text("First"), text("Last"), text("Suffix")]
        name = " ".join(part for part in parts if part)
        if not name:
            raise HouseIndexError("House index row has no filer name")
        rows.append(HouseFilingCandidate(
            source_id="house_clerk", document_id=document_id, filing_type=filing_type,
            filer_name=name, state_district=text("StateDst") or None, filing_year=year,
            filed_date=filed_date,
            document_url=document_url(year, filing_type, document_id),
            verification_status="official_raw_unparsed",
        ))
        seen.add(document_id)
    rows.sort(key=lambda row: (row.filed_date or "", row.document_id))
    return rows


class HouseIndexClient:
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def download(self, year: int) -> tuple[bytes, dict[str, str]]:
        url = INDEX_URL.format(year=year)
        request = urllib.request.Request(url, headers={"Accept": "application/zip",
            "User-Agent": "unison-house-discovery/0.2"}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status != 200 or response.geturl() != url:
                    raise HouseIndexError("House index returned an unexpected response")
                content = response.read(MAX_ZIP_BYTES + 1)
                headers = {name.lower(): value for name, value in response.headers.items()
                           if name.lower() in {"etag", "last-modified", "content-type"}}
        except urllib.error.HTTPError as exc:
            raise HouseIndexError(f"House index returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise HouseIndexError("House index is unavailable") from None
        if len(content) > MAX_ZIP_BYTES:
            raise HouseIndexError("House index archive exceeds its size limit")
        return content, headers


class HouseDocumentClient:
    """Download one index-confirmed PTR without following a changed destination."""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def download(self, candidate: HouseFilingCandidate) -> tuple[bytes, dict[str, str]]:
        if candidate.filing_type != "P" or candidate.document_url != document_url(
                candidate.filing_year, candidate.filing_type, candidate.document_id):
            raise HouseIndexError("House PTR candidate is not index-confirmed")
        url = candidate.document_url
        request = urllib.request.Request(url, headers={"Accept": "application/pdf",
            "User-Agent": "unison-house-evidence/0.2"}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status != 200 or response.geturl() != url:
                    raise HouseIndexError("House PTR returned an unexpected response")
                content = response.read(MAX_PDF_BYTES + 1)
                headers = {name.lower(): value for name, value in response.headers.items()
                           if name.lower() in {"etag", "last-modified", "content-type"}}
        except urllib.error.HTTPError as exc:
            raise HouseIndexError(f"House PTR returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise HouseIndexError("House PTR is unavailable") from None
        validate_pdf(content)
        return content, headers


def validate_pdf(content: bytes) -> None:
    if len(content) > MAX_PDF_BYTES:
        raise HouseIndexError("House PTR exceeds its size limit")
    if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-2048:]:
        raise HouseIndexError("House PTR is not a complete PDF")


def archive_document(root: Path, candidate: HouseFilingCandidate, content: bytes,
                     headers: dict[str, str], retrieved_at: str | None = None) -> dict:
    if candidate.filing_type != "P" or candidate.document_url != document_url(
            candidate.filing_year, candidate.filing_type, candidate.document_id):
        raise HouseIndexError("House PTR candidate is not index-confirmed")
    validate_pdf(content)
    root = root.resolve()
    sha = hashlib.sha256(content).hexdigest()
    folder = root / "house_clerk" / "documents" / str(candidate.filing_year) / candidate.document_id
    target = folder / f"{sha}.pdf"
    folder.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_bytes() != content:
        raise HouseIndexError("Archived House PTR hash collision")
    if not target.exists():
        with tempfile.NamedTemporaryFile(dir=folder, prefix=f".{sha}.", suffix=".tmp", delete=False) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        temporary.replace(target)
    metadata = {
        "source_id": "house_clerk", "source_url": candidate.document_url,
        "document_id": candidate.document_id, "filing_type": candidate.filing_type,
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
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            raise HouseIndexError("Archived House PTR metadata is invalid") from None
        stable_fields = ("source_id", "source_url", "document_id", "filing_type", "filing_year",
                         "sha256", "byte_length", "archive_path", "verification_status")
        if any(existing.get(field) != metadata[field] for field in stable_fields):
            raise HouseIndexError("Archived House PTR metadata conflicts with its content")
        return existing
    encoded = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=folder, prefix=f".{sha}.", suffix=".tmp", delete=False) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(meta_path)
    return metadata


def archive_indexed_ptr(root: Path, year: int, index_sha: str, document_id: str,
                        *, client: HouseDocumentClient | None = None) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", index_sha) or not re.fullmatch(r"[0-9]{1,20}", document_id):
        raise HouseIndexError("Invalid House index hash or document ID")
    index_path = root.resolve() / "house_clerk" / "index" / str(year) / f"{index_sha}.zip"
    try:
        index_content = index_path.read_bytes()
    except OSError:
        raise HouseIndexError("Archived House index is unavailable") from None
    if hashlib.sha256(index_content).hexdigest() != index_sha:
        raise HouseIndexError("Archived House index hash does not match its path")
    matches = [row for row in parse_index(index_content, year) if row.document_id == document_id]
    if len(matches) != 1 or matches[0].filing_type != "P":
        raise HouseIndexError("Document is not a PTR in the archived House index")
    content, headers = (client or HouseDocumentClient()).download(matches[0])
    return archive_document(root, matches[0], content, headers)


def archive_discovery(root: Path, year: int, content: bytes, headers: dict[str, str],
                      rows: list[HouseFilingCandidate], retrieved_at: str | None = None) -> dict:
    root = root.resolve()
    sha = hashlib.sha256(content).hexdigest()
    target = root / "house_clerk" / "index" / str(year) / f"{sha}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_bytes() != content:
        raise HouseIndexError("Archived House index hash collision")
    if not target.exists():
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{sha}.", suffix=".tmp", delete=False) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        temporary.replace(target)
    metadata = {
        "source_id": "house_clerk", "source_url": INDEX_URL.format(year=year),
        "filing_year": year, "retrieved_at": retrieved_at or datetime.now(timezone.utc).isoformat(),
        "sha256": sha, "record_count": len(rows), "headers": headers,
        "archive_path": target.relative_to(root).as_posix(),
    }
    meta_path = target.with_suffix(".json")
    if meta_path.exists():
        try:
            existing = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            raise HouseIndexError("Archived House index metadata is invalid") from None
        stable_fields = ("source_id", "source_url", "filing_year", "sha256", "record_count", "archive_path")
        if any(existing.get(field) != metadata[field] for field in stable_fields):
            raise HouseIndexError("Archived House index metadata conflicts with its content")
        return existing
    encoded = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=meta_path.parent, prefix=f".{sha}.", suffix=".tmp", delete=False) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(meta_path)
    return metadata


def discover(year: int, archive_root: Path, *, client: HouseIndexClient | None = None) -> dict:
    content, headers = (client or HouseIndexClient()).download(year)
    rows = parse_index(content, year)
    metadata = archive_discovery(archive_root, year, content, headers, rows)
    return {"metadata": metadata, "filings": [asdict(row) for row in rows]}
