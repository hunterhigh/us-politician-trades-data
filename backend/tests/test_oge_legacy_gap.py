"""Regression checks for the fixed five-report OGE legacy coverage replay."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from unison_snapshot.pipeline_qa.oge_legacy_gap import (
    DOCUMENT_IDS, LegacyGapError, _git_bytes, audit_legacy_oge_gap,
)


FIXTURE = Path(__file__).parent / "fixtures" / "oge_legacy_gap_124.json"
EVIDENCE = "5fe5f0adb3181c5d1552dff3f07336464c3043a7"
REVIEW = "105333d651bb88822ddc6d2565c77d52d49aa5ef"


class LegacyGapTests(unittest.TestCase):
    def test_fixed_replay_fixture_conserves_every_legacy_id(self):
        result = json.loads(FIXTURE.read_text(encoding="utf-8"))
        documents = result["documents"]
        self.assertEqual(result["schema_version"], "oge-legacy-gap-replay/v1")
        self.assertEqual(result["evidence_commit"], EVIDENCE)
        self.assertEqual(result["review_commit"], REVIEW)
        self.assertEqual({item["document_id"] for item in documents}, set(DOCUMENT_IDS))
        self.assertEqual([item["candidate_row_count"] for item in documents], [2, 1, 1, 72, 48])
        self.assertEqual(result["candidate_row_count"], 124)
        self.assertEqual(result["source_row_count"], 125)
        self.assertEqual(result["dispositions"], {
            "qualified": 124, "quarantined": 1, "excluded": 0, "unrecognized": 0})
        ids = [row_id for item in documents for row_id in item["candidate_transaction_ids"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 124)
        self.assertTrue(all(item["replayed_source_row_count"] == sum(
            item["replayed_dispositions"].values()) for item in documents))
        self.assertIs(result["projection_ready"], False)
        self.assertIs(result["production_refs_written"], False)

    def test_replay_rejects_changed_candidate_bytes_before_git_access(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate.json"
            coverage = root / "coverage.json"
            candidate.write_text("{}", encoding="utf-8")
            coverage.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(LegacyGapError, "byte hash differs"):
                audit_legacy_oge_gap(repo=root, candidate_path=candidate,
                                     coverage_path=coverage, evidence_commit=EVIDENCE,
                                     review_commit=REVIEW)

    def test_replay_rejects_unpinned_commit_and_unsafe_git_path(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(LegacyGapError, "full immutable"):
                audit_legacy_oge_gap(repo=root, candidate_path=root / "missing",
                                     coverage_path=root / "missing",
                                     evidence_commit="origin/evidence", review_commit=REVIEW)
            with self.assertRaisesRegex(LegacyGapError, "invalid pinned"):
                _git_bytes(root, EVIDENCE, "../secrets")
