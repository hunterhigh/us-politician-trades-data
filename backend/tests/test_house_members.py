from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_members import parse_members, suggest_identity


XML = b'''<?xml version="1.0"?><MemberData publish-date="September 2, 2026"><members><member>
<statedistrict>FL19</statedistrict><member-info><bioguideID>D000032</bioguideID>
<official-name>Byron Donalds</official-name><party>R</party><state postal-code="FL"/></member-info>
</member></members></MemberData>'''


class HouseMemberTests(unittest.TestCase):
    def test_official_roster_builds_stable_bioguide_identity(self):
        published, members = parse_members(XML)
        self.assertEqual(published, "2026-09-02")
        self.assertEqual((members[0].person_id, members[0].state_district), ("house:D000032", "FL19"))

    def test_official_vacant_seat_is_not_an_invalid_member(self):
        vacancy = b'''<member><statedistrict>FL20</statedistrict><member-info><bioguideID/>
<official-name/><party/><state postal-code="FL"/></member-info></member>'''
        content = XML.replace(b"</members>", vacancy + b"</members>")
        _, members = parse_members(content)
        self.assertEqual(len(members), 1)

    def test_identity_suggestion_requires_name_and_district(self):
        published, members = parse_members(XML)
        roster = {"metadata": {"sha256": "a" * 64, "published_date": published},
                  "members": [member.__dict__ for member in members]}
        extraction = {"source": {"document_id": "20035420", "filer_name": "Hon. Byron Donalds",
                                  "state_district": "FL19"}}
        suggestion = suggest_identity(extraction, roster)
        self.assertEqual((suggestion["status"], suggestion["person_id"]),
                         ("matched_automatically", "house:D000032"))
        extraction["source"]["filer_name"] = "Hon. Different Person"
        self.assertEqual(suggest_identity(extraction, roster)["status"], "unresolved")

    def test_identity_accepts_surname_first_roster_name(self):
        published, members = parse_members(XML.replace(b"Byron Donalds", b"Donalds, Byron"))
        roster = {"metadata": {"sha256": "b" * 64, "published_date": published},
                  "members": [member.__dict__ for member in members]}
        extraction = {"source": {"document_id": "1", "filer_name": "Hon. Byron Donalds",
                                  "state_district": "FL19"}}
        suggestion = suggest_identity(extraction, roster)
        self.assertEqual(suggestion["person_id"], "house:D000032")
        self.assertEqual(suggestion["match_basis"],
                         "official_roster_exact_district_first_last_name")

    def test_unique_surname_in_current_district_recovers_official_alias(self):
        published, members = parse_members(XML)
        roster = {"metadata": {"sha256": "c" * 64, "published_date": published},
                  "members": [member.__dict__ for member in members]}
        extraction = {"source": {"document_id": "2", "filer_name": "Hon. B. Donalds",
                                  "state_district": "FL19"}}
        suggestion = suggest_identity(extraction, roster)
        self.assertEqual(suggestion["person_id"], "house:D000032")
        self.assertEqual(suggestion["match_basis"],
                         "official_roster_exact_district_unique_surname")

    def test_credentials_and_comma_do_not_hide_the_filers_surname(self):
        published, members = parse_members(XML.replace(b"Byron Donalds", b"Neal P. Dunn")
                                           .replace(b"D000032", b"D000628")
                                           .replace(b"FL19", b"FL02"))
        roster = {"metadata": {"sha256": "1" * 64, "published_date": published},
                  "members": [member.__dict__ for member in members]}
        extraction = {"source": {"document_id": "6",
                                  "filer_name": "Hon. Neal Patrick MD, Facs Dunn",
                                  "state_district": "FL02"}}
        suggestion = suggest_identity(extraction, roster)
        self.assertEqual(suggestion["person_id"], "house:D000628")
        self.assertEqual(suggestion["match_basis"],
                         "official_roster_exact_district_first_last_name")

    def test_redistricted_member_requires_unique_statewide_full_name(self):
        second = b'''<member><statedistrict>FL18</statedistrict><member-info>
<bioguideID>C000001</bioguideID><official-name>Ada Carter</official-name><party>D</party>
<state postal-code="FL"/></member-info></member>'''
        published, members = parse_members(XML.replace(b"</members>", second + b"</members>"))
        roster = {"metadata": {"sha256": "d" * 64, "published_date": published},
                  "members": [member.__dict__ for member in members]}
        extraction = {"source": {"document_id": "3", "filer_name": "Byron Donalds",
                                  "state_district": "FL13"}}
        suggestion = suggest_identity(extraction, roster)
        self.assertEqual(suggestion["person_id"], "house:D000032")
        self.assertEqual(suggestion["match_basis"],
                         "official_roster_same_state_unique_first_last_name_redistricted")

        extraction["source"]["state_district"] = "GA13"
        self.assertEqual(suggest_identity(extraction, roster)["status"], "unresolved")

    def test_ambiguous_fallbacks_fail_closed(self):
        published, members = parse_members(XML)
        base = members[0].__dict__
        extraction = {"source": {"document_id": "4", "filer_name": "B. Donalds",
                                  "state_district": "FL19"}}
        duplicate_district = {**base, "person_id": "house:D000033", "bioguide_id": "D000033",
                              "official_name": "Alice Donalds"}
        roster = {"metadata": {"sha256": "e" * 64}, "members": [base, duplicate_district]}
        self.assertEqual(suggest_identity(extraction, roster)["status"], "unresolved")

        same_name_other_district = {**base, "person_id": "house:D000034",
                                    "bioguide_id": "D000034", "state_district": "FL20"}
        roster["members"] = [base, same_name_other_district]
        extraction["source"].update({"filer_name": "Byron Donalds", "state_district": "FL13"})
        self.assertEqual(suggest_identity(extraction, roster)["status"], "unresolved")

    def test_redistricted_target_district_conflict_fails_closed(self):
        published, members = parse_members(XML)
        base = members[0].__dict__
        conflict = {**base, "person_id": "house:C000001", "bioguide_id": "C000001",
                    "official_name": "Ada Carter"}
        roster = {"metadata": {"sha256": "f" * 64}, "members": [base, conflict]}
        extraction = {"source": {"document_id": "5", "filer_name": "Byron Donalds",
                                  "state_district": "FL13"}}
        self.assertEqual(suggest_identity(extraction, roster)["status"], "unresolved")

    def test_malformed_or_duplicate_roster_fails(self):
        with self.assertRaises(HouseIndexError):
            parse_members(XML.replace(b"D000032", b"invalid"))
        duplicate = XML.replace(b"</members>", XML.split(b"<member>", 1)[1].replace(
            b"</members></MemberData>", b"") + b"</members>")
        with self.assertRaises(HouseIndexError):
            parse_members(duplicate)


if __name__ == "__main__":
    unittest.main()
