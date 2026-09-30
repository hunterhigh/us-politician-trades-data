import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot import whitehouse_annual_fixed_replay as replay


def _blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _synthetic_replay():
    source = {"section": "part7", "source_row_locator": "p1-y1",
              "asset_name": "Example", "owner": "Unknown",
              "transaction_type": "purchase", "transaction_date": "2025-01-01",
              "amount_low": 1001, "amount_high": 15000}
    row_id = "wh-annual-tx:" + hashlib.sha256(
        f"{replay.PDF_SHA256}|part7|p1-y1".encode()).hexdigest()[:24]
    candidate_row = {**{field: source[field] for field in replay.SOURCE_FIELDS},
                     "id": row_id, "filing_id": replay.DOCUMENT_ID,
                     "person_id": "oge:fixed", "source_url": "https://example.org/report.pdf",
                     "filed_at": "2026-06-29T00:00:00Z", "source_id": "oge",
                     "verification_status": "official_matched"}
    candidate = json.dumps({"transactions": [candidate_row]}).encode()
    extraction = json.dumps({
        "source_sha256": replay.PDF_SHA256,
        "source_url": "https://example.org/report.pdf",
        "parser_version": replay.PARSER_VERSION, "page_count": 927,
        "printed_row_count": 1, "holdings": [], "transactions": [source],
        "excluded": [], "quarantined": [],
        "ocr_checkpoint": {"reused_shard_count": 38,
                           "created_shard_count": 0,
                           "pending_page_count": 0, "completed_page_count": 927},
        "source_row_census_complete": False,
    }).encode()
    audit = {"schema_version": "whitehouse-wh-url-document-audit/v1",
             "candidate_sha256": replay._sha(candidate),
             "candidate_commit": "a" * 40, "evidence_commit": "b" * 40,
             "documents": [{"document_id": replay.DOCUMENT_ID,
                            "candidate_row_count": 1,
                            "archive_pdf_sha256_from_path": replay.PDF_SHA256,
                            "document_type_from_public_label": "278e_annual",
                            "source_url": "https://example.org/report.pdf",
                            "person_id": "oge:fixed"}]}
    return candidate, extraction, audit


class AnnualFixedReplayTests(unittest.TestCase):
    def test_existing_id_and_source_fields_reconciled(self):
        candidate, extraction, audit = _synthetic_replay()
        with (patch.object(replay, "EXTRACTION_SHA256", replay._sha(extraction)),
              patch.object(replay, "EXPECTED_COUNTS", {
                  "holdings": 0, "transactions": 1, "excluded": 0,
                  "quarantined": 0})):
            result = replay.replay_annual_candidate(
                candidate, extraction, audit, candidate_blob=_blob(candidate),
                extraction_blob=_blob(extraction),
                checkpoint_tree="c57ecdcd0e780fa7dc256fae6d0fe728735c8f09")
            self.assertEqual(result["existing_candidate_id_count"], 1)
            self.assertEqual(result["candidate_source_field_conflict_count"], 0)
            altered = json.loads(candidate)
            altered["transactions"][0]["amount_high"] = 50000
            changed = json.dumps(altered).encode()
            changed_audit = {**audit, "candidate_sha256": replay._sha(changed)}
            with self.assertRaisesRegex(ValueError, "amount_high"):
                replay.replay_annual_candidate(
                    changed, extraction, changed_audit,
                    candidate_blob=_blob(changed),
                    extraction_blob=_blob(extraction),
                    checkpoint_tree="c57ecdcd0e780fa7dc256fae6d0fe728735c8f09")

    def test_frozen_result_records_unresolved_scope(self):
        path = Path(__file__).resolve().parent / "fixtures/whitehouse_annual_fixed_replay.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(result["existing_candidate_id_count"], 6759)
        self.assertEqual(sum(result["disposition_counts"].values()), 28099)
        self.assertEqual(result["annual_rows_with_coarse_periodic_candidate"], 361)
        self.assertFalse(result["coarse_periodic_matches_are_duplicates"])
        self.assertFalse(result["source_row_census_complete"])
        self.assertFalse(result["production_mutation_authorized"])
        self.assertEqual(result["archive_pdf_byte_verification"],
                         "not_reverified_this_run")


if __name__ == "__main__":
    unittest.main()
