from copy import deepcopy
import unittest

from unison_snapshot.pipeline_qa.oge_missing_disposition import explain_missing_rows
from unison_snapshot.pipeline_qa.projection_audit import ProjectionAuditError

SHA = "a" * 64


def inputs():
    identity = {"bundle_run_id": "fixed", "counts": {"canonical_id_missing": 2},
                "missing_canonical_documents": {"miran-doc": 1, "duplicate-doc": 1},
                "missing_canonical_rows": [
                    {"document_id": "miran-doc", "source_sha256": "b" * 64,
                     "candidate_id": "miran-row", "extraction_id": "oge-278t:miran"},
                    {"document_id": "duplicate-doc", "source_sha256": SHA,
                     "candidate_id": "same-row", "extraction_id": "oge-278t:shared"}],
                "candidate_id_collisions": [{"candidate_id": "same-row", "scoped_rows": [
                    ["oge", "duplicate-doc", SHA, "same-row"],
                    ["oge", "peer-doc", SHA, "same-row"]]}]}
    qualification = {"reports": [
        {"document_id": "miran-doc", "document_reasons": ["identity_ambiguous"],
         "promoted_count": 0, "quarantined_count": 1,
         "quarantined": [{"extraction_id": "oge-278t:miran", "reasons": ["identity_ambiguous"]}]},
        {"document_id": "duplicate-doc", "document_reasons": [],
         "promoted_count": 0, "quarantined_count": 1,
         "quarantined": [{"extraction_id": "oge-278t:shared",
                          "reasons": ["extraction_id_invalid_or_duplicated"]}]}]}
    catalog = {"transactions": [
        {"access_method": "direct_pdf", "source_document_id": "miran-doc",
         "filer_name": "Miran, Stephen I", "agency": "Council of Economic Advisers",
         "position_title": "Chairman", "amended_label": None,
         "pending_final_oge_disposition": False},
        {"access_method": "direct_pdf", "source_document_id": "other-miran-doc",
         "filer_name": "Miran, Stephen I", "agency": "Federal Reserve",
         "position_title": "Governor"},
        {"access_method": "direct_pdf", "source_document_id": "duplicate-doc",
         "filer_name": "Sherman, Wendy R", "agency": "Department of State",
         "position_title": "Deputy Secretary", "amended_label": None,
         "pending_final_oge_disposition": False}]}
    candidate = {"transactions": [{"id": "oge-278t:shared", "filing_id": "peer-doc"}]}
    return identity, qualification, catalog, candidate


class MissingOgeDispositionTests(unittest.TestCase):
    def test_exact_quarantines_and_peer_are_explained_without_promotion(self):
        result = explain_missing_rows(*inputs())
        self.assertEqual(result["missing_rows"], 2)
        self.assertEqual(result["reason_counts"], {
            "identity_ambiguous": 1, "extraction_id_invalid_or_duplicated": 1})
        duplicate = next(doc for doc in result["documents"]
                         if doc["document_id"] == "duplicate-doc")
        self.assertEqual(duplicate["duplicate_peer_document_ids"], ["peer-doc"])
        self.assertEqual(result["production_action"], "none")
        self.assertFalse(result["projection_ready"])

    def test_missing_extraction_id_quarantine_fails_closed(self):
        values = list(inputs())
        values[1]["reports"][0]["quarantined"][0]["extraction_id"] = "different"
        with self.assertRaisesRegex(ProjectionAuditError, "unique quarantine"):
            explain_missing_rows(*values)

    def test_same_name_without_two_catalog_variants_is_not_proven_ambiguous(self):
        values = list(inputs())
        values[2]["transactions"].pop(1)
        with self.assertRaisesRegex(ProjectionAuditError, "lacks catalog variants"):
            explain_missing_rows(*values)

    def test_duplicate_requires_peer_at_same_source_hash(self):
        values = list(inputs())
        values[0]["candidate_id_collisions"][0]["scoped_rows"][1][2] = "c" * 64
        with self.assertRaisesRegex(ProjectionAuditError, "source-hash peer"):
            explain_missing_rows(*values)


if __name__ == "__main__":
    unittest.main()
