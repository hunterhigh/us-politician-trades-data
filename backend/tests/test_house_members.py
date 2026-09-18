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
                         ("suggested_requires_review", "house:D000032"))
        extraction["source"]["filer_name"] = "Hon. Different Person"
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
