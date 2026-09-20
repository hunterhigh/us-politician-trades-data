from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unison_snapshot.codec import encode
from unison_snapshot.disclosure_candidate import (
    DisclosureCandidateError,
    build_disclosure_candidate,
    project_source_candidate,
)


CUTOFF = "2026-09-20T00:00:00Z"
BASE = {
    "meta": {"is_demo": False, "data_cutoff_at": "2026-01-01T00:00:00Z",
             "title": "Candidate", "subtitle": "All official sources"},
    "people": [],
    "transactions": [],
    "reported_holdings": [],
    "security_market_data": [{"ticker": "KEEP", "date": "2026-09-19"}],
    "source_health": [
        {"source_id": "house_clerk", "status": "disabled"},
        {"source_id": "senate_efd", "status": "disabled"},
        {"source_id": "alpaca_sip_eod", "status": "disabled",
         "last_checked_at": CUTOFF},
    ],
}


def candidate(source_id: str, person_id: str, fact_id: str, *, holding: bool = False) -> dict:
    authority = "U.S. House Clerk" if source_id == "house_clerk" else "U.S. Senate eFD"
    person = {
        "id": person_id,
        "display_name": "Example Person",
        "short_name": "Person",
        "role": "U.S. Representative" if source_id == "house_clerk" else "U.S. Senator",
        "office_type": "Congress",
        "chamber": "House" if source_id == "house_clerk" else "Senate",
        "party": "I",
        "state": "ME",
        "disclosure_authority": source_id,
        "priority": False,
        "priority_reason": None,
        "portrait_url": None,
    }
    fact = {
        "id": fact_id,
        "filing_id": fact_id + "-filing",
        "person_id": person_id,
        "owner": "Self",
        "asset_name": "Example Asset",
        "ticker": "EXM",
        "ticker_mapping_basis": "filing_explicit",
        "instrument_type": "Stock",
        "filed_at": "2026-09-19T00:00:00Z",
        "source_id": source_id,
        "source": authority,
        "source_url": "https://example.invalid/filing",
        "verification_status": "official_matched",
    }
    if holding:
        fact.update(report_period_end="2026-08-31", value_low=1001, value_high=15000,
                    change_from_prior="unknown")
    else:
        fact.update(transaction_type="purchase", transaction_date="2026-09-01",
                    amount_low=1001, amount_high=15000, position_effect="unknown",
                    position_effect_basis=None)
    return {
        "meta": {"is_demo": False, "data_cutoff_at": CUTOFF},
        "people": [person],
        "transactions": [] if holding else [fact],
        "reported_holdings": [fact] if holding else [],
        "security_market_data": [],
        "source_health": [{
            "source_id": source_id,
            "source": authority,
            "status": "partial",
            "last_checked_at": CUTOFF,
            "last_successful_sync_at": CUTOFF,
            "data_cutoff_at": CUTOFF,
        }],
    }


class DisclosureCandidateTests(unittest.TestCase):
    def setUp(self):
        self.house = candidate("house_clerk", "house:P000197", "house-ptr:one")
        self.senate = candidate("senate_efd", "senate:M001153", "senate-ptr:one",
                                holding=True)

    def test_merges_house_and_senate_deterministically_without_rewriting_rows(self):
        first = build_disclosure_candidate(
            deepcopy(BASE), {"senate_efd": self.senate, "house_clerk": self.house}
        )
        second = build_disclosure_candidate(
            deepcopy(BASE), {"house_clerk": self.house, "senate_efd": self.senate}
        )

        self.assertEqual(encode(first), encode(second))
        self.assertEqual(first["meta"]["data_cutoff_at"], CUTOFF)
        self.assertEqual(first["transactions"], self.house["transactions"])
        self.assertEqual(first["reported_holdings"], self.senate["reported_holdings"])
        self.assertEqual([row["id"] for row in first["people"]],
                         ["house:P000197", "senate:M001153"])
        self.assertEqual(first["security_market_data"], BASE["security_market_data"])
        self.assertEqual([row["source_id"] for row in first["source_health"]],
                         ["house_clerk", "senate_efd", "alpaca_sip_eod"])

    def test_projection_extracts_only_requested_source_and_preserves_house_bytes(self):
        mixed = deepcopy(self.house)
        mixed["people"] += deepcopy(self.senate["people"])
        mixed["reported_holdings"] += deepcopy(self.senate["reported_holdings"])
        mixed["source_health"] += deepcopy(self.senate["source_health"])

        projection = project_source_candidate("house_clerk", mixed)

        self.assertEqual(encode(projection["people"]), encode(self.house["people"]))
        self.assertEqual(encode(projection["transactions"]),
                         encode(self.house["transactions"]))
        self.assertEqual(projection["reported_holdings"], [])

    def test_identity_conflict_fails_closed(self):
        senate = deepcopy(self.senate)
        senate["people"][0] = deepcopy(self.house["people"][0])
        senate["people"][0]["display_name"] = "Another Name"
        senate["people"][0]["disclosure_authority"] = "senate_efd"
        senate["reported_holdings"][0]["person_id"] = "house:P000197"

        with self.assertRaisesRegex(DisclosureCandidateError, "Identity fields conflict"):
            build_disclosure_candidate(
                deepcopy(BASE), {"house_clerk": self.house, "senate_efd": senate}
            )

    def test_duplicate_fact_id_fails_closed_across_fact_kinds(self):
        senate = deepcopy(self.senate)
        senate["reported_holdings"][0]["id"] = self.house["transactions"][0]["id"]

        with self.assertRaisesRegex(DisclosureCandidateError, "Duplicate disclosure fact ID"):
            build_disclosure_candidate(
                deepcopy(BASE), {"house_clerk": self.house, "senate_efd": senate}
            )

    def test_missing_person_reference_fails_closed(self):
        house = deepcopy(self.house)
        house["transactions"][0]["person_id"] = "house:UNKNOWN"

        with self.assertRaisesRegex(DisclosureCandidateError, "unknown person"):
            build_disclosure_candidate(deepcopy(BASE), {"house_clerk": house})

    def test_unready_source_fails_closed(self):
        house = deepcopy(self.house)
        house["source_health"][0]["status"] = "pending"

        with self.assertRaisesRegex(DisclosureCandidateError, "not ready"):
            build_disclosure_candidate(deepcopy(BASE), {"house_clerk": house})

    def test_cutoff_disagreement_fails_closed(self):
        with self.subTest("source candidates"):
            senate = deepcopy(self.senate)
            senate["meta"]["data_cutoff_at"] = "2026-09-19T00:00:00Z"
            senate["source_health"][0]["data_cutoff_at"] = "2026-09-19T00:00:00Z"
            with self.assertRaisesRegex(DisclosureCandidateError, "cutoffs do not match"):
                build_disclosure_candidate(
                    deepcopy(BASE), {"house_clerk": self.house, "senate_efd": senate}
                )

        with self.subTest("health and candidate"):
            house = deepcopy(self.house)
            house["source_health"][0]["data_cutoff_at"] = "2026-09-19T00:00:00Z"
            with self.assertRaisesRegex(DisclosureCandidateError, "health cutoff"):
                build_disclosure_candidate(deepcopy(BASE), {"house_clerk": house})

    def test_explicit_harmonization_uses_earliest_waterline_and_filters_later_facts(self):
        house = deepcopy(self.house)
        house["transactions"][0]["filed_at"] = "2026-09-19T12:00:00Z"
        senate = deepcopy(self.senate)
        senate["reported_holdings"][0]["filed_at"] = "2026-09-18T00:00:00Z"
        senate_cutoff = "2026-09-19T00:00:00Z"
        senate["meta"]["data_cutoff_at"] = senate_cutoff
        senate["source_health"][0]["data_cutoff_at"] = senate_cutoff
        senate["source_health"][0]["last_successful_sync_at"] = senate_cutoff
        base = deepcopy(BASE)
        base["security_market_data"] = []
        before = encode({"house": house, "senate": senate})
        audit = {}

        result = build_disclosure_candidate(
            base, {"house_clerk": house, "senate_efd": senate},
            harmonize_cutoffs=True,
            harmonization_audit=audit,
        )

        self.assertEqual(result["meta"]["data_cutoff_at"], "2026-09-18T23:59:59Z")
        self.assertEqual(result["transactions"], [])
        self.assertEqual(result["reported_holdings"], senate["reported_holdings"])
        self.assertEqual([row["id"] for row in result["people"]], ["senate:M001153"])
        health = {row["source_id"]: row for row in result["source_health"]}
        self.assertEqual(health["house_clerk"]["data_cutoff_at"], "2026-09-18T23:59:59Z")
        self.assertEqual(health["house_clerk"]["last_successful_sync_at"], CUTOFF)
        self.assertEqual(encode({"house": house, "senate": senate}), before)
        self.assertEqual(audit["mode"], "last_complete_shared_utc_day")
        self.assertEqual(audit["sources"]["house_clerk"]["filtered_transaction_count"], 1)
        self.assertRegex(audit["sources"]["house_clerk"]["candidate_sha256"], r"^[0-9a-f]{64}$")

    def test_harmonization_rejects_market_data_without_a_market_cutoff_policy(self):
        with self.assertRaisesRegex(DisclosureCandidateError, "Market data"):
            build_disclosure_candidate(
                deepcopy(BASE), {"house_clerk": self.house, "senate_efd": self.senate},
                harmonize_cutoffs=True,
            )

    def test_harmonization_rejects_stale_sources_and_native_cutoff_violations(self):
        with self.subTest("stale source"):
            senate = deepcopy(self.senate)
            stale = "2026-09-17T00:00:00Z"
            senate["meta"]["data_cutoff_at"] = stale
            senate["source_health"][0]["data_cutoff_at"] = stale
            senate["reported_holdings"][0]["filed_at"] = "2026-09-16T00:00:00Z"
            base = deepcopy(BASE)
            base["security_market_data"] = []
            with self.assertRaisesRegex(DisclosureCandidateError, "too stale"):
                build_disclosure_candidate(
                    base, {"house_clerk": self.house, "senate_efd": senate},
                    harmonize_cutoffs=True,
                )
        with self.subTest("native cutoff"):
            house = deepcopy(self.house)
            house["transactions"][0]["filed_at"] = "2026-09-21T00:00:00Z"
            with self.assertRaisesRegex(DisclosureCandidateError, "native cutoff"):
                build_disclosure_candidate(deepcopy(BASE), {"house_clerk": house})

    def test_rejects_demo_or_nonempty_disclosure_base(self):
        with self.subTest("demo"):
            base = deepcopy(BASE)
            base["meta"]["is_demo"] = True
            with self.assertRaisesRegex(DisclosureCandidateError, "production"):
                build_disclosure_candidate(base, {"house_clerk": self.house})
        with self.subTest("hidden base facts"):
            base = deepcopy(BASE)
            base["people"] = deepcopy(self.house["people"])
            with self.assertRaisesRegex(DisclosureCandidateError, "must be empty"):
                build_disclosure_candidate(base, {"house_clerk": self.house})


if __name__ == "__main__":
    unittest.main()
