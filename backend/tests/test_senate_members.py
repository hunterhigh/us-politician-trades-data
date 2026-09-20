from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.senate_members import (
    SenateRosterError, build_roster, discover_members, parse_members, suggest_identity,
)


def member(full: str, first: str, last: str, state: str, bioguide: str, party: str = "D") -> bytes:
    return f"""<member><member_full>{full}</member_full><last_name>{last}</last_name>
<first_name>{first}</first_name><party>{party}</party><state>{state}</state>
<address>1 Senate Building</address><phone>202-555-0100</phone><email></email>
<website>https://www.senate.gov/</website><class>Class I</class>
<bioguide_id>{bioguide}</bioguide_id></member>""".encode()


def xml(*members: bytes) -> bytes:
    return b'<?xml version="1.0"?><contact_information>' + b"".join(members) + b"</contact_information>"


ADA_CA = member("Example, Ada A.", "Ada", "Example", "CA", "E000001")


class SenateMemberTests(unittest.TestCase):
    def test_member_client_retries_transient_official_source_rejection(self):
        calls = []

        class Response:
            status = 200
            headers = {"Content-Type": "application/xml"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def geturl(self):
                return "https://www.senate.gov/general/contact_information/senators_cfm.xml"

            def read(self, _limit):
                return xml(ADA_CA)

        def opener(request, timeout):
            calls.append((request.full_url, timeout))
            if len(calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)
            return Response()

        from unison_snapshot.senate_members import SenateMemberClient
        body, headers = SenateMemberClient(
            retry_delays=(0,), opener=opener, sleeper=lambda _: None,
        ).download()
        self.assertEqual((body, headers["content-type"], len(calls)),
                         (xml(ADA_CA), "application/xml", 2))

    def test_discovery_archives_raw_and_normalized_roster_by_hash(self):
        content = xml(ADA_CA)

        class Client:
            def __init__(self, body):
                self.body = body

            def download(self):
                return self.body, {"content-type": "application/xml"}

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = discover_members(root, client=Client(content))
            second = discover_members(root, client=Client(content))
            sha = first["metadata"]["sha256"]
            self.assertEqual(first["metadata"], second["metadata"])
            self.assertEqual((root / f"senate_efd/members/{sha}.xml").read_bytes(), content)
            self.assertTrue((root / f"senate_efd/members/{sha}.roster.json").is_file())

    def test_official_roster_builds_stable_bioguide_identity(self):
        with_leadership = ADA_CA.replace(
            b"</member>",
            b"<leadership_position>Committee Chair</leadership_position></member>",
        )
        roster = build_roster(xml(with_leadership))
        person = roster["members"][0]
        self.assertEqual((person["person_id"], person["state"]), ("senate:E000001", "CA"))
        self.assertEqual(roster["metadata"]["source_id"], "senate_members")
        self.assertEqual(len(roster["metadata"]["sha256"]), 64)

    def test_name_and_state_are_both_required_for_unique_match(self):
        roster = build_roster(xml(
            ADA_CA,
            member("Example, Ada B.", "Ada", "Example", "OR", "E000002"),
        ))
        match = suggest_identity("Hon. Ada A. Example", "ca", roster)
        self.assertEqual((match["status"], match["person_id"]),
                         ("matched_automatically", "senate:E000001"))
        self.assertEqual(suggest_identity("Ada Example", "WA", roster)["status"], "unresolved")

    def test_same_name_in_same_state_is_ambiguous(self):
        roster = build_roster(xml(
            ADA_CA,
            member("Example, Ada B.", "Ada", "Example", "CA", "E000002"),
        ))
        result = suggest_identity("Ada Example", "CA", roster)
        self.assertEqual((result["status"], result["candidate_count"]), ("unresolved", 2))

    def test_roster_field_drift_and_unsafe_xml_fail_closed(self):
        drift = ADA_CA.replace(b"</member>", b"<new_field>x</new_field></member>")
        cases = [xml(drift), xml(ADA_CA.replace(b"<state>CA</state>", b"")),
                 b'<!DOCTYPE x [<!ENTITY y "z">]><contact_information/>']
        for content in cases:
            with self.subTest(content=content), self.assertRaises(SenateRosterError):
                parse_members(content)

    def test_duplicate_or_invalid_bioguide_fails(self):
        with self.assertRaises(SenateRosterError):
            parse_members(xml(ADA_CA, member("Other, Bea", "Bea", "Other", "OR", "E000001")))
        with self.assertRaises(SenateRosterError):
            parse_members(xml(ADA_CA.replace(b"E000001", b"invalid")))


if __name__ == "__main__":
    unittest.main()
