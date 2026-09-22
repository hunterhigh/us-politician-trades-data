import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import unison_snapshot.whitehouse_disclosures as disclosures
from unison_snapshot.whitehouse_disclosure_audit import build_oge_public_crosswalk
from unison_snapshot.whitehouse_disclosures import (
    WhiteHouseDisclosureError, archive_public_batch, archive_public_index,
    parse_disclosure_index,
    read_archived_pdf,
)


BASE = "https://www.whitehouse.gov/wp-content/uploads/2026/09/"
ANNUAL = BASE + "Wiles-Susie-2026-Annual.pdf"
BARE = BASE + "Wiles-Susie.pdf"
PTR = BASE + "Wiles-Susie-278T.pdf"
BAD = BASE + "Wiles-Susie-�C-278T.pdf"


def page(*, include_bad=True):
    bad = f'<a href="{BAD}">Wiles, Susie - Periodic Transaction Report 09.03.26</a>' if include_bad else ""
    return ("<html><details><summary>Waivers</summary><a href='https://example.org/a.pdf'>skip</a></details>"
            "<details><summary>Financial Disclosure Reports</summary>"
            f'<a href="{ANNUAL}">Wiles, Susie (2026 Annual)</a>'
            f'<a href="{BARE}">Wiles, Susie</a></details>'
            "<details><summary>Transaction Reports</summary>"
            f'<a href="{PTR}">Wiles, Susie - Periodic Transaction Report 08.12.26</a>'
            f"{bad}</details></html>").encode()


class Client:
    def __init__(self, html=None):
        self.html = html or page()
        self.pdf = b"%PDF-1.7\nfirst\n%%EOF"
        self.calls = []
        self.fail_url = None

    def fetch_index(self):
        return self.html, {"content-type": "text/html"}

    def download_pdf(self, url):
        self.calls.append(url)
        if url == self.fail_url:
            raise WhiteHouseDisclosureError("HTTP 404")
        return self.pdf, {"content-type": "application/pdf"}


class PublicDisclosureTests(unittest.TestCase):
    def test_index_keeps_bare_name_unclassified_and_quarantines_malformed_href(self):
        result = parse_disclosure_index(page())
        self.assertEqual(result["report_link_count"], 3)
        self.assertEqual(result["quarantine_count"], 1)
        kinds = {row["document_url"]: row["document_type_from_label"]
                 for row in result["reports"]}
        self.assertEqual(kinds[ANNUAL], "278e_annual")
        self.assertEqual(kinds[BARE], "278e_unspecified")
        self.assertEqual(kinds[PTR], "278t")
        self.assertEqual(result["quarantine"][0]["url"], BAD)
        self.assertEqual(parse_disclosure_index(page())["reports"], result["reports"])

    def test_official_allowlist_and_required_sections_fail_closed(self):
        with self.assertRaises(WhiteHouseDisclosureError):
            parse_disclosure_index(page(include_bad=False).replace(b"www.whitehouse.gov", b"evil.example"))
        with self.assertRaises(WhiteHouseDisclosureError):
            parse_disclosure_index(b"<html>changed layout</html>")

    def test_page_and_pdfs_are_content_addressed_and_incremental(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = Client()
            index = archive_public_index(root, client=client,
                                         retrieved_at="2026-09-22T00:00:00Z")
            again = archive_public_index(root, client=client,
                                         retrieved_at="2026-09-23T00:00:00Z")
            self.assertEqual(index["page_archive"], again["page_archive"])
            first = archive_public_batch(root, index, limit=3, client=client,
                                         retrieved_at="2026-09-22T00:00:00Z")
            self.assertEqual(first["pending_url_count"], 0)
            self.assertEqual(first["attempted_count"], 3)
            self.assertEqual(len(client.calls), 3)
            second = archive_public_batch(root, index, limit=3, client=client)
            self.assertEqual(second["attempted_count"], 0)
            self.assertEqual(len(client.calls), 3)
            client.pdf = b"%PDF-1.7\nrevised\n%%EOF"
            revised = archive_public_batch(root, index, limit=1, client=client,
                                           refresh_existing=True)
            self.assertEqual(revised["archived_version_count"], 4)
            self.assertEqual(revised["attempted_count"], 1)
            metadata = revised["reports"][0]
            self.assertEqual(metadata["source_id"], "whitehouse_public_disclosures")
            self.assertEqual(metadata["sha256"], hashlib.sha256(
                b"%PDF-1.7\nfirst\n%%EOF").hexdigest())

    def test_404_is_reported_without_blocking_other_pdfs_and_cursor_rotates(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = Client()
            client.fail_url = ANNUAL
            index = parse_disclosure_index(page())
            first = archive_public_batch(Path(temporary), index, limit=1, client=client)
            self.assertEqual(len(first["failures"]), 1)
            second = archive_public_batch(Path(temporary), index, limit=2,
                                          start_after_id=first["last_attempted_id"],
                                          client=client)
            self.assertEqual(second["attempted_count"], 2)
            self.assertEqual(second["pending_url_count"], 1)
            self.assertEqual(len(second["failures"]), 0)

    def test_large_original_is_split_without_changing_its_pdf_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = Client()
            client.pdf = b"%PDF-1.7\n" + b"x" * 90 + b"\n%%EOF"
            index = parse_disclosure_index(page())
            index["reports"] = index["reports"][:1]
            index["report_link_count"] = 1
            with patch.object(disclosures, "MAX_SINGLE_FILE_BYTES", 40), \
                    patch.object(disclosures, "PDF_CHUNK_BYTES", 20):
                batch = archive_public_batch(root, index, limit=1, client=client)
                self.assertEqual(batch["pending_url_count"], 0)
                metadata = batch["reports"][0]
                self.assertEqual(metadata["schema_version"],
                                 disclosures.PDF_CHUNK_ARCHIVE_SCHEMA)
                self.assertEqual(len(metadata["chunks"]), 6)
                self.assertEqual(read_archived_pdf(root, metadata), client.pdf)
                again = archive_public_batch(root, index, limit=1, client=client)
                self.assertEqual(again["attempted_count"], 0)
                chunk = root / metadata["chunks"][0]["archive_path"]
                chunk.write_bytes(b"corrupt")
                with self.assertRaisesRegex(WhiteHouseDisclosureError, "chunk hash"):
                    read_archived_pdf(root, metadata)

    def test_catalog_crosswalk_is_candidate_only_and_checks_page_hash(self):
        index = parse_disclosure_index(page())
        request_url = ("https://extapps2.oge.gov/201/Presiden.nsf/201%20Request?"
                       "OpenForm&Filer=Wiles")
        types = ["Annual (2026)", "New Entrant", "278 Transaction"]
        rows = [{"type": f"{kind} (<a href='{request_url}'>Request this Document</a>)",
                 "name": "Wiles, Susie", "agency": "White House Office",
                 "title": "Chief of Staff", "level": "I",
                 "docDate": "2026-09-20T01:00:00", "amended": ""} for kind in types]
        raw = json.dumps({"draw": 1, "recordsTotal": 3, "recordsFiltered": 3,
                          "data": rows}).encode()
        sha = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "oge/catalog/pages" / f"{sha}.json"
            path.parent.mkdir(parents=True)
            path.write_bytes(raw)
            manifest = {"schema_version": "oge-catalog-archive/v1",
                        "source_id": "oge", "sha256": "a" * 64,
                        "record_count": 3, "pages": [{"start": 0, "length": 3,
                        "byte_length": len(raw), "sha256": sha,
                        "archive_path": path.relative_to(root).as_posix()}]}
            audit = build_oge_public_crosswalk(root, manifest, index)
            self.assertEqual(audit["request_catalog_occurrence_count"], 3)
            self.assertEqual(audit["counts_by_status"]["unverified_candidate"], 2)
            self.assertEqual(audit["counts_by_status"]["unverified_type_unknown"], 1)
            self.assertTrue(all(not row["same_document_verified"] for row in audit["rows"]))
            path.write_bytes(raw + b"x")
            with self.assertRaises(WhiteHouseDisclosureError):
                build_oge_public_crosswalk(root, manifest, index)


if __name__ == "__main__":
    unittest.main()
