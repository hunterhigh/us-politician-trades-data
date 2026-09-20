from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.senate_identity import (
    SenateIdentityError, build_catalog_identities, match_report_identity,
)


SHA = "a" * 64


def member(bioguide: str, first: str, last: str, state: str = "AA") -> dict:
    return {
        "bioguide_id": bioguide,
        "person_id": f"senate:{bioguide}",
        "official_name": f"{last} (I-{state})",
        "first_name": first,
        "last_name": last,
        "state": state,
        "party": "I",
        "evidence_url": f"https://bioguide.congress.gov/search/bio/{bioguide}",
    }


def roster(*members: dict) -> dict:
    return {"metadata": {"sha256": SHA}, "members": list(members)}


class SenateIdentityTests(unittest.TestCase):
    def test_catalog_identity_build_preserves_matched_and_unresolved_reports(self):
        members = roster(member("A000001", "Ada", "Example"))
        discovery = {
            "schema_version": "senate-efd-discovery/v1",
            "metadata": {"sha256": "b" * 64},
            "reports": [
                {"document_id": "two", "filer_name": "Missing Person", "office": "Senator"},
                {"document_id": "one", "filer_name": "Ada Example", "office": "Senator"},
            ],
        }
        result = build_catalog_identities(discovery, members)
        self.assertEqual(result["identity_counts"], {"exact": 1, "unresolved": 1})
        self.assertEqual([item["document_id"] for item in result["identities"]], ["one", "two"])
        self.assertEqual((result["catalog_sha256"], result["roster_sha256"]),
                         ("b" * 64, SHA))

    def test_exact_match_ignores_middle_names_suffix_case_and_trailing_comma(self):
        members = roster(
            member("K000001", "Angus S., Jr.", "King"),
            member("M000001", "Jerry", "Moran"),
        )
        cases = [
            ({"filer_name": "Angus S King, Jr.", "office": "King, Angus (Senator)"},
             "senate:K000001"),
            ({"filer_name": "JERRY MORAN,", "office": "Moran, Jerry (Senator)"},
             "senate:M000001"),
        ]
        for report, person_id in cases:
            with self.subTest(report=report):
                result = match_report_identity(report, members)
                self.assertEqual(
                    (result["status"], result["match_class"], result["person_id"]),
                    ("matched_automatically", "exact", person_id),
                )

    def test_named_office_allows_unique_official_alias(self):
        members = roster(member("T000001", "Tommy", "Tuberville", "AL"))
        result = match_report_identity({
            "document_id": "report-1",
            "filer_name": "Thomas H Tuberville",
            "office": "Tuberville, Tommy (Senator)",
        }, members)
        self.assertEqual(
            (result["match_class"], result["person_id"], result["document_id"]),
            ("alias", "senate:T000001", "report-1"),
        )

    def test_generic_paper_office_cannot_enable_alias_fallback(self):
        members = roster(member("H000001", "Bill", "Hagerty", "TN"))
        result = match_report_identity({
            "filer_name": "William F Hagerty, IV", "office": "Senator",
        }, members)
        self.assertEqual(
            (result["status"], result["match_class"], result["candidate_count"]),
            ("unresolved", "unresolved", 0),
        )

    def test_alias_fallback_requires_a_unique_current_roster_surname(self):
        members = roster(
            member("S000001", "Rick", "Scott", "FL"),
            member("S000002", "Tim", "Scott", "SC"),
        )
        result = match_report_identity({
            "filer_name": "Richard Scott", "office": "Scott, Richard (Senator)",
        }, members)
        self.assertEqual(
            (result["status"], result["candidate_count"]), ("unresolved", 0),
        )

    def test_named_office_cannot_map_a_filer_to_same_surname_successor(self):
        members = roster(member("G000001", "Darline", "Graham", "SC"))
        result = match_report_identity({
            "filer_name": "Lindsey Graham", "office": "Graham, Lindsey (Senator)",
        }, members)
        self.assertEqual(
            (result["status"], result["candidate_count"]), ("unresolved", 0),
        )

    def test_member_absent_from_current_roster_stays_unresolved(self):
        members = roster(member("A000001", "Alan", "Armstrong", "OK"))
        result = match_report_identity({
            "filer_name": "Markwayne Mullin", "office": "Mullin, Markwayne (Senator)",
        }, members)
        self.assertEqual(
            (result["status"], result["candidate_count"]), ("unresolved", 0),
        )

    def test_malformed_or_duplicated_roster_fails_closed(self):
        person = member("A000001", "Ada", "Example")
        cases = [
            {"metadata": {"sha256": "bad"}, "members": [person]},
            roster(person, person),
        ]
        for invalid in cases:
            with self.subTest(invalid=invalid), self.assertRaises(SenateIdentityError):
                match_report_identity({"filer_name": "Ada Example", "office": "Senator"}, invalid)


if __name__ == "__main__":
    unittest.main()
