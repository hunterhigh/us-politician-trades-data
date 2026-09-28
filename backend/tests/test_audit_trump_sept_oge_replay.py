from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from audit_trump_sept_oge_replay import audit
from unison_snapshot.oge_reports import (
    PARSER_VERSION, TRUMP_SEPT_2026_DOCUMENT_ID,
    TRUMP_SEPT_2026_FILING_DATE_EVIDENCE, TRUMP_SEPT_2026_PARSER_VERSION,
    TRUMP_SEPT_2026_SOURCE_SHA256, TRUMP_SEPT_2026_SOURCE_URL,
)


class TrumpSeptemberReplayAuditTests(unittest.TestCase):
    def test_replay_must_leave_all_rows_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory = (root / "oge/extractions" / TRUMP_SEPT_2026_DOCUMENT_ID /
                         TRUMP_SEPT_2026_SOURCE_SHA256)
            directory.mkdir(parents=True)
            old = {
                "document_id": TRUMP_SEPT_2026_DOCUMENT_ID,
                "source_url": TRUMP_SEPT_2026_SOURCE_URL,
                "source_sha256": TRUMP_SEPT_2026_SOURCE_SHA256,
                "parser_version": PARSER_VERSION,
                "filed_at": None,
                "document_reasons": ["filer_signature_date_not_unique"],
                "evidence_complete": False,
                "transactions": [{"extraction_id": str(number)} for number in range(228)],
                "quarantined": [{"extraction_id": str(number)} for number in range(925)],
            }
            new = deepcopy(old)
            new.update(parser_version=TRUMP_SEPT_2026_PARSER_VERSION,
                       filed_at="2026-09-08", document_reasons=[],
                       evidence_complete=True,
                       filing_date_evidence=TRUMP_SEPT_2026_FILING_DATE_EVIDENCE)
            (directory / "oge-278t-pdf-v2.json").write_text(json.dumps(old), encoding="utf-8")
            (directory / "oge-278t-pdf-v3.json").write_text(json.dumps(new), encoding="utf-8")
            self.assertEqual(audit(root)["transaction_count"], 228)
            new["quarantined"][0]["extraction_id"] = "changed"
            (directory / "oge-278t-pdf-v3.json").write_text(json.dumps(new), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed a table row"):
                audit(root)


if __name__ == "__main__":
    unittest.main()
