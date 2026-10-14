"""Senate annual transaction rows remain observations, separate from PTRs."""
from __future__ import annotations

import hashlib
import unittest

from unison_snapshot.senate import SenateEfdError
from unison_snapshot.senate_annual_transactions import observe_part4b


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
