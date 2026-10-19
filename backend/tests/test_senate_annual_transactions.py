"""Senate annual transaction rows remain observations, separate from PTRs."""
from __future__ import annotations

import hashlib
import unittest

from unison_snapshot.senate import SenateEfdError
from unison_snapshot.senate_annual_transactions import observe_part4b, relate_part4b_to_candidate


def sample() -> tuple[dict, bytes]:
    raw = ("<!doctype html><html><body>"
           "<h3>Part 4a. Periodic Transaction Report Summary</h3>"
           "<table><tr><th></th><th>#</th><th>Transaction Date</th><th>Owner</th>"
           "<th>Ticker</th><th>Asset Name</th><th>Type</th><th>Amount</th>"
           "<th>Comment</th></tr><tr><td></td><td>1</td><td>04/21/2025</td>"
           "<td>Self</td><td>ABC</td><td>ABC Corp</td><td>Purchase</td>"
           "<td>$1,001 - $15,000</td><td>--</td></tr></table>"
           "<h3>Part 4b. Transactions</h3>"
           "<table><tr><th></th><th>#</th><th>Owner</th><th>Ticker</th>"
           "<th>Asset Name</th><th>Transaction Type</th>"
           "<th>Transaction Date</th><th>Amount</th><th>Comments</th></tr>"
           "<tr><td></td><td>1</td><td>Spouse</td><td>--</td>"
           "<td>Example Fund</td><td>Sale (Full)</td><td>11/12/2025</td>"
           "<td>$500,001 - $1,000,000</td><td>n/a</td></tr></table>"
           "</body></html>").encode()
    metadata = {"schema_version": "senate-efd-annual-archive/v1",
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "byte_length": len(raw), "document_id": "example",
                "document_url": "https://efdsearch.senate.gov/search/view/annual/example/",
                "report_year": 2025, "amendment_number": 0}
    return metadata, raw


class SenateAnnualTransactionsTests(unittest.TestCase):
    def test_missing_ptr_summary_does_not_drop_independent_annual_transactions(self):
        metadata, raw = sample()
        start, end = raw.index(b"<table>"), raw.index(b"</table>") + len(b"</table>")
        changed = raw[:start] + raw[end:]
        result = observe_part4b({**metadata, "source_sha256": hashlib.sha256(changed).hexdigest(),
                               "byte_length": len(changed)}, changed)
        self.assertEqual(result["part4b_printed_row_count"], 1)
        self.assertIsNone(result["part4a_ptr_summary_row_count"])

    def test_exact_ptr_field_match_is_visible_but_never_merges_disclosure_rows(self):
        metadata, raw = sample()
        observation = observe_part4b(metadata, raw)
        tx = {"id": "senate-ptr:" + "a" * 24, "source_id": "senate_efd",
              "person_id": "senate:example", "owner": "Spouse", "ticker": None,
              "asset_name": "Example Fund", "transaction_date": "2025-11-12",
              "transaction_type": "sale", "amount_low": 500001, "amount_high": 1000000}
        relation = relate_part4b_to_candidate(observation, person_id="senate:example", transactions=[tx])
        self.assertEqual(relation["rows"][0]["status"], "possible_ptr_overlap")
        self.assertEqual(relation["rows"][0]["possible_ptr_transaction_ids"], [tx["id"]])
        self.assertIsNone(relation["rows"][0]["formal_transaction_id"])
        self.assertEqual(relation["new_candidate_transaction_count"], 0)
        other_account = {**tx, "id": "senate-ptr:" + "b" * 24}
        relation = relate_part4b_to_candidate(observation, person_id="senate:example",
                                             transactions=[tx, other_account])
        self.assertEqual(len(relation["rows"][0]["possible_ptr_transaction_ids"]), 2)

    def test_explicit_no_without_table_is_zero_but_missing_section_is_unknown(self):
        metadata, raw = sample()
        start = raw.index(b"<h3>Part 4b.")
        no = (raw[:start] + b"<h3>Part 4b. Transactions</h3><p>Did you, your spouse, or "
              b"dependent child buy, sell, or exchange an asset where the transaction "
              b"exceeded $1,000 and was not reported on Part 4a? <strong>No</strong></p>"
              b"</body></html>")
        def observe(content):
            return observe_part4b({**metadata, "source_sha256": hashlib.sha256(content).hexdigest(),
                                   "byte_length": len(content)}, content)
        result = observe(no)
        self.assertEqual(result["section_disposition"], "explicit_no_transactions")
        self.assertEqual(result["source_report_ledger"]["logical_row_count"], 0)
        with self.assertRaisesRegex(SenateEfdError, "missing or ambiguous"):
            observe(no.replace(b"<strong>No</strong>", b"<strong>Yes</strong>"))
        with self.assertRaisesRegex(SenateEfdError, "heading"):
            observe(no.replace(b"<h3>Part 4b. Transactions</h3>", b""))

    def test_matching_headers_in_another_section_cannot_supply_part4b(self):
        metadata, raw = sample()
        changed = raw.replace(b"<h3>Part 4b. Transactions</h3>",
                              b"<h3>Part 4b. Transactions</h3><h3>Part 5. Gifts</h3>")
        with self.assertRaisesRegex(SenateEfdError, "table is missing"):
            observe_part4b({**metadata, "source_sha256": hashlib.sha256(changed).hexdigest(),
                           "byte_length": len(changed)}, changed)

    def test_unknown_direction_stays_an_observed_row(self):
        metadata, raw = sample()
        changed = raw.replace(b"Sale (Full)", b"Unclear")
        result = observe_part4b({**metadata, "source_sha256": hashlib.sha256(changed).hexdigest(),
                               "byte_length": len(changed)}, changed)
        self.assertEqual(len(result["rows"]), 1)
        self.assertIn("transaction_type_unsupported", result["rows"][0]["problems"])
        self.assertIsNone(result["rows"][0]["transaction_type"])

    def test_separate_4b_from_ptr_summary(self):
        metadata, raw = sample()
        result = observe_part4b(metadata, raw)
        self.assertEqual((result["part4a_ptr_summary_row_count"],
                          result["part4b_printed_row_count"],
                          result["production_eligible_row_count"]), (1, 1, 0))
        row = result["rows"][0]
        self.assertEqual((row["printed_row_number"], row["transaction_date"],
                          row["amount_low"], row["amount_high"]),
                         ("1", "2025-11-12", 500001, 1000000))
        self.assertEqual(row["ptr_overlap_status"], "unassessed")

    def test_source_or_row_number_drift_fails_closed(self):
        metadata, raw = sample()
        with self.assertRaises(SenateEfdError):
            observe_part4b({**metadata, "source_sha256": "0" * 64}, raw)
        duplicated = raw.replace(b"<td>Spouse</td>",
                                 b"<td>Spouse</td>")
        duplicated = duplicated.replace(b"</table></body>",
            b"<tr><td></td><td>1</td><td>Self</td><td>--</td><td>Other</td>"
            b"<td>Purchase</td><td>01/01/2025</td><td>$1,001 - $15,000</td>"
            b"<td>--</td></tr></table></body>")
        updated = {**metadata, "source_sha256": hashlib.sha256(duplicated).hexdigest(),
                   "byte_length": len(duplicated)}
        with self.assertRaisesRegex(SenateEfdError, "row numbers"):
            observe_part4b(updated, duplicated)


if __name__ == "__main__":
    unittest.main()
