"""Source-bound Senate annual holding amendment transitions."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

from unison_snapshot.pipeline_qa.migration_inventory import InventoryError
from unison_snapshot.pipeline_qa.publication_delta import build_publication_delta


OLD_ID = "516c1638-5b26-4a14-99be-d89c1b426839"
NEW_ID = "c6b1e74d-4cc1-4411-bb78-b22e8fa01e8e"
EVIDENCE, REVIEW = "a" * 40, "b" * 40


def _fixture():
    old_sha = hashlib.sha256(b"original official annual HTML").hexdigest()
    new_sha = hashlib.sha256(b"amended official annual HTML").hexdigest()
    objects = {}
    paths = {}
    for filing, digest, data, amendment, date, extraction_id in (
        (OLD_ID, old_sha, b"original official annual HTML", 0, "2026-05-15", "senate-annual:old"),
        (NEW_ID, new_sha, b"amended official annual HTML", 1, "2026-10-06", "senate-annual:new"),
    ):
        url = f"https://efdsearch.senate.gov/search/view/annual/{filing}/"
        base = f"senate_efd/annual/reports/{filing}"
        paths[base] = [f"{digest}.html", f"{digest}.metadata.json"]
        meta = {"schema_version": "senate-efd-annual-archive/v1", "source_sha256": digest,
                "byte_length": len(data), "document_id": filing, "document_url": url,
                "filer_name": "Tim Scott", "office": "Scott, Tim (Senator)",
                "report_year": 2025, "amendment_number": amendment,
                "portal_listed_date": date}
        objects[(EVIDENCE, f"{base}/{digest}.html")] = data
        objects[(EVIDENCE, f"{base}/{digest}.metadata.json")] = json.dumps(meta).encode()
        extraction = {"source_sha256": digest, "document_id": filing,
                      "source_url": url, "filer_name": meta["filer_name"],
                      "report_year": 2025, "amendment_number": amendment,
                      "portal_listed_date": date, "row_count": 1,
                      "rows": [{"extraction_id": extraction_id, "row_number": "1",
                                "asset_name": "Example", "value_low": 1001}]}
        objects[(REVIEW, f"senate_efd/annual/extractions/{filing}/{digest}/"
                          "senate-efd-annual-html-2026-09-v2.json")] = json.dumps(extraction).encode()
    person = {"id": "senate:S001184", "disclosure_authority": "senate_efd"}
    base = {"person_id": person["id"], "source_id": "senate_efd", "source": "Senate",
            "verification_status": "official_matched", "owner": "Self",
            "asset_name": "Example", "instrument_type": "Stock",
            "report_period_end": "2025-12-31", "value_low": 1001,
            "value_high": 15000, "change_from_prior": "unknown", "ticker": None,
            "ticker_mapping_basis": None}
    old = {**base, "id": "senate-annual:old", "filing_id": OLD_ID,
           "source_url": f"https://efdsearch.senate.gov/search/view/annual/{OLD_ID}/",
           "filed_at": "2026-05-15T00:00:00Z"}
    new = {**base, "id": "senate-annual:new", "filing_id": NEW_ID,
           "source_url": f"https://efdsearch.senate.gov/search/view/annual/{NEW_ID}/",
           "filed_at": "2026-10-06T00:00:00Z"}
    previous = {"people": [person], "transactions": [], "reported_holdings": [old]}
    unified = {"meta": {"is_demo": False, "data_cutoff_at": "2026-10-08T23:59:59Z"},
               "people": [person], "transactions": [], "reported_holdings": [new]}
    prepared = deepcopy(unified)
    prepared.update(security_market_data=[], source_health=[])
    sources = {name: {"meta": {"is_demo": False}, "people": [],
                      "transactions": [], "reported_holdings": []}
               for name in ("house_clerk", "oge", "senate_efd")}
    sources["senate_efd"]["people"] = [person]
    sources["senate_efd"]["reported_holdings"] = [new]
    return objects, paths, previous, unified, prepared, sources


class SenateAnnualAmendmentTests(unittest.TestCase):
    def _run(self, fixture):
        objects, paths, previous, unified, prepared, sources = fixture
        def read_object(repo, commit, path):
            return objects[(commit, path)]
        def list_paths(repo, commit, directory):
            return paths[directory]
        with patch("unison_snapshot.pipeline_qa.senate_annual_amendment.git_object",
                   side_effect=read_object), patch(
                   "unison_snapshot.pipeline_qa.senate_annual_amendment.git_tree_paths",
                   side_effect=list_paths):
            # The function defaults bind at definition time, so patch the
            # publication boundary with a wrapper that supplies test readers.
            from unison_snapshot.pipeline_qa import senate_annual_amendment as module
            def verified(**kwargs):
                return module.verified_amendment_rekeys(
                    **kwargs, read_object=read_object, list_paths=list_paths)
            with patch("unison_snapshot.pipeline_qa.publication_delta.verified_amendment_rekeys",
                       side_effect=verified):
                return build_publication_delta(
                    previous_board=previous, unified=unified, prepared=prepared,
                    sources=sources, repo="repo", main_commit="c" * 40,
                    review_commit=REVIEW, evidence_commit=EVIDENCE)

    def test_complete_unchanged_report_rekeys_one_holding(self):
        report = self._run(_fixture())
        self.assertEqual(report["counts"]["reported_holdings"]["rekey"], 1)
        self.assertEqual(report["counts"]["reported_holdings"]["new"], 0)
        self.assertEqual(report["changes"][0]["reason"], "official_amendment")
        self.assertEqual(report["transition_verification_level"],
                         "archived_senate_amendment_bytes_and_rows")

    def test_changed_report_row_or_candidate_value_fails_closed(self):
        fixture = _fixture()
        objects, _, _, _, _, _ = fixture
        path = next(path for commit, path in objects if commit == REVIEW)
        extraction = json.loads(objects[(REVIEW, path)])
        extraction["rows"][0]["value_low"] = 2000
        objects[(REVIEW, path)] = json.dumps(extraction).encode()
        with self.assertRaises(InventoryError):
            self._run(fixture)
        fixture = _fixture()
        fixture[4]["reported_holdings"][0]["value_low"] = 2000
        fixture[3]["reported_holdings"][0]["value_low"] = 2000
        fixture[5]["senate_efd"]["reported_holdings"][0]["value_low"] = 2000
        with self.assertRaises(InventoryError):
            self._run(fixture)


if __name__ == "__main__":
    unittest.main()
