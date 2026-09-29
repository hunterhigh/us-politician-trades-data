"""Fixed source artifact handoff for OGE shadow runs."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.pipeline_qa.oge_run import OgeShadowInputError, build_oge_shadow_run
from unison_snapshot.pipeline_qa.bundle import build_bundle
from unison_snapshot.pipeline_qa.diff import verify_manifest_outputs


FIXTURE = Path(__file__).parent / "fixtures/oge_278t/gold_rows_v1.json"
CODE_SHA = "b" * 40
EVIDENCE_SHA = "c" * 40


def _inputs(root: Path, *, failed: bool = False) -> dict:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    catalog_record = fixture["catalog_record"]
    extraction = fixture["extraction"]
    document_id = extraction["document_id"]
    extraction_root = root / "extractions"
    extraction_root.mkdir()
    if not failed:
        (extraction_root / f"{document_id}.json").write_text(
            json.dumps(extraction), encoding="utf-8")
    catalog = root / "catalog.json"
    catalog.write_text(json.dumps({"transactions": [catalog_record]}), encoding="utf-8")
    archive_batch = root / "archive-batch.json"
    archive_batch.write_text(json.dumps({
        "schema_version": "oge-278t-archive-batch/v1", "catalog_direct_count": 1,
        "pending_count": 0, "attempted_count": 1, "archived_count": 1,
        "failure_count": 0, "failures": [],
        "reports": [{"document_id": document_id, "document_url": extraction["source_url"],
                     "sha256": extraction["source_sha256"],
                     "archive_path": (
                         f"oge/reports/{document_id}/{extraction['source_sha256']}.pdf"),
                     "byte_length": 12000,
                     "filer_name": catalog_record["filer_name"],
                     "agency": catalog_record["agency"],
                     "position_title": catalog_record["position_title"],
                     "catalog_added_date": catalog_record["catalog_added_date"],
                     "amended_label": catalog_record["amended_label"],
                     "pending_final_oge_disposition":
                         catalog_record["pending_final_oge_disposition"]}],
    }), encoding="utf-8")
    extraction_batch = root / "extraction-batch.json"
    extraction_batch.write_text(json.dumps({
        "schema_version": "oge-278t-extraction-batch/v1", "report_count": 1,
        "extraction_count": 0 if failed else 1,
        "failure_count": 1 if failed else 0,
        "failures": [{"document_id": document_id, "error": "parser failed"}] if failed else [],
    }), encoding="utf-8")
    return {"catalog_path": catalog, "archive_batch_path": archive_batch,
            "extraction_batch_path": extraction_batch, "extractions_dir": extraction_root,
            "output_dir": root / "output", "run_id": "oge-shadow-123",
            "code_commit": CODE_SHA, "evidence_commit": EVIDENCE_SHA,
            "review_commit": "d" * 40,
            "started_at": "2026-09-29T01:00:00Z",
            "completed_at": "2026-09-29T01:02:00Z"}


class OgeShadowRunTests(unittest.TestCase):
    def test_emits_exact_rows_and_hash_verified_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            inputs = _inputs(Path(temp))
            manifest = build_oge_shadow_run(**inputs)
            self.assertEqual(manifest["accounted_rows"], 5)
            self.assertEqual(manifest["review_commit"], "d" * 40)
            self.assertEqual(manifest["counts"]["discovered_documents"], 1)
            self.assertEqual(manifest["counts"]["qualified_rows"], 1)
            self.assertEqual(manifest["counts"]["quarantined_rows"], 1)
            self.assertEqual(manifest["counts"]["excluded_rows"], 2)
            self.assertEqual(manifest["counts"]["unrecognized_rows"], 1)
            rows = json.loads((inputs["output_dir"] / "candidate_rows.json").read_text())
            self.assertEqual({row["run_id"] for row in rows}, {"oge-shadow-123"})
            self.assertEqual(verify_manifest_outputs(manifest, inputs["output_dir"], "oge")["status"], "passed")
            combined = build_bundle([inputs["output_dir"]], Path(temp) / "combined",
                                    run_id="combined-123", code_commit=CODE_SHA)
            self.assertEqual(combined["accounted_rows"], 5)

    def test_keeps_failed_document_visible_without_inventing_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            inputs = _inputs(Path(temp), failed=True)
            manifest = build_oge_shadow_run(**inputs)
            self.assertEqual(manifest["counts"]["failed_documents"], 1)
            self.assertEqual(manifest["accounted_rows"], 0)
            self.assertEqual(manifest["documents"][0]["reason"], "extraction_failed")

    def test_rejects_extraction_with_wrong_source_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            inputs = _inputs(Path(temp))
            path = next(inputs["extractions_dir"].glob("*.json"))
            extraction = json.loads(path.read_text())
            extraction["source_sha256"] = "d" * 64
            path.write_text(json.dumps(extraction), encoding="utf-8")
            with self.assertRaisesRegex(OgeShadowInputError, "archived document"):
                build_oge_shadow_run(**inputs)
            self.assertFalse(inputs["output_dir"].exists())


if __name__ == "__main__":
    unittest.main()
