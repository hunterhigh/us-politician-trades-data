"""Conservative identity matching for Senate eFD catalog reports.

The eFD catalog does not expose a state or a stable member identifier.  This
module therefore combines the catalog's filer and office fields with two
official, archived identity inputs: the current Senate roster and the
Congress.gov roster for the filing Congress.  It fails closed unless those
inputs identify exactly one member.
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


def _congress_member_rows(roster: object | None) -> tuple[list[dict], str | None]:
    if roster is None:
        return [], None
    if (not isinstance(roster, dict) or
            roster.get("schema_version") != "congress-senate-members/v1" or
            not isinstance(roster.get("members"), list)):
        raise SenateIdentityError("Congress.gov identity roster is invalid")
    metadata = roster.get("metadata")
    sha = metadata.get("sha256") if isinstance(metadata, dict) else None
    if (not isinstance(sha, str) or not _ROSTER_SHA256.fullmatch(sha) or
            metadata.get("source_id") != "congress_gov_members" or
            not isinstance(metadata.get("congress"), int)):
        raise SenateIdentityError("Congress.gov identity roster has invalid metadata")
    members: list[dict] = []
    seen_person_ids: set[str] = set()
    for member in roster["members"]:
        if not isinstance(member, dict):
            raise SenateIdentityError("Congress.gov identity roster contains an invalid member")
        bioguide = member.get("bioguide_id")
        person_id = member.get("person_id")
        if (not isinstance(bioguide, str) or not _BIOGUIDE_ID.fullmatch(bioguide) or
                person_id != f"senate:{bioguide}" or person_id in seen_person_ids or
                not _words(member.get("first_name")) or not _words(member.get("last_name")) or
                not isinstance(member.get("official_name"), str) or
                not isinstance(member.get("evidence_url"), str) or
                not isinstance(member.get("senate_terms"), list) or
                not member["senate_terms"]):
            raise SenateIdentityError("Congress.gov identity roster contains an invalid member")
        members.append(member)
        seen_person_ids.add(person_id)
    if not members:
        raise SenateIdentityError("Congress.gov identity roster contains no members")
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
            match_basis: str, congress_roster_sha: str | None = None) -> dict:
    result = {
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
    if congress_roster_sha is not None:
        result["congress_roster_sha256"] = congress_roster_sha
    return result


def match_report_identity(report: object, roster: object,
                          congress_roster: object | None = None) -> dict:
    """Match one normalized eFD discovery row to archived official rosters.

    An official-directory alias is accepted only when the catalog supplies a
    named ``Surname, Given (Senator)`` office, its surname agrees with the
    filer name, and its first given-name token plus surname identify exactly
    one current roster member.  A generic paper-report office such as
    ``Senator`` never enables this fallback.
    """

    members, roster_sha = _member_rows(roster)
    congress_members, congress_roster_sha = _congress_member_rows(congress_roster)
    all_by_person = {member["person_id"]: member for member in congress_members}
    all_by_person.update({member["person_id"]: member for member in members})
    all_members = list(all_by_person.values())
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

    exact = [member for member in all_members if _name_matches_member(filer_words, member)]
    if len(exact) == 1:
        return _result(
            report, exact[0], roster_sha, match_class="exact",
            match_basis=("official_rosters_unique_exact_filer_name"
                         if congress_roster_sha else "official_roster_unique_exact_filer_name"),
            congress_roster_sha=congress_roster_sha,
        )

    office_surname, office_given = _named_office_name(report.get("office"))
    named_candidates = [
        member for member in all_members
        if (_words(member["last_name"]) == office_surname and office_given and
            _words(member["first_name"])[0] == office_given[0])
    ]
    filer_agrees = bool(
        office_surname and len(filer_words) > len(office_surname) and
        filer_words[-len(office_surname):] == office_surname
    )
    if not exact and filer_agrees and len(named_candidates) == 1:
        return _result(
            report, named_candidates[0], roster_sha, match_class="alias",
            match_basis="official_catalog_named_office_unique_roster_name",
            congress_roster_sha=congress_roster_sha,
        )

    candidates = {member["person_id"] for member in exact}
    if filer_agrees:
        candidates.update(member["person_id"] for member in named_candidates)
    result = {
        "status": "unresolved",
        "match_class": "unresolved",
        "match_basis": "no_unique_official_identity",
        "document_id": report.get("document_id"),
        "filer_name": report.get("filer_name"),
        "office": report.get("office"),
        "candidate_count": len(candidates),
        "roster_sha256": roster_sha,
    }
    if congress_roster_sha is not None:
        result["congress_roster_sha256"] = congress_roster_sha
    return result


def build_catalog_identities(discovery: object, roster: object,
                             congress_roster: object | None = None) -> dict:
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
    _, congress_roster_sha = _congress_member_rows(congress_roster)

    identities = []
    seen_documents: set[str] = set()
    for report in discovery["reports"]:
        if not isinstance(report, dict) or not isinstance(report.get("document_id"), str):
            raise SenateIdentityError("Senate eFD discovery contains an invalid report")
        document_id = report["document_id"]
        if document_id in seen_documents:
            raise SenateIdentityError("Senate eFD discovery contains a duplicate document")
        identities.append(match_report_identity(report, roster, congress_roster))
        seen_documents.add(document_id)
    identities.sort(key=lambda item: item["document_id"])
    counts = Counter(item["match_class"] for item in identities)
    result = {
        "schema_version": "senate-efd-identities/v1",
        "source_id": "senate_efd",
        "catalog_sha256": catalog_sha,
        "roster_sha256": roster_sha,
        "report_count": len(identities),
        "identity_counts": dict(sorted(counts.items())),
        "identities": identities,
    }
    if congress_roster_sha is not None:
        result["congress_roster_sha256"] = congress_roster_sha
    return result
