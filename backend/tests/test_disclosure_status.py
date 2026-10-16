from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.builder import build
from unison_snapshot.codec import digest, encode
from unison_snapshot.disclosure_status import attach_disclosure_status
from unison_snapshot.materialize import materialize
from unison_snapshot.public_repo import PublicSnapshotError, PublicSnapshotRepository
from test_public_repo import COMMIT, FakeTransport, production_payload

PROVENANCE = {"code_commit": "1" * 40, "evidence_commit": "2" * 40,
              "review_commit": "3" * 40, "previous_main_commit": "4" * 40}
NOW = "2026-10-16T01:00:00Z"


def record(key, ids=(), **changes):
    return {"record_key": key, "report_id": key, "document_type": "ptr",
            "source_url": None, "catalog_state": "current", "acquisition_state": "archived",
            "extraction_state": "extracted", "qualification_state": "qualified",
            "qualified_transaction_count": len(ids), "quarantined_row_count": 0,
            "reason_codes": [], "source_versions": ["a" * 64],
            "publication_transaction_ids": list(ids), "publication_binding": "verified", **changes}


def scopes(payload):
    return [{"scope_id": source, "source_id": source,
             "scope": {"document_type": "test PTR fixture"}, "observed_at": NOW,
             "limitations": [], "reports": [record(source, [r["id"] for r in payload["transactions"]
                                                           if r["source_id"] == source])],
             "unmatched_publication_transaction_ids": []}
            for source in sorted({r["source_id"] for r in payload["transactions"]})]


class DisclosureStatusTests(unittest.TestCase):
    def setUp(self):
        self.payload = production_payload()
        self.bundle = build(self.payload, generated_at=NOW, allow_production=True)
        self.scopes = scopes(self.payload)

    def attach(self, source_scopes=None, **kwargs):
        return attach_disclosure_status(self.bundle, source_scopes or self.scopes,
                                        transactions=self.payload["transactions"], provenance=PROVENANCE,
                                        **kwargs)

    def test_status_does_not_mutate_snapshot_identity_or_input(self):
        original = deepcopy(self.scopes)
        attached = self.attach()
        self.assertEqual(original, self.scopes)
        self.assertEqual(attached.manifest["snapshot_id"], self.bundle.manifest["snapshot_id"])
        self.assertEqual(attached.manifest["status_revision"], self.bundle.manifest["status_revision"])
        for path, content in self.bundle.files.items():
            if path != "manifest.json":
                self.assertEqual(attached.files[path], content)
        self.assertEqual(attached.files, self.attach(list(reversed(self.scopes))).files)

    def test_duplicate_or_unaccounted_publication_ids_fail(self):
        bad = deepcopy(self.scopes)
        bad[0]["reports"].append(deepcopy(bad[0]["reports"][0]))
        with self.assertRaisesRegex(ValueError, "duplicated"):
            self.attach(bad)
        bad = deepcopy(self.scopes)
        bad[0]["reports"][0]["publication_transaction_ids"] = []
        with self.assertRaisesRegex(ValueError, "every publication transaction"):
            self.attach(bad)

    def test_status_input_cannot_describe_a_different_board(self):
        with self.assertRaisesRegex(ValueError, "differ from the prepared board"):
            attach_disclosure_status(self.bundle, self.scopes, transactions=[], provenance=PROVENANCE)

    def test_unknown_identity_and_unmatched_are_not_zero_qualified_reports(self):
        source = deepcopy(self.scopes)
        source[0]["unmatched_publication_transaction_ids"] = source[0]["reports"][0]["publication_transaction_ids"]
        source[0]["reports"] = [record("occurrence", report_id=None, acquisition_state="request_required",
                                      extraction_state="not_attempted", qualification_state="not_evaluated",
                                      qualified_transaction_count=None, quarantined_row_count=None,
                                      publication_binding="unknown", source_versions=[])]
        attached = self.attach(source)
        reader = PublicSnapshotRepository("owner", "repo", transport=FakeTransport(attached.files))
        status = reader.disclosure_status(COMMIT, include_records=True)
        scope = next(s for s in status["sources"] if s["scope_id"] == source[0]["scope_id"])
        self.assertEqual(scope["counts"]["identified_report_count"], 0)
        self.assertGreater(scope["counts"]["unmatched_publication_transaction_count"], 0)
        self.assertIsNone(scope["records"][0]["qualified_transaction_count"])

    def test_content_pages_split_and_all_counts_read_back(self):
        source = deepcopy(self.scopes)
        source[0]["reports"].extend(record(f"extra-{i}") for i in range(12))
        attached = self.attach(source, max_page_bytes=1024)
        reader = PublicSnapshotRepository("owner", "repo", transport=FakeTransport(attached.files))
        status = reader.disclosure_status(COMMIT, include_records=True)
        self.assertGreater(len(status["sources"][0]["pages"]), 1)
        self.assertEqual(sum(s["counts"]["record_count"] for s in status["sources"]),
                         sum(len(s["reports"]) for s in source))

    def test_corrupt_status_and_wrong_publication_fail_readback(self):
        attached = self.attach()
        path = f"status/{attached.manifest['disclosure_status']['sha256']}.json"
        broken = dict(attached.files)
        broken[path] += b" "
        with self.assertRaisesRegex(PublicSnapshotError, "hash mismatch"):
            PublicSnapshotRepository("owner", "repo", transport=FakeTransport(broken)).disclosure_status(COMMIT)
        value = json.loads(attached.files[path])
        value["publication"]["board_sha256"] = "0" * 64
        content = encode(value)
        manifest = deepcopy(attached.manifest)
        manifest["disclosure_status"].update(sha256=digest(content), byte_length=len(content))
        broken = {**attached.files, "manifest.json": encode(manifest), f"status/{digest(content)}.json": content}
        with self.assertRaisesRegex(PublicSnapshotError, "another publication"):
            PublicSnapshotRepository("owner", "repo", transport=FakeTransport(broken)).disclosure_status(COMMIT)

    def test_old_publication_returns_unprovided_and_established_status_cannot_disappear(self):
        old_reader = PublicSnapshotRepository("owner", "repo", transport=FakeTransport(self.bundle.files))
        self.assertIsNone(old_reader.disclosure_status(COMMIT))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            materialize(root, self.attach())
            with self.assertRaisesRegex(ValueError, "remove processing status"):
                materialize(root, self.bundle)

    def test_status_update_retains_board_and_garbage_collects_previous_status(self):
        first = self.attach()
        source = deepcopy(self.scopes)
        source[0]["limitations"].append("A newly observed processing limit")
        second = self.attach(source)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            materialize(root, first)
            result = materialize(root, second)
            self.assertTrue(result.changed)
            self.assertFalse(result.business_changed)
            self.assertTrue(any(p.startswith("status/") for p in result.removed))
            self.assertEqual((root / f"board/{first.manifest['board']}.json").read_bytes(),
                             first.files[f"board/{first.manifest['board']}.json"])

    def test_dates_require_actual_filing_basis(self):
        source = deepcopy(self.scopes)
        source[0]["reports"][0]["filed_date"] = "2026-10-08"
        with self.assertRaisesRegex(ValueError, "source basis"):
            self.attach(source)


if __name__ == "__main__":
    unittest.main()
