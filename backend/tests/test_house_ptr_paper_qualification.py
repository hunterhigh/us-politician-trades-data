import unittest
import json
import tempfile
from pathlib import Path

from unison_snapshot.house_ptr_paper_qualification import qualify_paper_observation
from unison_snapshot.house_candidate import build_house_candidate
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_ptr_amount_header import LABELS, evaluate_amount_header_pages


def cell(text="", label="", ink=0):
    return {"raw_text": text, "label": label, "interior_dark_fraction": ink}


class HousePaperQualificationTests(unittest.TestCase):
    def setUp(self):
        self.sha = "a" * 64
        self.metadata = {"sha256": self.sha, "document_id": "9116141",
                         "filed_date": "2026-06-10", "filing_year": 2026,
                         "source_id": "house_clerk", "filing_type": "P",
                         "filer_name": "Hon. Michael T. McCaul", "state_district": "TX10",
                         "source_url": "https://disclosures-clerk.house.gov/public_disc/"
                                       "ptr-pdfs/2026/9116141.pdf"}
        self.identity = {"document_id": "9116141", "person_id": "house:M001157"}
        self.observation = {
            "schema_version": "house-ptr-cell-observation-shadow/v3",
            "qualification_status": "unverified_shadow_only", "source_sha256": self.sha,
            "pages": [{"page": 1, "rows": [
                {"physical_band_index": 1, "disposition": "data_candidate",
                 "band": [.4, .45], "cells": {
                     "owner": cell("SP"), "asset": cell("EXAMPLE INC"),
                     "event_date": cell("05/01/2026"),
                     "notification_date": cell("05/03/2026"),
                     "direction": [cell(label="purchase", ink=.12),
                                   cell(label="sale", ink=.001)],
                     "amount": [cell(label=chr(ord("A") + index),
                                     ink=.13 if index == 0 else .001)
                                for index in range(11)]}},
                {"physical_band_index": 2, "disposition": "unknown",
                 "band": [.45, .5], "cells": None},
                {"physical_band_index": 3, "disposition": "blank",
                 "band": [.5, .55], "cells": None}]}]}

    def test_full_raw_fields_still_cannot_enter_formal_transactions(self):
        result = qualify_paper_observation(self.observation, self.metadata, self.identity)
        self.assertEqual(result["transactions"], [])
        self.assertEqual(result["qualification"]["quarantined_count"], 2)
        self.assertFalse(result["qualification"]["production_eligible"])
        self.assertEqual(result["document_disposition"]["physical_counts"],
                         {"data_candidate": 1, "unknown": 1, "blank": 1})
        self.assertIn("paper_source_fields_unverified", result["quarantined"][0]["reasons"])
        self.assertEqual(result["quarantined"][0]["raw_fields"]["amount_letter"], "A")
        self.assertEqual(result["quarantined"][0]["source_field_evidence"]["asset"]
                         ["raw_text"], "EXAMPLE INC")
        self.assertEqual(result["quarantined"][0]["source_quality_flags"], [])
        self.assertIn("physical_row_disposition_unresolved",
                      result["quarantined"][1]["reasons"])
        self.assertNotIn("id", result["quarantined"][0])
        pending = result["pending_transaction_projection"]
        self.assertEqual(pending["row_count"], 1)
        self.assertEqual(pending["rows"][0]["owner"], "Spouse")
        self.assertEqual(pending["rows"][0]["transaction_date"], "2026-05-01")
        self.assertNotIn("id", pending["rows"][0])

    def test_peer_filing_dates_add_observation_without_promoting_rows(self):
        def official(document_id, filed_date, sha):
            return {"document_id": document_id, "sha256": sha,
                    "filed_date": filed_date, "filing_year": 2026,
                    "source_id": "house_clerk", "filing_type": "P",
                    "filer_name": "Hon. Michael T. McCaul", "state_district": "TX10",
                    "source_url": "https://disclosures-clerk.house.gov/public_disc/"
                                  f"ptr-pdfs/2026/{document_id}.pdf"}
        target = official("9116141", "2026-06-10", self.sha)
        peer = official("9115820", "2026-04-04", "b" * 64)
        result = qualify_paper_observation(self.observation, target, self.identity,
                                           peer_metadata=[peer])
        temporal = result["temporal_relation_observation"]
        self.assertEqual(temporal["observed_data_slot_count"], 1)
        self.assertEqual(temporal["peers"][0]["conditional_chronology_exclusion_count"], 1)
        self.assertEqual(temporal["peers"][0]["relationship_unresolved_count"], 0)
        self.assertEqual(result["transactions"], [])
        self.assertEqual(result["qualification"]["quarantined_count"], 2)

    def test_identity_and_source_must_match(self):
        with self.assertRaisesRegex(ValueError, "hashes differ"):
            qualify_paper_observation(self.observation,
                                      {**self.metadata, "sha256": "b" * 64}, self.identity)
        with self.assertRaisesRegex(ValueError, "identity does not match"):
            qualify_paper_observation(self.observation, self.metadata,
                                      {**self.identity, "document_id": "other"})

    def test_duplicate_slot_fails_closed(self):
        self.observation["pages"][0]["rows"].append(
            self.observation["pages"][0]["rows"][1].copy())
        with self.assertRaisesRegex(ValueError, "band order is invalid"):
            qualify_paper_observation(self.observation, self.metadata, self.identity)

    def test_correlated_header_adds_amount_hint_only_to_quarantine(self):
        self.observation["pages"].append({"page": 2, "rows": [
            {"physical_band_index": 1, "disposition": "blank",
             "band": [.4, .45], "cells": None}]})
        printed = "\n".join(["$1,000-$15,000", *LABELS.values()])
        header = evaluate_amount_header_pages(self.sha, [
            {"page": page, "status": "header_ocr_observed",
             "raw_header_text": printed} for page in (1, 2)])
        result = qualify_paper_observation(self.observation, self.metadata,
                                           self.identity, amount_header=header)
        amount = result["quarantined"][0]["amount_range_observation"]
        self.assertEqual((amount["letter"], amount["low"], amount["high"]),
                         ("A", 1000, 15000))
        self.assertEqual(result["amount_header_observation"]["source_sha256"], self.sha)
        self.assertEqual(result["transactions"], [])
        self.assertFalse(result["qualification"]["production_eligible"])
        header["ranges"]["A"]["low"] = 1
        with self.assertRaisesRegex(ValueError, "interpretation changed"):
            qualify_paper_observation(self.observation, self.metadata,
                                      self.identity, amount_header=header)

    def test_section_heading_is_structural_and_not_a_quarantined_transaction(self):
        rows = self.observation["pages"][0]["rows"]
        rows[1]["cells"] = {
            "asset": {"raw_text": "FAMILY TRUST", "crop_sha256": "b" * 64},
            "owner": {"raw_text": ""}, "event_date": {"raw_text": ""},
            "notification_date": {"raw_text": ""},
            "direction": [{"interior_dark_fraction": 0}],
            "amount": [{"interior_dark_fraction": 0}],
        }
        more = json.loads(json.dumps(rows[0]))
        more["physical_band_index"] = 3
        rows.insert(2, more)
        rows[3]["physical_band_index"] = 4
        result = qualify_paper_observation(self.observation, self.metadata, self.identity)
        self.assertEqual(len(result["structural_observations"]), 1)
        self.assertEqual(len(result["quarantined"]), 2)
        self.assertEqual(result["quarantined"][1]["account_section_observation"][
            "section_title_raw"], "FAMILY TRUST")
        self.assertEqual(result["transactions"], [])

    def test_same_section_repeated_physical_rows_stay_separate_without_extra_blocker(self):
        rows = self.observation["pages"][0]["rows"]
        rows[0] = {"physical_band_index": 1, "disposition": "unknown",
                   "band": [.4, .45], "cells": {
                       "asset": {"raw_text": "FAMILY TRUST", "crop_sha256": "b" * 64},
                       "owner": {"raw_text": ""}, "event_date": {"raw_text": ""},
                       "notification_date": {"raw_text": ""},
                       "direction": [{"interior_dark_fraction": 0}],
                       "amount": [{"interior_dark_fraction": 0}]}}
        data = json.loads(json.dumps(self.observation["pages"][0]["rows"][1]))
        data["disposition"] = "data_candidate"
        data["cells"] = {
            "owner": cell("SP"), "asset": cell("CALIFORNIA ST GO"),
            "event_date": cell("05/08/2026"),
            "notification_date": cell("06/05/2026"),
            "direction": [cell(label="purchase", ink=.12),
                          cell(label="sale", ink=.001)],
            "amount": [cell(label=chr(ord("A") + index),
                            ink=.13 if index == 2 else .001)
                       for index in range(11)],
        }
        rows[1] = data
        other = json.loads(json.dumps(data))
        other["physical_band_index"] = 3
        rows[2] = other
        result = qualify_paper_observation(self.observation, self.metadata, self.identity)
        self.assertEqual(len(result["quarantined"]), 2)
        self.assertEqual(result["duplicate_observation"]["repeated_group_count"], 1)
        self.assertTrue(all(len(row["possible_repeated_with"]) == 1
                            for row in result["quarantined"]))
        self.assertTrue(all(row["reasons"] == ["paper_source_fields_unverified"]
                            for row in result["quarantined"]))
        self.assertEqual(result["transactions"], [])

    def test_existing_candidate_builder_counts_quarantine_without_new_transaction(self):
        result = qualify_paper_observation(self.observation, self.metadata, self.identity)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "house_clerk/qualifications/2026/9116141/source.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(result), encoding="utf-8")
            status = root / "status/house_clerk.json"
            status.parent.mkdir(parents=True)
            status.write_text(json.dumps({
                "parser_commit": "a" * 40, "pending_parse_count": 0,
                "qualification_count": 1, "qualified_transaction_count": 0,
                "quarantined_row_count": 2, "evidence_count": 1,
                "extracted_count": 1, "failure_count": 0,
            }), encoding="utf-8")
            base = {"meta": {"is_demo": False}, "people": [],
                    "transactions": [], "reported_holdings": [],
                    "security_market_data": [],
                    "source_health": [{"source_id": "house_clerk"}]}
            source_state = {"status": "ok", "run_at": "2026-10-12T00:00:00Z",
                            "counts": {"archived": 1, "pending": 0}}
            candidate = build_house_candidate(root, source_state, base)
            self.assertEqual(candidate["transactions"], [])
            self.assertEqual(candidate["people"], [])
            self.assertIn("2 rows quarantined", candidate["source_health"][0]["detail"])
            self.assertIn("1 paper transaction projections pending",
                          candidate["source_health"][0]["detail"])
            legacy = json.loads(json.dumps(result))
            legacy.pop("pending_transaction_projection")
            target.write_text(json.dumps(legacy), encoding="utf-8")
            old_candidate = build_house_candidate(root, source_state, base)
            self.assertEqual(old_candidate["transactions"], [])
            self.assertNotIn("paper transaction projections pending",
                             old_candidate["source_health"][0]["detail"])
            result["pending_transaction_projection"]["rows"][0][
                "source_quality_flags"] = ["invented_quality_signal"]
            target.write_text(json.dumps(result), encoding="utf-8")
            with self.assertRaisesRegex(HouseIndexError, "conserve source slots"):
                build_house_candidate(root, source_state, base)
            result["pending_transaction_projection"]["rows"][0][
                "source_quality_flags"] = result["quarantined"][0][
                    "source_quality_flags"]
            result["pending_transaction_projection"]["rows"][0]["observation_key"] = "wrong"
            target.write_text(json.dumps(result), encoding="utf-8")
            with self.assertRaisesRegex(HouseIndexError, "conserve source slots"):
                build_house_candidate(root, source_state, base)
