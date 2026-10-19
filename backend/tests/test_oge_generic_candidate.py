"""Synthetic integration contracts; source accuracy is checked separately."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_oge_candidate import base, catalog, direct, extraction, DOCUMENT_ID
from test_oge_observed_candidate import original, candidate, bundle, ROW
from test_oge_content_relations import saved, column_read, EDGES
from test_oge_shadow_run import _inputs
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.oge_reports import GENERIC_PARSER_VERSION, parse_table_rows, read_278t_cover_dates
from unison_snapshot.oge_transaction_adapter import adapt_oge_278t_extraction, materialize_oge_278t_observations
from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns
from unison_snapshot.pipeline_qa.oge_run import build_oge_shadow_run
from unison_snapshot.pipeline_qa.oge_run_cli import main as run_cli


CELLS = ["1", "Example Inc. (EXM)", "Purchase", "05/01/2025", "", "$1,001 - $15,000"]


def generic(rows=None):
    result = extraction()
    physical = rows or [(1, CELLS)]
    good, bad = parse_table_rows(physical, source_sha=result["source_sha256"],
                                 default_owner="Undisclosed", allow_open_amounts=True)
    for row in good + bad:
        if row["owner"] == "Undisclosed":
            row["owner_disclosure_basis"] = "not_disclosed_in_bound_six_column_table"
    result.update(parser_version=GENERIC_PARSER_VERSION, document_page_count=3,
                  cover_date_evidence=read_278t_cover_dates("Filer's Certification\nDate: 06/03/2025\nAgency Review\nDate: 06/04/2025",
                                      source_sha256=result["source_sha256"]),
                  transactions=good, quarantined=bad,
                  source_rows=[{"page_number": page, "cells": cells} for page, cells in physical])
    return result


def build(value, production=None, legacy=None):
    return build_oge_candidate(catalog(), [value], production or base(),
                               data_cutoff_at="2025-06-04T00:00:00Z",
                               legacy_extractions_by_document=legacy)


class GenericCandidateTests(unittest.TestCase):
    def test_generic_enters_real_candidate_and_ledger(self):
        source = generic()
        result, audit = build(source)
        self.assertEqual(len(result["transactions"]), 1)
        self.assertEqual(result["transactions"][0]["owner"], "Undisclosed")
        self.assertNotIn("amount_low_exclusive", result["transactions"][0])
        adapted = adapt_oge_278t_extraction(source, direct())
        self.assertEqual(adapted["counts"]["qualified"], 1)
        self.assertEqual(audit["reports"][0]["cover_date_evidence"], source["cover_date_evidence"])

    def test_role_hash_source_and_filed_date_tampering_block(self):
        source = generic()
        mutations = []
        for key, value in [("cover_text_sha256", "f" * 64), ("source_sha256", "f" * 64),
                           ("observations", []), ("filed_at", "2025-06-02")]:
            changed = deepcopy(source)
            changed["cover_date_evidence"][key] = value
            mutations.append(changed)
        changed = deepcopy(source)
        changed["filed_at"] = "2025-06-02"
        mutations.append(changed)
        changed = deepcopy(source)
        changed.pop("cover_date_evidence")
        mutations.append(changed)
        for changed in mutations:
            result, audit = build(changed)
            self.assertEqual(result["transactions"], [])
            self.assertIn("cover_date_evidence_invalid", audit["reports"][0]["document_reasons"])
            adapted = adapt_oge_278t_extraction(changed, direct())
            self.assertEqual(adapted["counts"]["qualified"], 0)

    def test_known_formal_row_keeps_whole_projection_and_old_id(self):
        old = original()
        production, _ = build_oge_candidate(catalog(), [old], base(), data_cutoff_at="2025-06-04T00:00:00Z")
        before = deepcopy(production)
        result, audit = build(generic(), production, {DOCUMENT_ID: old})
        expected = deepcopy(production["transactions"])
        expected[0]["owner"] = "Undisclosed"
        self.assertEqual(result["transactions"], expected)
        self.assertEqual(production, before)
        self.assertEqual(audit["reports"][0]["legacy_inheritance"]["inherited_ids"],
                         [old["transactions"][0]["extraction_id"]])
        correction = audit["reports"][0]["legacy_inheritance"]["field_corrections"][0]
        self.assertEqual((correction["old_value"], correction["new_value"]), ("Self", "Undisclosed"))
        self.assertEqual(correction["source_reference"]["source_rows_index"], 0)

    def test_explicit_self_and_unknown_prior_scope_are_not_default_corrections(self):
        for heading in ["Filer assets", "Unclear owner assets"]:
            old = original()
            old["source_rows"].insert(0, {"page_number": 1, "cells": ["", heading, "", "", "", ""]})
            production, _ = build_oge_candidate(catalog(), [old], base(), data_cutoff_at="2025-06-04T00:00:00Z")
            result, audit = build(generic(), production, {DOCUMENT_ID: old})
            self.assertEqual(result["transactions"], production["transactions"])
            self.assertEqual(audit["reports"][0]["legacy_inheritance"]["field_corrections"], [])

    def test_explicit_owner_reference_or_non_v2_cache_cannot_be_relabelled(self):
        for mode in ["reference", "non_v2"]:
            old = original()
            if mode == "reference":
                old["transactions"][0]["owner_observation_reference"] = {"source_sha256": "a" * 64, "page_number": 1}
            else:
                # Generic cache is not evidence of the v2 implicit default.
                old["parser_version"] = GENERIC_PARSER_VERSION
                old.update(document_page_count=3, cover_date_evidence=generic()["cover_date_evidence"])
            production, _ = build_oge_candidate(catalog(), [old], base(), data_cutoff_at="2025-06-04T00:00:00Z")
            result, audit = build(generic(), production, {DOCUMENT_ID: old})
            self.assertEqual(result["transactions"], production["transactions"])
            self.assertEqual(audit["reports"][0]["legacy_inheritance"]["field_corrections"], [])

    def test_missing_old_binding_preserves_old_and_blocks_new(self):
        old = original()
        production, _ = build_oge_candidate(catalog(), [old], base(), data_cutoff_at="2025-06-04T00:00:00Z")
        result, audit = build(generic(), production)
        self.assertEqual(result["transactions"], production["transactions"])
        self.assertIn("legacy_source_binding_missing", audit["reports"][0]["quarantined"][0]["reasons"])

    def test_same_printed_locator_but_changed_asset_is_not_inherited(self):
        old = original()
        production, _ = build_oge_candidate(catalog(), [old], base(), data_cutoff_at="2025-06-04T00:00:00Z")
        changed = [*CELLS[:1], "OTHER", *CELLS[2:]]
        result, audit = build(generic([(1, changed)]), production, {DOCUMENT_ID: old})
        self.assertEqual(result["transactions"], production["transactions"])
        self.assertFalse(audit["reports"][0]["legacy_inheritance"]["inherited_ids"])
        self.assertIn("legacy_row_correspondence_unresolved", audit["reports"][0]["quarantined"][0]["reasons"])
        wrong_source = deepcopy(old)
        wrong_source["source_sha256"] = "c" * 64
        with self.assertRaisesRegex(OgeCatalogError, "differs from generic source"):
            build(generic(), production, {DOCUMENT_ID: wrong_source})

    def test_formal_field_mismatch_cannot_authorize_inheritance(self):
        old = original()
        production, _ = build_oge_candidate(catalog(), [old], base(), data_cutoff_at="2025-06-04T00:00:00Z")
        production["transactions"][0]["amount_low"] = 2000
        result, audit = build(generic(), production, {DOCUMENT_ID: old})
        self.assertEqual(result["transactions"], production["transactions"])
        self.assertEqual(audit["reports"][0]["legacy_inheritance"]["unbound_formal_ids"],
                         [old["transactions"][0]["extraction_id"]])

    def test_open_amount_enters_candidate_and_shared_ledger_only_with_marker(self):
        for amount, floor in [("Over $1,000,000", 1000000), ("Over $50,000,000", 50000000)]:
            source = generic([(1, [*CELLS[:5], amount])])
            result, _ = build(source)
            self.assertEqual(len(result["transactions"]), 1)
            row = result["transactions"][0]
            self.assertEqual((row["amount_low"], row["amount_high"], row["amount_low_exclusive"]), (floor, None, True))
            self.assertEqual(adapt_oge_278t_extraction(source, direct())["counts"]["qualified"], 1)
            for changes in [{"amount_low_exclusive": False}, {"amount_low": floor + 1}, {"amount_raw": "$1,000,000"}]:
                invalid = deepcopy(source)
                invalid["transactions"][0].update(changes)
                self.assertEqual(build(invalid)[0]["transactions"], [])
                self.assertEqual(adapt_oge_278t_extraction(invalid, direct())["counts"]["qualified"], 0)

    def test_saved_open_amount_removes_only_amount_missing_blocker(self):
        for label in ["Over $1,000,000", "Over $50,000,000"]:
            inputs = bundle([["", "Investment account #1", "", "", "", ""], [*ROW[:5], label]])
            result, audit = candidate(inputs)
            added = next(row for row in result["transactions"] if row["asset_name"].startswith("ALPHA"))
            self.assertTrue(added["amount_low_exclusive"])
            self.assertIsNone(added["amount_high"])
        broken = [*ROW[:3], "?", ROW[4], "Over $1,000,000"]
        result, audit = candidate(bundle([broken]))
        self.assertEqual(len(result["transactions"]), 1)
        self.assertIn("transaction_date_observation_missing", audit["reports"][0]["observation_receipt"]["rows"][0]["reasons"])

    def test_open_and_bounded_observation_conflict_stays_blocked(self):
        data = saved([[*ROW[:5], "Over $1,000,000"]])
        reading = column_read(data, "amount", "$1,001 - $15,000")
        result, audit = candidate({"observations": [data], "document_page_count": 3,
                                   "secondary_observations": [reading]})
        self.assertEqual(len(result["transactions"]), 1)
        self.assertIn("amount_reading_unresolved", audit["reports"][0]["observation_receipt"]["rows"][0]["reasons"])

    def test_account_title_is_consumed_and_instrument_uses_original_asset(self):
        rows = [["", "Investment account #1", "", "", "", ""], ROW,
                ["", "Retirement account #2", "", "", "", ""], ROW]
        result, audit = candidate(bundle(rows))
        new = [row for row in result["transactions"] if row["asset_name"].startswith("ALPHA")]
        self.assertEqual(len(new), 2)
        self.assertEqual(len({row["id"] for row in new}), 2)
        self.assertEqual({row["asset_name"] for row in new},
                         {"ALPHA — Investment account #1", "ALPHA — Retirement account #2"})
        self.assertEqual({row["instrument_type"] for row in new}, {"Unspecified"})

    def test_owner_title_uses_physical_order_and_unknown_account_blocks(self):
        data = saved([["", "Spouse assets", "", "", "", ""], ROW])
        slot = data["pages"][0]["slots"][0]
        slot["words"] = [{"text": text, "bbox_pdf_points": box} for text, box in [
            ("assets", [40, 102, 65, 108]), ("Spouse", [11, 102, 35, 108])]]
        slot.update(_observe_slot_columns(slot["words"], EDGES))
        result, _ = candidate({"observations": [data], "document_page_count": 3})
        self.assertEqual(next(row for row in result["transactions"] if row["asset_name"] == "ALPHA")["owner"], "Spouse")
        result, audit = candidate(bundle([["", "?", "", "", "", ""], ROW]))
        self.assertEqual(len(result["transactions"]), 2)
        self.assertEqual(audit["reports"][0]["observation_receipt"]["rows"][-1]["effective_account_context"]["status"], "not_observed")

    def test_generic_saved_page_count_and_missing_high_are_not_relaxed(self):
        with self.assertRaisesRegex(OgeCatalogError, "page count differs"):
            materialize_oge_278t_observations(generic(), direct(), observations=[saved([ROW])], document_page_count=2)
        missing = generic()
        missing["transactions"][0]["amount_high"] = None
        self.assertEqual(build(missing)[0]["transactions"], [])
        self.assertEqual(adapt_oge_278t_extraction(missing, direct())["counts"]["qualified"], 0)

    def test_optional_account_title_does_not_require_cover_or_table_start(self):
        header = ["#", "Description", "Type", "Date", "Notification", "Amount"]
        source = generic()
        source.update(transactions=[], quarantined=[], source_rows=[{"page_number": 2, "cells": header}])
        source["cover_date_evidence"] = read_278t_cover_dates(
            "Periodic Transaction Report\nFiler's Certification\nDate: 06/03/2025", source_sha256="a"*64)
        data = saved([header, ROW])
        positive = materialize_oge_278t_observations(source, direct(), observations=[data], document_page_count=3)
        self.assertEqual(positive["observation_receipt"]["initial_account_scope_basis"], "optional_account_title_not_observed_no_absence_claim")
        self.assertEqual(len(positive["transactions"]), 1)
        self.assertEqual(positive["transactions"][0]["owner"], "Undisclosed")
        # Missing earlier observation is a coverage warning, not a title requirement.
        for mode in ["no_native_header", "no_cover_role", "later_page"]:
            changed, observed = deepcopy(source), deepcopy(data)
            if mode == "no_native_header":
                changed["source_rows"] = []
            elif mode == "no_cover_role":
                changed["cover_date_evidence"] = read_278t_cover_dates("Filer's Certification\nDate: 06/03/2025", source_sha256="a"*64)
            else:
                observed["pages"][0]["page_number"] = 3
            result = materialize_oge_278t_observations(changed, direct(), observations=[observed], document_page_count=3)
            self.assertEqual(len(result["transactions"]), 1)
            receipt = result["observation_receipt"]["rows"][-1]
            self.assertNotIn("account_context_unresolved", receipt["reasons"])
            self.assertEqual(receipt["effective_account_context"]["status"], "not_observed")
            self.assertIn("coverage_warning", receipt["effective_account_context"])

    def test_late_first_page_does_not_invent_required_title(self):
        observed = saved([ROW])
        observed["pages"][0]["page_number"] = 18
        result, audit = candidate({"observations": [observed], "document_page_count": 20})
        self.assertEqual(len(result["transactions"]), 2)
        receipt = audit["reports"][0]["observation_receipt"]
        self.assertFalse(receipt["coverage_complete"])
        self.assertIn(17, receipt["unobserved_pages"])
        self.assertEqual(receipt["rows"][0]["effective_account_context"]["status"], "not_observed")

    def test_actual_lost_account_scope_still_blocks(self):
        first = saved([["", "Investment account #1", "", "", "", ""]])
        last = saved([ROW])
        last["pages"][0]["page_number"] = 4
        result, audit = candidate({"observations": [first, last], "document_page_count": 4})
        self.assertEqual(len(result["transactions"]), 1)
        self.assertIn("account_context_unresolved", audit["reports"][0]["observation_receipt"]["rows"][-1]["reasons"])

    def test_ambiguous_explicit_account_heading_stays_blocked(self):
        observed = saved([["", "Investment account #1", "", "", "", ""], ROW])
        slot = observed["pages"][0]["slots"][0]
        slot["words"][0]["bbox_pdf_points"][0] = 9.9
        slot.update(_observe_slot_columns(slot["words"], EDGES))
        result, audit = candidate({"observations": [observed], "document_page_count": 3})
        self.assertEqual(len(result["transactions"]), 1)
        self.assertIn("account_context_unresolved", audit["reports"][0]["observation_receipt"]["rows"][-1]["reasons"])

    def test_unknown_region_without_known_scope_is_coverage_warning(self):
        unknown = saved()
        unknown["pages"][0]["column_geometry"] = "unknown"
        last = saved([ROW])
        last["pages"][0]["page_number"] = 3
        result, audit = candidate({"observations": [unknown, last], "document_page_count": 3})
        self.assertEqual(len(result["transactions"]), 2)
        receipt = audit["reports"][0]["observation_receipt"]
        self.assertFalse(receipt["coverage_complete"])
        self.assertEqual(receipt["rows"][-1]["effective_account_context"]["status"], "not_observed")

    def test_same_page_unknown_number_or_empty_slot_does_not_erase_scope(self):
        for noise in [["#", "", "", "", "", ""], ["", "", "", "", "", ""]]:
            for heading in [[], [["", "Investment account #1", "", "", "", ""]],
                            [["", "Filer assets", "", "", "", ""]]]:
                result, audit = candidate(bundle([*heading, noise, ROW]))
                self.assertEqual(len(result["transactions"]), 2)
                row = next(row for row in result["transactions"] if row["asset_name"].startswith("ALPHA"))
                self.assertEqual(row["owner"], "Self" if heading and heading[0][1] == "Filer assets" else "Undisclosed")
                self.assertEqual(row["asset_name"], "ALPHA — Investment account #1"
                                 if heading and heading[0][1].startswith("Investment") else "ALPHA")
                receipt = audit["reports"][0]["observation_receipt"]
                self.assertEqual(receipt["unresolved_slot_count"], 1)
                self.assertFalse(receipt["coverage_complete"])
                self.assertNotIn("account_context_unresolved", receipt["rows"][-1]["reasons"])

    def test_gap_without_established_scope_is_not_a_lost_account(self):
        first = saved([ROW])
        last = saved([ROW])
        last["pages"][0]["page_number"] = 4
        result, audit = candidate({"observations": [first, last], "document_page_count": 4})
        self.assertEqual(len(result["transactions"]), 3)
        self.assertNotIn("account_context_unresolved", audit["reports"][0]["observation_receipt"]["rows"][-1]["reasons"])

    def test_explicit_continuation_scope_stays_unresolved(self):
        result, audit = candidate(bundle([["", "Continued from previous page", "", "", "", ""], ROW]))
        self.assertEqual(len(result["transactions"]), 1)
        self.assertIn("account_context_unresolved", audit["reports"][0]["observation_receipt"]["rows"][-1]["reasons"])

    def test_explicit_account_scope_allows_approved_undisclosed_owner(self):
        result, _ = candidate(bundle([["", "Investment account #1", "", "", "", ""], ROW]))
        row = next(row for row in result["transactions"] if row["asset_name"].startswith("ALPHA"))
        self.assertEqual(row["owner"], "Undisclosed")

    def test_materialized_table_resolves_only_table_discovery_evidence(self):
        source = original()
        source.update(transactions=[], quarantined=[], source_rows=[], evidence_complete=False,
                      document_reasons=["no_transaction_rows_found"])
        result = materialize_oge_278t_observations(source, direct(), **bundle())
        self.assertTrue(result["evidence_complete"])
        self.assertEqual(result["document_reasons"], [])
        self.assertEqual(result["legacy_document_reasons"], ["no_transaction_rows_found"])
        source["document_reasons"].append("filer_signature_date_missing")
        result = materialize_oge_278t_observations(source, direct(), **bundle())
        self.assertFalse(result["evidence_complete"])
        self.assertEqual(result["document_reasons"], ["filer_signature_date_missing"])

    def test_runner_consumes_explicit_legacy_map_and_builds_snapshot(self):
        old = original()
        production, _ = build_oge_candidate(catalog(), [old], base(), data_cutoff_at="2025-06-04T00:00:00Z")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = _inputs(root)
            for file in inputs["extractions_dir"].glob("*.json"):
                file.unlink()
            (inputs["extractions_dir"] / f"{DOCUMENT_ID}.json").write_text(json.dumps(generic()))
            inputs["catalog_path"].write_text(json.dumps(catalog()))
            archive = json.loads(inputs["archive_batch_path"].read_text())
            report = archive["reports"][0]
            report.update(document_id=DOCUMENT_ID, document_url=old["source_url"],
                          filer_name=direct()["filer_name"], agency=direct()["agency"],
                          position_title=direct()["position_title"], catalog_added_date=direct()["catalog_added_date"],
                          archive_path=f"oge/reports/{DOCUMENT_ID}/{'a'*64}.pdf")
            inputs["archive_batch_path"].write_text(json.dumps(archive))
            paths = {}
            for name, value in [("saved", {}), ("base", production), ("legacy", {DOCUMENT_ID: old})]:
                paths[name] = root / (name + ".json")
                paths[name].write_text(json.dumps(value))
            manifest = build_oge_shadow_run(**inputs, saved_observations_path=paths["saved"],
                          candidate_base_path=paths["base"], legacy_extractions_path=paths["legacy"],
                          data_cutoff_at="2025-06-04T00:00:00Z")
            actual = json.loads((inputs["output_dir"] / "candidate.json").read_text(encoding="utf-8"))
            expected = deepcopy(production["transactions"])
            expected[0]["owner"] = "Undisclosed"
            self.assertEqual(actual["transactions"], expected)
            ledger = json.loads((inputs["output_dir"] / "candidate_rows.json").read_text(encoding="utf-8"))
            self.assertEqual(ledger[0]["disposition"], "qualified")
            self.assertTrue(any(row["artifact_type"] == "legacy_extractions" for row in manifest["input_artifacts"]))
            self.assertTrue((inputs["output_dir"] / "snapshot/manifest.json").is_file())

    def test_cli_forwards_optional_legacy_map(self):
        with patch("unison_snapshot.pipeline_qa.oge_run_cli.build_oge_shadow_run", return_value={
                "run_id": "x", "counts": {"discovered_documents": 1, "archived_documents": 1}, "accounted_rows": 1}) as call:
            code = run_cli(["--catalog", "c", "--archive-batch", "a", "--extraction-batch", "e",
                            "--extractions-dir", "d", "--output-dir", "o", "--run-id", "x",
                            "--code-commit", "b"*40, "--evidence-commit", "c"*40,
                            "--started-at", "s", "--completed-at", "t", "--legacy-extractions", "old.json"])
        self.assertEqual(code, 0)
        self.assertEqual(call.call_args.kwargs["legacy_extractions_path"], "old.json")


if __name__ == "__main__":
    unittest.main()
