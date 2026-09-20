from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.builder import normalize
from unison_snapshot.senate import SenateEfdError
from unison_snapshot.senate_candidate import build_senate_candidate
from unison_snapshot.senate_reports import ELECTRONIC_EXTRACTION_SCHEMA


CATALOG_SHA = "a" * 64
ROSTER_SHA = "b" * 64
CONGRESS_ROSTER_SHA = "d" * 64
IDENTITY_BINDING = hashlib.sha256(
    f"{ROSTER_SHA}:{CONGRESS_ROSTER_SHA}".encode("ascii")).hexdigest()
PARSER = "senate-efd-report-parser-test"
BASE = {
    "meta": {
        "is_demo": False,
        "data_cutoff_at": "2026-09-18T00:00:00Z",
        "title": "Candidate",
        "subtitle": "Candidate",
        "timezone": "America/New_York",
        "default_window_days": 30,
    },
    "people": [],
    "transactions": [],
    "reported_holdings": [],
    "security_market_data": [],
    "source_health": [{"source_id": "senate_efd"}],
}


def identity(document_id: str, *, person_id="senate:S000001", match_class="exact"):
    if match_class == "unresolved":
        return {
            "document_id": document_id,
            "filer_name": "Unknown Senator",
            "match_class": "unresolved",
            "status": "unresolved",
        }
    return {
        "document_id": document_id,
        "evidence_url": "https://bioguide.congress.gov/search/bio/S000001",
        "filer_name": "Sample Senator",
        "match_class": match_class,
        "official_name": "Senator (I-DC)",
        "party": "I",
        "person_id": person_id,
        "roster_sha256": ROSTER_SHA,
        "congress_roster_sha256": CONGRESS_ROSTER_SHA,
        "state": "DC",
        "status": "matched_automatically",
    }


def row(row_id="senate-ptr:111111111111111111111111", **updates):
    value = {
        "amount_raw": "$1,001 - $15,000",
        "asset_name_raw": "Acme Inc",
        "asset_type_raw": "Stock",
        "extraction_id": row_id,
        "owner_raw": "Child",
        "qualification_status": "eligible",
        "quarantine_reasons": [],
        "row_number": 1,
        "ticker_raw": "ACME",
        "transaction_date": "2026-09-01",
        "transaction_type": "purchase",
    }
    value.update(updates)
    return value


def extraction(document_id: str, rows=None, **updates):
    value = {
        "document_id": document_id,
        "evidence_complete": True,
        "filed_at_raw": "Filed 09/17/2026 @ 8:55 AM",
        "parser_version": PARSER,
        "portal_listed_date": "2026-09-17",
        "report_amendment_number": None,
        "report_label_date": "2026-09-17",
        "report_title_date": "2026-09-17",
        "schema_version": ELECTRONIC_EXTRACTION_SCHEMA,
        "source_id": "senate_efd",
        "source_sha256": "c" * 64,
        "source_url": f"https://efdsearch.senate.gov/search/view/ptr/{document_id}/",
        "transactions": rows if rows is not None else [row()],
    }
    value.update(updates)
    return value


class SenateCandidateTests(unittest.TestCase):
    def fixture(self, root: Path, identities: list[dict], extractions: list[dict]):
        identity_path = (root / "senate_efd" / "identities" / CATALOG_SHA /
                         f"{IDENTITY_BINDING}.json")
        identity_path.parent.mkdir(parents=True)
        identity_path.write_text(json.dumps({
            "schema_version": "senate-efd-identities/v1",
            "source_id": "senate_efd",
            "catalog_sha256": CATALOG_SHA,
            "roster_sha256": ROSTER_SHA,
            "congress_roster_sha256": CONGRESS_ROSTER_SHA,
            "report_count": len(identities),
            "identities": identities,
        }), encoding="utf-8")
        for item in extractions:
            path = root / "senate_efd" / "extractions" / item["document_id"] / item["source_sha256"] / f"{PARSER}.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(item), encoding="utf-8")
        extracted = sum(len(item["transactions"]) for item in extractions)
        status = {
            "schema_version": "senate-review-run/v1",
            "source_id": "senate_efd",
            "status": "catalog_ready_for_review",
            "catalog_sha256": CATALOG_SHA,
            "catalog_record_count": len(identities),
            "identity_roster_sha256": ROSTER_SHA,
            "identity_congress_roster_sha256": CONGRESS_ROSTER_SHA,
            "identity_binding_sha256": IDENTITY_BINDING,
            "report_parser_version": PARSER,
            "report_entrypoint_count": len(identities),
            "report_entrypoint_pending_count": 0,
            "report_entrypoint_failure_count": 0,
            "report_evidence_count": len(extractions),
            "report_extraction_failure_count": 0,
            "extracted_transaction_count": extracted,
        }
        status_path = root / "status" / "senate_efd.json"
        status_path.parent.mkdir(parents=True)
        status_path.write_text(json.dumps(status), encoding="utf-8")
        return {
            "schema_version": "senate-source-run/v1",
            "source_id": "senate_efd",
            "status": "catalog_ready_for_review",
            "collection_enabled": True,
            "terms_acknowledged": True,
            "run_at": "2026-09-20T12:00:00Z",
            "historical_roster": {
                "status": "ok",
                "source_id": "congress_gov_members",
                "congress": 119,
                "record_count": 100,
                "sha256": CONGRESS_ROSTER_SHA,
            },
            "catalog": {"record_count": len(identities), "sha256": CATALOG_SHA},
            "reports": {
                "entrypoint_count": len(identities),
                "pending_count": 0,
                "evidence_count": len(extractions),
                "last_batch_transactions": extracted,
            },
        }

    def test_builds_contract_valid_candidate_and_option(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            document_id = "11111111-1111-4111-8111-111111111111"
            option = row(
                asset_name_raw="Acme Inc Option Type: Call Strike price: $12.50 Expires: 2027-01-16",
                asset_type_raw="Stock Option",
                ticker_raw=None,
            )
            state = self.fixture(root, [identity(document_id)], [extraction(document_id, [option])])
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))

            self.assertEqual((len(candidate["people"]), len(candidate["transactions"])), (1, 1))
            transaction = candidate["transactions"][0]
            self.assertEqual((transaction["owner"], transaction["asset_name"]),
                             ("Dependent Child", "Acme Inc"))
            self.assertEqual((transaction["option_type"], transaction["strike_price"]),
                             ("Call", 12.5))
            self.assertNotIn("filed_at_raw", transaction)
            self.assertNotIn("report_title_date", transaction)
            self.assertNotIn("portrait_source_url", candidate["people"][0])
            normalize(candidate, allow_production=True)
            self.assertEqual(audit["candidate_transaction_count"], 1)
            self.assertEqual(audit["input_transaction_count"], 1)
            self.assertEqual(audit["fully_qualified_report_count"], 1)
            self.assertEqual(audit["qualified_rows"][0]["source_sha256"], "c" * 64)

    def test_resolves_unique_amendment_and_quarantines_other_invalid_records(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base_id = "21111111-1111-4111-8111-111111111111"
            amendment_id = "31111111-1111-4111-8111-111111111111"
            unresolved_id = "41111111-1111-4111-8111-111111111111"
            normal_id = "51111111-1111-4111-8111-111111111111"
            identities = [identity(base_id), identity(amendment_id),
                          identity(unresolved_id, match_class="unresolved"), identity(normal_id)]
            extractions = [
                extraction(base_id, [row("senate-ptr:211111111111111111111111")],
                           filed_at_raw="Filed 09/16/2026 @ 8:55 AM",
                           portal_listed_date="2026-09-16"),
                extraction(amendment_id, [row("senate-ptr:311111111111111111111111")],
                           report_amendment_number=1),
                extraction(unresolved_id, [row("senate-ptr:411111111111111111111111")]),
                extraction(normal_id, [row("senate-ptr:511111111111111111111111", transaction_type="exchange"),
                                       row("senate-ptr:611111111111111111111111", ticker_raw="-- AMCR")],
                           report_title_date="2026-09-18", report_label_date="2026-09-18"),
            ]
            state = self.fixture(root, identities, extractions)
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))

            self.assertEqual(len(candidate["transactions"]), 1)
            self.assertEqual(candidate["transactions"][0]["filing_id"], amendment_id)
            self.assertEqual(audit["quarantined_report_count"], 1)
            self.assertEqual(audit["quarantined_report_reasons"], {
                "identity_unresolved": 1,
            })
            self.assertEqual(audit["quarantined_row_count"], 2)
            self.assertEqual(audit["quarantined_row_reasons"], {
                "ticker_invalid": 1,
                "transaction_type_not_supported": 1,
            })
            self.assertEqual(audit["report_quarantined_transaction_count"], 1)
            self.assertEqual(audit["quarantined_transaction_count"], 3)
            self.assertEqual(audit["resolved_amendment_chain_count"], 1)
            self.assertEqual(audit["superseded_report_count"], 1)
            self.assertEqual(audit["superseded_report_transaction_count"], 1)
            self.assertEqual(audit["input_transaction_count"], 5)
            self.assertIn("1 transactions superseded", candidate["source_health"][0]["detail"])

    def test_resolves_complete_three_version_amendment_chain(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            document_ids = [f"{prefix}1111111-1111-4111-8111-111111111111"
                            for prefix in ("2", "3", "4")]
            extractions = [
                extraction(document_ids[0], [row("senate-ptr:211111111111111111111111")],
                           filed_at_raw="Filed 09/15/2026 @ 8:00 AM",
                           portal_listed_date="2026-09-15"),
                extraction(document_ids[1], [row("senate-ptr:311111111111111111111111")],
                           report_amendment_number=1,
                           filed_at_raw="Filed 09/16/2026 @ 8:00 AM",
                           portal_listed_date="2026-09-16"),
                extraction(document_ids[2], [row("senate-ptr:411111111111111111111111")],
                           report_amendment_number=2,
                           filed_at_raw="Filed 09/17/2026 @ 8:00 AM"),
            ]
            state = self.fixture(root, [identity(value) for value in document_ids], extractions)
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual([item["filing_id"] for item in candidate["transactions"]],
                             [document_ids[2]])
            self.assertEqual(audit["superseded_report_count"], 2)
            self.assertEqual(audit["superseded_report_transaction_count"], 2)
            self.assertEqual(audit["resolved_amendment_chains"][0]["superseded_document_ids"],
                             [document_ids[1], document_ids[0]])
            self.assertEqual(len(audit["resolved_amendment_chains"][0]["links"]), 2)

    def test_amendment_filed_before_predecessor_is_quarantined(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base_id = "25111111-1111-4111-8111-111111111111"
            amendment_id = "35111111-1111-4111-8111-111111111111"
            state = self.fixture(
                root,
                [identity(base_id), identity(amendment_id)],
                [extraction(base_id, [row("senate-ptr:251111111111111111111111")],
                            filed_at_raw="Filed 09/17/2026 @ 9:00 AM"),
                 extraction(amendment_id, [row("senate-ptr:351111111111111111111111")],
                            report_amendment_number=1,
                            filed_at_raw="Filed 09/17/2026 @ 8:00 AM")],
            )
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual(candidate["transactions"], [])
            self.assertEqual(audit["quarantined_report_reasons"], {
                "amendment_relationship_pending": 2,
            })

    def test_congress_roster_state_must_match_review_identity_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            document_id = "65111111-1111-4111-8111-111111111111"
            state = self.fixture(root, [identity(document_id)], [extraction(document_id)])
            state["historical_roster"]["sha256"] = "e" * 64
            with self.assertRaisesRegex(SenateEfdError, "Congress.gov roster"):
                build_senate_candidate(root, state, deepcopy(BASE))

    def test_amendment_must_match_one_unique_predecessor(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base_id = "22111111-1111-4111-8111-111111111111"
            amendment_id = "32111111-1111-4111-8111-111111111111"
            amended = row("senate-ptr:321111111111111111111111", amount_raw="$15,001 - $50,000")
            state = self.fixture(
                root,
                [identity(base_id), identity(amendment_id)],
                [extraction(base_id, [row("senate-ptr:221111111111111111111111")]),
                 extraction(amendment_id, [amended], report_amendment_number=1)],
            )
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual(candidate["transactions"], [])
            self.assertEqual(audit["quarantined_report_reasons"], {
                "amendment_relationship_pending": 2,
            })
            self.assertEqual(audit["resolved_amendment_chain_count"], 0)

    def test_amendment_group_quarantines_unlinked_duplicate_predecessor(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base_ids = ["26111111-1111-4111-8111-111111111111",
                        "27111111-1111-4111-8111-111111111111"]
            amendment_id = "36111111-1111-4111-8111-111111111111"
            extractions = [
                extraction(base_ids[0], [row("senate-ptr:261111111111111111111111")],
                           filed_at_raw="Filed 09/15/2026 @ 8:00 AM",
                           portal_listed_date="2026-09-15"),
                extraction(base_ids[1], [row("senate-ptr:271111111111111111111111",
                                             amount_raw="$15,001 - $50,000")],
                           filed_at_raw="Filed 09/15/2026 @ 9:00 AM",
                           portal_listed_date="2026-09-15"),
                extraction(amendment_id, [row("senate-ptr:361111111111111111111111")],
                           report_amendment_number=1,
                           filed_at_raw="Filed 09/17/2026 @ 8:00 AM"),
            ]
            identities = [identity(value) for value in [*base_ids, amendment_id]]
            state = self.fixture(root, identities, extractions)
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual([item["filing_id"] for item in candidate["transactions"]],
                             [amendment_id])
            self.assertEqual(audit["quarantined_report_count"], 1)
            self.assertEqual(audit["quarantined_report_reasons"], {
                "amendment_relationship_pending": 1,
            })
            self.assertEqual(audit["superseded_report_count"], 1)
            self.assertEqual(audit["input_transaction_count"], 3)
            self.assertEqual(audit["candidate_transaction_count"] +
                             audit["quarantined_transaction_count"] +
                             audit["superseded_report_transaction_count"], 3)

    def test_state_and_review_count_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            document_id = "61111111-1111-4111-8111-111111111111"
            state = self.fixture(root, [identity(document_id)], [extraction(document_id)])
            state["reports"]["last_batch_transactions"] = 2
            with self.assertRaises(SenateEfdError):
                build_senate_candidate(root, state, deepcopy(BASE))

    def test_unknown_identity_class_and_inconsistent_qualification_do_not_advance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            identity_document = "71111111-1111-4111-8111-111111111111"
            row_document = "81111111-1111-4111-8111-111111111111"
            unknown = identity(identity_document)
            unknown["match_class"] = "manual"
            inconsistent = row(quarantine_reasons=["source_parser_warning"])
            state = self.fixture(
                root,
                [unknown, identity(row_document)],
                [extraction(identity_document), extraction(row_document, [inconsistent],
                                                               report_title_date="2026-09-18",
                                                               report_label_date="2026-09-18")],
            )
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual(candidate["transactions"], [])
            self.assertEqual(audit["quarantined_report_reasons"],
                             {"identity_not_automatically_matched": 1})
            self.assertEqual(audit["quarantined_row_reasons"], {
                "qualification_state_inconsistent": 1,
                "source_parser_warning": 1,
            })

    def test_extraction_payload_must_match_content_addressed_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            document_id = "91111111-1111-4111-8111-111111111111"
            item = extraction(document_id)
            state = self.fixture(root, [identity(document_id)], [item])
            path = next((root / "senate_efd" / "extractions").glob("*/*/*.json"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["source_sha256"] = "d" * 64
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(SenateEfdError):
                build_senate_candidate(root, state, deepcopy(BASE))

    def test_report_date_conflict_counts_all_transactions_as_quarantined(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            document_id = "a1111111-1111-4111-8111-111111111111"
            rows = [row("senate-ptr:a11111111111111111111111"),
                    row("senate-ptr:b11111111111111111111111")]
            item = extraction(document_id, rows, portal_listed_date="2026-09-16")
            state = self.fixture(root, [identity(document_id)], [item])
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual(candidate["transactions"], [])
            self.assertEqual(audit["report_quarantined_transaction_count"], 2)
            self.assertEqual(audit["quarantined_transaction_count"], 2)


if __name__ == "__main__":
    unittest.main()
