from copy import deepcopy
import unittest

from unison_snapshot.pipeline_ledger import (
    CANDIDATE_ROW_SCHEMA,
    OBSERVATION_SCHEMA,
    RUN_MANIFEST_SCHEMA,
    LedgerValidationError,
    idempotency_key,
    validate_candidate_row,
    validate_observation,
    validate_run_manifest,
)


SOURCE_SHA = "a" * 64
CODE_SHA = "b" * 40


def _key(*, parser_version="house-ptr-2026-04", rules_version="eligibility-v1"):
    return idempotency_key(
        source_id="house_clerk", source_sha256=SOURCE_SHA,
        parser_id="house-ptr", parser_version=parser_version,
        rules_version=rules_version, layout_fingerprint="electronic-ptr-v2",
    )


def _location():
    return {"page": 2, "row_locator": "row-17", "kind": "table_row",
            "bbox": {"x0": 12, "y0": 20, "x1": 280, "y1": 42,
                     "unit": "pdf_points", "page_width": 612, "page_height": 792}}


def _observation(field, raw, normalized, *, selected=True, method="embedded_text"):
    return {"schema_version": OBSERVATION_SCHEMA,
            "observation_id": f"obs:{field}:{'selected' if selected else 'alternate'}",
            "field": field, "raw_value": raw, "normalized_value": normalized,
            "method": method, "confidence": 1.0 if method == "embedded_text" else 0.8,
            "selected": selected, "required_for_projection": field in {"transaction_date", "amount_low"},
            "locations": [_location()], "conditions": {}}


def _candidate():
    return {"schema_version": CANDIDATE_ROW_SCHEMA,
            "candidate_id": "house-ptr:sha-row-17", "run_id": "run-20260929-001",
            "source": {"source_id": "house_clerk", "document_id": "20035420",
                       "source_url": "https://example.gov/official.pdf",
                       "source_sha256": SOURCE_SHA},
            "parser": {"parser_id": "house-ptr", "parser_version": "house-ptr-2026-04",
                       "rules_version": "eligibility-v1",
                       "layout_fingerprint": "electronic-ptr-v2"},
            "idempotency_key": _key(), "disposition": "qualified", "reasons": [],
            "unresolved_conflicts": [], "required_projection_fields": ["transaction_date", "amount_low"],
            "evidence_locations": [_location()],
            "observations": [_observation("transaction_date", "08/14/2026", "2026-08-14"),
                             _observation("amount_low", "$1,001", 1001)]}


def _manifest():
    document = {"source_id": "house_clerk", "document_id": "20035420",
                "source_url": "https://example.gov/official.pdf", "source_sha256": SOURCE_SHA,
                "parser_id": "house-ptr", "parser_version": "house-ptr-2026-04",
                "rules_version": "eligibility-v1", "layout_fingerprint": "electronic-ptr-v2",
                "idempotency_key": _key(), "disposition": "parsed"}
    no_rows = {**document, "document_id": "20035421", "source_sha256": "c" * 64,
               "idempotency_key": idempotency_key(
                   source_id="house_clerk", source_sha256="c" * 64, parser_id="house-ptr",
                   parser_version="house-ptr-2026-04", rules_version="eligibility-v1",
                   layout_fingerprint="electronic-ptr-v2"), "disposition": "no_rows"}
    return {"schema_version": RUN_MANIFEST_SCHEMA, "run_id": "run-20260929-001",
            "workflow": "house-review", "workflow_run_id": "12345", "trigger": "workflow_run",
            "code_commit": CODE_SHA, "started_at": "2026-09-29T01:00:00Z",
            "completed_at": "2026-09-29T01:02:00Z", "source_scope": ["house_clerk"],
            "documents": [document, no_rows],
            "counts": {"discovered_documents": 2, "archived_documents": 2,
                       "parsed_documents": 1, "failed_documents": 0, "no_row_documents": 1,
                       "excluded_documents": 0, "qualified_rows": 2, "quarantined_rows": 1,
                       "excluded_rows": 0, "unrecognized_rows": 0},
            "accounted_rows": 3,
            "outputs": [{"artifact_type": "candidate_rows", "path": "rows/candidates.json",
                         "sha256": "d" * 64}],
            "downstream_run_ids": []}


class PipelineLedgerTests(unittest.TestCase):
    def test_idempotency_key_is_stable_and_version_bound(self):
        self.assertEqual(_key(), _key())
        self.assertNotEqual(_key(), _key(parser_version="house-ptr-2026-05"))
        self.assertNotEqual(_key(), _key(rules_version="eligibility-v2"))

    def test_valid_observation_and_candidate_row(self):
        self.assertEqual(validate_observation(_observation("asset_name", "Example Corp", "Example Corp")),
                         _observation("asset_name", "Example Corp", "Example Corp"))
        self.assertEqual(validate_candidate_row(_candidate())["disposition"], "qualified")

    def test_rejects_candidate_key_not_bound_to_parser_inputs(self):
        candidate = _candidate()
        candidate["parser"]["parser_version"] = "house-ptr-2026-05"
        with self.assertRaisesRegex(LedgerValidationError, "idempotency_key"):
            validate_candidate_row(candidate)

    def test_rejects_qualified_row_with_unresolved_conflict(self):
        candidate = _candidate()
        candidate["unresolved_conflicts"] = ["transaction_date"]
        with self.assertRaisesRegex(LedgerValidationError, "unresolved field conflicts"):
            validate_candidate_row(candidate)

    def test_rejects_multiple_selected_values_for_one_field(self):
        candidate = _candidate()
        duplicate = _observation("transaction_date", "08/15/2026", "2026-08-15")
        duplicate["observation_id"] = "obs:transaction_date:second-value"
        candidate["observations"].append(duplicate)
        with self.assertRaisesRegex(LedgerValidationError, "only one value per field"):
            validate_candidate_row(candidate)

    def test_rejects_duplicate_observation_ids(self):
        candidate = _candidate()
        candidate["observations"][1]["observation_id"] = candidate["observations"][0]["observation_id"]
        with self.assertRaisesRegex(LedgerValidationError, "duplicate observation_id"):
            validate_candidate_row(candidate)

    def test_quarantined_row_requires_reason(self):
        candidate = _candidate()
        candidate.update(disposition="quarantined", reasons=[])
        with self.assertRaisesRegex(LedgerValidationError, "machine-readable reason"):
            validate_candidate_row(candidate)

    def test_qualified_row_requires_every_frontend_required_value(self):
        candidate = _candidate()
        candidate["observations"][0]["normalized_value"] = None
        with self.assertRaisesRegex(LedgerValidationError, "transaction_date"):
            validate_candidate_row(candidate)

    def test_rejects_invalid_evidence_box(self):
        observation = _observation("transaction_type", "P", "purchase", method="ocr")
        observation["locations"][0]["bbox"]["x1"] = 900
        with self.assertRaisesRegex(LedgerValidationError, "fit within"):
            validate_observation(observation)

    def test_valid_manifest_accounts_for_all_document_and_row_dispositions(self):
        self.assertEqual(validate_run_manifest(_manifest())["accounted_rows"], 3)

    def test_manifest_rejects_unaccounted_row(self):
        manifest = _manifest()
        manifest["accounted_rows"] = 2
        with self.assertRaisesRegex(LedgerValidationError, "all row dispositions"):
            validate_run_manifest(manifest)

    def test_manifest_rejects_document_disposition_count_mismatch(self):
        manifest = _manifest()
        manifest["counts"]["parsed_documents"] = 2
        with self.assertRaisesRegex(LedgerValidationError, "document dispositions"):
            validate_run_manifest(manifest)

    def test_manifest_rejects_artifact_path_escape(self):
        manifest = _manifest()
        manifest["outputs"][0]["path"] = "../outside.json"
        with self.assertRaisesRegex(LedgerValidationError, "stay inside"):
            validate_run_manifest(manifest)

    def test_manifest_rejects_windows_artifact_path_escape(self):
        manifest = _manifest()
        manifest["outputs"][0]["path"] = "C:\\outside.json"
        with self.assertRaisesRegex(LedgerValidationError, "stay inside"):
            validate_run_manifest(manifest)

    def test_manifest_rejects_naive_timestamps(self):
        manifest = deepcopy(_manifest())
        manifest["started_at"] = "2026-09-29T01:00:00"
        with self.assertRaisesRegex(LedgerValidationError, "include a timezone"):
            validate_run_manifest(manifest)

    def test_manifest_rejects_invalid_fixed_input_commit(self):
        manifest = _manifest()
        manifest["evidence_commit"] = "short"
        with self.assertRaisesRegex(LedgerValidationError, "evidence_commit"):
            validate_run_manifest(manifest)

    def test_manifest_source_run_must_bind_to_hashed_output(self):
        manifest = _manifest()
        manifest["source_runs"] = [{"run_id": "source-1", "workflow": "oge",
                                    "manifest_path": "sources/missing.json",
                                    "manifest_sha256": "e" * 64}]
        with self.assertRaisesRegex(LedgerValidationError, "listed output"):
            validate_run_manifest(manifest)

    def test_manifest_requires_document_sources_in_run_scope(self):
        manifest = _manifest()
        manifest["source_scope"] = ["senate_efd"]
        with self.assertRaisesRegex(LedgerValidationError, "included in source_scope"):
            validate_run_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
