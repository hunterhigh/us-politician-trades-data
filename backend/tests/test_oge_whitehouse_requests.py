"""Request planning preserves OGE evidence without inventing report identities."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.oge_whitehouse_requests import (
    OgeWhiteHouseRequestError, build_request_plan,
)


def _row(name: str, agency: str, kind: str, *, date: str = "2026-08-21T04:21:52") -> dict:
    return {
        "type": kind,
        "name": name,
        "agency": agency,
        "title": "Senior Advisor",
        "level": "n/a",
        "docDate": date,
        "amended": "",
    }


def _request(label: str, filer: str) -> str:
    return (f"{label} (<a href='https://extapps2.oge.gov/201/Presiden.nsf/"
            f"201%20Request?OpenForm&Filer={filer}'>Request this Document</a>)")


class OgeWhiteHouseRequestTests(unittest.TestCase):
    def _archive(self, folder: str, rows: list[dict]) -> dict:
        raw = json.dumps({"draw": 1, "recordsTotal": len(rows),
                          "recordsFiltered": len(rows), "data": rows}).encode()
        digest = hashlib.sha256(raw).hexdigest()
        relative = f"oge/catalog/pages/{digest}.json"
        path = Path(folder) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return {
            "schema_version": "oge-catalog-archive/v1",
            "source_id": "oge",
            "sha256": "a" * 64,
            "record_count": len(rows),
            "pages": [{"start": 0, "length": len(rows), "byte_length": len(raw),
                       "sha256": digest, "archive_path": relative}],
        }

    def test_groups_request_intents_but_preserves_every_catalog_occurrence(self):
        annual = _row("Example, Ada", "White House Office", _request("Annual (2026)", "Example"))
        ptr = _row("Example, Ada", "White House Office", _request("278 Transaction", "Example"))
        term = _row("Example, Ada", "White House Office", _request("Annual Term", "Example"))
        vice = _row("Example, Bea", "Office of The Vice President",
                    _request("Annual (2025)", "Example"))
        vice_lowercase = _row("Example, Cam", "Office of the Vice President",
                             _request("Annual (2025)", "Cam"))
        direct = _row("Example, Ada", "White House Office",
                      "<a href='https://extapps2.oge.gov/a.pdf'>Annual (2026)</a>")
        other = _row("Example, Ada", "Department of State", _request("Annual (2026)", "Example"))
        with tempfile.TemporaryDirectory() as folder:
            metadata = self._archive(folder, [annual, annual.copy(), ptr, ptr.copy(),
                                              term, vice, vice_lowercase, direct, other])
            result = build_request_plan(Path(folder), metadata)
        self.assertEqual(result["scope_catalog_rows"], 8)
        self.assertEqual(result["request_catalog_rows"], 7)
        self.assertEqual(result["request_intent_count"], 5)
        annual_request = next(item for item in result["requests"]
                              if item["document_type"] == "annual_278e" and
                              item["filer_name"] == "Example, Ada")
        self.assertEqual(annual_request["catalog_occurrence_count"], 2)
        self.assertEqual([item["catalog_index"] for item in
                          annual_request["catalog_occurrences"]], [0, 1])
        self.assertEqual(annual_request["filing_year_from_catalog_label"], 2026)
        self.assertIsNone(annual_request["distinct_report_count"])
        self.assertEqual(annual_request["status"], "not_submitted")
        self.assertNotIn("filed_at", annual_request)
        self.assertTrue(all(item["source_page_sha256"] for item in
                            annual_request["catalog_occurrences"]))

    def test_tampered_page_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            metadata = self._archive(folder, [_row("Example, Ada", "White House Office",
                                                  _request("Annual (2026)", "Example"))])
            (Path(folder) / metadata["pages"][0]["archive_path"]).write_bytes(b"{}")
            with self.assertRaises(OgeWhiteHouseRequestError):
                build_request_plan(Path(folder), metadata)

    def test_request_link_must_be_official(self):
        bad = _request("278 Transaction", "Example").replace("extapps2.oge.gov", "evil.example")
        with tempfile.TemporaryDirectory() as folder:
            metadata = self._archive(folder, [_row("Example, Ada", "White House Office", bad)])
            with self.assertRaises(OgeWhiteHouseRequestError):
                build_request_plan(Path(folder), metadata)

    def test_page_length_must_match_requested_coverage_even_on_final_page(self):
        with tempfile.TemporaryDirectory() as folder:
            metadata = self._archive(folder, [
                _row("Example, Ada", "White House Office", _request("Annual (2026)", "Ada")),
                _row("Example, Bea", "White House Office", _request("Annual (2026)", "Bea")),
            ])
            metadata["pages"][0]["length"] = 1
            with self.assertRaises(OgeWhiteHouseRequestError):
                build_request_plan(Path(folder), metadata)


if __name__ == "__main__":
    unittest.main()
