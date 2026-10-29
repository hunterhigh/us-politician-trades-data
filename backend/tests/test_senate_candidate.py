from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.builder import normalize
from unison_snapshot.senate import SenateEfdError
from unison_snapshot.senate_candidate import (
    AMENDMENT_COMPARE_RULE_VERSION, build_senate_candidate, _transaction,
)
from unison_snapshot.senate_reports import ELECTRONIC_EXTRACTION_SCHEMA, electronic_ptr_source_ledger
from unison_snapshot.senate_paper import PAPER_EXTRACTION_SCHEMA


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
    def test_empty_report_decision_identifies_only_the_evaluated_source(self):
        doc = "11111111-1111-4111-8111-111111111111"
        empty = extraction(doc, [])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = self.fixture(root, [identity(doc)], [empty])
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual(candidate["transactions"], [])
            self.assertEqual(audit["empty_eligible_report_count"], 1)
            self.assertEqual(audit["empty_eligible_reports"], [{
                "document_id": doc, "source_sha256": empty["source_sha256"],
                "source_url": empty["source_url"], "filed_at_raw": empty["filed_at_raw"],
                "basis": "qualified_empty_transaction_table_with_complete_source_coverage"}])

    def test_official_exchange_reaches_candidate_and_final_consumer_without_buy_sell_inference(self):
        from tests.test_senate_reports import html, metadata_for
        from unison_snapshot.senate_reports import parse_electronic_ptr
        from unison_snapshot.builder import build
        from unison_snapshot.legacy import load
        raw = html(rows=[["1", "09/01/2026", "Self", "--", "Acme", "Stock",
                          "Exchange", "$1,001 - $15,000", ""]])
        parsed = parse_electronic_ptr(metadata_for(raw), raw)
        # Frozen parser output remains unchanged; the candidate resolves only
        # the obsolete exchange-specific rule from the exact official enum.
        self.assertEqual(parsed["transactions"][0]["quarantine_reasons"],
                         ["exchange_requires_contract_resolution"])
        doc = parsed["document_id"]
        item = extraction(doc, parsed["transactions"], source_sha256=parsed["source_sha256"])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = self.fixture(root, [identity(doc)], [item])
            before = deepcopy(item)
            data, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual(item, before)
            self.assertEqual(audit["candidate_transaction_count"], 1)
            tx = data["transactions"][0]
            self.assertEqual(tx["id"], parsed["transactions"][0]["extraction_id"])
            self.assertEqual(tx["transaction_type"], "exchange")
            self.assertEqual(tx["position_effect"], "unknown")
            built = build(data, generated_at="2026-09-20T12:00:00Z", allow_production=True)
            self.assertTrue(built.manifest["snapshot_id"])
            data["meta"]["snapshot_id"] = "exchange-test"
            result = load("process_snapshot", version="v2").build_snapshot(data)
            self.assertEqual(len(result["transactions"]), 1)
            from datetime import date
            moves = load("process_snapshot", version="v2").build_market_moves(
                [dict(tx, ticker="ACME")], {}, date(2026, 9, 18), 30)
            self.assertEqual(moves[0]["transaction_count"], 1)
            self.assertEqual(moves[0]["buy_count"], 0)
            self.assertEqual(moves[0]["sell_count"], 0)

    def test_paper_exchange_mark_reaches_real_candidate(self):
        from tests.test_senate_paper import REPORT, MANIFEST, page, word
        from unison_snapshot.senate_paper import parse_paper_word_pages
        from unison_snapshot.builder import build
        words = [word("(S)", 650, 2280), word("Acme", 760, 2280),
                 word("(Stock)", 980, 2280), word("09/01/26", 1730, 2280),
                 word("X", 1595, 2280), word("X", 1963, 2280)]
        parsed = parse_paper_word_pages(REPORT, MANIFEST, [page(1), page(2, words)])
        observed = parsed["transactions"][0]
        self.assertEqual(observed["transaction_type_raw"], "Exchange")
        doc = REPORT["document_id"]
        item = extraction(doc, [observed], schema_version=PAPER_EXTRACTION_SCHEMA,
                          source_url=REPORT["document_url"], source_sha256=parsed["source_sha256"])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = self.fixture(root, [identity(doc)], [item])
            data, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual(audit["candidate_transaction_count"], 1)
            self.assertEqual(data["transactions"][0]["id"], observed["extraction_id"])
            self.assertEqual(data["transactions"][0]["transaction_type"], "exchange")
            self.assertTrue(build(data, generated_at="2026-09-20T12:00:00Z",
                                  allow_production=True).manifest["snapshot_id"])

    def test_exchange_does_not_relax_unknown_direction_or_other_blockers(self):
        item = extraction("11111111-1111-4111-8111-111111111111")
        for updates in [dict(transaction_type="exchange", transaction_type_raw="Exchange",
                             qualification_status="quarantined", quarantine_reasons=[]),
                        dict(transaction_type="exchange", transaction_type_raw="Gift"),
                        dict(transaction_type="unknown", transaction_type_raw="Exchange"),
                        dict(transaction_type="exchange", transaction_type_raw="Exchange",
                             qualification_status="quarantined",
                             quarantine_reasons=["exchange_requires_contract_resolution", "unreadable"])]:
            with self.subTest(updates=updates):
                tx, reasons = _transaction(row(**updates), item, identity(item["document_id"]))
                self.assertIsNone(tx)
                self.assertTrue(reasons)

    def test_annual_ptr_relations_consume_resolved_current_ptr_without_new_trade(self):
        from tests.test_senate_annual_transactions import sample
        from unison_snapshot.senate_annual_transactions import observe_part4b
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            review, evidence = root / "review", root / "evidence"
            original = "11111111-1111-4111-8111-111111111111"
            amendment = "22222222-2222-4222-8222-222222222222"
            old_id, new_id = "senate-ptr:" + "1" * 24, "senate-ptr:" + "2" * 24
            def sale(identifier, raw_type):
                return row(identifier, owner_raw="Spouse", ticker_raw=None,
                           asset_name_raw="Example Fund", asset_type_raw="Other",
                           transaction_type="sale", transaction_type_raw=raw_type,
                           transaction_date="2025-11-12", amount_raw="$500,001 - $1,000,000")
            before = extraction(original, [sale(old_id, "Sale (Full)")], source_sha256="d" * 64)
            after = extraction(amendment, [sale(new_id, "Sale (Partial)")], source_sha256="e" * 64,
                               report_amendment_number=1, portal_listed_date="2026-09-18",
                               filed_at_raw="Filed 09/18/2026 @ 8:55 AM")
            state = self.fixture(review, [identity(original), identity(amendment)], [before, after])
            metadata, raw = sample()
            observation = observe_part4b(metadata, raw)
            document, sha = metadata["document_id"], metadata["source_sha256"]
            folder = evidence / "senate_efd/annual/reports" / document
            folder.mkdir(parents=True)
            (folder / f"{sha}.html").write_bytes(raw)
            (folder / f"{sha}.metadata.json").write_text(json.dumps(metadata), encoding="utf8")
            relative = Path("senate_efd/annual/part4b") / document / sha / "observation-v2.json"
            (review / relative).parent.mkdir(parents=True)
            (review / relative).write_text(json.dumps(observation), encoding="utf8")
            (review / "senate_efd/annual/current.json").write_text(json.dumps({
                "schema_version": "senate-efd-annual-review/v1", "source_id": "senate_efd",
                "roster_sha256": ROSTER_SHA, "people": [], "reported_holdings": [],
                "qualified_report_count": 1, "holding_count": 0, "exact_source_copies": [],
                "reports": [{"document_id": document, "person_id": "senate:S000001",
                             "source_sha256": sha, "publication_state": "qualified",
                             "part4b_observation_path": relative.as_posix()}]}), encoding="utf8")
            candidate, audit = build_senate_candidate(review, state, deepcopy(BASE), evidence_root=evidence)
            self.assertEqual([tx["id"] for tx in candidate["transactions"]], [new_id])
            relation = audit["annual_part4b_relationships"][0]
            self.assertEqual(relation["rows"][0]["possible_ptr_transaction_ids"], [new_id])
            self.assertEqual(relation["new_candidate_transaction_count"], 0)
            self.assertEqual(audit["superseded_report_count"], 1)

    def test_official_open_and_largest_finite_amounts_reach_candidate_exactly(self):
        item = extraction("11111111-1111-4111-8111-111111111111")
        for raw, low, high, exclusive in [
                ("Over $1,000,000", 1000000, None, True),
                ("Over $50,000,000", 50000000, None, True),
                ("$25,000,001 - $50,000,000", 25000001, 50000000, None)]:
            with self.subTest(amount=raw):
                tx, reasons = _transaction(row(amount_raw=raw), item, identity(item["document_id"]))
                self.assertEqual(reasons, [])
                self.assertEqual((tx["amount_low"], tx["amount_high"], tx.get("amount_low_exclusive")),
                                 (low, high, exclusive))
                self.assertEqual(tx["id"], row()["extraction_id"])

    def test_candidate_consumes_electronic_row_ledger_and_rejects_drift(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            document_id = "11111111-1111-4111-8111-111111111111"
            item = extraction(document_id)
            item["source_report_ledger"] = electronic_ptr_source_ledger(
                item["source_sha256"], item["transactions"])
            state = self.fixture(root, [identity(document_id)], [item])
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual(candidate["transactions"][0]["id"], item["transactions"][0]["extraction_id"])
            self.assertEqual(audit["electronic_source_report_ledgers"][0]["ledger"]["logical_row_count"], 1)
            item["source_report_ledger"]["logical_rows"] = []
            path = root / "senate_efd/extractions" / document_id / item["source_sha256"] / f"{PARSER}.json"
            path.write_text(json.dumps(item), encoding="utf-8")
            with self.assertRaisesRegex(SenateEfdError, "row ledger differs"):
                build_senate_candidate(root, state, deepcopy(BASE))

    def fixture(self, root: Path, identities: list[dict], extractions: list[dict],
                failures: list[dict] | None = None):
        failures = failures or []
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
        for item in failures:
            path = (root / "senate_efd" / "paper_report_failures" / item["document_id"] /
                    item["source_sha256"] / f"{PARSER}.json")
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
            "report_extraction_failure_count": len(failures),
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
                "last_batch_extraction_failures": len(failures),
                "last_batch_transactions": extracted,
            },
        }

    def test_same_roster_person_allows_report_name_case_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = "11111111-1111-4111-8111-111111111111"
            second = "22222222-2222-4222-8222-222222222222"
            earlier = identity(first)
            earlier["filer_name"] = "RICHARD BLUMENTHAL"
            later = identity(second)
            later["filer_name"] = "Richard Blumenthal"
            state = self.fixture(root, [earlier, later], [
                extraction(first, [row("senate-ptr:111111111111111111111111")]),
                extraction(second, [row("senate-ptr:222222222222222222222222")]),
            ])
            candidate, _ = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual(len(candidate["transactions"]), 2)
            self.assertEqual(candidate["people"][0]["display_name"], "RICHARD BLUMENTHAL")

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            later["state"] = "NY"
            state = self.fixture(root, [earlier, later], [
                extraction(first, [row("senate-ptr:111111111111111111111111")]),
                extraction(second, [row("senate-ptr:222222222222222222222222")]),
            ])
            with self.assertRaisesRegex(SenateEfdError, "person_id=senate:S000001.*fields=display_name,state"):
                build_senate_candidate(root, state, deepcopy(BASE))

    def test_accounted_extraction_failure_does_not_block_valid_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            valid_id = "11111111-1111-4111-8111-111111111111"
            failed_id = "22222222-2222-4222-8222-222222222222"
            failure = {
                "schema_version": "senate-efd-paper-ptr-failure/v1",
                "parser_version": PARSER,
                "source_id": "senate_efd",
                "document_id": failed_id,
                "source_sha256": "e" * 64,
                "reason": "paper amount mark is ambiguous",
            }
            state = self.fixture(
                root, [identity(valid_id), identity(failed_id)],
                [extraction(valid_id)], [failure])
            # Source state describes this collector batch. Review status is cumulative
            # and also includes durable paper parsing outcomes from earlier jobs.
            state["reports"]["evidence_count"] = 0
            state["reports"]["last_batch_extraction_failures"] = 0
            state["reports"]["last_batch_transactions"] = 0
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual(len(candidate["transactions"]), 1)
            self.assertEqual(audit["extraction_failure_count"], 1)
            self.assertIn("1 reports retained as extraction failures",
                          candidate["source_health"][0]["detail"])

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

    def test_paper_pending_projection_reaches_audit_without_new_trades(self):
        from unison_snapshot.senate_paper_observation import (
            SCHEMA, project_pending_slots)
        from unison_snapshot.senate_slot_inventory import SCHEMA as SLOT_SCHEMA
        from senate_source_fixture import EVIDENCE_COMMIT

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            document_id = "11111111-1111-4111-8111-111111111111"
            state = self.fixture(root, [identity(document_id)], [extraction(document_id)])
            observation = {
                "schema_version": SCHEMA,
                "fixed_inventory_evidence_commit": EVIDENCE_COMMIT,
                "slot_classification_schema_version": SLOT_SCHEMA,
                "report_count": 0,
                "unobserved_page_count": 0,
                "label_counts": {"data_observed": 0, "unknown": 0},
                "reports": [],
            }
            pending = project_pending_slots(observation)
            observed_path = root / "senate_efd/paper_slot_observations/current.json"
            pending_path = root / "senate_efd/paper_pending_projections/current.json"
            observed_path.parent.mkdir(parents=True)
            pending_path.parent.mkdir(parents=True)
            observed_path.write_text(json.dumps(observation), encoding="utf-8")
            pending_path.write_text(json.dumps(pending), encoding="utf-8")
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual(len(candidate["transactions"]), 1)
            self.assertEqual(audit["paper_pending_projection"]["qualified_transaction_count"], 0)
            self.assertEqual(audit["paper_pending_projection"]["physical_slot_count"], 0)
            pending["pending_row_count"] = 1
            pending_path.write_text(json.dumps(pending), encoding="utf-8")
            with self.assertRaisesRegex(SenateEfdError, "differs from its observation"):
                build_senate_candidate(root, state, deepcopy(BASE))

    def test_report_relations_reach_existing_candidate_audit_without_changing_trades(self):
        from tests.test_senate_paper_observation import SenateReportRelationTests
        from unison_snapshot.senate_paper_observation import project_pending_slots

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            document_id = "11111111-1111-4111-8111-111111111111"
            state = self.fixture(root, [identity(document_id)], [extraction(document_id)])
            before, _ = build_senate_candidate(root, state, deepcopy(BASE))
            observation = SenateReportRelationTests.observation()
            pending = project_pending_slots(observation)
            observed_path = root / "senate_efd/paper_slot_observations/current.json"
            pending_path = root / "senate_efd/paper_pending_projections/current.json"
            observed_path.parent.mkdir(parents=True)
            pending_path.parent.mkdir(parents=True)
            observed_path.write_text(json.dumps(observation), encoding="utf-8")
            pending_path.write_text(json.dumps(pending), encoding="utf-8")
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual(candidate, before)
            self.assertEqual(audit["paper_pending_projection"]["source_report_relations"], {
                "report_count": 1, "linked_observation_row_count": 2,
                "coverage_unknown_report_count": 1, "coverage_unknown_page_count": 1,
                "qualified_transaction_count": None})
            pending["source_report_ledgers"][0]["ledger"]["logical_row_count"] = 3
            pending_path.write_text(json.dumps(pending), encoding="utf-8")
            with self.assertRaisesRegex(SenateEfdError, "differs from its observation"):
                build_senate_candidate(root, state, deepcopy(BASE))

    def test_unknown_source_relations_stay_unknown_in_candidate_audit(self):
        from tests.test_senate_paper_observation import SenateReportRelationTests
        from unison_snapshot.senate_paper_observation import (
            project_pending_slots, _source_report_relations)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            document_id = "11111111-1111-4111-8111-111111111111"
            state = self.fixture(root, [identity(document_id)], [extraction(document_id)])
            observation = SenateReportRelationTests.observation()
            report = observation["reports"][0]
            report.update(coverage_status="unknown_source_version", slots=[], pages=[],
                          physical_grid_slot_count=None, label_counts={},
                          unobserved_page_numbers=[1, 2, 3])
            report["source_report_ledger"] = _source_report_relations(report)
            observation.update(label_counts={}, unobserved_page_count=3)
            pending = project_pending_slots(observation)
            for directory, payload in (("paper_slot_observations", observation),
                                       ("paper_pending_projections", pending)):
                path = root / "senate_efd" / directory / "current.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(payload), encoding="utf-8")
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual(len(candidate["transactions"]), 1)
            self.assertEqual(audit["paper_pending_projection"]["source_report_relations"], {
                "report_count": 1, "linked_observation_row_count": 0,
                "coverage_unknown_report_count": 1, "coverage_unknown_page_count": 3,
                "qualified_transaction_count": None})

    def test_accepts_qualified_paper_extraction_without_inventing_filing_time(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            document_id = "11111111-1111-4111-8111-111111111111"
            paper = extraction(
                document_id,
                [row(owner_raw="Spouse")],
                schema_version=PAPER_EXTRACTION_SCHEMA,
                source_url=f"https://efdsearch.senate.gov/search/view/paper/{document_id}/",
                filed_at_raw="Filed 09/17/2026",
            )
            state = self.fixture(root, [identity(document_id)], [paper])
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))

            self.assertEqual(len(candidate["transactions"]), 1)
            self.assertEqual(candidate["transactions"][0]["filed_at"], "2026-09-17T00:00:00Z")
            self.assertEqual((audit["electronic_report_count"], audit["paper_report_count"]),
                             (0, 1))

    def test_same_parser_version_loads_electronic_and_paper_reports_together(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            electronic_id = "11111111-1111-4111-8111-111111111111"
            paper_id = "22222222-2222-4222-8222-222222222222"
            electronic = extraction(
                electronic_id, [row("senate-ptr:111111111111111111111111")])
            paper = extraction(
                paper_id, [row("senate-ptr:222222222222222222222222", owner_raw="Spouse")],
                schema_version=PAPER_EXTRACTION_SCHEMA,
                source_url=f"https://efdsearch.senate.gov/search/view/paper/{paper_id}/",
                source_sha256="e" * 64,
                filed_at_raw="Filed 09/16/2026",
                portal_listed_date="2026-09-16",
                report_label_date="2026-09-16",
                report_title_date="2026-09-16",
            )
            state = self.fixture(
                root,
                [identity(electronic_id), identity(paper_id)],
                [electronic, paper],
            )
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))

            self.assertEqual(len(candidate["transactions"]), 2)
            self.assertEqual((audit["extracted_report_count"],
                              audit["electronic_report_count"],
                              audit["paper_report_count"]), (2, 1, 1))

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
                extraction(normal_id, [row("senate-ptr:511111111111111111111111", transaction_type="unrecognized"),
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

    def test_active_historical_supplement_only_supplies_amendment_relationship(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            predecessor_id = "24111111-1111-4111-8111-111111111111"
            amendment_id = "34111111-1111-4111-8111-111111111111"
            current = extraction(
                amendment_id, [row("senate-ptr:341111111111111111111111")],
                report_amendment_number=1,
                filed_at_raw="Filed 09/17/2026 @ 8:55 AM",
            )
            state = self.fixture(root, [identity(amendment_id)], [current])
            predecessor = extraction(
                predecessor_id, [row("senate-ptr:241111111111111111111111")],
                source_sha256="e" * 64,
                filed_at_raw="Filed 09/16/2026 @ 8:55 AM",
            )
            manifest = {
                "schema_version": "senate-amendment-supplement/v1",
                "source_id": "senate_efd",
                "status": "ready",
                "reasons": [],
                "parser_version": PARSER,
                "compare_rule_version": AMENDMENT_COMPARE_RULE_VERSION,
                "target_count": 1,
                "selected_predecessor_count": 1,
                "selected_document_ids": [predecessor_id],
                "targets": [{
                    "amendment_document_id": amendment_id,
                    "amendment_source_sha256": "c" * 64,
                    "person_id": "senate:S000001",
                    "report_title_date": "2026-09-17",
                    "content_match_count": 1,
                    "content_matches": [{
                        "document_id": predecessor_id,
                        "source_sha256": "e" * 64,
                    }],
                }],
            }
            supplement_sha = hashlib.sha256(json.dumps(
                manifest, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")).encode("utf-8")).hexdigest()
            manifest["supplement_sha256"] = supplement_sha
            supplement_root = root / "senate_efd" / "amendment_supplements"
            manifest_path = supplement_root / "manifests" / f"{supplement_sha}.json"
            extraction_key = hashlib.sha256(
                f"{predecessor_id}:{'e' * 64}:{PARSER}".encode("ascii")).hexdigest()
            extraction_path = supplement_root / "extractions" / f"{extraction_key}.json"
            extraction_path.parent.mkdir(parents=True)
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            extraction_path.write_text(json.dumps(predecessor), encoding="utf-8")
            (supplement_root / "current.json").write_text(json.dumps({
                "schema_version": "senate-amendment-supplement-pointer/v1",
                "source_id": "senate_efd",
                "status": "active",
                "supplement_sha256": supplement_sha,
                "manifest_path": manifest_path.relative_to(root).as_posix(),
                "parser_version": PARSER,
                "compare_rule_version": AMENDMENT_COMPARE_RULE_VERSION,
                "selected_predecessor_count": 1,
            }), encoding="utf-8")
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual([item["filing_id"] for item in candidate["transactions"]],
                             [amendment_id])
            self.assertNotIn(predecessor_id,
                             [item["filing_id"] for item in candidate["transactions"]])
            self.assertEqual(audit["input_transaction_count"], 1)
            self.assertEqual(audit["amendment_supplement_report_count"], 1)
            self.assertEqual(audit["amendment_supplement_transaction_count"], 1)

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

    def test_amendment_allows_one_strongly_anchored_row_correction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base_id = "22111111-1111-4111-8111-111111111111"
            amendment_id = "32111111-1111-4111-8111-111111111111"
            amended = row("senate-ptr:321111111111111111111111", amount_raw="$15,001 - $50,000")
            state = self.fixture(
                root,
                [identity(base_id), identity(amendment_id)],
                [extraction(base_id, [row("senate-ptr:221111111111111111111111")],
                            filed_at_raw="Filed 09/16/2026 @ 8:55 AM"),
                 extraction(amendment_id, [amended], report_amendment_number=1)],
            )
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual([item["filing_id"] for item in candidate["transactions"]],
                             [amendment_id])
            self.assertEqual(audit["resolved_amendment_chain_count"], 1)

    def test_amendment_rejects_an_unanchored_row_rewrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base_id = "23111111-1111-4111-8111-111111111111"
            amendment_id = "33111111-1111-4111-8111-111111111111"
            rewritten = row(
                "senate-ptr:331111111111111111111111",
                transaction_date="2026-08-01", owner_raw="Spouse", ticker_raw="OTHER",
                asset_name_raw="Other Asset", asset_type_raw="Other",
                transaction_type="sale", amount_raw="$50,001 - $100,000", comment_raw="Changed",
            )
            state = self.fixture(
                root,
                [identity(base_id), identity(amendment_id)],
                [extraction(base_id, [row("senate-ptr:231111111111111111111111")]),
                 extraction(amendment_id, [rewritten], report_amendment_number=1)],
            )
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual(candidate["transactions"], [])
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
                                             transaction_date="2026-08-01",
                                             owner_raw="Spouse", ticker_raw="OTHER",
                                             asset_name_raw="Other Asset", asset_type_raw="Other",
                                             transaction_type="sale",
                                             amount_raw="$15,001 - $50,000",
                                             comment_raw="Different report")],
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

    def test_state_and_review_entrypoint_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            document_id = "61111111-1111-4111-8111-111111111111"
            state = self.fixture(root, [identity(document_id)], [extraction(document_id)])
            state["reports"]["entrypoint_count"] = 2
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
