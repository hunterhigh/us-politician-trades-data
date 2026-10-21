"""Synthetic regressions for row disposal, not official-source accuracy gold."""
import unittest

from unison_snapshot.oge_reports import parse_table_rows
from unison_snapshot.whitehouse_278t import _generic_table_rows


class NumberedRowConservationTests(unittest.TestCase):
    def test_asset_words_cannot_discard_a_numbered_disclosure(self):
        for asset in ("Description Type Date Fund", "Amount Received Days Ago Trust"):
            with self.subTest(asset=asset):
                raw = ["1", asset, "Purchase", "05/01/2025", "No", "$1,001 - $15,000"]
                good, bad = parse_table_rows([(2, raw)], source_sha="a" * 64)
                self.assertEqual(len(good), 1)
                self.assertEqual(bad, [])
                self.assertEqual(good[0]["asset_name"], asset)
                generic_good, generic_bad, _ = _generic_table_rows(
                    [(2, raw)], source_sha256="a" * 64)
                self.assertEqual(len(generic_good), 1)
                self.assertEqual(generic_bad, [])
                self.assertEqual(generic_good[0]["owner"], "Undisclosed")

    def test_numbered_malformed_row_is_rejected_instead_of_dropped(self):
        raw = ["2", "Description Type Date Fund", "Purchase", "unclear", "No", "$1,001 - $15,000"]
        good, bad = parse_table_rows([(2, raw)], source_sha="a" * 64)
        self.assertEqual(good, [])
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["cells"], raw)
        self.assertIn("transaction_date_invalid", bad[0]["reasons"])

    def test_numeric_marker_with_exact_column_roles_is_still_a_header(self):
        # This is our constructed structure contract, not official-source gold.
        # Column roles positively identify a header even with a numeric marker.
        from unison_snapshot.oge_transaction_adapter import _structural_disposition
        raw = ["11", "Description", "Type", "Date", "Notification", "Amount"]
        self.assertEqual(parse_table_rows([(2, raw)], source_sha="a" * 64), ([], []))
        good, bad, _ = _generic_table_rows([(2, raw)], source_sha256="a" * 64)
        self.assertEqual((good, bad), ([], []))
        self.assertEqual(_structural_disposition(raw), ("excluded", "table_header"))

    def test_actual_unnumbered_headers_are_still_non_data(self):
        rows = [(2, ["#", "Description", "Type", "Date", "Notification", "Amount"]),
                (2, ["", "Description", "Type", "Date", "Notification", "Amount"]),
                (2, ["", "", "", "", "Notification received over 30 days ago", "Amount"])]
        self.assertEqual(parse_table_rows(rows, source_sha="a" * 64), ([], []))


class OwnerScopeConservationTests(unittest.TestCase):
    row = ["1", "Example Corp", "Purchase", "05/01/2025", "No", "$1,001 - $15,000"]

    def test_new_account_after_missing_page_cannot_restore_prior_owner(self):
        rows = [(2, ["", "Spouse assets", "", "", "", ""]), (2, self.row),
                (4, ["", "Investment account #2", "", "", "", ""]), (4, self.row)]
        good, bad, _ = _generic_table_rows(rows, source_sha256="a" * 64)
        self.assertEqual([(r["page_number"], r["owner"]) for r in good], [(2, "Spouse")])
        self.assertEqual(len(bad), 1)
        self.assertIn("owner_context_unresolved", bad[0]["reasons"])
        self.assertEqual(bad[0]["account_title"], "Investment account #2")

    def test_new_account_cannot_clear_ambiguous_owner_heading(self):
        rows = [(2, ["", "Spouse retirement assets unclear", "", "", "", ""]),
                (2, ["", "Investment account #2", "", "", "", ""]), (2, self.row)]
        good, bad, _ = _generic_table_rows(rows, source_sha256="a" * 64)
        self.assertEqual(good, [])
        self.assertIn("owner_context_unresolved", bad[-1]["reasons"])

    def test_explicit_owner_restores_scope_after_a_gap(self):
        rows = [(2, ["", "Spouse assets", "", "", "", ""]), (2, self.row),
                (4, ["", "Self assets", "", "", "", ""]),
                (4, ["", "Investment account #2", "", "", "", ""]), (4, self.row)]
        good, bad, _ = _generic_table_rows(rows, source_sha256="a" * 64)
        self.assertEqual(bad, [])
        self.assertEqual([r["owner"] for r in good], ["Spouse", "Self"])

    def test_new_account_restores_account_only_when_no_owner_was_established(self):
        rows = [(2, ["", "Investment account #1", "", "", "", ""]), (2, self.row),
                (4, ["", "Investment account #2", "", "", "", ""]), (4, self.row)]
        good, bad, _ = _generic_table_rows(rows, source_sha256="a" * 64)
        self.assertEqual(bad, [])
        self.assertEqual([r["owner"] for r in good], ["Undisclosed", "Undisclosed"])

    def test_explicit_partial_region_barrier_cannot_be_cleared_by_account(self):
        rows = [(2, ["", "Spouse assets", "", "", "", ""]),
                (2, ["", "Investment account #2", "", "", "", ""]), (2, self.row)]
        good, bad, _ = _generic_table_rows(rows, source_sha256="a" * 64,
                                         scope_barriers_before={1: True})
        self.assertEqual(good, [])
        self.assertIn("owner_context_unresolved", bad[0]["reasons"])
