import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.congress_members import CongressMemberError, build_congress_senate_roster


def row(bioguide: str, name: str, state: str, *, chamber: str = "Senate") -> dict:
    return {
        "bioguideId": bioguide,
        "name": name,
        "partyName": "Republican",
        "state": state,
        "terms": {"item": [{"chamber": chamber, "startYear": 2025}]},
        "updateDate": "2026-09-20T00:00:00Z",
        "url": f"https://api.congress.gov/v3/member/{bioguide}?format=json",
    }


def page(*members: dict, count: int | None = None) -> bytes:
    return json.dumps({
        "members": list(members),
        "pagination": {"count": len(members) if count is None else count},
        "request": {"format": "json"},
    }, sort_keys=True).encode()


class CongressMemberTests(unittest.TestCase):
    def test_builds_complete_senate_only_roster_with_stable_ids(self):
        content = page(
            row("B001299", "Banks, Jim", "Indiana"),
            row("M001190", "Mullin, Markwayne", "Oklahoma"),
            row("H000001", "House, Ada", "California", chamber="House of Representatives"),
        )
        result = build_congress_senate_roster([content], 119)
        self.assertEqual(result["schema_version"], "congress-senate-members/v1")
        self.assertEqual(result["metadata"]["source_record_count"], 3)
        self.assertEqual(result["metadata"]["record_count"], 2)
        self.assertEqual(
            [(item["person_id"], item["state"]) for item in result["members"]],
            [("senate:B001299", "IN"), ("senate:M001190", "OK")],
        )

    def test_incomplete_pagination_and_duplicate_members_fail_closed(self):
        with self.assertRaises(CongressMemberError):
            build_congress_senate_roster([page(row("B001299", "Banks, Jim", "Indiana"), count=2)])
        duplicate = row("B001299", "Banks, Jim", "Indiana")
        with self.assertRaises(CongressMemberError):
            build_congress_senate_roster([page(duplicate, duplicate)])


if __name__ == "__main__":
    unittest.main()
