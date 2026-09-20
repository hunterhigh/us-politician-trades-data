"""Conservative identity matching for Senate eFD catalog reports.

The eFD catalog does not expose a state or a stable member identifier.  This
module therefore uses only two official, archived inputs: the catalog's filer
and office fields, and the current Senate roster.  It fails closed when those
inputs do not identify exactly one current member.
"""
from __future__ import annotations

from collections import Counter
import re
import unicodedata


_BIOGUIDE_ID = re.compile(r"[A-Z][0-9]{6}")
_ROSTER_SHA256 = re.compile(r"[0-9a-f]{64}")
_NAMED_OFFICE = re.compile(r"\s*(.+?)\s*,\s*(.+?)\s*\(Senator\)\s*", re.IGNORECASE)
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


class SenateIdentityError(ValueError):
    """The archived identity input does not satisfy its expected contract."""


def _words(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return tuple(re.findall(r"[a-z]+", folded.lower()))


def _without_suffix(words: tuple[str, ...]) -> tuple[str, ...]:
    while words and words[-1] in _SUFFIXES:
        words = words[:-1]
    return words


def _member_rows(roster: object) -> tuple[list[dict], str]:
    if not isinstance(roster, dict) or not isinstance(roster.get("members"), list):
        raise SenateIdentityError("Senate identity roster is invalid")
    metadata = roster.get("metadata")
    sha = metadata.get("sha256") if isinstance(metadata, dict) else None
    if not isinstance(sha, str) or not _ROSTER_SHA256.fullmatch(sha):
        raise SenateIdentityError("Senate identity roster has no valid content hash")

    members: list[dict] = []
    seen_person_ids: set[str] = set()
    for member in roster["members"]:
        if not isinstance(member, dict):
            raise SenateIdentityError("Senate identity roster contains an invalid member")
        bioguide = member.get("bioguide_id")
        person_id = member.get("person_id")
        first = _words(member.get("first_name"))
        last = _words(member.get("last_name"))
        if (not isinstance(bioguide, str) or not _BIOGUIDE_ID.fullmatch(bioguide) or
                person_id != f"senate:{bioguide}" or person_id in seen_person_ids or
                not first or not last or not isinstance(member.get("official_name"), str) or
                not isinstance(member.get("evidence_url"), str)):
            raise SenateIdentityError("Senate identity roster contains an invalid member")
        members.append(member)
        seen_person_ids.add(person_id)
    if not members:
        raise SenateIdentityError("Senate identity roster contains no members")
    return members, sha


def _name_matches_member(filer_words: tuple[str, ...], member: dict) -> bool:
    """Match the first given-name token and the complete structured surname.

    Middle names and suffixes vary between the two official sources.  The
    first given-name token is retained, so nicknames and leading initials do
    not silently become exact matches.
    """

    filer_words = _without_suffix(filer_words)
    given = _words(member["first_name"])
    surname = _words(member["last_name"])
    return bool(filer_words and given and surname and filer_words[0] == given[0] and
                len(filer_words) > len(surname) and filer_words[-len(surname):] == surname)


def _named_office_name(office: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not isinstance(office, str):
        return (), ()
    match = _NAMED_OFFICE.fullmatch(office)
    return (_words(match.group(1)), _words(match.group(2))) if match else ((), ())


def _result(report: dict, member: dict, roster_sha: str, *, match_class: str,
            match_basis: str) -> dict:
    return {
        "status": "matched_automatically",
        "match_class": match_class,
        "match_basis": match_basis,
        "document_id": report.get("document_id"),
        "filer_name": report.get("filer_name"),
        "office": report.get("office"),
        "person_id": member["person_id"],
        "official_name": member["official_name"],
        "state": member.get("state"),
        "party": member.get("party"),
        "evidence_url": member["evidence_url"],
        "roster_sha256": roster_sha,
    }


def match_report_identity(report: object, roster: object) -> dict:
    """Match one normalized eFD discovery row to the current official roster.

    An official-directory alias is accepted only when the catalog supplies a
    named ``Surname, Given (Senator)`` office, its surname agrees with the
    filer name, and its first given-name token plus surname identify exactly
    one current roster member.  A generic paper-report office such as
    ``Senator`` never enables this fallback.
    """

    members, roster_sha = _member_rows(roster)
    if not isinstance(report, dict):
        raise SenateIdentityError("Senate eFD identity input is not an object")
    filer_words = _without_suffix(_words(report.get("filer_name")))
    if len(filer_words) < 2:
        return {
            "status": "unresolved", "match_class": "unresolved",
            "match_basis": "invalid_filer_name", "document_id": report.get("document_id"),
            "filer_name": report.get("filer_name"), "office": report.get("office"),
            "candidate_count": 0, "roster_sha256": roster_sha,
        }

    exact = [member for member in members if _name_matches_member(filer_words, member)]
    if len(exact) == 1:
        return _result(
            report, exact[0], roster_sha, match_class="exact",
            match_basis="official_roster_unique_exact_filer_name",
        )

    office_surname, office_given = _named_office_name(report.get("office"))
    surname_candidates = [
        member for member in members
        if (_words(member["last_name"]) == office_surname and office_given and
            _words(member["first_name"])[0] == office_given[0])
    ]
    filer_agrees = bool(
        office_surname and len(filer_words) > len(office_surname) and
        filer_words[-len(office_surname):] == office_surname
    )
    if not exact and filer_agrees and len(surname_candidates) == 1:
        return _result(
            report, surname_candidates[0], roster_sha, match_class="alias",
            match_basis="official_catalog_named_office_unique_roster_name",
        )

    candidates = {member["person_id"] for member in exact}
    if filer_agrees:
        candidates.update(member["person_id"] for member in surname_candidates)
    return {
        "status": "unresolved",
        "match_class": "unresolved",
        "match_basis": "no_unique_official_identity",
        "document_id": report.get("document_id"),
        "filer_name": report.get("filer_name"),
        "office": report.get("office"),
        "candidate_count": len(candidates),
        "roster_sha256": roster_sha,
    }


def build_catalog_identities(discovery: object, roster: object) -> dict:
    """Resolve every catalog report without dropping unresolved identities."""

    if (not isinstance(discovery, dict) or
            discovery.get("schema_version") != "senate-efd-discovery/v1" or
            not isinstance(discovery.get("reports"), list)):
        raise SenateIdentityError("Senate eFD discovery is invalid")
    metadata = discovery.get("metadata")
    catalog_sha = metadata.get("sha256") if isinstance(metadata, dict) else None
    if not isinstance(catalog_sha, str) or not _ROSTER_SHA256.fullmatch(catalog_sha):
        raise SenateIdentityError("Senate eFD discovery has no valid content hash")
    _, roster_sha = _member_rows(roster)

    identities = []
    seen_documents: set[str] = set()
    for report in discovery["reports"]:
        if not isinstance(report, dict) or not isinstance(report.get("document_id"), str):
            raise SenateIdentityError("Senate eFD discovery contains an invalid report")
        document_id = report["document_id"]
        if document_id in seen_documents:
            raise SenateIdentityError("Senate eFD discovery contains a duplicate document")
        identities.append(match_report_identity(report, roster))
        seen_documents.add(document_id)
    identities.sort(key=lambda item: item["document_id"])
    counts = Counter(item["match_class"] for item in identities)
    return {
        "schema_version": "senate-efd-identities/v1",
        "source_id": "senate_efd",
        "catalog_sha256": catalog_sha,
        "roster_sha256": roster_sha,
        "report_count": len(identities),
        "identity_counts": dict(sorted(counts.items())),
        "identities": identities,
    }
