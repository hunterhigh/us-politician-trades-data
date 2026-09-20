"""Official Congress.gov member history used to resolve Senate filer identities."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


API_ROOT = "https://api.congress.gov/v3"
DEFAULT_CONGRESS = 119
PAGE_LIMIT = 250
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_MEMBER_ROWS = 2_000
_BIOGUIDE_ID = re.compile(r"[A-Z][0-9]{6}")
_STATE_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "American Samoa": "AS", "District of Columbia": "DC", "Guam": "GU",
    "Northern Mariana Islands": "MP", "Puerto Rico": "PR", "Virgin Islands": "VI",
}
_PARTIES = {"Republican": "R", "Democratic": "D", "Independent": "I"}


class CongressMemberError(RuntimeError):
    """Congress.gov member data did not satisfy the production contract."""


class CongressMemberClient:
    def __init__(self, api_key: str, timeout: float = 30.0,
                 retry_delays: tuple[float, ...] = (3, 12, 30), opener=None, sleeper=None):
        if not isinstance(api_key, str) or not api_key.strip():
            raise CongressMemberError("Congress.gov API key is required")
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.retry_delays = retry_delays
        self.opener = opener or urllib.request.urlopen
        self.sleeper = sleeper or time.sleep

    def _download_page(self, congress: int, offset: int) -> tuple[bytes, dict[str, str]]:
        public_url = f"{API_ROOT}/member/congress/{congress}"
        query = urllib.parse.urlencode({
            "limit": PAGE_LIMIT, "offset": offset, "format": "json",
        })
        request = urllib.request.Request(f"{public_url}?{query}", headers={
            "Accept": "application/json",
            "X-Api-Key": self.api_key,
            "User-Agent": (
                "unison-congress-identity/0.1 "
                "(+https://github.com/hunterhigh/us-politician-trades-data)"
            ),
        }, method="GET")
        attempts = len(self.retry_delays) + 1
        for attempt in range(attempts):
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    parsed = urllib.parse.urlsplit(response.geturl())
                    if (response.status != 200 or parsed.scheme != "https" or
                            parsed.hostname != "api.congress.gov" or
                            parsed.path != f"/v3/member/congress/{congress}"):
                        raise CongressMemberError(
                            "Congress.gov member endpoint returned an unexpected response")
                    content = response.read(MAX_PAGE_BYTES + 1)
                    headers = {
                        name.lower(): value for name, value in response.headers.items()
                        if name.lower() in {
                            "etag", "last-modified", "content-type", "x-ratelimit-limit",
                            "x-ratelimit-remaining",
                        }
                    }
                    break
            except urllib.error.HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == attempts - 1:
                    raise CongressMemberError(
                        f"Congress.gov member endpoint returned HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError):
                if attempt == attempts - 1:
                    raise CongressMemberError("Congress.gov member endpoint is unavailable") from None
            self.sleeper(self.retry_delays[attempt])
        if len(content) > MAX_PAGE_BYTES:
            raise CongressMemberError("Congress.gov member page exceeds its size limit")
        return content, headers

    def download_congress(self, congress: int) -> list[tuple[bytes, dict[str, str]]]:
        if not isinstance(congress, int) or congress < 1 or congress > 999:
            raise CongressMemberError("Congress number is invalid")
        pages: list[tuple[bytes, dict[str, str]]] = []
        offset = 0
        expected_count: int | None = None
        while expected_count is None or offset < expected_count:
            content, headers = self._download_page(congress, offset)
            try:
                payload = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise CongressMemberError("Congress.gov member page is not valid JSON") from None
            members = payload.get("members") if isinstance(payload, dict) else None
            pagination = payload.get("pagination") if isinstance(payload, dict) else None
            count = pagination.get("count") if isinstance(pagination, dict) else None
            if (not isinstance(members, list) or not members or not isinstance(count, int) or
                    count < len(members) or count > MAX_MEMBER_ROWS or
                    (expected_count is not None and count != expected_count)):
                raise CongressMemberError("Congress.gov member pagination is invalid")
            expected_count = count
            pages.append((content, headers))
            offset += len(members)
            if len(pages) > (MAX_MEMBER_ROWS // PAGE_LIMIT + 2):
                raise CongressMemberError("Congress.gov member pagination did not terminate")
        if offset != expected_count:
            raise CongressMemberError("Congress.gov member pagination is incomplete")
        return pages


def _name_parts(value: object) -> tuple[str, str]:
    if not isinstance(value, str):
        raise CongressMemberError("Congress.gov member has no valid name")
    surname, separator, given = value.partition(",")
    surname = surname.strip()
    given = given.strip()
    first = given.split()[0] if given else ""
    if not separator or not surname or not first:
        raise CongressMemberError("Congress.gov member has no valid name")
    return first, surname


def build_congress_senate_roster(pages: list[bytes], congress: int = DEFAULT_CONGRESS) -> dict:
    """Validate complete Congress.gov pages and retain every Senate member in the Congress."""

    if not pages:
        raise CongressMemberError("Congress.gov member snapshot contains no pages")
    rows: list[dict] = []
    expected_count: int | None = None
    page_hashes: list[str] = []
    for content in pages:
        if not isinstance(content, bytes) or len(content) > MAX_PAGE_BYTES:
            raise CongressMemberError("Congress.gov member page bytes are invalid")
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CongressMemberError("Congress.gov member page is not valid JSON") from None
        members = payload.get("members") if isinstance(payload, dict) else None
        pagination = payload.get("pagination") if isinstance(payload, dict) else None
        count = pagination.get("count") if isinstance(pagination, dict) else None
        if (not isinstance(members, list) or not isinstance(count, int) or count > MAX_MEMBER_ROWS or
                (expected_count is not None and count != expected_count)):
            raise CongressMemberError("Congress.gov member snapshot pagination changed")
        expected_count = count
        rows.extend(members)
        page_hashes.append(hashlib.sha256(content).hexdigest())
    if len(rows) != expected_count:
        raise CongressMemberError("Congress.gov member snapshot is incomplete")

    normalized: list[dict] = []
    seen_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise CongressMemberError("Congress.gov member row is invalid")
        bioguide = row.get("bioguideId")
        name = row.get("name")
        state = _STATE_CODES.get(row.get("state"))
        party = _PARTIES.get(row.get("partyName"))
        url = row.get("url")
        update_date = row.get("updateDate")
        terms = row.get("terms")
        term_items = terms.get("item") if isinstance(terms, dict) else None
        if (not isinstance(bioguide, str) or not _BIOGUIDE_ID.fullmatch(bioguide) or
                bioguide in seen_ids or not state or not party or
                not isinstance(url, str) or
                url != f"https://api.congress.gov/v3/member/{bioguide}?format=json" or
                not isinstance(update_date, str) or not update_date.endswith("Z") or
                not isinstance(term_items, list) or not term_items):
            raise CongressMemberError("Congress.gov member row has invalid required fields")
        first, last = _name_parts(name)
        senate_terms = []
        for term in term_items:
            if not isinstance(term, dict) or term.get("chamber") not in {
                    "Senate", "House of Representatives"}:
                raise CongressMemberError("Congress.gov member term is invalid")
            if term.get("chamber") != "Senate":
                continue
            start_year = term.get("startYear")
            end_year = term.get("endYear")
            if (not isinstance(start_year, int) or
                    (end_year is not None and (not isinstance(end_year, int) or end_year < start_year))):
                raise CongressMemberError("Congress.gov Senate term is invalid")
            senate_terms.append({"start_year": start_year, "end_year": end_year})
        if senate_terms:
            normalized.append({
                "person_id": f"senate:{bioguide}",
                "bioguide_id": bioguide,
                "official_name": name,
                "first_name": first,
                "last_name": last,
                "state": state,
                "party": party,
                "senate_terms": senate_terms,
                "congress": congress,
                "source_updated_at": update_date,
                "evidence_url": f"https://bioguide.congress.gov/search/bio/{bioguide}",
                "source_url": f"https://api.congress.gov/v3/member/{bioguide}?format=json",
            })
        seen_ids.add(bioguide)
    if not normalized:
        raise CongressMemberError("Congress.gov snapshot contains no Senate members")
    normalized.sort(key=lambda member: member["person_id"])
    fingerprint = json.dumps({
        "congress": congress, "page_sha256": page_hashes, "members": normalized,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    snapshot_sha = hashlib.sha256(fingerprint).hexdigest()
    return {
        "schema_version": "congress-senate-members/v1",
        "metadata": {
            "source_id": "congress_gov_members",
            "source_url": f"{API_ROOT}/member/congress/{congress}",
            "congress": congress,
            "sha256": snapshot_sha,
            "page_sha256": page_hashes,
            "source_record_count": len(rows),
            "record_count": len(normalized),
        },
        "members": normalized,
    }


def _write_once(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise CongressMemberError("Archived Congress.gov member content conflicts with its hash")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
                                     delete=False) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def archive_congress_senate_roster(root: Path, pages: list[tuple[bytes, dict[str, str]]],
                                   roster: dict, retrieved_at: str | None = None) -> dict:
    root = root.resolve()
    snapshot_sha = roster["metadata"]["sha256"]
    folder = root / "senate_efd" / "congress_members" / snapshot_sha
    page_paths = []
    page_metadata = []
    for index, ((content, headers), page_sha) in enumerate(
            zip(pages, roster["metadata"]["page_sha256"], strict=True)):
        path = folder / f"page-{index:03d}-{page_sha}.json"
        _write_once(path, content)
        page_paths.append(path.relative_to(root).as_posix())
        page_metadata.append({"index": index, "sha256": page_sha, "headers": headers,
                              "archive_path": path.relative_to(root).as_posix()})
    roster_path = folder / "roster.json"
    _write_once(roster_path, json.dumps(
        roster, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    metadata = {
        **roster["metadata"],
        "retrieved_at": retrieved_at or datetime.now(timezone.utc).isoformat(),
        "pages": page_metadata,
        "roster_path": roster_path.relative_to(root).as_posix(),
    }
    metadata_path = folder / "metadata.json"
    if metadata_path.exists():
        try:
            existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise CongressMemberError("Archived Congress.gov member metadata is invalid") from None
        stable = ("source_id", "source_url", "congress", "sha256", "page_sha256",
                  "source_record_count", "record_count", "roster_path")
        if any(existing.get(field) != metadata[field] for field in stable):
            raise CongressMemberError("Archived Congress.gov member metadata conflicts")
        return existing
    _write_once(metadata_path, json.dumps(
        metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return metadata


def discover_congress_senate_members(root: Path, api_key: str, *,
                                     congress: int = DEFAULT_CONGRESS,
                                     client: CongressMemberClient | None = None) -> dict:
    downloaded = (client or CongressMemberClient(api_key)).download_congress(congress)
    roster = build_congress_senate_roster([content for content, _ in downloaded], congress)
    metadata = archive_congress_senate_roster(root, downloaded, roster)
    return {"schema_version": roster["schema_version"], "metadata": metadata,
            "members": roster["members"]}
