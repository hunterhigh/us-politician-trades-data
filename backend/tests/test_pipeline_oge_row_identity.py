import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from test_pipeline_canonical_compare import full_row, transaction
from test_pipeline_projection_audit import bundle, SHA
from unison_snapshot.pipeline_qa.canonical_compare import git_blob_sha1
from unison_snapshot.pipeline_qa.oge_row_identity import audit_oge_row_identity
from unison_snapshot.pipeline_qa.projection_audit import ProjectionAuditError

TREE_SHA = "d" * 40
REVIEW_SHA = "c" * 40


def fixture(root, *, documents=("doc-1",), candidate_documents=("doc-1",),
            duplicate_canonical_values=False, corrupt_extraction=False):
    root = Path(root)
    rows = []
    extraction_dir = root / "extractions"
    extraction_dir.mkdir()
    tree_entries = []
    canonical = []
    for document in documents:
        candidate_row = full_row(document)
        candidate_row["candidate_id"] = f"{SHA[:16]}:1"
        candidate_row["observations"].append({
            **candidate_row["observations"][0], "observation_id": "row_number",
            "field": "row_number", "raw_value": "1", "normalized_value": 1,
            "required_for_projection": False})
        rows.append(candidate_row)
        extracted = transaction(document=document, tx_id="oge-278t:official-1")
        extracted.update({"extraction_id": extracted["id"], "page_number": 1,
                          "row_number": 1})
        extraction = {"document_id": document, "source_id": "oge",
                      "source_sha256": SHA, "source_url": candidate_row["source"]["source_url"],
                      "parser_version": "v1", "transactions": [extracted]}
        raw = json.dumps(extraction).encode()
        (extraction_dir / f"{document}.json").write_bytes(raw)
        path = f"oge/extractions/{document}/{SHA}/v1.json"
        tree_entries.append({"path": path, "type": "blob", "sha": git_blob_sha1(raw),
                             "size": len(raw)})
        if document in candidate_documents:
            canonical.append(transaction(document=document, tx_id=extracted["id"]))
    if duplicate_canonical_values:
        canonical.append(transaction(document="doc-1", tx_id="oge-278t:other"))
    bundle(root, rows)
    if corrupt_extraction:
        (extraction_dir / "doc-1.json").write_text("{}", encoding="utf-8")
    tree_path = root / "tree.json"
    tree_path.write_text(json.dumps({"sha": TREE_SHA, "truncated": False,
                                     "tree": tree_entries}), encoding="utf-8")
    candidate_path = root / "candidate.json"
    candidate_path.write_text(json.dumps({"meta": {"is_demo": False},
                                          "transactions": canonical}), encoding="utf-8")
    tree_entries.append({"path": "candidates/sources/oge-current.json", "type": "blob",
                         "sha": git_blob_sha1(candidate_path.read_bytes()),
                         "size": candidate_path.stat().st_size})
    tree_path.write_text(json.dumps({"sha": TREE_SHA, "truncated": False,
                                     "tree": tree_entries}), encoding="utf-8")
    return (root, extraction_dir, tree_path, TREE_SHA, REVIEW_SHA,
            candidate_path, git_blob_sha1(candidate_path.read_bytes()))


class OgeRowIdentityTests(unittest.TestCase):
    def test_locator_resolves_duplicate_values_by_extraction_id(self):
        with TemporaryDirectory() as tmp:
            args = fixture(tmp, duplicate_canonical_values=True)
            result = audit_oge_row_identity(*args)
        self.assertEqual(result["counts"], {"canonical_id_matched": 1,
                                            "extraction_id_bound": 1})
        self.assertEqual(result["previously_value_ambiguous_rows"], 1)
        self.assertEqual(result["matched_canonical_rows"], [{
            "document_id": "doc-1", "source_sha256": SHA,
            "candidate_id": f"{SHA[:16]}:1",
            "extraction_id": "oge-278t:official-1",
            "canonical_transaction_id": "oge-278t:official-1",
        }])
        self.assertFalse(result["projection_ready"])

    def test_same_pdf_rows_in_distinct_official_documents_remain_separate(self):
        with TemporaryDirectory() as tmp:
            args = fixture(tmp, documents=("doc-1", "doc-2"))
            result = audit_oge_row_identity(*args)
        self.assertEqual(result["counts"], {"canonical_id_matched": 1,
                                            "canonical_id_missing": 1,
                                            "extraction_id_bound": 2})
        self.assertEqual(result["missing_canonical_documents"], {"doc-2": 1})
        self.assertEqual(len(result["candidate_id_collisions"]), 1)

    def test_changed_extraction_blob_fails_closed(self):
        with TemporaryDirectory() as tmp:
            args = fixture(tmp, corrupt_extraction=True)
            with self.assertRaisesRegex(ProjectionAuditError, "blob mismatch"):
                audit_oge_row_identity(*args)

    def test_missing_locator_fails_closed(self):
        with TemporaryDirectory() as tmp:
            args = fixture(tmp)
            path = Path(tmp) / "extractions" / "doc-1.json"
            extraction = json.loads(path.read_text())
            extraction["transactions"][0]["row_number"] = 2
            raw = json.dumps(extraction).encode()
            path.write_bytes(raw)
            tree_path = Path(tmp) / "tree.json"
            tree = json.loads(tree_path.read_text())
            tree["tree"][0]["sha"] = git_blob_sha1(raw)
            tree["tree"][0]["size"] = len(raw)
            tree_path.write_text(json.dumps(tree), encoding="utf-8")
            with self.assertRaisesRegex(ProjectionAuditError, "row locator"):
                audit_oge_row_identity(*args)


if __name__ == "__main__":
    unittest.main()
