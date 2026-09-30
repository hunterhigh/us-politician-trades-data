from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from unison_snapshot.pipeline_ledger import idempotency_key
from unison_snapshot.pipeline_qa.projection_audit import ProjectionAuditError, audit_bundle, scoped_identity

SHA = "a" * 64


def row(document="doc-1"):
    parser = {"parser_id": "p", "parser_version": "v1", "rules_version": "v1", "layout_fingerprint": "table"}
    source = {"source_id": "oge", "document_id": document, "source_sha256": SHA,
              "source_url": "https://example.gov/report.pdf"}
    location = {"kind": "table_row", "page": 1, "row_locator": "1"}
    return {"schema_version": "pipeline-candidate-row/v1", "candidate_id": "same-row", "run_id": "fixed-run",
            "source": source, "parser": parser,
            "idempotency_key": idempotency_key(source_id="oge", source_sha256=SHA, **parser),
            "disposition": "qualified", "reasons": [], "unresolved_conflicts": [],
            "evidence_locations": [location], "required_projection_fields": ["asset_name"],
            "observations": [{"schema_version": "pipeline-field-observation/v1", "observation_id": "asset:1",
                              "field": "asset_name", "raw_value": "Fund", "normalized_value": "Fund",
                              "method": "embedded_text", "confidence": 1.0, "selected": True,
                              "required_for_projection": True, "locations": [location], "conditions": {}}]}


def bundle(root, rows):
    root = Path(root)
    raw = (json.dumps(rows, sort_keys=True) + "\n").encode()
    (root / "candidate_rows.json").write_bytes(raw)
    docs = []
    for doc_id in sorted({r["source"]["document_id"] for r in rows}):
        r = next(r for r in rows if r["source"]["document_id"] == doc_id)
        docs.append({**r["source"], **r["parser"], "idempotency_key": r["idempotency_key"],
                     "disposition": "parsed"})
    manifest = {"schema_version": "pipeline-run-manifest/v1", "run_id": "fixed-run",
                "workflow": "shadow-bundle", "trigger": "local", "code_commit": "b" * 40,
                "started_at": "2026-09-30T00:00:00Z", "completed_at": "2026-09-30T00:01:00Z",
                "source_scope": ["oge"], "documents": docs,
                "counts": {"discovered_documents": len(docs), "archived_documents": len(docs),
                           "parsed_documents": len(docs), "failed_documents": 0,
                           "no_row_documents": 0, "excluded_documents": 0,
                           "qualified_rows": len(rows), "quarantined_rows": 0,
                           "excluded_rows": 0, "unrecognized_rows": 0},
                "accounted_rows": len(rows),
                "outputs": [{"artifact_type": "candidate_rows", "path": "candidate_rows.json",
                             "sha256": hashlib.sha256(raw).hexdigest()}],
                "downstream_run_ids": []}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


class ProjectionAuditTests(unittest.TestCase):
    def test_distinct_documents_share_candidate_id_without_scoped_collision(self):
        rows = [row("doc-1"), row("doc-2")]
        self.assertNotEqual(scoped_identity(rows[0]), scoped_identity(rows[1]))
        with TemporaryDirectory() as tmp:
            bundle(tmp, rows)
            result = audit_bundle(tmp)
        self.assertEqual(result["qualified_rows"], 2)
        self.assertEqual(result["unique_candidate_ids"], 1)
        self.assertEqual(len(result["candidate_id_collisions"]), 1)
        self.assertFalse(result["projection_ready"])
        self.assertIn("person_id", result["required_external_bindings"])

    def test_duplicate_scoped_identity_fails_closed(self):
        with TemporaryDirectory() as tmp:
            bundle(tmp, [row(), deepcopy(row())])
            with self.assertRaisesRegex(ProjectionAuditError, "duplicate scoped"):
                audit_bundle(tmp)

    def test_unbound_document_fails_closed(self):
        with TemporaryDirectory() as tmp:
            bundle(tmp, [row()])
            path = Path(tmp) / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest["documents"][0]["document_id"] = "other"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ProjectionAuditError, "not bound"):
                audit_bundle(tmp)

    def test_corrupt_output_fails_before_audit(self):
        with TemporaryDirectory() as tmp:
            bundle(tmp, [row()])
            (Path(tmp) / "candidate_rows.json").write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ProjectionAuditError, "integrity"):
                audit_bundle(tmp)


if __name__ == "__main__":
    unittest.main()
