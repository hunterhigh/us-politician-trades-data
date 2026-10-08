import hashlib
import json
from pathlib import Path
import unittest

from unison_snapshot.pipeline_qa.holdings_rebinding import audit_holdings_rebinding
from unison_snapshot.pipeline_qa.migration_inventory import InventoryError


OLD = "a" * 40
NEW = "b" * 40
EVIDENCE = "c" * 40


class HoldingsRebindingTests(unittest.TestCase):
    def _fixture(self, *, corrupt_pdf=False, bad_index=False):
        objects = {}
        directories = {}
        for commit, suffix in ((OLD, ""), (NEW, "-1")):
            url = f"https://www.whitehouse.gov/Underwood-Emily-2026-Annual{suffix}.pdf"
            row = {"id": f"holding{suffix}", "source_id": "oge", "person_id": "p1",
                   "filing_id": f"f{suffix}", "source_url": url, "asset_name": "A"}
            board = {"reported_holdings": [row]}
            raw = json.dumps(board).encode()
            digest = hashlib.sha256(raw).hexdigest()
            objects[(commit, "manifest.json")] = json.dumps({"board": digest}).encode()
            objects[(commit, f"board/{digest}.json")] = raw
            document_id = hashlib.sha256(url.encode()).hexdigest()[:24]
            pdf = b"old" if not suffix else b"new"
            pdf_sha = hashlib.sha256(pdf).hexdigest()
            directory = f"whitehouse/disclosures/reports/{document_id}"
            pdf_path = f"{directory}/{pdf_sha}.pdf"
            meta_path = f"{directory}/{pdf_sha}.json"
            old_name = "Underwood-Emily-2026-Annual.pdf"
            new_name = "Underwood-Emily-2026-Annual-1.pdf"
            html = (new_name if suffix else old_name)
            if suffix and bad_index:
                html += old_name
            page_raw = html.encode()
            page_sha = hashlib.sha256(page_raw).hexdigest()
            page_path = f"whitehouse/disclosures/pages/{page_sha}"
            objects[(EVIDENCE, f"{page_path}.html")] = page_raw
            objects[(EVIDENCE, f"{page_path}.json")] = json.dumps({
                "sha256": page_sha, "page_url": "https://www.whitehouse.gov/disclosures/",
                "retrieved_at": "2026-10-01T00:00:00+00:00" if suffix else
                                "2026-09-22T00:00:00+00:00"}).encode()
            metadata = {"document_url": url, "document_id": f"wh-url:{document_id}",
                        "archive_path": pdf_path, "sha256": pdf_sha,
                        "byte_length": len(pdf), "filer_name_from_label": "Underwood, Emily",
                        "report_year_from_label": 2026, "page_sha256": page_sha,
                        "headers": {}}
            objects[(EVIDENCE, meta_path)] = json.dumps(metadata).encode()
            objects[(EVIDENCE, pdf_path)] = b"bad" if suffix and corrupt_pdf else pdf
            directories[directory] = [f"{pdf_sha}.json", f"{pdf_sha}.pdf"]
        return objects, directories

    def _run(self, objects, directories):
        return audit_holdings_rebinding(
            repo=Path("."), old_main_commit=OLD, new_main_commit=NEW,
            evidence_commit=EVIDENCE,
            read_object=lambda _repo, commit, path: objects[(commit, path)],
            list_paths=lambda _repo, _commit, directory: directories[directory],
        )

    def test_official_index_replacement_binds_distinct_archived_pdfs(self):
        result = self._run(*self._fixture())
        self.assertEqual(result["paired_count"], 1)
        self.assertFalse(result["pairs"][0]["same_pdf_bytes"])
        self.assertFalse(result["pairs"][0]["revision_relation_verified"])
        self.assertTrue(result["pairs"][0]["official_index_replacement_verified"])
        self.assertTrue(result["rebinding_complete"])

    def test_index_with_both_links_does_not_bind_replacement(self):
        result = self._run(*self._fixture(bad_index=True))
        self.assertFalse(result["rebinding_complete"])

    def test_pdf_bytes_must_match_archive_metadata(self):
        with self.assertRaisesRegex(InventoryError, "archived PDF bytes"):
            self._run(*self._fixture(corrupt_pdf=True))


if __name__ == "__main__":
    unittest.main()
