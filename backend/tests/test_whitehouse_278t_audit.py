"""Read-only qualification checks for public White House 278-T reports."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import unittest

from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_candidate import _identity_key, _person_id
from unison_snapshot.whitehouse_278t_audit import audit_whitehouse_278t


def catalog(name="Wiles, Susie", title="Assistant to the President & Chief of Staff",
            agency="White House Office"):
    return {"schema_version": "oge-whitehouse-coverage/v1", "catalog_sha256": "a" * 64,
            "reports": [{"document_type": "278t", "filer_name": name,
                         "position_title": title, "agency": agency}]}


def extraction(document_id="wiles-1", *, name="Wiles, Susie",
               role="Assistant to the President and Chief of Staff, Trump-Vance (2025)",
               agency="White House", sha="b" * 64, asset="Procter & Gamble Co.",
               ticker="PG"):
    return {
        "schema_version": "whitehouse-278t-extraction/v1",
        "parser_version": "whitehouse-278t-pdf/v1", "source_id": "oge",
        "document_id": document_id, "source_sha256": sha,
        "source_url": "https://www.whitehouse.gov/wp-content/uploads/report.pdf",
        "filer_name": name, "pdf_filer_name": name,
        "pdf_position_title": role, "pdf_agency_label": agency,
        "filed_at": "2025-06-13", "evidence_complete": True,
        "document_reasons": [], "quarantined": [],
        "transactions": [{"extraction_id": "oge-278t:" + hashlib.sha256(
                              document_id.encode()).hexdigest()[:24],
                          "row_number": 1, "owner": "Self", "asset_name": asset,
                          "ticker": ticker, "transaction_type": "sale",
                          "transaction_date": "2025-06-09", "amount_low": 15001,
                          "amount_high": 50000}],
    }


def candidate(transactions=None):
    return {"meta": {"is_demo": False}, "transactions": transactions or []}


class WhiteHouse278TAuditTests(unittest.TestCase):
    def test_unique_official_identity_and_ampersand_equivalence(self):
        result = audit_whitehouse_278t([extraction()], catalog(), candidate())
        report = result["reports"][0]
        self.assertEqual(report["status"], "eligible")
        self.assertEqual(report["rows"][0]["status"], "eligible")
        self.assertEqual(report["matched_catalog_identity"]["agency"], "White House Office")
        self.assertEqual(result["eligible_row_count"], 1)

    def test_middle_initial_can_match_with_unique_full_role(self):
        source = extraction(name="Miller, Stephen N",
                            role="Deputy Chief of Staff for Policy and Homeland Security Advisor, Trump-Vance (2025)")
        directory = catalog(name="Miller, Stephen",
                            title="Deputy Chief of Staff for Policy and Homeland Security Advisor")
        result = audit_whitehouse_278t([source], directory, candidate())
        self.assertEqual(result["reports"][0]["status"], "eligible")

    def test_position_disagreement_and_nickname_are_quarantined(self):
        bresso = extraction(name="Bresso, Gineen", role="Principal Deputy Counsel to the President, Trump-Vance (2025)")
        directory = catalog(name="Bresso, Gineen M", title="Deputy Counsel to the President")
        result = audit_whitehouse_278t([bresso], directory, candidate())
        self.assertIn("catalog_position_mismatch", result["reports"][0]["document_reasons"])
        scavino = extraction(name="Scavino, Daniel", role="Deputy Chief of Staff, Trump-Vance (2025)")
        directory = catalog(name="Scavino, Dan J", title="Deputy Chief of Staff")
        result = audit_whitehouse_278t([scavino], directory, candidate())
        self.assertIn("catalog_filer_identity_missing", result["reports"][0]["document_reasons"])

    def test_multiple_official_identities_are_not_guessed(self):
        directory = catalog()
        directory["reports"].append({"document_type": "278t", "filer_name": "Wiles, Susie M",
                                     "position_title": "Assistant to the President and Chief of Staff",
                                     "agency": "White House Office"})
        result = audit_whitehouse_278t([extraction()], directory, candidate())
        self.assertIn("catalog_identity_ambiguous", result["reports"][0]["document_reasons"])

    def test_unsupported_agency_and_missing_pdf_identity_are_quarantined(self):
        other_office = extraction(agency="Executive Office")
        missing = extraction(document_id="second")
        missing["pdf_filer_name"] = None
        result = audit_whitehouse_278t([other_office, missing], catalog(), candidate())
        self.assertIn("pdf_agency_unrecognized", result["reports"][0]["document_reasons"])
        self.assertIn("pdf_identity_fields_missing", result["reports"][1]["document_reasons"])

    def test_amendment_and_exact_pdf_duplicate_block_all_rows(self):
        first = extraction()
        first["document_reasons"] = ["amendment_relationship_unresolved"]
        first["evidence_complete"] = False
        second = extraction(document_id="copy")
        result = audit_whitehouse_278t([first, second], catalog(), candidate())
        self.assertTrue(all(report["status"] == "quarantined" for report in result["reports"]))
        self.assertTrue(all("duplicate_pdf_unresolved" in report["document_reasons"]
                            for report in result["reports"]))

    def test_existing_oge_candidate_and_cross_report_rows_are_screened(self):
        identity = catalog()["reports"][0]
        person_id = _person_id(_identity_key(identity))
        existing = candidate([{**extraction()["transactions"][0],
                               "person_id": person_id, "source_id": "oge"}])
        one = extraction()
        two = extraction(document_id="second", sha="c" * 64)
        result = audit_whitehouse_278t([one, two], catalog(), existing)
        for report in result["reports"]:
            self.assertIn("possible_existing_oge_transaction_duplicate",
                          report["rows"][0]["reasons"])
            self.assertIn("possible_whitehouse_report_transaction_duplicate",
                          report["rows"][0]["reasons"])
        self.assertEqual(result["eligible_row_count"], 0)
        self.assertEqual(result["eligible_report_count"], 0)

    def test_distinct_rows_same_person_and_date_remain_eligible(self):
        one = extraction()
        two = extraction(document_id="second", sha="c" * 64, asset="Other Corp.", ticker="OTHER")
        result = audit_whitehouse_278t([one, two], catalog(), candidate())
        self.assertEqual(result["eligible_row_count"], 2)

    def test_tampered_transaction_fields_cannot_become_eligible(self):
        source = extraction()
        source["transactions"][0]["transaction_date"] = "2025-06-14"
        source["transactions"][0]["amount_high"] = 0
        result = audit_whitehouse_278t([source], catalog(), candidate())
        row = result["reports"][0]["rows"][0]
        self.assertEqual(row["status"], "quarantined")
        self.assertIn("transaction_after_filer_signature", row["reasons"])
        self.assertIn("transaction_amount_range_invalid", row["reasons"])
        self.assertEqual(result["reports"][0]["status"], "quarantined")

    def test_input_is_not_mutated_and_demo_candidate_fails_closed(self):
        source = extraction()
        before = deepcopy(source)
        audit_whitehouse_278t([source], catalog(), candidate())
        self.assertEqual(source, before)
        with self.assertRaises(OgeCatalogError):
            audit_whitehouse_278t([source], catalog(), {"meta": {"is_demo": True},
                                                           "transactions": []})


if __name__ == "__main__":
    unittest.main()
