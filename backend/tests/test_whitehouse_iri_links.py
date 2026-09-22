import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from unison_snapshot.whitehouse_disclosures import (
    WhiteHouseDisclosureError, parse_disclosure_index,
)
from unison_snapshot.whitehouse_iri_links import (
    recover_official_iri_links, verify_recovered_pdfs,
)


PATH = "/wp-content/uploads/2025/11/Example-Ada-Periodic-Transaction-Report\u2013-06.13.25.pdf"
SOURCE_HREF = "https://www.whitehouse.gov" + PATH
ENCODED_URL = SOURCE_HREF.replace("\u2013", "%E2%80%93")


def index(href=SOURCE_HREF):
    html = ("<details><summary>Financial Disclosure Reports</summary>"
            '<a href="https://www.whitehouse.gov/wp-content/uploads/2026/09/Example-Ada.pdf">'
            "Example, Ada</a></details>"
            "<details><summary>Transaction Reports</summary>"
            f'<a href="{href}">Example, Ada - Periodic Transaction Report 06.13.25</a>'
            "</details>").encode("utf-8")
    return parse_disclosure_index(html)


class FakeClient:
    def __init__(self):
        self.urls = []

    def download_pdf(self, url):
        self.urls.append(url)
        return b"%PDF-1.7\nexample\n%%EOF", {"content-type": "application/pdf"}


class UnicodeLinkTests(unittest.TestCase):
    def test_exact_iri_encoding_recovers_official_link_without_guessing(self):
        original = index()
        self.assertEqual(original["quarantine_count"], 1)
        expanded, audit = recover_official_iri_links(original)
        self.assertEqual(original["quarantine_count"], 1)
        self.assertEqual(expanded["quarantine_count"], 0)
        self.assertEqual(expanded["report_link_count"], 2)
        report = next(row for row in expanded["reports"]
                      if row["document_type_from_label"] == "278t")
        self.assertEqual(report["document_url"], ENCODED_URL)
        self.assertEqual(report["source_href"], SOURCE_HREF)
        self.assertEqual(report["classification_status"], "official_iri_encoded_pdf_pending")
        self.assertEqual(audit["source_page_sha256"], original["page_sha256"])
        self.assertEqual(audit["rows"][0]["status"], "pending_pdf_download")
        client = FakeClient()
        verified = verify_recovered_pdfs(audit, client=client)
        self.assertEqual(client.urls, [ENCODED_URL])
        self.assertEqual(verified["official_pdf_verified_count"], 1)
        self.assertEqual(verified["rows"][0]["pdf_sha256"], hashlib.sha256(
            b"%PDF-1.7\nexample\n%%EOF").hexdigest())

    def test_replacement_character_and_nonofficial_hosts_remain_quarantined(self):
        for href in (SOURCE_HREF.replace("\u2013", "\ufffd"),
                     SOURCE_HREF.replace("www.whitehouse.gov", "evil.example"),
                     SOURCE_HREF + "?download=1"):
            with self.subTest(href=href):
                expanded, audit = recover_official_iri_links(index(href))
                self.assertEqual(expanded["quarantine_count"], 1)
                self.assertEqual(audit["recovered_url_count"], 0)

    def test_invalid_audit_fails_closed(self):
        with self.assertRaises(WhiteHouseDisclosureError):
            verify_recovered_pdfs({"schema_version": "wrong"})

    def test_cli_writes_recovered_index_and_audit_without_network(self):
        backend = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "index.json"
            expanded = root / "expanded.json"
            audit_path = root / "audit.json"
            source.write_text(json.dumps(index(), ensure_ascii=False), encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=str(backend / "src"))
            completed = subprocess.run(
                [sys.executable, str(backend / "scripts/whitehouse_iri_links.py"),
                 "--index", str(source), "--index-out", str(expanded),
                 "--audit-out", str(audit_path)],
                env=env, cwd=backend, capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(expanded.read_text(encoding="utf-8"))
                             ["report_link_count"], 2)
            self.assertEqual(json.loads(audit_path.read_text(encoding="utf-8"))
                             ["recovered_url_count"], 1)


if __name__ == "__main__":
    unittest.main()
