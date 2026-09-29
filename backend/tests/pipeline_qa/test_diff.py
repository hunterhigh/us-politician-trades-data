from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.pipeline_ledger import RUN_MANIFEST_SCHEMA, idempotency_key
from unison_snapshot.pipeline_qa.__main__ import main
from unison_snapshot.pipeline_qa.diff import (
    DiffInputError, check_conservation, compare_runs, load_rows, stable_identity,
)


def manifest(run_id="r1", qualified=1):
    digest = "a" * 64
    parser = {"parser_id": "p", "parser_version": "1", "rules_version": "r",
              "layout_fingerprint": "l"}
    key = idempotency_key(source_id="s", source_sha256=digest, **parser)
    doc = {"source_id": "s", "document_id": "d", "source_url": "https://example.test/d",
           "source_sha256": digest, **parser, "idempotency_key": key, "disposition": "parsed"}
    return {"schema_version": RUN_MANIFEST_SCHEMA, "run_id": run_id, "workflow": "offline",
            "workflow_run_id": "1", "trigger": "local", "code_commit": "b" * 40,
            "started_at": "2026-09-29T00:00:00Z", "completed_at": "2026-09-29T00:01:00Z",
            "source_scope": ["s"], "documents": [doc],
            "counts": {"discovered_documents": 1, "archived_documents": 1,
                        "parsed_documents": 1, "failed_documents": 0, "no_row_documents": 0,
                        "excluded_documents": 0, "qualified_rows": qualified,
                        "quarantined_rows": 0, "excluded_rows": 0, "unrecognized_rows": 0},
            "accounted_rows": qualified, "outputs": [], "downstream_run_ids": []}


def row(row_id="row-1", value="old", digest="a" * 64):
    return {"source_id": "s", "document_id": "d", "source_sha256": digest,
            "row_id": row_id, "disposition": "qualified", "amount": value}


class PipelineDiffTests(unittest.TestCase):
    def test_compares_fields_and_detects_added_removed(self):
        old = [row(), row("row-2")]
        new = [row(value="new"), row("row-3")]
        report = compare_runs(old, new, manifest("old", qualified=2), manifest("new", qualified=2))
        self.assertEqual((report["added_count"], report["removed_count"], report["changed_count"]), (1, 1, 1))
        self.assertEqual(report["changed"][0]["changes"][0]["field"], "amount")
        self.assertTrue(report["ok"])

    def test_source_sha_is_compared_and_exposed_as_field_change(self):
        report = compare_runs([row()], [row(digest="c" * 64)], manifest(), manifest("r2"))
        self.assertEqual(report["changed_count"], 1)
        self.assertIn("source_sha256", {c["field"] for c in report["changed"][0]["changes"]})

    def test_duplicate_identity_is_never_silently_collapsed(self):
        report = compare_runs([row(), row(value="second")], [row(), row(value="second")],
                              manifest(qualified=2), manifest("r2", qualified=2))
        self.assertEqual(len(report["duplicates"]), 2)
        self.assertFalse(report["ok"])

    def test_manifest_must_match_actual_disposition_totals(self):
        report = compare_runs([row(), row("row-2")], [row()], manifest(qualified=1), manifest("r2"))
        self.assertFalse(report["ok"])
        self.assertIn("qualified", report["conservation"][0]["mismatches"])

    def test_unrecognized_dispositions_count_as_unaccounted(self):
        check = check_conservation([row() | {"disposition": "mystery"}], manifest(), "old")
        self.assertTrue(check["invalid_dispositions"])
        self.assertIn("unaccounted_rows", check["mismatches"])

    def test_non_string_disposition_is_reported_without_crashing(self):
        check = check_conservation([row() | {"disposition": {"unexpected": True}}], manifest(), "old")
        self.assertEqual(check["invalid_dispositions"], {"dict": 1})
        self.assertIn("unaccounted_rows", check["mismatches"])

    def test_requires_stable_identity(self):
        with self.assertRaises(DiffInputError):
            stable_identity({"source_id": "s", "document_id": "d"})

    def test_loads_json_array_and_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            json_path = Path(folder) / "rows.json"
            csv_path = Path(folder) / "rows.csv"
            json_path.write_text(json.dumps([row()]), encoding="utf-8")
            csv_path.write_text("source_id,document_id,source_sha256,row_id,disposition\ns,d," + "a" * 64 + ",row-1,qualified\n", encoding="utf-8")
            self.assertEqual(len(load_rows(json_path)), 1)
            self.assertEqual(stable_identity(load_rows(csv_path)[0]), ("s", "d", "row-1"))

    def test_manifest_arg_not_mutated(self):
        value = manifest()
        before = deepcopy(value)
        check_conservation([row()], value, "old")
        self.assertEqual(value, before)

    def test_manifest_output_hashes_are_checked_inside_artifact_roots(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_root, new_root = root / "old", root / "new"
            old_root.mkdir(); new_root.mkdir()
            old_bytes, new_bytes = b"old artifact", b"new artifact"
            (old_root / "rows.json").write_bytes(old_bytes)
            (new_root / "rows.json").write_bytes(new_bytes)
            old_manifest, new_manifest = manifest("old"), manifest("new")
            old_manifest["outputs"] = [{"artifact_type": "candidate_rows", "path": "rows.json",
                                         "sha256": hashlib.sha256(old_bytes).hexdigest()}]
            new_manifest["outputs"] = [{"artifact_type": "candidate_rows", "path": "rows.json",
                                         "sha256": hashlib.sha256(new_bytes).hexdigest()}]
            report = compare_runs([row()], [row()], old_manifest, new_manifest,
                                  old_artifact_root=old_root, new_artifact_root=new_root)
            self.assertTrue(report["ok"])
            self.assertEqual([item["status"] for item in report["artifact_checks"]],
                             ["passed", "passed"])

            (new_root / "rows.json").write_bytes(b"modified after manifest")
            report = compare_runs([row()], [row()], old_manifest, new_manifest,
                                  old_artifact_root=old_root, new_artifact_root=new_root)
            self.assertFalse(report["ok"])
            self.assertEqual(report["artifact_checks"][1]["failures"][0]["reason"],
                             "sha256 mismatch")

    def test_manifest_output_hash_check_reports_missing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            value = manifest()
            value["outputs"] = [{"artifact_type": "candidate_rows", "path": "missing.json",
                                 "sha256": "c" * 64}]
            report = compare_runs([row()], [row()], value, manifest("new"),
                                  old_artifact_root=folder, new_artifact_root=folder)
            self.assertFalse(report["ok"])
            self.assertEqual(report["artifact_checks"][0]["failures"][0]["reason"], "file missing")

    def test_cli_writes_machine_report_and_prints_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, value in (("old", "old"), ("new", "new")):
                (root / f"{name}-rows.json").write_text(json.dumps([row(value=value)]), encoding="utf-8")
                (root / f"{name}-manifest.json").write_text(json.dumps(manifest(name)), encoding="utf-8")
            output = root / "report.json"
            code = main(["--old-rows", str(root / "old-rows.json"), "--new-rows", str(root / "new-rows.json"),
                         "--old-manifest", str(root / "old-manifest.json"),
                         "--new-manifest", str(root / "new-manifest.json"), "--json-out", str(output)])
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(code, 0)
            self.assertEqual(report["changed_count"], 1)


if __name__ == "__main__":
    unittest.main()
