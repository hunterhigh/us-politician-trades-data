"""Strict parsing and identity matching for the official Senate roster XML."""
from __future__ import annotations

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


MEMBERS_URL = "https://www.senate.gov/general/contact_information/senators_cfm.xml"
MAX_MEMBERS_BYTES = 5 * 1024 * 1024
_REQUIRED_FIELDS = {"member_full", "first_name", "last_name", "party", "state", "bioguide_id"}
_OPTIONAL_FIELDS = {"address", "phone", "email", "website", "class"}


class SenateRosterError(RuntimeError):
    """The official roster did not satisfy the expected XML contract."""


class SenateMemberClient:
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def download(self) -> tuple[bytes, dict[str, str]]:
        request = urllib.request.Request(MEMBERS_URL, headers={
            "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
            "User-Agent": "unison-senate-identity/0.2 (+https://github.com/hunterhigh/us-politician-trades-data)",
        }, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status != 200 or response.geturl() != MEMBERS_URL:
                    raise SenateRosterError("Senate member roster returned an unexpected response")
                content = response.read(MAX_MEMBERS_BYTES + 1)
                headers = {name.lower(): value for name, value in response.headers.items()
                           if name.lower() in {"etag", "last-modified", "content-type"}}
        except urllib.error.HTTPError as exc:
            raise SenateRosterError(f"Senate member roster returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise SenateRosterError("Senate member roster is unavailable") from None
        if len(content) > MAX_MEMBERS_BYTES:
            raise SenateRosterError("Senate member roster exceeds its size limit")
        return content, headers


@dataclass(frozen=True)
class SenateMember:
    person_id: str
    bioguide_id: str
    official_name: str
    first_name: str
    last_name: str
    state: str
    party: str
    senate_class: str | None
    evidence_url: str


def _text(fields: dict[str, ET.Element], name: str) -> str:
    node = fields[name]
    value = (node.text or "").strip()
    if not value:
        raise SenateRosterError(f"Senate roster member has invalid {name}")
    return value


def parse_members(content: bytes) -> list[SenateMember]:
    if (not isinstance(content, bytes) or len(content) > MAX_MEMBERS_BYTES or
            b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper()):
        raise SenateRosterError("Invalid Senate member roster")
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise SenateRosterError("Senate member roster XML is invalid") from None
    if root.tag != "contact_information" or root.attrib:
        raise SenateRosterError("Senate member roster has an unexpected root")

    members: list[SenateMember] = []
    seen_ids: set[str] = set()
    for node in list(root):
        if node.tag != "member" or node.attrib:
            raise SenateRosterError("Senate member roster has an unexpected entry")
        children = list(node)
        if any(child.attrib or list(child) for child in children):
            raise SenateRosterError("Senate member roster field structure changed")
        names = [child.tag for child in children]
        if (len(names) != len(set(names)) or not _REQUIRED_FIELDS.issubset(names) or
                not set(names).issubset(_REQUIRED_FIELDS | _OPTIONAL_FIELDS)):
            missing = sorted(_REQUIRED_FIELDS - set(names))
            unexpected = sorted(set(names) - (_REQUIRED_FIELDS | _OPTIONAL_FIELDS))
            raise SenateRosterError(
                f"Senate member roster fields changed; missing={missing}; unexpected={unexpected}")
        fields = {child.tag: child for child in children}
        bioguide = _text(fields, "bioguide_id")
        official_name = _text(fields, "member_full")
        first_name = _text(fields, "first_name")
        last_name = _text(fields, "last_name")
        state = _text(fields, "state")
        party = _text(fields, "party")
        senate_class = (fields.get("class").text or "").strip() if fields.get("class") is not None else None
        senate_class = senate_class or None
        if (not re.fullmatch(r"[A-Z][0-9]{6}", bioguide) or
                not re.fullmatch(r"[A-Z]{2}", state) or party not in {"R", "D", "I"} or
                (senate_class is not None and not re.fullmatch(r"Class (I|II|III)", senate_class)) or
                bioguide in seen_ids):
            raise SenateRosterError("Senate member roster entry is invalid or duplicated")
        members.append(SenateMember(
            person_id=f"senate:{bioguide}",
            bioguide_id=bioguide,
            official_name=official_name,
            first_name=first_name,
            last_name=last_name,
            state=state,
            party=party,
            senate_class=senate_class,
            evidence_url=f"https://bioguide.congress.gov/search/bio/{bioguide}",
        ))
        seen_ids.add(bioguide)
    if not members:
        raise SenateRosterError("Senate member roster contains no members")
    members.sort(key=lambda member: member.person_id)
    return members


def build_roster(content: bytes) -> dict:
    members = parse_members(content)
    return {
        "metadata": {
            "source_id": "senate_members",
            "source_url": MEMBERS_URL,
            "sha256": hashlib.sha256(content).hexdigest(),
            "record_count": len(members),
        },
        "members": [asdict(member) for member in members],
    }


def archive_members(root: Path, content: bytes, headers: dict[str, str], roster: dict,
                    retrieved_at: str | None = None) -> dict:
    root = root.resolve()
    sha = roster["metadata"]["sha256"]
    folder = root / "senate_efd" / "members"
    folder.mkdir(parents=True, exist_ok=True)
    xml_path = folder / f"{sha}.xml"
    roster_path = folder / f"{sha}.roster.json"
    metadata_path = folder / f"{sha}.json"
    if xml_path.exists() and xml_path.read_bytes() != content:
        raise SenateRosterError("Archived Senate member roster hash collision")
    def write_once(path: Path, payload: bytes) -> None:
        if path.exists():
            if path.read_bytes() != payload:
                raise SenateRosterError("Archived Senate roster content conflicts with its hash")
            return
        with tempfile.NamedTemporaryFile(dir=folder, prefix=f".{sha}.", suffix=".tmp",
                                         delete=False) as handle:
            handle.write(payload)
            temporary = Path(handle.name)
        temporary.replace(path)

    write_once(xml_path, content)
    roster_bytes = json.dumps(roster, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")).encode("utf-8")
    write_once(roster_path, roster_bytes)
    metadata = {
        "source_id": "senate_members",
        "source_url": MEMBERS_URL,
        "retrieved_at": retrieved_at or datetime.now(timezone.utc).isoformat(),
        "sha256": sha,
        "record_count": len(roster["members"]),
        "headers": headers,
        "archive_path": xml_path.relative_to(root).as_posix(),
        "roster_path": roster_path.relative_to(root).as_posix(),
    }
    if metadata_path.exists():
        try:
            existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise SenateRosterError("Archived Senate member metadata is invalid") from None
        stable = ("source_id", "source_url", "sha256", "record_count", "archive_path", "roster_path")
        if any(existing.get(field) != metadata[field] for field in stable):
            raise SenateRosterError("Archived Senate member metadata conflicts with its content")
        return existing
    write_once(metadata_path, json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode("utf-8"))
    return metadata


def discover_members(root: Path, *, client: SenateMemberClient | None = None) -> dict:
    content, headers = (client or SenateMemberClient()).download()
    roster = build_roster(content)
    metadata = archive_members(root, content, headers, roster)
    return {"metadata": metadata, "members": roster["members"]}


def _ascii_words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    return re.findall(r"[a-z]+", normalized)


def _name_key(value: str) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    before, separator, after = value.partition(",")
    if separator:
        last_words = _ascii_words(before)
        given_words = _ascii_words(after)
        while given_words and given_words[0] in {"hon", "honorable", "mr", "mrs", "ms", "dr"}:
            given_words.pop(0)
        if not last_words or not given_words:
            return None
        return given_words[0], last_words[-1]
    words = _ascii_words(value)
    while words and words[0] in {"hon", "honorable", "mr", "mrs", "ms", "dr", "sen", "senator"}:
        words.pop(0)
    while words and words[-1] in {"jr", "sr", "ii", "iii", "iv"}:
        words.pop()
    return (words[0], words[-1]) if len(words) >= 2 else None


def suggest_identity(filer_name: object, state: object, roster: dict) -> dict:
    """Return a match only when exact normalized name plus state is unique."""

    normalized_state = state.strip().upper() if isinstance(state, str) else ""
    filer_key = _name_key(filer_name) if isinstance(filer_name, str) else None
    members = roster.get("members", []) if isinstance(roster, dict) else []
    matches = [member for member in members
               if isinstance(member, dict) and member.get("state") == normalized_state and
               (_name_key(str(member.get("official_name", ""))) == filer_key or
                _name_key(f"{member.get('first_name', '')} {member.get('last_name', '')}") == filer_key)]
    if not filer_key or not re.fullmatch(r"[A-Z]{2}", normalized_state) or len(matches) != 1:
        return {
            "status": "unresolved",
            "filer_name": filer_name,
            "state": normalized_state or state,
            "candidate_count": len(matches),
        }
    member = matches[0]
    return {
        "status": "matched_automatically",
        "person_id": member["person_id"],
        "official_name": member["official_name"],
        "state": member["state"],
        "party": member["party"],
        "evidence_url": member["evidence_url"],
        "roster_sha256": roster.get("metadata", {}).get("sha256"),
        "match_basis": "official_roster_exact_state_first_last_name",
    }
