from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_candidate import build_house_candidate


BASE = {
    "meta": {"is_demo": False, "data_cutoff_at": "2026-09-18T00:00:00Z", "title": "Candidate"},
    "people": [], "transactions": [], "reported_holdings": [], "security_market_data": [],
    "source_health": [{"source_id": "house_clerk"}],
}
IDENTITY = {"person_id": "house:P000197", "official_name": "Nancy Pelosi", "state": "CA",
            "party": "D", "state_district": "CA11",
            "evidence_url": "https://bioguide.congress.gov/search/bio/P000197"}
TRANSACTION = {"id": "house-ptr:one", "filing_id": "20000001", "person_id": "house:P000197",
               "owner": "Self", "asset_name": "Example", "ticker": "EXM",
               "ticker_mapping_basis": "filing_explicit", "instrument_type": "Stock",
               "transaction_type": "purchase", "transaction_date": "2026-09-01",
               "filed_at": "2026-09-10T00:00:00Z", "amount_low": 1001, "amount_high": 15000,
               "position_effect": "unknown", "position_effect_basis": None,
               "source_id": "house_clerk", "source": "U.S. House Clerk",
               "source_url": "https://disclosures-clerk.house.gov/test.pdf",
               "verification_status": "official_matched"}


class HouseCandidateTests(unittest.TestCase):
    def fixture(self, root: Path):
        qualification = {"schema_version": "house-ptr-qualification/v1", "identity": IDENTITY,
                         "transactions": [TRANSACTION], "quarantined": []}
        target = root / "house_clerk/qualifications/2026/20000001/a.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(qualification), encoding="utf-8")
        summary = {"parser_commit": "a" * 40, "pending_parse_count": 0, "qualification_count": 1,
                   "qualified_transaction_count": 1, "quarantined_row_count": 0,
                   "evidence_count": 1, "extracted_count": 1, "failure_count": 0}
        status = root / "status/summary.json"
        status.parent.mkdir(parents=True)
        status.write_text(json.dumps(summary), encoding="utf-8")

    def test_builds_frontend_candidate_from_qualified_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            state = {"status": "ok", "run_at": "2026-09-18T12:00:00Z",
                     "counts": {"archived": 1, "pending": 2, "failed": 0}}
            result = build_house_candidate(root, state, deepcopy(BASE))
            self.assertEqual((len(result["people"]), len(result["transactions"])), (1, 1))
            self.assertTrue(result["people"][0]["priority"])
            self.assertEqual(result["people"][0]["short_name"], "Pelosi")
            self.assertEqual(result["source_health"][0]["status"], "partial")

    def test_count_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            summary_path = root / "status/summary.json"
            summary = json.loads(summary_path.read_text())
            summary["qualified_transaction_count"] = 2
            summary_path.write_text(json.dumps(summary))
            state = {"status": "ok", "run_at": "2026-09-18T12:00:00Z",
                     "counts": {"archived": 1, "pending": 2, "failed": 0}}
            with self.assertRaises(HouseIndexError):
                build_house_candidate(root, state, deepcopy(BASE))

    def test_parse_status_conflict_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            summary_path = root / "status/summary.json"
            summary = json.loads(summary_path.read_text())
            summary["status_conflict_count"] = 1
            summary_path.write_text(json.dumps(summary))
            state = {"status": "ok", "run_at": "2026-09-18T12:00:00Z",
                     "counts": {"archived": 1, "pending": 0, "failed": 0}}
            with self.assertRaises(HouseIndexError):
                build_house_candidate(root, state, deepcopy(BASE))

    def test_source_scoped_summary_takes_precedence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            scoped = root / "status/house_clerk.json"
            summary = json.loads((root / "status/summary.json").read_text())
            summary["pending_parse_count"] = 1
            scoped.write_text(json.dumps(summary))
            state = {"status": "ok", "run_at": "2026-09-18T12:00:00Z",
                     "counts": {"archived": 1, "pending": 0, "failed": 0}}
            with self.assertRaises(HouseIndexError):
                build_house_candidate(root, state, deepcopy(BASE))

    def test_latest_complete_annual_report_adds_holdings_and_person(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            identity = {**IDENTITY, "roster_sha256": "b" * 64,
                        "status": "matched_automatically",
                        "match_basis": "official_roster_exact_district_first_last_name"}
            holding = {"id": "house-holding:one", "filing_id": "10000002",
                       "person_id": "house:P000197", "owner": "Self",
                       "asset_name": "Example Corp", "ticker": "EXM",
                       "ticker_mapping_basis": "filing_explicit", "instrument_type": "Stock",
                       "report_period_end": "2025-12-31", "filed_at": "2026-05-01T00:00:00Z",
                       "value_low": 1001, "value_high": 15000, "change_from_prior": "unknown",
                       "source_id": "house_clerk", "source": "U.S. House Clerk",
                       "source_url": "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2025/10000002.pdf",
                       "verification_status": "official_matched"}
            artifact = {"schema_version": "house-holding-qualification/v1",
                        "source": {"document_id": "10000002", "filed_date": "2026-05-01"},
                        "report_period_end": "2025-12-31", "identity": identity,
                        "production_eligible": True, "holdings": [holding],
                        "excluded": [], "quarantined": []}
            target = root / "house_clerk/holding_qualifications/2025/10000002/a.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(artifact), encoding="utf-8")
            status = {"qualification_count": 1, "qualified_holding_count": 1,
                      "quarantined_row_count": 0}
            (root / "status/house_holdings.json").write_text(json.dumps(status), encoding="utf-8")
            state = {"status": "ok", "run_at": "2026-09-18T12:00:00Z",
                     "counts": {"archived": 1, "pending": 0, "failed": 0}}
            result = build_house_candidate(root, state, deepcopy(BASE))
            self.assertEqual(result["reported_holdings"], [holding])

    def test_does_not_fall_back_when_latest_annual_report_is_ineligible(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            identity = {**IDENTITY, "roster_sha256": "b" * 64,
                        "status": "matched_automatically",
                        "match_basis": "official_roster_exact_district_first_last_name"}
            holding = {"id": "house-holding:old", "filing_id": "10000001",
                       "person_id": "house:P000197", "owner": "Self", "asset_name": "Old",
                       "ticker": None, "ticker_mapping_basis": None, "instrument_type": "Other",
                       "report_period_end": "2024-12-31", "filed_at": "2025-05-01T00:00:00Z",
                       "value_low": 1001, "value_high": 15000, "change_from_prior": "unknown",
                       "source_id": "house_clerk", "source": "U.S. House Clerk",
                       "source_url": "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2024/10000001.pdf",
                       "verification_status": "official_matched"}
            artifacts = [
                ("2024", "10000001", {"schema_version": "house-holding-qualification/v1",
                    "source": {"document_id": "10000001", "filed_date": "2025-05-01"},
                    "report_period_end": "2024-12-31", "identity": identity,
                    "production_eligible": True, "holdings": [holding], "excluded": [], "quarantined": []}),
                ("2025", "10000002", {"schema_version": "house-holding-qualification/v1",
                    "source": {"document_id": "10000002", "filed_date": "2026-05-01"},
                    "report_period_end": "2025-12-31", "identity": identity,
                    "production_eligible": False, "holdings": [], "excluded": [],
                    "quarantined": [{"extraction_id": "new", "reasons": ["value_not_representable"]}]}),
            ]
            for year, document_id, artifact in artifacts:
                target = root / f"house_clerk/holding_qualifications/{year}/{document_id}/a.json"
                target.parent.mkdir(parents=True)
                target.write_text(json.dumps(artifact), encoding="utf-8")
            (root / "status/house_holdings.json").write_text(json.dumps({
                "qualification_count": 2, "qualified_holding_count": 1,
                "quarantined_row_count": 1}), encoding="utf-8")
            state = {"status": "ok", "run_at": "2026-09-18T12:00:00Z",
                     "counts": {"archived": 1, "pending": 0, "failed": 0}}
            result = build_house_candidate(root, state, deepcopy(BASE))
            self.assertEqual(result["reported_holdings"], [])


if __name__ == "__main__":
    unittest.main()
