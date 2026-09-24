"""Conservation and append-only tests for the White House 278-T review projection."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import unittest

from unison_snapshot.builder import FIELDS
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.whitehouse_278t_audit import (
    TRUMP_TARGET_DOCUMENT_ID, TRUMP_TARGET_SOURCE_SHA256, TRUMP_TARGET_SOURCE_URL,
    audit_whitehouse_278t,
)
from unison_snapshot.whitehouse_278t import TRUMP_081225_PARSER_VERSION
from unison_snapshot.whitehouse_278t_candidate import build_whitehouse_278t_review_candidate


def _base():
    return {
        "meta": {"is_demo": False, "data_cutoff_at": "2025-06-01T00:00:00Z"},
        "people": [], "transactions": [], "reported_holdings": [],
        "security_market_data": [], "source_health": [{
            "source_id": "oge", "source": "U.S. Office of Government Ethics",
            "source_type": "official_disclosure", "source_url": "https://www.oge.gov/",
            "status": "partial", "last_checked_at": "2025-06-01T00:00:00Z",
            "last_successful_sync_at": "2025-06-01T00:00:00Z",
            "data_cutoff_at": "2025-06-01T00:00:00Z",
            "detail": "0 direct 278-T entries; 0 transactions qualified",
        }],
    }


def _catalog():
    return {
        "schema_version": "oge-whitehouse-coverage/v1", "catalog_sha256": "a" * 64,
        "reports": [{
            "document_type": "278t", "filer_name": "Wiles, Susie",
            "position_title": "Assistant to the President & Chief of Staff",
            "agency": "White House Office", "catalog_entry_id": "oge-wiles-1",
        }],
    }


def _wiles():
    """Three differently valued rows from the June 2025 Wiles 278-T shape."""
    document_id = "wh-url:111111111111111111111111"
    source_sha = "b" * 64
    trades = [
        ("Procter & Gamble Co.", "PG", "2025-06-09", 15001, 50000),
        ("Six Flags Entertainment Corp.", "SIXG", "2025-06-10", 100001, 250000),
        ("Six Flags Entertainment Corp.", "SIXG", "2025-06-10", 15001, 50000),
    ]
    rows = []
    for index, (asset, ticker, day, low, high) in enumerate(trades, start=1):
        key = f"{source_sha}:{index}:{asset}:{day}"
        rows.append({
            "extraction_id": "oge-278t:" + hashlib.sha256(key.encode()).hexdigest()[:24],
            "row_number": index, "owner": "Self", "asset_name": asset,
            "ticker": ticker, "transaction_type": "sale",
            "transaction_date": day, "amount_low": low, "amount_high": high,
        })
    return {
        "schema_version": "whitehouse-278t-extraction/v1",
        "parser_version": "whitehouse-278t-pdf/v1", "source_id": "oge",
        "document_id": document_id,
        "source_url": "https://www.whitehouse.gov/wp-content/uploads/2025/06/Wiles%E2%80%93PTR.pdf",
        "source_sha256": source_sha,
        "filer_name": "Wiles, Susie", "pdf_filer_name": "Wiles, Susie",
        "pdf_position_title": "Assistant to the President and Chief of Staff, Trump-Vance (2025)",
        "pdf_agency_label": "White House", "filed_at": "2025-06-13",
        "signature_method": "electronic",
        "filer_signature_name": "Susie Wiles",
        "filer_signature_evidence": (
            "/s/ Susie Wiles [electronically signed on 06/13/2025 "
            "by Susie Wiles in Integrity.gov]"),
        "evidence_complete": True, "document_reasons": [],
        "transactions": rows, "quarantined": [],
    }


def _build(base=None, reports=None, cutoff="2025-06-14T00:00:00Z"):
    base = _base() if base is None else base
    reports = [_wiles()] if reports is None else reports
    catalog = _catalog()
    audit = audit_whitehouse_278t(reports, catalog, base)
    return build_whitehouse_278t_review_candidate(
        base, reports, audit, catalog, data_cutoff_at=cutoff)


def _trump_source_bound():
    report = _wiles()
    report.update({
        "document_id": TRUMP_TARGET_DOCUMENT_ID,
        "source_url": TRUMP_TARGET_SOURCE_URL,
        "source_sha256": TRUMP_TARGET_SOURCE_SHA256,
        "parser_version": TRUMP_081225_PARSER_VERSION,
        "filer_name": "Trump, Donald J.", "pdf_filer_name": "Donald J Trump",
        "pdf_position_title": "President of the United States of America",
        "pdf_agency_label": None,
        "filed_at": "2025-08-12", "signature_method": "handwritten_source_bound",
        "filer_signature_name": None, "filer_signature_evidence": None,
        "filing_date_evidence": {
            "page_number": 1, "label": "Filer's Certification Date",
            "raw": "8/12/25", "normalized": "2025-08-12",
            "geometry": {"x0": 10.0, "top": 20.0, "x1": 40.0, "bottom": 30.0,
                         "coordinate_space": "pdf_points"},
            "source_url": TRUMP_TARGET_SOURCE_URL,
            "source_sha256": TRUMP_TARGET_SOURCE_SHA256,
        },
        "filer_identity_evidence": {
            "page_number": 1, "pdf_filer_name": "Donald J Trump",
            "pdf_position_title": "President of the United States of America",
            "agency_basis": "exact_sha_official_oge_catalog_alias",
            "source_url": TRUMP_TARGET_SOURCE_URL,
            "source_sha256": TRUMP_TARGET_SOURCE_SHA256,
        },
    })
    report["transactions"] = [{
        "extraction_id": "oge-278t:" + hashlib.sha256(
            f"{TRUMP_TARGET_SOURCE_SHA256}:{index}".encode()).hexdigest()[:24],
        "row_number": index, "owner": "Self", "asset_name": f"Asset {index}",
        "ticker": None, "transaction_type": "sale",
        "transaction_date": "2025-01-01", "amount_low": 1001,
        "amount_high": 15000, "geometry_row_index": index,
        "geometry_top": float(index), "ocr_confidence": 90.0,
    } for index in range(1, 508)]
    report.update({
        "quarantined": [], "source_row_count": 507,
        "extraction_method": "tesseract_ocr_geometry", "ocr_engine": "Tesseract 5.3.4",
        "page_count": 22,
    })
    return report


class WhiteHouse278TCandidateTests(unittest.TestCase):
    def test_wiles_three_rows_append_with_five_array_contract(self):
        base, report = _base(), _wiles()
        original = deepcopy((base, report))
        candidate, audit = _build(base, [report])
        self.assertEqual((base, report), original)
        self.assertEqual(audit["report_count"], 1)
        self.assertEqual(audit["source_row_count"], 3)
        self.assertEqual(audit["promoted_transaction_count"], 3)
        self.assertEqual(audit["quarantined_row_count"], 0)
        self.assertEqual(audit["added_person_count"], 1)
        self.assertEqual(audit["promoted_transaction_ids"],
                         sorted(row["id"] for row in candidate["transactions"]))
        self.assertTrue(audit["candidate_is_append_only"])
        self.assertTrue(audit["source_row_conservation_complete"])
        self.assertEqual(audit["existing_transaction_mutation_count"], 0)
        self.assertEqual(len(candidate["people"]), 1)
        self.assertEqual({item["filing_id"] for item in candidate["transactions"]},
                         {report["document_id"]})
        self.assertEqual({item["filed_at"] for item in candidate["transactions"]},
                         {"2025-06-13T00:00:00Z"})
        self.assertEqual({item["source_url"] for item in candidate["transactions"]},
                         {report["source_url"]})
        self.assertTrue(all(item["verification_status"] == "official_matched"
                            for item in candidate["transactions"]))
        self.assertTrue(all(set(item) == FIELDS["transactions"]
                            for item in candidate["transactions"]))
        self.assertTrue(all(set(item) == FIELDS["people"] for item in candidate["people"]))
        for name in ("reported_holdings", "security_market_data"):
            self.assertEqual(candidate[name], base[name])
        self.assertEqual(len(candidate["source_health"]), 1)
        self.assertIn("3 new transactions qualified", candidate["source_health"][0]["detail"])
        self.assertIn("3 total OGE candidate transactions", candidate["source_health"][0]["detail"])
        self.assertIs(candidate["meta"]["is_demo"], False)

    def test_existing_unsorted_health_is_canonicalized_for_publication(self):
        base = _base()
        extra = deepcopy(base["source_health"][0])
        extra["source_id"] = "house_clerk"
        base["source_health"].append(extra)
        candidate, _ = _build(base)
        self.assertEqual([row["source_id"] for row in candidate["source_health"]],
                         ["house_clerk", "oge"])

    def test_existing_id_conflict_is_quarantined_without_overwrite(self):
        first, _ = _build()
        original = deepcopy(first["transactions"][0])
        # An existing OGE fact with the same ID but different transaction
        # values is not a semantic cross-source duplicate in the eligibility
        # audit.  The projection must still catch the primary-key conflict.
        original["asset_name"] = "Different Corporation"
        original["ticker"] = "DIFF"
        original["transaction_date"] = "2025-05-01"
        original["source_url"] = "https://www.oge.gov/report.pdf"
        base = _base()
        base["meta"]["data_cutoff_at"] = "2025-06-14T00:00:00Z"
        base["people"] = deepcopy(first["people"])
        base["transactions"] = [original]
        candidate, audit = _build(base)
        self.assertEqual(audit["source_row_count"], 3)
        self.assertEqual(audit["promoted_transaction_count"], 2)
        self.assertEqual(audit["quarantined_row_count"], 1)
        self.assertEqual(len(candidate["transactions"]), 3)
        self.assertIn(original, candidate["transactions"])
        conflicted = [row for row in audit["reports"][0]["rows"]
                      if "transaction_id_conflict_existing" in row["reasons"]]
        self.assertEqual(len(conflicted), 1)
        self.assertEqual(conflicted[0]["status"], "quarantined")

    def test_preexisting_row_quarantine_is_accounted_for(self):
        report = _wiles()
        bad = deepcopy(report["transactions"][0])
        bad["row_number"] = 4
        bad["extraction_id"] = "oge-278t:" + "f" * 24
        bad["reasons"] = ["amount_unparsed"]
        report["quarantined"].append(bad)
        candidate, audit = _build(reports=[report])
        self.assertEqual(len(candidate["transactions"]), 3)
        self.assertEqual(audit["source_row_count"], 4)
        self.assertEqual(audit["promoted_transaction_count"], 3)
        self.assertEqual(audit["quarantined_row_count"], 1)
        self.assertEqual(audit["reports"][0]["rows"][-1]["reasons"], ["amount_unparsed"])

    def test_duplicate_source_id_quarantines_both_rows(self):
        report = _wiles()
        report["transactions"][1]["extraction_id"] = report["transactions"][0]["extraction_id"]
        candidate, audit = _build(reports=[report])
        self.assertEqual(audit["source_row_count"], 3)
        self.assertEqual(audit["promoted_transaction_count"], 1)
        self.assertEqual(audit["quarantined_row_count"], 2)
        self.assertEqual(len(candidate["transactions"]), 1)
        self.assertTrue(all("transaction_id_conflict_source" in row["reasons"]
                            for row in audit["reports"][0]["rows"][:2]))

    def test_signature_and_cutoff_do_not_silently_promote(self):
        report = _wiles()
        report["filer_signature_evidence"] = report["filer_signature_evidence"].replace(
            "06/13/2025", "06/12/2025")
        candidate, audit = _build(reports=[report])
        self.assertFalse(candidate["transactions"])
        self.assertEqual(audit["quarantined_row_count"], 3)
        self.assertIn("filer_signature_date_not_verified", audit["reports"][0]["document_reasons"])
        candidate, audit = _build(cutoff="2025-06-12T00:00:00Z")
        self.assertFalse(candidate["transactions"])
        self.assertIn("filing_exceeds_cutoff", audit["reports"][0]["document_reasons"])

    def test_fixed_trump_source_bound_cover_date_can_promote(self):
        base, report = _base(), _trump_source_bound()
        trump_id = "oge:076544f8ba0638cf"
        directory = {
            "schema_version": "oge-whitehouse-coverage/v1", "catalog_sha256": "a" * 64,
            "reports": [{"document_type": "278t", "filer_name": "Trump, Donald J.",
                         "position_title": "President", "agency": "White House Office",
                         "catalog_entry_id": "oge-trump"}],
        }
        audit = audit_whitehouse_278t([report], directory, base)
        self.assertEqual(audit["reports"][0]["matched_catalog_identity"]["person_id"],
                         trump_id)
        candidate, conservation = build_whitehouse_278t_review_candidate(
            base, [report], audit, directory, data_cutoff_at="2025-08-13T00:00:00Z")
        self.assertEqual(conservation["promoted_transaction_count"], 507)
        self.assertEqual({row["person_id"] for row in candidate["transactions"]},
                         {trump_id})
        self.assertEqual({row["filed_at"] for row in candidate["transactions"]},
                         {"2025-08-12T00:00:00Z"})
        report["filing_date_evidence"]["raw"] = "8/13/25"
        with self.assertRaisesRegex(OgeCatalogError, "source-bound contract"):
            audit_whitehouse_278t([report], directory, base)
        report["filing_date_evidence"]["raw"] = "8/12/25"
        report["filer_identity_evidence"]["agency_basis"] = "pdf_agency_cell"
        with self.assertRaisesRegex(OgeCatalogError, "source-bound contract"):
            audit_whitehouse_278t([report], directory, base)

    def test_stale_audit_and_demo_base_fail_closed(self):
        base, report, catalog = _base(), _wiles(), _catalog()
        audit = audit_whitehouse_278t([report], catalog, base)
        audit["reports"][0]["rows"][0]["status"] = "quarantined"
        with self.assertRaises(OgeCatalogError):
            build_whitehouse_278t_review_candidate(
                base, [report], audit, catalog, data_cutoff_at="2025-06-14T00:00:00Z")
        base["meta"]["is_demo"] = True
        with self.assertRaises(OgeCatalogError):
            _build(base)


if __name__ == "__main__":
    unittest.main()
