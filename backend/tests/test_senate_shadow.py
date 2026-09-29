"""Fixed-archive integration checks for the electronic PTR shadow ledger."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.senate_shadow import SenateShadowError, build_shadow, main


REPO = Path(__file__).resolve().parents[2]
CODE = subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()
EVIDENCE = "80f086267677b8fde34314f24024bdc505d97cb0"
REVIEW = "9749718e5ae837057db7c031d431714fc72988d1"
IDENTITY = ("senate_efd/identities/8d8c5a7abbec9e3d956a9cceae78505f374b2237e156a4a9fcca7463d43e4619/"
            "984865a4a6e00af68c9617ea45f52b8939f49143780c832cb68c747fa4bfef5e.json")
ROSTER = ("senate_efd/members/"
          "984865a4a6e00af68c9617ea45f52b8939f49143780c832cb68c747fa4bfef5e.xml")
OLDER = "cce52b36-d00c-4710-a8ee-e84893fb4be1"
NEWER = "2b076d77-6bc1-4b67-8be9-8f45a787479f"


def replay(**updates):
    arguments = dict(repo=REPO, code_commit=CODE, evidence_commit=EVIDENCE,
                     review_commit=REVIEW, identity_path=IDENTITY,
                     roster_source_path=ROSTER, document_ids=[OLDER, NEWER],
                     run_id="real-senate-two", started_at=datetime.now(timezone.utc).isoformat())
    arguments.update(updates)
    return build_shadow(**arguments)


class SenateShadowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for ref, path in ((REVIEW, IDENTITY), (EVIDENCE, ROSTER)):
            result = subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", f"{ref}:{path}"],
                                    capture_output=True)
            if result.returncode:
                raise unittest.SkipTest("fixed Senate archive Git objects unavailable")

    def test_real_amendment_chain_accounts_every_printed_row(self):
        rows, manifest = replay()
        self.assertEqual(len(rows), 24)
        self.assertEqual(manifest["counts"]["qualified_rows"], 12)
        self.assertEqual(manifest["counts"]["excluded_rows"], 12)
        self.assertEqual(manifest["counts"]["quarantined_rows"], 0)
        self.assertEqual(manifest["accounted_rows"], 24)
        self.assertEqual(len(manifest["amendment_resolution"]), 1)
        for row in rows:
            if row["source"]["document_id"] == OLDER:
                self.assertEqual(row["disposition"], "excluded")
                self.assertEqual(row["reasons"], ["superseded_by_verified_amendment"])
            else:
                self.assertEqual(row["disposition"], "qualified")

    def test_missing_predecessor_fails_closed_to_quarantine(self):
        rows, manifest = replay(document_ids=[NEWER])
        self.assertEqual(len(rows), 12)
        self.assertEqual(manifest["counts"]["quarantined_rows"], 12)
        self.assertTrue(all(row["reasons"] == ["amendment_relationship_pending"]
                            for row in rows))

    def test_roster_commit_binding_rejects_wrong_source(self):
        with self.assertRaisesRegex(SenateShadowError, "roster"):
            replay(roster_source_path="senate_efd/members/0.xml")

    def test_duplicate_document_id_rejected(self):
        with self.assertRaisesRegex(SenateShadowError, "unique"):
            replay(document_ids=[OLDER, OLDER])

    def test_cli_writes_bundle_compatible_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "senate-run"
            argv = ["senate_shadow", "--repo", str(REPO), "--code-commit", CODE,
                    "--evidence-commit", EVIDENCE, "--review-commit", REVIEW,
                    "--identity-path", IDENTITY, "--roster-source-path", ROSTER,
                    "--document-id", OLDER, "--document-id", NEWER,
                    "--run-id", "senate-test-run", "--output-root", str(output)]
            with patch.object(sys, "argv", argv):
                main()
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            rows = json.loads((output / "candidate_rows.json").read_text(encoding="utf-8"))
            self.assertEqual(len(rows), manifest["accounted_rows"])
            self.assertEqual(manifest["outputs"], [{
                "artifact_type": "candidate_rows", "path": "candidate_rows.json",
                "sha256": hashlib.sha256((output / "candidate_rows.json").read_bytes()).hexdigest(),
            }])
            self.assertFalse((output / "run_manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
