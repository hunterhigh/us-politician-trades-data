import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.pipeline_qa.bundle import BundleInputError, build_bundle
from unison_snapshot.pipeline_qa.diff import verify_manifest_outputs
from unison_snapshot.pipeline_ledger import idempotency_key
from test_pipeline_ledger import CODE_SHA, SOURCE_SHA


def _source_run(root: Path, *, source_id="house_clerk", code_commit=CODE_SHA,
                document_id="20035420", source_sha=SOURCE_SHA, run_name=None):
    run = root / (run_name or source_id)
    run.mkdir()
    parser = {"parser_id": "house-ptr", "parser_version": "v1",
              "rules_version": "rules-v1", "layout_fingerprint": "layout-v1"}
    key = idempotency_key(source_id=source_id, source_sha256=source_sha, **parser)
    document = {"source_id": source_id, "document_id": document_id,
                "source_url": "https://example.gov/report.pdf", "source_sha256": source_sha,
                **parser, "idempotency_key": key, "disposition": "parsed"}
    def candidate(candidate_id, disposition):
        location = {"page": 1, "kind": "table_row", "row_locator": candidate_id}
        return {
            "schema_version": "pipeline-candidate-row/v1",
            "candidate_id": candidate_id, "run_id": f"{source_id}-run",
            "source": {"source_id": source_id, "document_id": document_id,
                       "source_url": document["source_url"], "source_sha256": source_sha},
            "parser": parser, "idempotency_key": key,
            "disposition": disposition,
            "reasons": [] if disposition == "qualified" else ["uncertain_row"],
            "unresolved_conflicts": [],
            "required_projection_fields": ["asset_name"] if disposition == "qualified" else [],
            "evidence_locations": [location],
            "observations": [{"schema_version": "pipeline-field-observation/v1",
                              "observation_id": candidate_id + ":asset_name",
                              "field": "asset_name", "raw_value": "Example",
                              "normalized_value": "Example", "method": "embedded_text",
                              "confidence": 1.0, "selected": True,
                              "required_for_projection": True,
                              "locations": [location], "conditions": {}}],
        }
    rows = [candidate("1", "qualified"), candidate("2", "quarantined")]
    payload = (json.dumps(rows, separators=(",", ":")) + "\n").encode()
    (run / "rows.json").write_bytes(payload)
    manifest = {
        "schema_version": "pipeline-run-manifest/v1", "run_id": f"{source_id}-run",
        "workflow": f"{source_id}-review", "trigger": "local", "code_commit": code_commit,
        "started_at": "2026-09-29T01:00:00Z", "completed_at": "2026-09-29T01:02:00Z",
        "source_scope": [source_id], "documents": [document],
        "counts": {"discovered_documents": 1, "archived_documents": 1,
                    "parsed_documents": 1, "failed_documents": 0, "no_row_documents": 0,
                    "excluded_documents": 0, "qualified_rows": 1, "quarantined_rows": 1,
                    "excluded_rows": 0, "unrecognized_rows": 0},
        "accounted_rows": 2,
        "outputs": [{"artifact_type": "candidate_rows", "path": "rows.json",
                     "sha256": hashlib.sha256(payload).hexdigest()}],
        "downstream_run_ids": [],
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run


class PipelineShadowBundleTests(unittest.TestCase):
    def test_combines_fixed_source_runs_and_emits_verified_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            house = _source_run(root)
            senate = _source_run(root, source_id="senate_efd")
            output = root / "combined"
            result = build_bundle([house, senate], output,
                                  run_id="shadow-001", code_commit=CODE_SHA,
                                  trigger="workflow_dispatch")
            self.assertEqual(result["source_scope"], ["house_clerk", "senate_efd"])
            self.assertEqual(result["accounted_rows"], 4)
            self.assertEqual(result["counts"]["qualified_rows"], 2)
            self.assertEqual(result["trigger"], "workflow_dispatch")
            self.assertEqual(result["outputs"][0]["sha256"],
                             hashlib.sha256((output / "candidate_rows.json").read_bytes()).hexdigest())
            self.assertEqual(verify_manifest_outputs(result, output, "combined")["status"], "passed")
            self.assertEqual(len(result["source_runs"]), 2)

    def test_rejects_output_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = _source_run(root)
            (run / "rows.json").write_text("[]\n", encoding="utf-8")
            with self.assertRaisesRegex(BundleInputError, "SHA-256 mismatch"):
                build_bundle([run], root / "out", run_id="shadow-002", code_commit=CODE_SHA)

    def test_rejects_rows_from_a_different_run_even_with_matching_artifact_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = _source_run(root)
            rows_path = run / "rows.json"
            rows = json.loads(rows_path.read_text(encoding="utf-8"))
            rows[0]["run_id"] = "another-run"
            payload = (json.dumps(rows, separators=(",", ":")) + "\n").encode()
            rows_path.write_bytes(payload)
            manifest_path = run / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["outputs"][0]["sha256"] = hashlib.sha256(payload).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(BundleInputError, "not bound"):
                build_bundle([run], root / "out", run_id="shadow-006", code_commit=CODE_SHA)

    def test_rejects_mixed_code_commits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = _source_run(root, code_commit="c" * 40)
            with self.assertRaisesRegex(BundleInputError, "fixed SHA"):
                build_bundle([run], root / "out", run_id="shadow-003", code_commit=CODE_SHA)

    def test_does_not_overwrite_existing_bundle_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = _source_run(root)
            output = root / "out"
            output.mkdir()
            sentinel = output / "existing.json"
            sentinel.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(BundleInputError, "immutable"):
                build_bundle([run], output, run_id="shadow-005", code_commit=CODE_SHA)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_allows_distinct_documents_from_repeated_source_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = _source_run(root, run_name="house-1", document_id="20035420")
            second = _source_run(root, run_name="house-2", document_id="20035421",
                                 source_sha="e" * 64)
            result = build_bundle([first, second], root / "out",
                                  run_id="shadow-004", code_commit=CODE_SHA)
            self.assertEqual(result["source_scope"], ["house_clerk"])
            self.assertEqual(result["counts"]["discovered_documents"], 2)
            self.assertEqual(result["accounted_rows"], 4)


if __name__ == "__main__":
    unittest.main()
