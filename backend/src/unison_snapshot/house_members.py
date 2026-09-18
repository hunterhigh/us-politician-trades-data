"""Official House Clerk current-member roster and conservative identity suggestions."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from .house import HouseIndexError

MEMBERS_URL = "https://clerk.house.gov/xml/lists/MemberData.xml"
MAX_MEMBERS_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class HouseMember:
    person_id: str
    bioguide_id: str
    official_name: str
    state_district: str
    state: str
    party: str
    evidence_url: str


def parse_members(content: bytes) -> tuple[str, list[HouseMember]]:
    if len(content) > MAX_MEMBERS_BYTES or b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise HouseIndexError("Invalid House member roster")
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise HouseIndexError("House member roster XML is invalid") from None
    if root.tag != "MemberData":
        raise HouseIndexError("House member roster has an unexpected root")
    published = root.attrib.get("publish-date", "")
    try:
        published_date = datetime.strptime(published, "%B %d, %Y").date().isoformat()
    except ValueError:
        raise HouseIndexError("House member roster has an invalid publish date") from None
    members: list[HouseMember] = []
    seen_ids: set[str] = set()
    seen_districts: set[str] = set()
    for member in root.findall("./members/member"):
        info = member.find("member-info")
        if info is None:
            raise HouseIndexError("House member roster entry has no member-info")
        district = (member.findtext("statedistrict") or "").strip()
        bioguide = (info.findtext("bioguideID") or "").strip()
        name = (info.findtext("official-name") or "").strip()
        state_node = info.find("state")
        state = (state_node.attrib.get("postal-code", "") if state_node is not None else "").strip()
        party = (info.findtext("party") or "").strip()
        if not bioguide and not name and not party:
            # The official roster retains vacant seats with an empty member-info block.
            continue
        if (not re.fullmatch(r"[A-Z]{2}[0-9]{2}", district) or
                not re.fullmatch(r"[A-Z][0-9]{6}", bioguide) or not name or
                not re.fullmatch(r"[A-Z]{2}", state) or party not in {"R", "D", "I"} or
                bioguide in seen_ids or district in seen_districts):
            raise HouseIndexError("House member roster entry is invalid or duplicated")
        members.append(HouseMember(
            person_id=f"house:{bioguide}", bioguide_id=bioguide, official_name=name,
            state_district=district, state=state, party=party,
            evidence_url=f"https://bioguide.congress.gov/search/bio/{bioguide}",
        ))
        seen_ids.add(bioguide)
        seen_districts.add(district)
    if not members:
        raise HouseIndexError("House member roster contains no members")
    members.sort(key=lambda member: member.person_id)
    return published_date, members


class HouseMemberClient:
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def download(self) -> tuple[bytes, dict[str, str]]:
        request = urllib.request.Request(MEMBERS_URL, headers={
            "Accept": "text/xml,application/xhtml+xml,text/html;q=0.9,*/*;q=0.8",
            "User-Agent": "unison-house-identity/0.2"}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status != 200 or response.geturl() != MEMBERS_URL:
                    raise HouseIndexError("House member roster returned an unexpected response")
                content = response.read(MAX_MEMBERS_BYTES + 1)
                headers = {name.lower(): value for name, value in response.headers.items()
                           if name.lower() in {"etag", "last-modified", "content-type"}}
        except urllib.error.HTTPError as exc:
            raise HouseIndexError(f"House member roster returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise HouseIndexError("House member roster is unavailable") from None
        if len(content) > MAX_MEMBERS_BYTES:
            raise HouseIndexError("House member roster exceeds its size limit")
        return content, headers


def archive_members(root: Path, content: bytes, headers: dict[str, str], members: list[HouseMember],
                    published_date: str, retrieved_at: str | None = None) -> dict:
    root = root.resolve()
    sha = hashlib.sha256(content).hexdigest()
    folder = root / "house_clerk" / "members"
    target = folder / f"{sha}.xml"
    folder.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_bytes() != content:
        raise HouseIndexError("Archived House member roster hash collision")
    if not target.exists():
        with tempfile.NamedTemporaryFile(dir=folder, prefix=f".{sha}.", suffix=".tmp", delete=False) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        temporary.replace(target)
    metadata = {"source_id": "house_clerk_members", "source_url": MEMBERS_URL,
                "published_date": published_date,
                "retrieved_at": retrieved_at or datetime.now(timezone.utc).isoformat(),
                "sha256": sha, "record_count": len(members), "headers": headers,
                "archive_path": target.relative_to(root).as_posix()}
    meta_path = target.with_suffix(".json")
    if meta_path.exists():
        try:
            existing = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            raise HouseIndexError("Archived House member metadata is invalid") from None
        stable = ("source_id", "source_url", "published_date", "sha256", "record_count", "archive_path")
        if any(existing.get(field) != metadata[field] for field in stable):
            raise HouseIndexError("Archived House member metadata conflicts with its content")
        return existing
    encoded = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=folder, prefix=f".{sha}.", suffix=".tmp", delete=False) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(meta_path)
    return metadata


def discover_members(root: Path, *, client: HouseMemberClient | None = None) -> dict:
    content, headers = (client or HouseMemberClient()).download()
    published, members = parse_members(content)
    metadata = archive_members(root, content, headers, members, published)
    return {"metadata": metadata, "members": [asdict(member) for member in members]}


def _name_key(value: str) -> tuple[str, str] | None:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    words = re.findall(r"[a-z]+", normalized)
    while words and words[0] in {"hon", "honorable", "mr", "mrs", "ms", "miss", "dr"}:
        words.pop(0)
    while words and words[-1] in {"jr", "sr", "ii", "iii", "iv"}:
        words.pop()
    return (words[0], words[-1]) if len(words) >= 2 else None


def suggest_identity(extraction: dict, roster: dict) -> dict:
    source = extraction.get("source") or {}
    candidates = [member for member in roster.get("members", [])
                  if member.get("state_district") == source.get("state_district")]
    filer_key = _name_key(str(source.get("filer_name", "")))
    matches = [member for member in candidates if _name_key(str(member.get("official_name", ""))) == filer_key]
    if len(matches) != 1:
        return {"status": "unresolved", "document_id": source.get("document_id"),
                "filer_name": source.get("filer_name"), "state_district": source.get("state_district"),
                "candidate_count": len(matches)}
    member = matches[0]
    return {"status": "suggested_requires_review", "document_id": source.get("document_id"),
            "person_id": member["person_id"], "official_name": member["official_name"],
            "state": member["state"], "state_district": member["state_district"], "party": member["party"],
            "evidence_url": member["evidence_url"], "roster_sha256": roster.get("metadata", {}).get("sha256"),
            "match_basis": "official_roster_exact_district_first_last_name"}
