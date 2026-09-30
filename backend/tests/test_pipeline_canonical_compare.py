import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from test_pipeline_projection_audit import bundle, row
from unison_snapshot.pipeline_qa.canonical_compare import (
    compare_fixed_candidates, git_blob_sha1,
)
from unison_snapshot.pipeline_qa.projection_audit import ProjectionAuditError

REVIEW_SHA = "c" * 40


def candidate(root, transactions):
    path = Path(root) / "candidate.json"
    path.write_text(json.dumps({"meta": {"is_demo": False}, "transactions": transactions}), encoding="utf-8")
    return path, git_blob_sha1(path.read_bytes()), REVIEW_SHA


def transaction(*, document="doc-1", tx_id="same-row", name="Fund"):
    return {"id": tx_id, "filing_id": document, "source_id": "oge",
            "source_url": "https://example.gov/report.pdf", "asset_name": name,
            "transaction_date": "2026-01-02", "transaction_type": "purchase",
            "amount_low": 1001, "amount_high": 15000, "owner": "Self"}


def full_row(document="doc-1"):
    result = row(document)
    values = {"asset_name": "Fund", "transaction_date": "2026-01-02",
              "transaction_type": "purchase", "amount_low": 1001,
              "amount_high": 15000, "owner": "Self"}
    template = result["observations"][0]
    result["observations"] = []
    for field, value in values.items():
        observation = {**template, "observation_id": field, "field": field,
                       "raw_value": value, "normalized_value": value,
                       "required_for_projection": field in result["required_projection_fields"]}
        result["observations"].append(observation)
    return result


class FixedCandidateComparisonTests(unittest.TestCase):
    def test_direct_id_match_reports_field_difference(self):
        with TemporaryDirectory() as tmp:
            bundle(tmp, [full_row()])
            spec = candidate(tmp, [transaction(name="Different")])
            result = compare_fixed_candidates(tmp, {"oge": spec})["sources"]["oge"]
        self.assertEqual(result["counts"], {"matched_direct_id": 1})
        self.assertEqual(result["direct_id_field_differences"], {"asset_name": 1})

    def test_exact_values_are_provisional_only(self):
        with TemporaryDirectory() as tmp:
            bundle(tmp, [full_row()])
            spec = candidate(tmp, [transaction(tx_id="canonical-1")])
            result = compare_fixed_candidates(tmp, {"oge": spec})
        self.assertEqual(result["sources"]["oge"]["counts"], {"provisional_value_match": 1})
        self.assertFalse(result["sources"]["oge"]["value_matches_are_bindings"])
        self.assertFalse(result["projection_ready"])

    def test_repeated_values_remain_ambiguous(self):
        with TemporaryDirectory() as tmp:
            bundle(tmp, [full_row()])
            spec = candidate(tmp, [transaction(tx_id="canonical-1"), transaction(tx_id="canonical-2")])
            result = compare_fixed_candidates(tmp, {"oge": spec})["sources"]["oge"]
        self.assertEqual(result["counts"], {"ambiguous_value_match": 1})

    def test_missing_official_document_is_not_reassigned(self):
        with TemporaryDirectory() as tmp:
            bundle(tmp, [full_row()])
            spec = candidate(tmp, [transaction(document="other", tx_id="canonical-1")])
            result = compare_fixed_candidates(tmp, {"oge": spec})["sources"]["oge"]
        self.assertEqual(result["counts"], {"missing_canonical_document": 1})

    def test_review_candidate_blob_must_match_pinned_hash(self):
        with TemporaryDirectory() as tmp:
            bundle(tmp, [full_row()])
            path, _, commit = candidate(tmp, [transaction()])
            with self.assertRaisesRegex(ProjectionAuditError, "blob mismatch"):
                compare_fixed_candidates(tmp, {"oge": (path, "0" * 40, commit)})


if __name__ == "__main__":
    unittest.main()
