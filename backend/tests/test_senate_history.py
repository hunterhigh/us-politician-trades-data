from pathlib import Path
import hashlib
import json
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.senate_history import SenateHistoryError, plan_amendment_predecessors


SHA = "a" * 64
ROSTER_SHA = "c" * 64
IDENTITY_BINDING = "d" * 64
SOURCE_SHA = "e" * 64


def report(document_id: str, *, filer="Sample Senator", label="2025-05-15",
           portal="2025-05-16", amendment=None, access="electronic_ptr") -> dict:
    return {
        "document_id": document_id,
        "filer_name": filer,
        "report_label_date": label,
        "portal_listed_date": portal,
        "report_amendment_number": amendment,
        "access_method": access,
    }


def inputs(history_reports: list[dict]):
    amendment_id = "11111111-1111-4111-8111-111111111111"
    history = {
        "schema_version": "senate-efd-discovery/v1",
        "source_id": "senate_efd",
        "records_total": len(history_reports),
        "catalog_rows_covered": len(history_reports),
        "metadata": {
            "sha256": "b" * 64,
            "source_id": "senate_efd",
            "record_count": len(history_reports),
        },
        "reports": history_reports,
    }
    history_sha = hashlib.sha256(json.dumps(
        {key: value for key, value in history.items() if key != "metadata"},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    history["metadata"]["sha256"] = history_sha
    historical_identities = {
        "schema_version": "senate-efd-identities/v1",
        "catalog_sha256": history_sha,
        "roster_sha256": ROSTER_SHA,
        "report_count": len(history_reports),
        "identities": [{
            "document_id": item["document_id"],
            "person_id": ("senate:S000001" if item["filer_name"] == "Sample Senator"
                          else "senate:O000001"),
            "status": "matched_automatically",
            "match_class": "exact",
        } for item in history_reports],
    }
    status = {
        "schema_version": "senate-review-run/v1",
        "source_id": "senate_efd",
        "status": "catalog_ready_for_review",
        "catalog_sha256": SHA,
        "catalog_record_count": 1,
        "identity_binding_sha256": IDENTITY_BINDING,
        "identity_roster_sha256": ROSTER_SHA,
        "report_parser_version": "parser-v1",
        "report_evidence_count": 1,
    }
    audit = {
        "schema_version": "senate-efd-candidate-audit/v2",
        "catalog_sha256": SHA,
        "candidate_snapshot_id": "f" * 64,
        "identity_binding_sha256": IDENTITY_BINDING,
        "roster_sha256": ROSTER_SHA,
        "congress_roster_sha256": None,
        "parser_version": "parser-v1",
        "builder_version": "builder-v1",
        "input_transaction_count": 1,
        "quarantined_reports": [{
            "document_id": amendment_id,
            "reasons": ["amendment_relationship_pending"],
            "source_sha256": SOURCE_SHA,
        }],
    }
    identities = {
        "schema_version": "senate-efd-identities/v1",
        "catalog_sha256": SHA,
        "roster_sha256": ROSTER_SHA,
        "congress_roster_sha256": None,
        "report_count": 1,
        "identities": [{
            "document_id": amendment_id,
            "filer_name": "Sample Senator",
            "person_id": "senate:S000001",
            "status": "matched_automatically",
            "match_class": "exact",
        }],
    }
    extractions = [{
        "document_id": amendment_id,
        "report_amendment_number": 1,
        "report_title_date": "2025-05-15",
        "portal_listed_date": "2026-08-05",
        "source_sha256": SOURCE_SHA,
        "transactions": [{}],
    }]
    return history, historical_identities, status, audit, identities, extractions


class SenateHistoryTests(unittest.TestCase):
    def test_unique_official_catalog_predecessor_is_ready(self):
        predecessor = report("21111111-1111-4111-8111-111111111111")
        history, historical_identities, status, audit, identities, extractions = inputs([
            predecessor,
            report("31111111-1111-4111-8111-111111111111", filer="Other Senator"),
            report("41111111-1111-4111-8111-111111111111", amendment=1),
            report("51111111-1111-4111-8111-111111111111", access="paper_ptr"),
        ])
        result = plan_amendment_predecessors(
            history, historical_identities, status, audit, identities, extractions,
            expected_target_count=1)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["selected_document_ids"], [predecessor["document_id"]])
        self.assertEqual(result["targets"][0]["candidate_predecessors"], [predecessor])
        self.assertRegex(result["historical_identities_sha256"], r"^[0-9a-f]{64}$")

    def test_missing_or_ambiguous_predecessor_requires_attention(self):
        for reports, count in ([[], 0], ([
                report("21111111-1111-4111-8111-111111111111"),
                report("31111111-1111-4111-8111-111111111111"),
        ], 2)):
            with self.subTest(count=count):
                history, historical_identities, status, audit, identities, extractions = inputs(reports)
                result = plan_amendment_predecessors(
                    history, historical_identities, status, audit, identities, extractions,
                    expected_target_count=1)
                self.assertEqual(result["status"], "attention")
                self.assertIn("predecessor_not_unique", result["reasons"])
                self.assertEqual(result["targets"][0]["candidate_count"], count)

    def test_tampered_historical_discovery_fails_closed(self):
        history, historical_identities, status, audit, identities, extractions = inputs([
            report("21111111-1111-4111-8111-111111111111")])
        history["reports"][0]["filer_name"] = "Tampered Senator"
        with self.assertRaisesRegex(SenateHistoryError, "content hash does not match"):
            plan_amendment_predecessors(
                history, historical_identities, status, audit, identities, extractions,
                expected_target_count=1)

    def test_target_count_and_bound_inputs_fail_closed(self):
        history, historical_identities, status, audit, identities, extractions = inputs([
            report("21111111-1111-4111-8111-111111111111")])
        result = plan_amendment_predecessors(
            history, historical_identities, status, audit, identities, extractions,
            expected_target_count=2)
        self.assertEqual(result["status"], "attention")
        self.assertIn("target_count_mismatch", result["reasons"])
        identities["catalog_sha256"] = "c" * 64
        with self.assertRaises(SenateHistoryError):
            plan_amendment_predecessors(
                history, historical_identities, status, audit, identities, extractions,
                expected_target_count=1)


if __name__ == "__main__":
    unittest.main()
