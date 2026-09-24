"""White House annual holdings overlay stays report-complete and idempotent."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unison_snapshot.whitehouse_annual_candidate import overlay_whitehouse_annual_candidate


def _base() -> dict:
    return {
        "meta": {"is_demo": False, "data_cutoff_at": "2026-09-22T23:59:59Z",
                 "title": "x", "subtitle": "x", "timezone": "America/New_York",
                 "default_window_days": 30},
        "people": [], "transactions": [], "reported_holdings": [],
        "security_market_data": [],
        "source_health": [{"source_id": "oge", "source": "OGE",
                           "source_type": "official_disclosure",
                           "source_url": "https://www.oge.gov/", "status": "partial",
                           "last_checked_at": "2026-09-22T23:59:59Z",
                           "last_successful_sync_at": "2026-09-22T23:59:59Z",
                           "data_cutoff_at": "2026-09-22T23:59:59Z", "detail": "base"}],
    }


def _annual() -> dict:
    report = {"document_id": "wh-url:123456789012345678901234",
              "filer_reported_name": "Example, Ada",
              "position_title_raw": "Assistant to the President",
              "agency_office_raw": "White House", "source_holdings_eligible": True,
              "source_url": "https://www.whitehouse.gov/wp-content/uploads/example.pdf",
              "source_sha256": "a" * 64, "filing_date": "2026-05-15",
              "report_period_end": "2025-12-31"}
    holding = {"row_id": "wh-annual:" + "b" * 24,
               "document_id": report["document_id"], "filer_reported_name": "Example, Ada",
               "asset_owner": "Unknown", "asset_name": "Example Fund",
               "value_low": 1001, "value_high": 15000,
               "report_period_end": "2025-12-31", "source_holdings_eligible": True,
               "source_url": report["source_url"], "source_sha256": report["source_sha256"]}
    report.update(source_candidate_eligible=True, holding_coverage_status="complete")
    return {"schema_version": "whitehouse-annual-filer-reported/v2",
            "production_status": "review_only_complete_or_source_bound_partial_rows",
            "report_count": 1, "holding_count": 1,
            "source_eligible_report_count": 1, "source_candidate_report_count": 1,
            "source_partial_report_count": 0, "source_eligible_holding_count": 1,
            "reports": [report], "holdings": [holding]}


class WhiteHouseAnnualCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        path = self.root / "whitehouse/annual/filer-reported-current.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(_annual()), encoding="utf-8")

    def test_unknown_owner_is_published_as_filer_reported(self) -> None:
        candidate, audit = overlay_whitehouse_annual_candidate(_base(), self.root)
        self.assertEqual(audit["annual_holding_count"], 1)
        self.assertEqual(audit["annual_unknown_owner_count"], 1)
        self.assertEqual(candidate["reported_holdings"][0]["owner"], "Unknown")
        self.assertEqual(candidate["reported_holdings"][0]["verification_status"],
                         "official_matched")
        self.assertEqual(len(candidate["people"]), 1)

    def test_source_bound_annual_transaction_is_published(self) -> None:
        annual = _annual()
        report = annual["reports"][0]
        report["source_candidate_transaction_eligible"] = True
        annual.update(
            schema_version="whitehouse-annual-filer-reported/v3",
            source_candidate_transaction_report_count=1,
            transaction_count=1, source_eligible_transaction_count=1,
            transactions=[{
                "row_id": "wh-annual-tx:" + "d" * 24,
                "document_id": report["document_id"],
                "filer_reported_name": report["filer_reported_name"],
                "asset_owner": "Unknown", "asset_name": "Vanguard Growth ETF",
                "transaction_type": "purchase", "transaction_date": "2025-09-18",
                "amount_low": 1001, "amount_high": 15000,
                "source_transaction_eligible": True,
                "source_url": report["source_url"],
                "source_sha256": report["source_sha256"],
            }])
        path = self.root / "whitehouse/annual/filer-reported-current.json"
        path.write_text(json.dumps(annual), encoding="utf-8")
        candidate, audit = overlay_whitehouse_annual_candidate(_base(), self.root)
        self.assertEqual(audit["annual_transaction_count"], 1)
        self.assertEqual(candidate["transactions"][0]["owner"], "Unknown")
        self.assertEqual(candidate["transactions"][0]["verification_status"],
                         "official_matched")

    def test_overlay_replaces_prior_whitehouse_annual_rows(self) -> None:
        first, _ = overlay_whitehouse_annual_candidate(_base(), self.root)
        second, _ = overlay_whitehouse_annual_candidate(first, self.root)
        self.assertEqual(first, second)

    def test_existing_unique_oge_person_is_reused(self) -> None:
        base = _base()
        first, _ = overlay_whitehouse_annual_candidate(base, self.root)
        person = first["people"][0]
        base["people"] = [person]
        base["transactions"] = [{"id": "oge-278t:" + "c" * 24,
          "filing_id": "existing", "person_id": person["id"], "owner": "Self",
          "asset_name": "ABC Inc.", "ticker": "ABC", "ticker_mapping_basis": "filing_explicit",
          "instrument_type": "Stock", "option_type": None, "strike_price": None,
          "expiration_date": None, "transaction_type": "purchase",
          "transaction_date": "2026-01-01", "filed_at": "2026-01-02T00:00:00Z",
          "amount_low": 1001, "amount_high": 15000, "position_effect": "unknown",
          "position_effect_basis": None, "source_id": "oge", "source": "OGE",
          "source_url": "https://www.oge.gov/report.pdf", "verification_status": "official_matched"}]
        result, _ = overlay_whitehouse_annual_candidate(base, self.root)
        self.assertEqual(len(result["people"]), 1)
        self.assertEqual(result["reported_holdings"][0]["person_id"], person["id"])

    def test_partial_report_is_labeled_without_claiming_completeness(self) -> None:
        annual = _annual()
        report = annual["reports"][0]
        report.update(source_holdings_eligible=False, holding_coverage_status="partial")
        annual.update(source_eligible_report_count=0, source_partial_report_count=1)
        path = self.root / "whitehouse/annual/filer-reported-current.json"
        path.write_text(json.dumps(annual), encoding="utf-8")
        candidate, audit = overlay_whitehouse_annual_candidate(_base(), self.root)
        self.assertEqual(audit["annual_complete_report_count"], 0)
        self.assertEqual(audit["annual_partial_report_count"], 1)
        self.assertIn("0 complete and 1 partial", candidate["source_health"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
