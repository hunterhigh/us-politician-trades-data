import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_whitehouse import (
    archive_direct_annual_batch, build_whitehouse_coverage, coverage_from_archive,
)


DIRECT = ("https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/"
          "69AEAA9D7455ACD585258E27002DDEE1/$FILE/Donald-J-Trump-2026-278ANNUAL.pdf")
REQUEST = ("https://extapps2.oge.gov/201/Presiden.nsf/201%20Request?"
           "OpenForm&Filer=Example")


def row(name, agency, markup):
    return {"type": markup, "name": name, "agency": agency, "title": "President",
            "level": "I", "docDate": "2026-07-01T04:21:01", "amended": ""}


def page(rows):
    return {"draw": 1, "recordsTotal": len(rows), "recordsFiltered": len(rows),
            "data": rows}


class FakePdfClient:
    def __init__(self):
        self.urls = []

    def download(self, url):
        self.urls.append(url)
        return b"%PDF-1.7\nexample\n%%EOF", {"content-type": "application/pdf"}


class WhiteHouseCoverageTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            row("Trump, Donald J", "White House Office",
                f"<a href='{DIRECT}'>Annual (2026)</a>"),
            row("Example, Jane", "Office of the Vice President",
                f"Annual (2026) (<a href='{REQUEST}'>Request this Document</a>)"),
            row("Other, John", "Other Agency",
                f"<a href='{DIRECT}'>Annual (2026)</a>"),
        ]

    def test_direct_and_request_remain_separate(self):
        result = build_whitehouse_coverage([(0, page(self.rows))])
        self.assertEqual(result["counts"]["278e_annual"],
                         {"direct_pdf": 1, "request_required": 1})
        self.assertEqual(len(result["reports"]), 2)
        self.assertEqual(result["reports"][0]["source_document_id"],
                         "69aeaa9d7455acd585258e27002ddee1")
        self.assertIsNone(result["reports"][1]["source_document_id"])

    def test_missing_page_fails_closed(self):
        with self.assertRaises(OgeCatalogError):
            build_whitehouse_coverage([(1, page(self.rows))])

    def test_archived_page_hash_is_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = json.dumps(page(self.rows)).encode()
            sha = hashlib.sha256(content).hexdigest()
            source = root / "oge/catalog/pages" / f"{sha}.json"
            source.parent.mkdir(parents=True)
            source.write_bytes(content)
            metadata = root / "oge/catalog/catalog.json"
            metadata.write_text(json.dumps({
                "schema_version": "oge-catalog-archive/v1", "sha256": "cataloghash",
                "record_count": 3, "retrieved_at": "2026-09-22T00:00:00Z", "pages": [{"start": 0, "archive_path":
                    source.relative_to(root).as_posix(), "sha256": sha,
                    "byte_length": len(content)}],
            }))
            self.assertEqual(coverage_from_archive(root, metadata)["counts"]
                             ["278e_annual"]["direct_pdf"], 1)
            source.write_bytes(content + b"x")
            with self.assertRaises(OgeCatalogError):
                coverage_from_archive(root, metadata)

    def test_archive_only_direct_pdf(self):
        coverage = build_whitehouse_coverage([(0, page(self.rows))])
        coverage["catalog_sha256"] = "cataloghash"
        client = FakePdfClient()
        with tempfile.TemporaryDirectory() as temporary:
            result = archive_direct_annual_batch(Path(temporary), coverage, limit=1, client=client)
            self.assertEqual(result["archived_count"], 1)
            self.assertEqual(result["request_required_count"], 1)
            self.assertEqual(client.urls, [DIRECT])
            again = archive_direct_annual_batch(Path(temporary), coverage, limit=0, client=client)
            self.assertEqual(again["pending_count"], 0)

    def test_workflow_keeps_originals_and_coverage_on_separate_branches(self):
        path = Path(__file__).resolve().parents[2] / ".github/workflows/oge-whitehouse.yml"
        workflow = path.read_text(encoding="utf-8")
        self.assertIn("environment: production", workflow)
        self.assertIn('test "$OGE_COLLECTION_ENABLED" = true', workflow)
        self.assertIn('test "$OGE_TERMS_ACKNOWLEDGED" = true', workflow)
        self.assertIn('git -C "$EVIDENCE_ROOT" add oge/annual/reports', workflow)
        self.assertIn('git -C "$REVIEW_ROOT" add oge/whitehouse', workflow)
        self.assertNotIn("201 Request", workflow)


if __name__ == "__main__":
    unittest.main()
