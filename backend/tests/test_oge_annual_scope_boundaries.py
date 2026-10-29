"""Scope interruption contracts; generated PDFs are not recognition gold."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_oge_annual_candidate import HEADER, COVER, DOC, source, inputs, base, ledger
import test_oge_annual_native_holdings as native_holdings_tests
from unison_snapshot.oge_annual import _parse_rows, _reject_nested_aggregates, materialize_saved_annual_scan
from unison_snapshot.whitehouse_annual_candidate import overlay_oge_annual_candidate


def asset(number, name):
    return [number, name, "Yes", "$1,001 - $15,000", "", ""]


def boundary_rows(*, restore=False):
    return [HEADER, ["", "Spouse assets", "", "", "", ""], asset("1", "Earlier parent"),
            ["", "Unreadable account heading", "", "", "", ""],
            *([["", "Spouse assets", "", "", "", ""]] if restore else []),
            asset("1.1", "Later leaf")]


class AnnualScopeBoundaryTests(unittest.TestCase):
    def parse(self, rows, **kwargs):
        return _parse_rows("part2", "Self", kwargs.pop("page", 2), rows, 2025,
                           qualified=True, preserve_valued_unreadable=True, **kwargs)

    def test_unread_boundary_cannot_create_a_parent_child_relation_or_qualified_owner(self):
        good, bad, excluded = self.parse(boundary_rows())
        good = _reject_nested_aggregates(good, bad, excluded, qualified=True)
        self.assertEqual([row["asset_name"] for row in good], ["Earlier parent", "Later leaf"])
        self.assertFalse(excluded)
        later = good[-1]
        self.assertEqual(later["account_context"]["status"], "unknown")
        self.assertEqual(later["account_context"]["boundary_ref"]["cells"][1], "Unreadable account heading")
        self.assertEqual(later["owner_context"]["basis"], "unresolved_after_source_boundary")
        self.assertEqual(later["hierarchy_evidence"]["status"], "scope_unresolved")
        extraction = source()
        extraction.update(holdings=good, quarantined=bad, excluded=excluded)
        decisions = ledger(extraction)["rows"]
        selected = next(row for row in decisions if row["annual_relationships"]["account_context"]
                        and row["annual_relationships"]["account_context"]["status"] == "unknown")
        self.assertEqual(selected["disposition"], "quarantined")
        self.assertIn("account_scope_unresolved", selected["reasons"])

    def test_repeated_explicit_owner_heading_restores_scope_without_linking_old_parent(self):
        good, bad, excluded = self.parse(boundary_rows(restore=True))
        good = _reject_nested_aggregates(good, bad, excluded, qualified=True)
        self.assertEqual(len(good), 2)
        self.assertFalse(excluded)
        self.assertEqual(good[-1]["owner"], "Spouse")
        self.assertEqual(good[-1]["account_context"]["status"], "not_observed")
        self.assertFalse(good[-1]["hierarchy_evidence"]["parent_refs"])
        self.assertNotEqual(good[0]["owner_context"]["heading_ref"], good[-1]["owner_context"]["heading_ref"])

    def test_account_heading_alone_does_not_restore_unknown_owner(self):
        state = {}
        self.parse(boundary_rows(), scope_state=state)
        good, _, _ = self.parse([HEADER, ["", "INVESTMENT ACCOUNT #2", "", "", "", ""],
                                asset("2", "Account leaf")], page=3, scope_state=state)
        self.assertEqual(good[0]["account_context"]["title"], "INVESTMENT ACCOUNT #2")
        self.assertEqual(good[0]["account_context"]["status"], "unknown")

    def test_unread_value_in_a_numbered_row_does_not_invent_a_scope_boundary(self):
        unread = asset("2", "Unread value")
        unread[3] = "unread"
        good, bad, _ = self.parse([HEADER, ["", "Spouse assets", "", "", "", ""],
                                  unread, asset("3", "Later leaf")])
        self.assertEqual(bad[0]["reasons"], ["holding_value_unreadable"])
        self.assertEqual(good[0]["owner"], "Spouse")
        self.assertEqual(good[0]["account_context"]["status"], "not_observed")

    def test_skipped_page_interrupts_section_default_even_without_explicit_headings(self):
        state = {}
        self.parse([HEADER, asset("1", "Earlier parent")], scope_state=state)
        later, _, _ = self.parse([HEADER, asset("1.1", "Later leaf")], page=4, scope_state=state)
        self.assertEqual(later[0]["owner_context"]["basis"], "unresolved_after_source_boundary")
        self.assertEqual(later[0]["account_context"]["status"], "unknown")

    def test_unrecognized_header_interrupts_scope_but_supported_blank_and_endnote_do_not(self):
        state = {}
        self.parse(boundary_rows()[:3], scope_state=state)
        self.parse([["unreadable table"]], scope_state=state)
        after, _, _ = self.parse([HEADER, asset("2", "Later")], scope_state=state)
        self.assertEqual(after[0]["account_context"]["status"], "unknown")
        good, _, _ = self.parse([HEADER, ["", "Spouse assets", "", "", "", ""],
            ["", "", "", "", "", ""], ["Endnote for Line 1", "", "", "", "", ""],
            asset("1", "Leaf")], native_table_context=True)
        self.assertEqual(good[0]["owner_context"]["basis"], "explicit_owner_heading")
        self.assertEqual(good[0]["account_context"]["status"], "not_observed")

    def test_saved_native_materializer_uses_the_same_scope_boundary(self):
        helper = native_holdings_tests.NativeAnnualHoldingTests()
        cover, sections, obs, meta = helper.setup_inputs()
        obs = deepcopy(obs)
        obs["pages"][0]["tables"] = [boundary_rows()]
        with patch("unison_snapshot.oge_annual_scan_sections.observe_holding_pages", return_value=obs), \
             patch("unison_snapshot.oge_annual_native_coverage.observe_native_holding_coverage", return_value={}):
            result = materialize_saved_annual_scan(cover, sections, archive_metadata=meta,
                holding_page_observations=obs, archived_pdf_path=Path("original.pdf"))
        later = next(row for row in result["holdings"] if row["asset_name"] == "Later leaf")
        self.assertEqual(later["account_context"]["status"], "unknown")
        self.assertFalse(later["hierarchy_evidence"]["parent_refs"])

    def test_real_pdf_normal_review_cli_and_candidate_keep_unknown_scope_blocked(self):
        import pymupdf
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            document = pymupdf.open()
            cover = document.new_page(width=700, height=600)
            cover.insert_text((25, 35), COVER, fontsize=10)
            page = document.new_page(width=700, height=600)
            page.insert_text((25, 35), "Part 2: Filer's Employment Assets", fontsize=10)
            columns = [25, 55, 310, 350, 480, 570, 675]
            rows = boundary_rows()
            for x in columns:
                page.draw_line((x, 65), (x, 65 + len(rows)*28))
            for index in range(len(rows)+1):
                page.draw_line((columns[0], 65+index*28), (columns[-1], 65+index*28))
            for index, row in enumerate(rows):
                for col, text in enumerate(row):
                    if text:
                        page.insert_text((columns[col]+3, 84+index*28), text, fontsize=9)
            raw = document.tobytes(); document.close()
            sha = hashlib.sha256(raw).hexdigest()
            request = inputs(source())
            meta = request["archive_metadata_by_document"][DOC]
            meta.update(sha256=sha, archive_path=f"oge/annual/reports/{DOC}/{sha}.pdf")
            evidence, review = folder/"evidence", folder/"review"
            path = evidence/meta["archive_path"]; path.parent.mkdir(parents=True)
            path.write_bytes(raw)
            path.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
            run = subprocess.run([sys.executable, str(root/"backend/src/unison_snapshot/commands/oge_annual_review.py"),
                "--evidence-root", str(evidence), "--review-root", str(review),
                "--parser-version", "oge-278e-tables/v3", "--document-id", DOC],
                cwd=root, env={**os.environ, "PYTHONPATH": str(root/"backend/src")},
                capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            out = review/f"oge/annual/extractions/{DOC}/{sha}/oge-278e-tables-v3.json"
            extracted = json.loads(out.read_bytes())
            later = next(row for row in extracted["holdings"] if row["asset_name"] == "Later leaf")
            self.assertEqual(later["account_context"]["status"], "unknown")
            self.assertFalse(later["hierarchy_evidence"]["parent_refs"])
            request["extractions"] = [extracted]
            candidate, audit = overlay_oge_annual_candidate(base(), request)
            self.assertFalse(candidate["reported_holdings"])
            self.assertTrue(audit["reports"][0]["report_reasons"])
