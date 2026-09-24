import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location(
    "audit_trump_ocr_checkpoints",
    ROOT / "scripts/audit_trump_ocr_checkpoints.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


class TrumpOcrCheckpointAuditTests(unittest.TestCase):
    def test_rejects_missing_shards(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "Expected 38"):
                script.audit_checkpoints(Path(folder))

    def test_rejects_binding_before_accepting_digest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for start in range(1, 928, 25):
                end = min(start + 24, 927)
                value = {
                    "schema_version": script.OCR_SHARD_SCHEMA,
                    "parser_version": script.PARSER_VERSION,
                    "source_url": script.TRUMP_2025_SOURCE_URL,
                    "source_sha256": script.TRUMP_2025_SOURCE_SHA256,
                    "ocr_engine": script.TRUMP_2025_LEGACY_OCR_ENGINE,
                    "page_start": start,
                    "page_end": end,
                    "pages": [{"page_number": page, "width": 1, "height": 1,
                               "words": []} for page in range(start, end + 1)],
                }
                (root / f"pages-{start:04d}-{end:04d}.json").write_text(
                    json.dumps(value), encoding="utf-8")
            first = root / "pages-0001-0025.json"
            value = json.loads(first.read_text(encoding="utf-8"))
            value["source_url"] = "https://example.invalid/source.pdf"
            first.write_text(json.dumps(value), encoding="utf-8")
            with patch.object(script, "EXPECTED_AUDIT_SHA256", "0" * 64), \
                    self.assertRaisesRegex(ValueError, "binding changed"):
                script.audit_checkpoints(root)


if __name__ == "__main__":
    unittest.main()
