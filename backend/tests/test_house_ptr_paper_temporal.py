"""Chronology observations never qualify paper PTR transactions."""

from __future__ import annotations

from copy import deepcopy
import unittest

from unison_snapshot.house_ptr import QUALIFICATION_SCHEMA
from unison_snapshot.house_ptr_paper_qualification import PARSER_VERSION
from unison_snapshot.house_ptr_paper_temporal import audit_paper_temporal_relations


SHA = "a" * 64


def metadata(document_id="9116141", filed="2026-06-10", sha=SHA):
    return {
        "document_id": document_id, "sha256": sha,
        "filed_date": filed, "filing_year": 2026,
        "source_id": "house_clerk", "filing_type": "P",
        "filer_name": "Hon. Michael T. McCaul", "state_district": "TX10",
        "source_url": "https://disclosures-clerk.house.gov/public_disc/"
                      f"ptr-pdfs/2026/{document_id}.pdf",
    }


def qualification():
    return {
        "schema_version": QUALIFICATION_SCHEMA,
        "parser_version": PARSER_VERSION,
        "document_id": "9116141", "source_sha256": SHA,
        "qualification": {"production_eligible": False},
        "transactions": [], "quarantined": [
            {"observation_key": f"paper-slot:{SHA}:1:1",
             "raw_fields": {"event_date": "2026-05-01",
                            "event_date_status": "ocr_date_shape_unverified"}},
            {"observation_key": f"paper-slot:{SHA}:1:2",
             "raw_fields": {"event_date": "2026-05-10",
                            "event_date_status": "ocr_date_shape_unverified"}},
            {"observation_key": f"paper-slot:{SHA}:1:3",
             "raw_fields": {"event_date": None,
                            "event_date_status": "not_exact_date_shape"}},
        ],
    }


class HousePaperTemporalTests(unittest.TestCase):
    def test_prior_filing_only_conditionally_excludes_later_event_shapes(self):
        result = audit_paper_temporal_relations(
            qualification(), metadata(),
            [metadata("9115820", "2026-05-04", "b" * 64),
             metadata("9116211", "2026-07-08", "c" * 64)])
        self.assertEqual(result["qualification_status"], "read_only_unverified_dates")
        self.assertEqual(result["observed_data_slot_count"], 3)
        self.assertEqual(result["observed_event_date_count"], 2)
        self.assertEqual(result["peers"][0]["conditional_chronology_exclusion_count"], 1)
        self.assertEqual(result["peers"][0]["relationship_unresolved_count"], 2)
        self.assertEqual(result["peers"][1]["conditional_chronology_exclusion_count"], 0)
        self.assertEqual(result["peers"][1]["relationship_unresolved_count"], 3)

    def test_rejects_cross_filer_and_duplicate_peer(self):
        peer = metadata("9115820", "2026-05-04", "b" * 64)
        other = deepcopy(peer)
        other["filer_name"] = "Hon. Another Member"
        with self.assertRaisesRegex(ValueError, "another filer"):
            audit_paper_temporal_relations(qualification(), metadata(), [other])
        with self.assertRaisesRegex(ValueError, "duplicated"):
            audit_paper_temporal_relations(qualification(), metadata(), [peer, peer])

    def test_unverified_date_cannot_be_relabeled_as_verified_fact(self):
        q = qualification()
        q["quarantined"][0]["raw_fields"]["event_date_status"] = "verified"
        with self.assertRaisesRegex(ValueError, "unverified observation"):
            audit_paper_temporal_relations(q, metadata(), [])
        q = qualification()
        q["transactions"] = [{"id": "house-ptr:unverified"}]
        with self.assertRaisesRegex(ValueError, "inputs are invalid"):
            audit_paper_temporal_relations(q, metadata(), [])


if __name__ == "__main__":
    unittest.main()
