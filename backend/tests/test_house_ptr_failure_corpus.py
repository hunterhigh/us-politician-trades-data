import json
from pathlib import Path
import re
import unittest


class HousePtrFailureCorpusTests(unittest.TestCase):
    def test_fixed_failure_sample_is_complete_and_never_silently_promoted(self):
        manifest_path = Path(__file__).resolve().parent / "fixtures" / "house_ptr_failures_2026" / "corpus.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows = manifest["documents"]
        expected = {
            "8221321", "9115704", "9115711", "9115808", "9115809", "9115811",
            "9115814", "9115816", "9115901", "9116197", "9116212", "9116217",
            "9116249", "9116256", "9116257", "9116260", "9116292", "9116308",
            "9116326", "9116331",
        }
        self.assertEqual(manifest["schema_version"], "house-ptr-failure-corpus-audit/v1")
        self.assertEqual({row["document_id"] for row in rows}, expected)
        self.assertEqual(len(rows), len(expected))
        self.assertEqual(manifest["ocr_engine_available"], False)
        for row in rows:
            with self.subTest(document_id=row["document_id"]):
                self.assertRegex(row["source_sha256"], re.compile(r"^[0-9a-f]{64}$"))
                self.assertEqual(row["disposition"], "quarantined_unreplayed")
                self.assertEqual(row["row_disposition"], "not_observed_ocr_unavailable")
                self.assertEqual(row["text_layer_pages"], 0)

        self.assertEqual(sum(row["pages"] for row in rows), 32)
        self.assertEqual(sum(row["previous_failure"] == "House legacy PTR contains no recognized transaction rows"
                             for row in rows), 18)
        self.assertEqual(sum(row["previous_failure"] == "House PTR header does not match its archived identity"
                             for row in rows), 2)


if __name__ == "__main__":
    unittest.main()
