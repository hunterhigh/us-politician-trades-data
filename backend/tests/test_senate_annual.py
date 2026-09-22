"""Source-bound Senate annual discovery, amendment choice, and holdings."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.senate_annual import (
    DISCOVERY_SCHEMA, build_annual_review, extract_annual, overlay_annual_candidate,
    parse_annual_search_page, _json,
)
from tests.test_senate_identity import member, roster


def annual_html(*, amendment: int = 0, value: str = "$1,001 - $15,000",
                filed: str = "05/01/2026") -> bytes:
    suffix = f" (Amendment {amendment})" if amendment else ""
    return (f"<!doctype html><html><body><h1>Annual Report for Calendar 2025{suffix}</h1>"
            f"<h2>Senator Ada Example (Example, Ada)</h2><p>Filed {filed} @ 1:00 PM</p>"
            "<table><tr><th></th><th>Asset</th><th>Asset Type</th><th>Owner</th>"
            "<th>Value</th><th>Income Type</th><th>Income</th></tr>"
            f"<tr><td>1</td><td>ACME - Acme Corp</td><td>Corporate SecuritiesStock</td>"
            f"<td>Self</td><td>{value}</td><td>Dividends</td><td>--</td></tr>"
            "<tr><td>2</td><td>Old Income Asset</td><td>Corporate SecuritiesStock</td>"
            "<td>Spouse</td><td>None (or less than $1,001)</td><td>Dividends</td>"
            "<td>$201 - $1,000</td></tr></table></body></html>").encode()


class SenateAnnualTests(unittest.TestCase):
    def test_catalog_filters_candidate_and_keeps_annual_amendment(self):
        value = {"draw": 1, "recordsTotal": 2, "recordsFiltered": 2,
                 "result": "ok", "data": [
                     ["Ada", "Example", "Example, Ada (Senator)",
                      '<a href="/search/view/annual/11111111-1111-1111-1111-111111111111/" '
                      'target="_blank">Annual Report for CY 2025 (Amendment 1)</a>',
                      "05/02/2026"],
                     ["Other", "Person", "Candidate (Candidate)",
                      '<a href="/search/view/annual/22222222-2222-2222-2222-222222222222/" '
                      'target="_blank">Candidate Report</a>', "05/01/2026"],
                 ]}
        total, rows, skipped = parse_annual_search_page(value, start=0, length=100)
        self.assertEqual((total, skipped, len(rows)), (2, 1, 1))
        self.assertEqual((rows[0]["report_year"], rows[0]["amendment_number"]), (2025, 1))

    def test_extraction_preserves_valuation_and_excludes_no_end_value(self):
        raw = annual_html()
        metadata = {"schema_version": "senate-efd-annual-archive/v1",
                    "document_id": "11111111-1111-1111-1111-111111111111",
                    "document_url": "https://efdsearch.senate.gov/search/view/annual/"
                                    "11111111-1111-1111-1111-111111111111/",
                    "source_sha256": hashlib.sha256(raw).hexdigest(),
                    "byte_length": len(raw), "filer_name": "Ada Example",
                    "report_year": 2025, "amendment_number": 0,
                    "portal_listed_date": "2026-05-01"}
        parsed = extract_annual(metadata, raw)
        self.assertEqual(parsed["report_period_end"], "2025-12-31")
        self.assertEqual(parsed["filed_at"], "2026-05-01")
        self.assertEqual([row["disposition"] for row in parsed["rows"]],
                         ["eligible", "excluded"])
        self.assertEqual((parsed["rows"][0]["ticker"], parsed["rows"][0]["value_low"]),
                         ("ACME", 1001))

    def test_latest_complete_amendment_replaces_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            evidence = root / "evidence"; review = root / "review"
            report_rows = []
            for amendment in (0, 1):
                document_id = f"11111111-1111-1111-1111-{amendment + 1:012d}"
                url = f"https://efdsearch.senate.gov/search/view/annual/{document_id}/"
                day = f"2026-05-0{amendment + 1}"
                raw = annual_html(amendment=amendment,
                                  value="$15,001 - $50,000" if amendment else
                                  "$1,001 - $15,000",
                                  filed=f"05/0{amendment + 1}/2026")
                sha = hashlib.sha256(raw).hexdigest()
                path = evidence / "senate_efd/annual/reports" / document_id
                path.mkdir(parents=True)
                (path / f"{sha}.html").write_bytes(raw)
                meta = {"schema_version": "senate-efd-annual-archive/v1",
                        "document_id": document_id, "document_url": url,
                        "source_sha256": sha, "byte_length": len(raw),
                        "filer_name": "Ada Example", "office": "Example, Ada (Senator)",
                        "portal_listed_date": day, "report_year": 2025,
                        "amendment_number": amendment}
                (path / f"{sha}.metadata.json").write_bytes(_json(meta))
                report_rows.append({"catalog_index": amendment, "document_id": document_id,
                                    "document_url": url, "filer_name": "Ada Example",
                                    "office": "Example, Ada (Senator)",
                                    "portal_listed_date": day, "report_year": 2025,
                                    "amendment_number": amendment})
            catalog = {"schema_version": DISCOVERY_SCHEMA, "source_id": "senate_efd",
                       "catalog_record_count": 2, "annual_report_count": 2,
                       "nonannual_count": 0, "reports": report_rows}
            path = evidence / "senate_efd/annual/catalog/current.json"
            path.parent.mkdir(parents=True)
            path.write_bytes(_json(catalog))
            members = roster(member("A000001", "Ada", "Example"))
            result = build_annual_review(evidence, review, members)
            self.assertEqual((result["qualified_report_count"], result["holding_count"]),
                             (1, 1))
            holding = result["reported_holdings"][0]
            self.assertEqual(holding["filing_id"], report_rows[1]["document_id"])
            self.assertEqual((holding["value_low"], holding["value_high"]), (15001, 50000))
            base = {"people": [], "transactions": [], "reported_holdings": [],
                    "source_health": [{"source_id": "senate_efd", "detail": "PTR"}]}
            combined, audit = overlay_annual_candidate(
                base, review, expected_roster_sha256=members["metadata"]["sha256"])
            self.assertEqual(len(combined["people"]), 1)
            self.assertEqual(len(combined["reported_holdings"]), 1)
            self.assertEqual(audit["annual_holding_count"], 1)
            combined, audit = overlay_annual_candidate(
                combined, review, expected_roster_sha256=members["metadata"]["sha256"])
            self.assertEqual(len(combined["reported_holdings"]), 1)
            self.assertEqual(len(combined["people"]), 1)


if __name__ == "__main__":
    unittest.main()
