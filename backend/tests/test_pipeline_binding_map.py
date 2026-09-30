from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from test_pipeline_oge_row_identity import fixture
from unison_snapshot.pipeline_qa.binding_map import build_binding_map
from unison_snapshot.pipeline_qa.oge_row_identity import audit_oge_row_identity
from unison_snapshot.pipeline_qa.projection_audit import ProjectionAuditError


def disposition(identity, *, include_missing=True):
    rows = [{**item, "qualification_reasons": ["identity_ambiguous"]}
            for item in identity["missing_canonical_rows"]] if include_missing else []
    return {"bundle_run_id": identity["bundle_run_id"], "production_action": "none",
            "missing_rows": len(rows), "rows": rows}


class BindingMapTests(unittest.TestCase):
    def test_exact_row_binding_and_fixed_missing_reason_conserve_oge(self):
        with TemporaryDirectory() as tmp:
            args = fixture(tmp, documents=("doc-1", "doc-2"))
            identity = audit_oge_row_identity(*args)
            candidate = (args[5], args[6], args[4])
            result = build_binding_map(
                Path(tmp), {"oge": candidate}, oge_identity_audit=identity,
                oge_disposition_audit=disposition(identity))
        self.assertEqual(result["qualified_rows"], 2)
        self.assertEqual(result["counts_by_source"],
                         {"oge": {"blocked": 1, "matched": 1}})
        self.assertEqual(result["matched_rows"][0]["canonical_transaction_id"],
                         "oge-278t:official-1")
        self.assertEqual(result["blocked_rows"][0]["reason"], "identity_ambiguous")
        self.assertFalse(result["projection_ready"])

    def test_unexplained_missing_row_fails_closed(self):
        with TemporaryDirectory() as tmp:
            args = fixture(tmp, documents=("doc-1", "doc-2"))
            identity = audit_oge_row_identity(*args)
            with self.assertRaisesRegex(ProjectionAuditError, "do not conserve"):
                build_binding_map(Path(tmp), {"oge": (args[5], args[6], args[4])},
                                  oge_identity_audit=identity,
                                  oge_disposition_audit=disposition(identity,
                                                                    include_missing=False))

    def test_wrong_fixed_candidate_blob_fails_closed(self):
        with TemporaryDirectory() as tmp:
            args = fixture(tmp)
            identity = audit_oge_row_identity(*args)
            with self.assertRaisesRegex(ProjectionAuditError, "blob mismatch"):
                build_binding_map(Path(tmp), {"oge": (args[5], "0" * 40, args[4])},
                                  oge_identity_audit=identity,
                                  oge_disposition_audit=disposition(identity))


if __name__ == "__main__":
    unittest.main()
