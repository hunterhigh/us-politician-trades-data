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
from unison_snapshot.oge_278e_public import (
    OcrCheckpointPending, TRUMP_2025_PARSER_VERSION,
    TRUMP_2025_SOURCE_SHA256, TRUMP_2025_SOURCE_URL,
)
from unison_snapshot.whitehouse_278t import (
    TRUMP_081225_DOCUMENT_ID, TRUMP_081225_PARSER_VERSION,
    TRUMP_081225_SOURCE_SHA256, TRUMP_081225_SOURCE_URL,
)
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
        row = {"schema_version": script.PDF_ARCHIVE_SCHEMA,
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
        with self.assertRaisesRegex(ValueError, "hash differs"):
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

    def test_chunked_official_pdf_is_reassembled_only_for_parsing(self):
        row = self.archive("c")
        folder = (self.evidence / "whitehouse/disclosures/reports" /
                  row["document_id"][7:])
        pdf = folder / f"{row['sha256']}.pdf"
        content = pdf.read_bytes()
        pdf.unlink()
        row["schema_version"] = script.PDF_CHUNK_ARCHIVE_SCHEMA
        del row["archive_path"]
        row["chunks"] = []
        for number, part in enumerate((content[:10], content[10:])):
            path = folder / f"{row['sha256']}.part-{number:03d}.bin"
            path.write_bytes(part)
            row["chunks"].append({
                "archive_path": path.relative_to(self.evidence).as_posix(),
                "sha256": hashlib.sha256(part).hexdigest(), "byte_length": len(part),
            })
        (folder / f"{row['sha256']}.json").write_text(json.dumps(row), encoding="utf-8")
        def parse(path, *, source_url, source_sha256, **_kwargs):
            self.assertEqual(path.read_bytes(), content)
            return {"source_url": source_url, "source_sha256": source_sha256,
                    "parser_version": script.TRADE_PARSER_VERSION}
        with patch.object(script, "parse_whitehouse_278t_pdf", side_effect=parse):
            result = script.extract_batch(self.evidence, self.review, limit=1)
        self.assertEqual(result["extraction_created_count"], 1)
        self.assertEqual(result["failure_count"], 0)

    def test_checkpoint_progress_is_pending_not_a_parser_failure(self):
        row = self.archive("d", kind="278e_annual")
        status = {"schema_version": "whitehouse-278e-ocr-checkpoint/v1",
                  "source_sha256": row["sha256"], "completed_page_count": 50,
                  "pending_page_count": 877}
        with patch.object(script, "extract_public_278e_pdf",
                          side_effect=OgeCatalogError(
                              "White House 278e requires checkpointed OCR")), patch.object(
                          script, "extract_public_278e_pdf_checkpointed",
                          side_effect=OcrCheckpointPending(status)):
            result = script.extract_batch(self.evidence, self.review, limit=1)
        self.assertEqual(result["checkpoint_pending_count"], 1)
        self.assertEqual(result["pending_count"], 1)
        self.assertEqual(result["failure_count"], 0)
        failure = (self.review / "whitehouse/extractions" / row["document_id"][7:] /
                   row["sha256"] /
                   f"{script.ANNUAL_PARSER_VERSION.replace('/', '-')}.failure.json")
        self.assertFalse(failure.exists())

    def test_fixed_replay_selects_exact_immutable_source(self):
        selected = self.archive("e")
        other = self.archive("f")

        def parse(_, *, source_url, source_sha256, **_kwargs):
            self.assertEqual(source_url, selected["document_url"])
            self.assertEqual(source_sha256, selected["sha256"])
            return {"source_url": source_url, "source_sha256": source_sha256,
                    "parser_version": script.TRADE_PARSER_VERSION}

        with patch.object(script, "parse_whitehouse_278t_pdf", side_effect=parse) as parser:
            result = script.extract_batch(
                self.evidence, self.review, limit=1,
                document_id=selected["document_id"], source_sha256=selected["sha256"])
        self.assertEqual(parser.call_count, 1)
        self.assertEqual(result["archived_version_count"], 2)
        self.assertEqual(result["selected_version_count"], 1)
        self.assertEqual(result["fixed_document_id"], selected["document_id"])
        self.assertEqual(result["fixed_source_sha256"], selected["sha256"])
        other_target = (self.review / "whitehouse/extractions" / other["document_id"][7:] /
                        other["sha256"] /
                        f"{script.TRADE_PARSER_VERSION.replace('/', '-')}.json")
        self.assertFalse(other_target.exists())

    def test_fixed_replay_rejects_partial_or_mismatched_binding(self):
        row = self.archive("e")
        with self.assertRaisesRegex(ValueError, "requires a valid document id"):
            script.extract_batch(self.evidence, self.review, limit=1,
                                 document_id=row["document_id"])
        with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
            script.extract_batch(self.evidence, self.review, limit=1,
                                 document_id=row["document_id"], source_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "does not accept a queue cursor"):
            script.extract_batch(self.evidence, self.review, limit=1,
                                 document_id=row["document_id"],
                                 source_sha256=row["sha256"],
                                 start_after_id=row["document_id"])

    def test_fixed_trump_replay_uses_v6_target_and_read_only_v5_checkpoints(self):
        document_id = "wh-url:0c14d3849ca60768024e470b"
        metadata = {
            "document_id": document_id,
            "document_url": TRUMP_2025_SOURCE_URL,
            "sha256": TRUMP_2025_SOURCE_SHA256,
            "filer_name_from_label": "Trump, Donald J.",
            "document_type_from_label": "278e_annual",
        }
        pdf = self.root / "trump.pdf"
        pdf.write_bytes(b"fixture")
        with patch.object(script, "_archive_rows", return_value=[(metadata, pdf)]), patch.object(
                script, "extract_public_278e_pdf",
                side_effect=OgeCatalogError(
                    "White House 278e requires checkpointed OCR")), patch.object(
                script, "extract_public_278e_pdf_checkpointed",
                return_value={"source_url": TRUMP_2025_SOURCE_URL,
                              "source_sha256": TRUMP_2025_SOURCE_SHA256,
                              "parser_version": TRUMP_2025_PARSER_VERSION}) as checkpointed:
            result = script.extract_batch(
                self.evidence, self.review, limit=1, document_id=document_id,
                source_sha256=TRUMP_2025_SOURCE_SHA256)
        self.assertEqual(result["extraction_created_count"], 1)
        call = checkpointed.call_args.kwargs
        v5 = script.ANNUAL_PARSER_VERSION.replace("/", "-")
        v6 = TRUMP_2025_PARSER_VERSION.replace("/", "-")
        self.assertEqual(call["legacy_checkpoint_root"].name, v5)
        self.assertEqual(call["checkpoint_root"].name, v6)
        self.assertNotEqual(call["legacy_checkpoint_root"], call["checkpoint_root"])
        target = (self.review / "whitehouse/extractions" / document_id[7:] /
                  TRUMP_2025_SOURCE_SHA256 / f"{v6}.json")
        self.assertTrue(target.is_file())

    def test_fixed_trump_278t_selects_only_exact_source_bound_v3(self):
        metadata = {
            "document_id": TRUMP_081225_DOCUMENT_ID,
            "document_url": TRUMP_081225_SOURCE_URL,
            "sha256": TRUMP_081225_SOURCE_SHA256,
            "filer_name_from_label": "President Donald J. Trump",
            "document_type_from_label": "278t",
            "link_label": "President Donald J. Trump Periodic Transaction Report 08.12.25 (1)",
        }
        pdf = self.root / "trump-278t.pdf"
        pdf.write_bytes(b"fixture")
        with patch.object(script, "_archive_rows", return_value=[(metadata, pdf)]), patch.object(
                script, "parse_whitehouse_278t_pdf", return_value={
                    "source_url": TRUMP_081225_SOURCE_URL,
                    "source_sha256": TRUMP_081225_SOURCE_SHA256,
                    "parser_version": TRUMP_081225_PARSER_VERSION,
                }) as parser:
            result = script.extract_batch(
                self.evidence, self.review, limit=1,
                document_id=TRUMP_081225_DOCUMENT_ID,
                source_sha256=TRUMP_081225_SOURCE_SHA256)
        self.assertEqual(result["extraction_created_count"], 1)
        self.assertEqual(parser.call_count, 1)
        target = (self.review / "whitehouse/extractions" /
                  TRUMP_081225_DOCUMENT_ID.split(":", 1)[1] /
                  TRUMP_081225_SOURCE_SHA256 /
                  f"{TRUMP_081225_PARSER_VERSION.replace('/', '-')}.json")
        self.assertTrue(target.is_file())
        generic = self.archive("g")
        self.assertEqual(script.trade_parser_version_for_source(
            generic["document_url"], generic["sha256"]), script.TRADE_PARSER_VERSION)


if __name__ == "__main__":
    unittest.main()
