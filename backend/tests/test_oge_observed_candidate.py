"""Synthetic consumer integration; these cases do not measure OCR accuracy."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_oge_candidate import base, catalog, direct, extraction, DOCUMENT_ID
from test_oge_content_relations import saved, column_read
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.oge_transaction_adapter import materialize_oge_278t_observations, adapt_oge_278t_extraction
from unison_snapshot.disclosure_status_oge import _apply_audit
from unison_snapshot.oge_278t_fields import observe_content_rows
from unison_snapshot.builder import build


ROW = ["2", "ALPHA", "purchase", "05/02/2025", "No", "$1,001 - $15,000"]
OWNER = ["", "Filer assets", "", "", "", ""]


def original():
    result = extraction()
    cells = ["1", "Example Inc. (EXM)", "Purchase", "05/01/2025", "", "$1,001 - $15,000"]
    result["transactions"][0]["cells"] = cells
    result["source_rows"] = [{"page_number": 1, "cells": cells}]
    return result


def bundle(rows=None):
    return {"observations": [saved(rows if rows is not None else [OWNER, ROW])],
            "document_page_count": 3}


def candidate(inputs=None, old=None):
    return build_oge_candidate(catalog(), [old or original()], base(),
                               data_cutoff_at="2025-06-04T00:00:00Z",
                               saved_observations_by_document={DOCUMENT_ID: inputs or bundle()})


class ObservedCandidateTests(unittest.TestCase):
    def test_actual_candidate_receives_new_fact_and_preserves_old_id(self):
        old = original()
        before = deepcopy(old)
        result, audit = candidate(old=old)
        self.assertEqual(old, before)
        self.assertEqual(len(result["transactions"]), 2)
        self.assertIn(old["transactions"][0]["extraction_id"], {r["id"] for r in result["transactions"]})
        row = next(r for r in result["transactions"] if r["asset_name"] == "ALPHA")
        self.assertEqual(row["transaction_date"], "2025-05-02")
        self.assertEqual(row["amount_low"], 1001)
        self.assertEqual(row["owner"], "Self")
        receipt = audit["reports"][0]["observation_receipt"]
        self.assertFalse(receipt["coverage_complete"])
        self.assertEqual(receipt["unobserved_pages"], [1, 3])

    def test_owner_absence_does_not_inherit_parser_default(self):
        result, audit = candidate(bundle([ROW]))
        self.assertEqual(len(result["transactions"]), 2)
        alpha = next(row for row in result["transactions"] if row["asset_name"] == "ALPHA")
        self.assertEqual(alpha["owner"], "Undisclosed")
        receipt = audit["reports"][0]["observation_receipt"]["rows"][0]
        self.assertEqual(receipt["effective_account_context"]["status"], "not_observed")
        self.assertIn("coverage_warning", receipt["effective_account_context"])
        # Missing optional account title is independent of approved unknown owner.
        self.assertTrue(build(result, generated_at="2025-06-04T00:00:00Z", allow_production=True).manifest["snapshot_id"])

    def test_owner_heading_conflict_never_becomes_undisclosed_fallback(self):
        for first, second in [("Filer assets", "Spouse assets"), ("Spouse assets", "Filer assets")]:
            with self.subTest(primary=first):
                inputs = bundle([["", first, "", "", "", ""], ROW])
                decoded = observe_content_rows(**inputs)
                readings = decoded["field_observations"]["pages"][0]["slots"][0]["field_readings"]["description"]["observations"]
                other = deepcopy(readings[0])
                other["shape"]["value"] = second
                readings.append(other)
                with patch("unison_snapshot.oge_278t_fields.observe_content_rows", return_value=decoded):
                    result, audit = candidate(inputs)
                self.assertEqual(len(result["transactions"]), 1)
                self.assertIn("owner_heading_observation_unresolved", audit["reports"][0]["observation_receipt"]["rows"][-1]["reasons"])

    def test_same_legacy_row_keeps_exact_id_without_duplicate(self):
        old = original()
        cells = old["transactions"][0]["cells"]
        data = saved([cells])
        data["pages"][0]["page_number"] = 1
        result, audit = candidate({"observations": [data], "document_page_count": 2}, old)
        self.assertEqual([r["id"] for r in result["transactions"]], [old["transactions"][0]["extraction_id"]])
        self.assertEqual(audit["reports"][0]["observation_receipt"]["rows"][0]["disposition"], "retained_legacy")

    def test_changed_raw_legacy_row_is_not_a_second_formal_record(self):
        old = original()
        old["transactions"][0]["page_number"] = 2
        old["transactions"][0]["row_number"] = 2
        old["source_rows"][0]["page_number"] = 2
        result, audit = candidate(old=old)
        self.assertEqual(len(result["transactions"]), 1)
        self.assertIn("legacy_row_correspondence_unresolved", audit["reports"][0]["observation_receipt"]["rows"][1]["reasons"])

    def test_new_conflicting_read_does_not_erase_old_fact_or_claim_complete(self):
        old = original()
        old["transactions"][0]["page_number"] = 2
        old["source_rows"][0]["page_number"] = 2
        data = saved([old["transactions"][0]["cells"]])
        reading = column_read(data, "transaction_date", "05/03/2025")
        result, audit = candidate({"observations": [data], "document_page_count": 2,
                                   "secondary_observations": [reading]}, old)
        self.assertEqual(len(result["transactions"]), 1)
        receipt = audit["reports"][0]["observation_receipt"]
        self.assertFalse(receipt["coverage_complete"])
        self.assertIn("retained_legacy_with_observation_conflict", receipt["rows"][0]["reasons"])

    def test_both_number_and_name_change_cannot_hide_legacy_overlap(self):
        old = original()
        old["transactions"][0]["page_number"] = 2
        old["transactions"][0]["row_number"] = 99
        old["source_rows"][0]["page_number"] = 2
        result, audit = candidate(old=old)
        self.assertEqual(len(result["transactions"]), 1)
        self.assertIn("legacy_row_correspondence_unresolved", audit["reports"][0]["observation_receipt"]["rows"][1]["reasons"])

    def test_unknown_geometry_is_not_complete_and_breaks_owner_scope(self):
        first = saved([OWNER])
        first["pages"][0]["page_number"] = 1
        unknown = saved()
        unknown["pages"][0]["column_geometry"] = "unknown"
        last = saved([ROW])
        last["pages"][0]["page_number"] = 3
        result, audit = candidate({"observations": [first, unknown, last], "document_page_count": 3})
        self.assertEqual(len(result["transactions"]), 1)
        receipt = audit["reports"][0]["observation_receipt"]
        self.assertEqual(receipt["geometry_unknown_pages"], [2])
        self.assertFalse(receipt["coverage_complete"])
        self.assertIn("owner_heading_observation_unresolved", receipt["rows"][-1]["reasons"])

    def test_unresolved_rows_reach_existing_quarantine_consumer(self):
        row = ROW.copy()
        row[3] = "?"
        result, audit = candidate(bundle([row]))
        self.assertEqual(len(result["transactions"]), 1)
        self.assertEqual(audit["reports"][0]["quarantined_count"], 1)
        self.assertIn("transaction_date_reading_unresolved", audit["reports"][0]["quarantined"][0]["reasons"])

    def test_arbitrary_numeric_pair_cannot_become_official_range(self):
        row = [*ROW[:5], "$1,002 - $15,000"]
        result, audit = candidate(bundle([OWNER, row]))
        self.assertEqual(len(result["transactions"]), 1)
        self.assertIn("amount_range_not_qualified", audit["reports"][0]["observation_receipt"]["rows"][1]["reasons"])

    def test_conflicting_secondary_reading_does_not_promote(self):
        data = saved([OWNER, ROW])
        secondary = column_read(saved([ROW]), "transaction_date", "05/03/2025")
        empty = column_read(saved([OWNER]), "transaction_date", "")["pages"][0]["slots"][0]
        slot = secondary["pages"][0]["slots"][0]
        slot["slot_index"] = 2
        slot["band_pdf_points"] = [115, 130]
        for word in slot["column_detections"]:
            word["bbox_pdf_points"][1] += 15
            word["bbox_pdf_points"][3] += 15
        secondary["pages"][0]["slots"].insert(0, empty)
        secondary["pages"][0]["slot_count"] = 2
        secondary["summary"]["observed_slots"] = 2
        inputs = {"observations": [data], "document_page_count": 3,
                  "secondary_observations": [secondary]}
        result, audit = candidate(inputs)
        self.assertEqual(len(result["transactions"]), 1)
        self.assertIn("transaction_date_reading_unresolved", audit["reports"][0]["observation_receipt"]["rows"][1]["reasons"])

    def test_wrong_source_and_serialized_new_version_are_rejected(self):
        inputs = bundle()
        inputs["observations"][0]["source_sha256"] = "c" * 64
        with self.assertRaises(OgeCatalogError):
            candidate(inputs)
        old = materialize_oge_278t_observations(original(), direct(), **bundle())
        result, audit = build_oge_candidate(catalog(), [old], base(), data_cutoff_at="2025-06-04T00:00:00Z")
        self.assertFalse(result["transactions"])
        self.assertIn("extraction_contract_invalid", audit["reports"][0]["document_reasons"])

    def test_unknown_coverage_never_becomes_complete_or_zero(self):
        _, audit = candidate()
        record = {"reason_codes": []}
        _apply_audit(record, audit["reports"][0])
        self.assertEqual(record["qualification_state"], "partial")
        report = deepcopy(audit["reports"][0])
        report.update(promoted_count=0, quarantined_count=0)
        _apply_audit(record, report)
        self.assertEqual(record["qualification_state"], "unknown")

    def test_unverified_page_count_cannot_claim_whole_document(self):
        old = original()
        data = saved([old["transactions"][0]["cells"]])
        data["pages"][0]["page_number"] = 1
        _, audit = candidate({"observations": [data], "document_page_count": 1}, old)
        receipt = audit["reports"][0]["observation_receipt"]
        self.assertEqual(receipt["unobserved_pages"], [])
        self.assertEqual(receipt["page_count_basis"], "declared_only")
        self.assertFalse(receipt["coverage_complete"])

    def test_old_row_outside_declared_page_count_is_rejected(self):
        old = original()
        old["source_rows"][0]["page_number"] = 9
        old["transactions"][0]["page_number"] = 9
        with self.assertRaisesRegex(OgeCatalogError, "page count"):
            candidate(old=old)

    def test_old_empty_cache_and_nonempty_reference_have_distinct_inventory_meanings(self):
        old = original()
        del old["source_rows"]
        before = deepcopy(old)
        result, audit = candidate(old=old)
        self.assertEqual(len(result["transactions"]), 1)  # Only the independently observed new row.
        self.assertEqual(old, before)
        self.assertNotIn(old["transactions"][0]["extraction_id"], {row["id"] for row in result["transactions"]})
        self.assertTrue(audit["reports"][0]["observation_receipt"]["legacy_normalized_not_sent_to_qualification"])
        self.assertEqual(audit["reports"][0]["observation_receipt"]["legacy_normalized_transaction_count"], 1)
        old["transactions"] = []
        result, audit = candidate(old=old)
        self.assertEqual(len(result["transactions"]), 1)
        self.assertNotIn("source_rows", old)
        self.assertEqual(audit["reports"][0]["observation_receipt"]["legacy_inventory_basis"],
                         "empty_legacy_dispositions_without_physical_inventory")

    def test_normalized_reference_without_cells_preserves_formal_fact_but_not_unknown_overlap(self):
        old = original()
        formal, _ = build_oge_candidate(catalog(), [old], base(), data_cutoff_at="2025-06-04T00:00:00Z")
        del old["source_rows"]
        del old["transactions"][0]["cells"]
        old["transactions"][0]["page_number"] = 2
        before = deepcopy(formal["transactions"])
        result, audit = build_oge_candidate(catalog(), [old], formal, data_cutoff_at="2025-06-04T00:00:00Z",
            saved_observations_by_document={DOCUMENT_ID: bundle()})
        self.assertEqual(result["transactions"], before)
        self.assertIn("legacy_row_correspondence_unresolved", audit["reports"][0]["observation_receipt"]["rows"][-1]["reasons"])
        self.assertEqual(audit["reports"][0]["legacy_inheritance"]["retained_formal_ids"], [before[0]["id"]])

    def test_nonformal_normalized_cache_does_not_block_new_same_page_observation(self):
        old = original()
        del old["source_rows"]
        del old["transactions"][0]["cells"]
        old["transactions"][0]["page_number"] = 2
        result, audit = candidate(old=old)
        self.assertEqual(len(result["transactions"]), 1)
        self.assertEqual(result["transactions"][0]["asset_name"], "ALPHA")
        self.assertNotIn("legacy_row_correspondence_unresolved", audit["reports"][0]["observation_receipt"]["rows"][-1]["reasons"])
        self.assertEqual(audit["reports"][0]["legacy_normalized_reference_count"], 1)

    def test_unknown_formal_reference_is_preserved_and_blocks_unbound_new_overlap(self):
        old = original()
        formal, _ = build_oge_candidate(catalog(), [old], base(), data_cutoff_at="2025-06-04T00:00:00Z")
        formal["transactions"][0]["id"] = "oge-278t:" + "e" * 24
        del old["source_rows"]
        del old["transactions"][0]["cells"]
        before = deepcopy(formal["transactions"])
        result, audit = build_oge_candidate(catalog(), [old], formal, data_cutoff_at="2025-06-04T00:00:00Z",
            saved_observations_by_document={DOCUMENT_ID: bundle()})
        self.assertEqual(result["transactions"], before)
        self.assertIn("legacy_formal_reference_unbound", audit["reports"][0]["observation_receipt"]["rows"][-1]["reasons"])

    def test_reference_can_feed_real_adapter_without_inventing_old_physical_rows(self):
        old = original()
        del old["source_rows"]
        del old["transactions"][0]["cells"]
        before = deepcopy(old)
        materialized = materialize_oge_278t_observations(old, direct(), **bundle([ROW]))
        adapted = adapt_oge_278t_extraction(materialized, direct())
        self.assertEqual(old, before)
        self.assertEqual(materialized["legacy_disposition_reference"]["source_extraction"], old)
        self.assertEqual(adapted["row_count"], 1)
        self.assertEqual(adapted["legacy_disposition_reference"]["normalized_transaction_count"], 1)
        self.assertFalse(materialized["observation_receipt"]["coverage_complete"])
        materialized["legacy_disposition_reference"]["source_extraction"]["transactions"][0]["asset_name"] = "tampered"
        with self.assertRaisesRegex(OgeCatalogError, "altered or crossed"):
            adapt_oge_278t_extraction(materialized, direct())

    def test_nonempty_reference_rejects_cross_source_or_out_of_range_old_locators(self):
        old = original()
        del old["source_rows"]
        old["transactions"][0]["page_number"] = 4
        with self.assertRaisesRegex(OgeCatalogError, "page count"):
            candidate(old=old)
        old["source_url"] += "?wrong"
        with self.assertRaisesRegex(OgeCatalogError, "archived catalog source"):
            candidate(old=old)




if __name__ == "__main__":
    unittest.main()
