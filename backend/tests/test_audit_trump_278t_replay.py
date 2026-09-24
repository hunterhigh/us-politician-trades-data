import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location(
    "audit_trump_278t_replay", ROOT / "scripts/audit_trump_278t_replay.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


def extraction():
    rows = [{"row_number": number, "extraction_id": f"row:{number}"}
            for number in range(1, 508)]
    rows[-1]["reasons"] = ["asset_name_missing"]
    return {
        "schema_version": script.EXTRACTION_SCHEMA,
        "parser_version": script.TRUMP_081225_PARSER_VERSION,
        "document_id": script.TRUMP_081225_DOCUMENT_ID,
        "source_url": script.TRUMP_081225_SOURCE_URL,
        "source_sha256": script.TRUMP_081225_SOURCE_SHA256,
        "filed_at": "2025-08-12", "evidence_complete": True,
        "document_reasons": [], "source_row_count": 507,
        "transactions": rows[:-1], "quarantined": rows[-1:],
    }


def status():
    return {
        "schema_version": "whitehouse-public-extraction-status/v1",
        "fixed_document_id": script.TRUMP_081225_DOCUMENT_ID,
        "fixed_source_sha256": script.TRUMP_081225_SOURCE_SHA256,
        "selected_version_count": 1, "failure_count": 0,
        "recorded_failure_count": 0, "checkpoint_pending_count": 0,
        "pending_count": 0, "extraction_created_count": 1,
        "existing_extraction_count": 0,
    }


class Trump278TReplayAuditTests(unittest.TestCase):
    def test_allows_explicit_row_quarantine_and_conserves_source(self):
        result = script.audit_replay(extraction(), status())
        self.assertEqual(result["transaction_count"], 506)
        self.assertEqual(result["quarantined_row_count"], 1)
        self.assertTrue(result["row_conservation_complete"])

    def test_missing_or_duplicate_rows_fail(self):
        value = extraction()
        value["transactions"][0]["row_number"] = 2
        with self.assertRaisesRegex(ValueError, "identities"):
            script.audit_replay(value, status())

    def test_row_level_problem_must_have_a_reason(self):
        value = extraction()
        value["quarantined"][0]["reasons"] = []
        with self.assertRaisesRegex(ValueError, "explicit reasons"):
            script.audit_replay(value, status())


if __name__ == "__main__":
    unittest.main()
