import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

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

    def test_sync_cli_uses_one_expanded_index_for_batch_and_review(self):
        backend = Path(__file__).resolve().parents[1]
        script = backend / "scripts/whitehouse_disclosures.py"
        spec = importlib.util.spec_from_file_location("whitehouse_sync_cli_test", script)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        seen = []

        def fake_batch(root, expanded, **kwargs):
            seen.append(expanded)
            return {"schema_version": "whitehouse-public-disclosures-batch/v1",
                    "indexed_count": len(expanded["reports"]), "attempted_count": 1,
                    "pending_url_count": 1, "failures": [],
                    "last_attempted_id": None, "reports": [],
                    "index_quarantine": expanded["quarantine"]}

        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(module, "archive_public_index", return_value=index()), \
                patch.object(module, "archive_public_batch", side_effect=fake_batch), \
                redirect_stdout(io.StringIO()):
            root = Path(temporary)
            args = ["sync", "--evidence-root", str(root / "evidence"),
                    "--index-out", str(root / "index.json"),
                    "--iri-audit-out", str(root / "iri-audit.json"),
                    "--batch-out", str(root / "batch.json"), "--limit", "1"]
            self.assertEqual(module.main(args), 0)
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0]["report_link_count"], 2)
            self.assertEqual(json.loads((root / "index.json").read_text(encoding="utf-8"))
                             ["report_link_count"], 2)
            self.assertEqual(json.loads((root / "batch.json").read_text(encoding="utf-8"))
                             ["indexed_count"], 2)
            self.assertEqual(json.loads((root / "iri-audit.json").read_text(encoding="utf-8"))
                             ["recovered_url_count"], 1)

    def test_workflow_writes_iri_audit_to_review_in_same_sync(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / ".github/workflows/whitehouse-public-disclosures.yml")
        content = workflow.read_text(encoding="utf-8")
        self.assertIn("python backend/scripts/whitehouse_disclosures.py sync", content)
        self.assertIn('--iri-audit-out "$REVIEW_ROOT/whitehouse/disclosures/iri-link-audit-current.json"',
                      content)
        self.assertIn('--batch-out "$REVIEW_ROOT/whitehouse/disclosures/batch-current.json"',
                      content)
        self.assertIn('git -C "$REVIEW_ROOT" add whitehouse', content)


if __name__ == "__main__":
    unittest.main()
