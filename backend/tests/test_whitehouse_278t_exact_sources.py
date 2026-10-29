"""Synthetic exact-byte groups exercise the real opt-in candidate consumer."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tests.test_whitehouse_278t_candidate import _base, _catalog, _wiles
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.whitehouse_278t_candidate import (
    audit_whitehouse_278t_exact_source_candidate as audit_exact,
    build_whitehouse_278t_exact_source_candidate as build_exact,
)


def copies():
    first = _wiles()
    other = deepcopy(first)
    other.update(document_id="wh-url:" + "2" * 24,
                 source_url="https://www.whitehouse.gov/wp-content/uploads/2025/06/copy.pdf")
    for report in (first, other):
        report["document_id"] = "wh-url:" + hashlib.sha256(report["source_url"].encode()).hexdigest()[:24]
    return [first, other]


def build(base, reports):
    audit = audit_exact(base, reports, _catalog())
    return build_exact(base, reports, audit, _catalog(), data_cutoff_at="2025-06-14T00:00:00Z")


class ExactSourceTests(unittest.TestCase):
    def _cross_fixture(self):
        report = copies()[0]
        for row in report["transactions"]:
            row["page_number"] = 1
        base, _ = build(_base(), [report])
        doc = "3" * 32
        url = f"https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/{doc}/$FILE/copy.pdf"
        parsed = {"schema_version": "oge-278t-extraction/v1", "document_id": doc,
                  "source_url": url, "source_sha256": report["source_sha256"],
                  "catalog_filer_name": report["pdf_filer_name"],
                  "transactions": deepcopy(report["transactions"]), "quarantined": []}
        for index, row in enumerate(parsed["transactions"], 1):
            row["extraction_id"] = "oge-278t:" + str(index) * 24
        by_number = {row["row_number"]: row for row in parsed["transactions"]}
        for fact in base["transactions"]:
            original = next(row for row in report["transactions"] if row["extraction_id"] == fact["id"])
            fact.update(id=by_number[original["row_number"]]["extraction_id"], source_url=url, filing_id=doc)
        bindings = {"schema_version": "whitehouse-278t-exact-source-bindings/v1", "reports": [{
            "whitehouse_archive": {"document_id": report["document_id"], "document_url": report["source_url"],
                                   "sha256": report["source_sha256"]},
            "oge_archive": {"document_id": doc, "document_url": url, "sha256": report["source_sha256"]},
            "oge_extraction": parsed}]}
        return base, report, bindings

    def _build_cross(self, base, report, bindings):
        checked = audit_exact(base, [report], _catalog(), source_bindings=bindings)
        return build_exact(base, [report], checked, _catalog(), source_bindings=bindings,
                           data_cutoff_at="2025-06-14T00:00:00Z")

    def test_cross_source_old_ids_require_same_pdf_physical_row_and_actual_formal_id(self):
        base, report, bindings = self._cross_fixture()
        candidate, audit = self._build_cross(base, report, bindings)
        self.assertEqual(candidate["transactions"], sorted(base["transactions"], key=lambda row: row["id"]))
        self.assertEqual(audit["retained_existing_transaction_count"], 3)
        bindings["reports"][0]["oge_archive"]["sha256"] = "d" * 64
        with self.assertRaisesRegex(OgeCatalogError, "same PDF and filer"):
            self._build_cross(base, report, bindings)

    def test_cross_source_missing_observation_does_not_block_new_canonical_rows(self):
        _, report, bindings = self._cross_fixture()
        bindings["reports"][0]["oge_extraction"]["transactions"] = []
        candidate, audit = self._build_cross(_base(), report, bindings)
        self.assertEqual(len(candidate["transactions"]), 3)
        self.assertEqual(audit["promoted_transaction_count"], 3)
        self.assertTrue(all(row["status"] == "no_old_formal_fact" for row in audit["cross_source_relations"][0]["rows"]))

    def test_cross_source_status_excludes_unpublished_oge_copy_and_counts_wh_once(self):
        from unison_snapshot.disclosure_status_oge import build_oge_status
        _, report, bindings = self._cross_fixture()
        old, _ = build(_base(), [report])
        candidate, audit = self._build_cross(old, report, bindings)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for kind in ("oge", "whitehouse"):
                item = bindings["reports"][0][kind + "_archive"]
                prefix = "oge/reports" if kind == "oge" else "whitehouse/disclosures/reports"
                path = root / "evidence" / prefix / item["document_id"].split(":")[-1] / (item["sha256"] + ".json")
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(item), encoding="utf8")
            path = root / "review/whitehouse/qualifications/candidate-current.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(audit), encoding="utf8")
            scopes = build_oge_status(root / "evidence", root / "review", candidate["transactions"], [])
            self.assertEqual(scopes[0]["reports"][0]["qualification_state"], "superseded")
            self.assertEqual(scopes[0]["reports"][0]["publication_transaction_ids"], [])
            self.assertEqual(len(scopes[2]["reports"][0]["publication_transaction_ids"]), 3)
            self.assertEqual(scopes[2]["reports"][0]["qualified_transaction_count"], 3)

    def test_cross_source_conflicting_old_fact_blocks_only_that_row(self):
        base, report, bindings = self._cross_fixture()
        base["transactions"][0]["owner"] = "Spouse"
        candidate, audit = self._build_cross(base, report, bindings)
        self.assertEqual(len(candidate["transactions"]), 3)
        self.assertEqual(audit["retained_existing_transaction_count"], 2)
        self.assertEqual(audit["quarantined_row_count"], 1)
        self.assertIn("existing_exact_source_row_interpretation_conflict", audit["reports"][0]["rows"][0]["reasons"])

    def test_saved_binding_alone_cannot_remove_an_old_formal_duplicate(self):
        base, report, bindings = self._cross_fixture()
        wh, _ = build(_base(), [report])
        base["transactions"].append(wh["transactions"][0])
        candidate, audit = self._build_cross(base, report, bindings)
        self.assertEqual(len(base["transactions"]), 4)
        self.assertEqual(len(candidate["transactions"]), 4)
        self.assertNotIn("removed_duplicate_formal_rows", audit)
        self.assertTrue(audit["candidate_is_append_only"])

    def test_exact_bytes_consumed_once_and_all_links_retained(self):
        reports = copies()
        before = deepcopy(reports)
        candidate, audit = build(_base(), reports)
        self.assertEqual(len(candidate["transactions"]), 3)
        self.assertEqual(audit["promoted_transaction_count"], 3)
        self.assertEqual(len(audit["exact_source_aliases"][0]["aliases"]), 2)
        self.assertEqual(audit["source_row_count"], 6)
        self.assertEqual(audit["exact_source_alias_row_count"], 3)
        self.assertEqual(reports, before)
        reverse, _ = build(_base(), list(reversed(reports)))
        self.assertEqual(candidate, reverse)

    def test_alias_local_ids_only_reference_the_single_formal_id(self):
        reports = copies()
        for index, row in enumerate(reports[1]["transactions"], 1):
            row["extraction_id"] = "oge-278t:" + str(index) * 24
        candidate, audit = build(_base(), reports)
        formal = {row["id"] for row in candidate["transactions"]}
        self.assertEqual(len(formal), 3)
        for source in audit["exact_source_aliases"][0]["aliases"]:
            self.assertEqual({row["formal_transaction_id"] for row in source["rows"]}, formal)

    def test_existing_formal_ids_and_facts_are_retained(self):
        reports = copies()
        base, _ = build(_base(), reports)
        base["transactions"][0]["id"] = "oge-278t:" + "f" * 24
        prior = deepcopy(base["transactions"])
        candidate, audit = build(base, reports)
        self.assertEqual(candidate["transactions"], sorted(prior, key=lambda row: row["id"]))
        self.assertEqual(audit["promoted_transaction_count"], 0)
        self.assertEqual(audit["retained_existing_transaction_count"], 3)
        self.assertEqual({row["formal_transaction_id"] for row in audit["retained_rows"]},
                         {row["id"] for row in prior})

    def test_unrelated_bad_fields_are_not_forgiven(self):
        reports = copies()
        for report in reports:
            report["transactions"][0]["owner"] = "unclear"
        candidate, audit = build(_base(), reports)
        self.assertEqual(len(candidate["transactions"]), 2)
        self.assertIn("transaction_owner_unsupported", audit["reports"][0]["rows"][0]["reasons"])

    def test_conflicting_interpretations_or_identities_do_not_resolve(self):
        for field, value in [("transactions", None), ("pdf_filer_name", "Someone Else"),
                             ("filer_signature_evidence", "unreadable")]:
            with self.subTest(field=field):
                reports = copies()
                if field == "transactions":
                    reports[1][field][0]["owner"] = "Spouse"
                else:
                    reports[1][field] = value
                candidate, audit = build(_base(), reports)
                self.assertEqual(candidate["transactions"], [])
                self.assertEqual(audit["exact_source_aliases"], [])

    def test_common_bad_date_is_preserved_and_no_source_rows_disappear(self):
        reports = copies()
        for report in reports:
            report["transactions"][0]["transaction_date"] = "2025-06-20"
        candidate, audit = build(_base(), reports)
        self.assertEqual(len(candidate["transactions"]), 2)
        alias = audit["exact_source_aliases"][0]
        self.assertTrue(all("transaction_after_filer_signature" in source["rows"][0]["reasons"]
                            for source in alias["aliases"]))
        self.assertEqual(audit["source_row_count"], audit["promoted_transaction_count"] +
                         audit["quarantined_row_count"] + audit["exact_source_alias_row_count"])

    def test_previous_published_alias_becomes_canonical_without_rekeying(self):
        reports = copies()
        original, _ = build(_base(), [reports[1]])
        candidate, audit = build(original, reports)
        self.assertEqual(candidate["transactions"], original["transactions"])
        self.assertEqual(audit["exact_source_aliases"][0]["canonical_document_id"], reports[1]["document_id"])

    def test_duplicate_document_ids_are_rejected_not_removed_as_aliases(self):
        reports = copies()
        reports[1]["document_id"] = reports[0]["document_id"]
        with self.assertRaisesRegex(OgeCatalogError, "identities are missing or duplicated"):
            build(_base(), reports)

    def test_different_bytes_with_similar_rows_stay_unresolved(self):
        reports = copies()
        reports[1]["source_sha256"] = "c" * 64
        candidate, audit = build(_base(), reports)
        self.assertEqual(candidate["transactions"], [])
        self.assertEqual(audit["exact_source_aliases"], [])

    def test_distinct_account_rows_are_never_collapsed(self):
        reports = copies()
        for report in reports:
            first = report["transactions"][0]
            first["asset_name"] += " (Account: Alpha)"
            first["ticker"] = None
            extra = deepcopy(first)
            extra.update(extraction_id="oge-278t:" + "e" * 24, row_number=4,
                         asset_name=first["asset_name"].replace("Alpha", "Beta"))
            report["transactions"].append(extra)
        candidate, audit = build(_base(), reports)
        self.assertEqual(len(candidate["transactions"]), 4)
        self.assertEqual(len({row["id"] for row in candidate["transactions"]}), 4)
        self.assertEqual(audit["exact_source_aliases"][0]["canonical_row_count"], 4)

    def test_stale_alias_binding_rejected_by_actual_consumer(self):
        reports = copies()
        audit = audit_exact(_base(), reports, _catalog())
        reports[1]["source_url"] = reports[1]["source_url"].replace("copy.pdf", "changed.pdf")
        with self.assertRaisesRegex(OgeCatalogError, "stale or altered"):
            build_exact(_base(), reports, audit, _catalog(), data_cutoff_at="2025-06-14T00:00:00Z")

    def test_existing_report_status_and_publication_id_gate_consume_alias_once(self):
        from test_public_repo import production_payload
        from unison_snapshot.builder import build as build_bundle
        from unison_snapshot.disclosure_status_oge import build_oge_status
        from unison_snapshot.disclosure_status import attach_disclosure_status
        reports = copies()
        for retain in (False, True):
            base = _base()
            if retain:
                base, _ = build(base, reports)
            candidate, audit = build(base, reports)
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                for report in reports:
                    path = root / "evidence/whitehouse/disclosures/reports" / report["document_id"].split(":")[1]
                    path.mkdir(parents=True)
                    (path / (report["source_sha256"] + ".json")).write_text(json.dumps({
                        "document_id": report["document_id"], "document_url": report["source_url"],
                        "sha256": report["source_sha256"]}), encoding="utf8")
                path = root / "review/whitehouse/qualifications/candidate-current.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(audit), encoding="utf8")
                scopes = build_oge_status(root / "evidence", root / "review", candidate["transactions"], [])
                records = scopes[2]["reports"]
                canonical_id = audit["exact_source_aliases"][0]["canonical_document_id"]
                canonical = next(row for row in records if row["report_id"] == canonical_id)
                alias = next(row for row in records if row["report_id"] != canonical_id)
                self.assertEqual(len(canonical["publication_transaction_ids"]), 3)
                self.assertEqual(canonical["qualified_transaction_count"], 3)
                self.assertEqual(alias["publication_transaction_ids"], [])
                self.assertEqual(alias["qualification_state"], "superseded")
                self.assertEqual(alias["exact_source_canonical_report_id"], canonical_id)
                payload = production_payload()
                for key in ("people", "transactions", "reported_holdings", "security_market_data", "source_health"):
                    payload[key] = candidate[key]
                payload["meta"]["data_cutoff_at"] = candidate["meta"]["data_cutoff_at"]
                bundle = build_bundle(payload, generated_at="2025-06-14T00:00:00Z", allow_production=True)
                attached = attach_disclosure_status(bundle, scopes, transactions=candidate["transactions"],
                    provenance={key: str(index) * 40 for index, key in enumerate(
                        ("code_commit", "evidence_commit", "review_commit", "previous_main_commit"), 1)})
                self.assertIn("disclosure_status", attached.manifest)


if __name__ == "__main__":
    unittest.main()
