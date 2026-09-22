import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from unison_snapshot.oge import OgeCatalogError
spec = importlib.util.spec_from_file_location(
    "whitehouse_extract_script", ROOT / "scripts/whitehouse_extract.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


class WhiteHouseExtractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = self.root / "evidence"
        self.review = self.root / "review"

    def archive(self, suffix: str, kind: str = "278t") -> dict:
        document_id = f"wh-url:{suffix * 24}"
        content = b"%PDF-1.4\nfixture\n%%EOF"
        sha = hashlib.sha256(content).hexdigest()
        folder = self.evidence / "whitehouse/disclosures/reports" / document_id[7:]
        folder.mkdir(parents=True)
        (folder / f"{sha}.pdf").write_bytes(content)
        row = {"schema_version": script.METADATA_SCHEMA,
               "source_id": "whitehouse_public_disclosures", "document_id": document_id,
               "document_url": f"https://www.whitehouse.gov/wp-content/uploads/2026/09/{suffix}.pdf",
               "sha256": sha, "byte_length": len(content),
               "archive_path": (folder / f"{sha}.pdf").relative_to(self.evidence).as_posix(),
               "filer_name_from_label": "Example, Ada", "document_type_from_label": kind,
               "link_label": "Example, Ada Periodic Transaction Report"}
        (folder / f"{sha}.json").write_text(json.dumps(row), encoding="utf-8")
        return row

    def test_hash_bound_review_extraction_and_incremental_skip(self):
        row = self.archive("a")
        def parse(_, *, source_url, source_sha256, **_kwargs):
            return {"source_url": source_url, "source_sha256": source_sha256,
                    "parser_version": script.TRADE_PARSER_VERSION,
                    "document_reasons": [], "transactions": []}
        with patch.object(script, "parse_whitehouse_278t_pdf", side_effect=parse) as parser:
            first = script.extract_batch(self.evidence, self.review, limit=1)
            second = script.extract_batch(self.evidence, self.review, limit=1)
        self.assertEqual((first["extraction_created_count"], first["pending_count"]), (1, 0))
        self.assertEqual((second["attempted_count"], second["existing_extraction_count"]), (0, 1))
        self.assertEqual(parser.call_count, 1)
        target = (self.review / "whitehouse/extractions" / row["document_id"][7:] /
                  row["sha256"] / f"{script.TRADE_PARSER_VERSION.replace('/', '-')}.json")
        self.assertTrue(target.is_file())
        pdf = (self.evidence / "whitehouse/disclosures/reports" /
               row["document_id"][7:] / f"{row['sha256']}.pdf")
        pdf.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            script.extract_batch(self.evidence, self.review, limit=1)

    def test_failed_report_does_not_starve_later_document(self):
        first = self.archive("a")
        second = self.archive("b")
        def parse(_, *, source_url, source_sha256, **_kwargs):
            if source_url == first["document_url"]:
                raise OgeCatalogError("unsupported PDF")
            return {"source_url": source_url, "source_sha256": source_sha256,
                    "parser_version": script.TRADE_PARSER_VERSION}
        with patch.object(script, "parse_whitehouse_278t_pdf", side_effect=parse):
            one = script.extract_batch(self.evidence, self.review, limit=1)
            two = script.extract_batch(self.evidence, self.review, limit=1,
                                       start_after_id=one["last_attempted_id"])
        self.assertEqual(one["failure_count"], 1)
        self.assertEqual(one["pending_count"], 1)
        self.assertEqual(one["recorded_failure_count"], 1)
        self.assertEqual(two["last_attempted_id"], second["document_id"])
        self.assertEqual(two["extraction_created_count"], 1)
        with patch.object(script, "parse_whitehouse_278t_pdf", side_effect=AssertionError(
                "quarantined source must not be parsed again")):
            settled = script.extract_batch(self.evidence, self.review, limit=1)
        self.assertEqual(settled["attempted_count"], 0)
        self.assertEqual(settled["existing_failure_count"], 1)
        self.assertEqual(settled["pending_count"], 0)


if __name__ == "__main__":
    unittest.main()
