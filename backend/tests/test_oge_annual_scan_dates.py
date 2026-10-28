from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from observe_oge_annual_scan_dates import observe_fixed_batch
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_annual_scan_dates import (cover_observations,
                                                    observe_scan_date_blocks)


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "oge_annual_scanned_cover_dates_summary.json"
FIXED_COVERS = cover_observations()


class OgeAnnualScanDatesTests(unittest.TestCase):
    def test_six_fixed_covers_keep_roles_and_receipt_distinct(self) -> None:
        rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 6)
        self.assertEqual(len(FIXED_COVERS), 6)
        self.assertEqual({row["source_document_id"] for row in rows}, set(FIXED_COVERS))
        for row in rows:
            sha, kind, dates = FIXED_COVERS[row["source_document_id"]]
            self.assertEqual(row["source_sha256"], sha)
            self.assertEqual(row["cover_kind"], kind)
            self.assertEqual(row["dates"], {**dates, "agency_receipt": None})
            self.assertFalse(row["agency_receipt_verified"])
            self.assertTrue(row["part7_filing_date_unverified"])
        amendment = next(row for row in rows if row["cover_kind"] == "amendment_letter")
        self.assertIsNone(amendment["dates"]["agency_ethics_review"])
        biden_2022 = next(row for row in rows if row["source_document_id"].startswith("e53c"))
        self.assertIsNone(biden_2022["dates"]["filer_signature"])

    def test_unpinned_or_changed_source_never_yields_dates(self) -> None:
        document_id = next(iter(FIXED_COVERS))
        sha = FIXED_COVERS[document_id][0]
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / "report.pdf"
            pdf.write_bytes(b"%PDF-1.7\nnot the archived report\n%%EOF")
            with self.assertRaisesRegex(OgeCatalogError, "hash changed"):
                observe_scan_date_blocks(pdf, source_sha256=sha,
                                         document_id=document_id)
            with self.assertRaisesRegex(OgeCatalogError, "not pinned"):
                observe_scan_date_blocks(pdf, source_sha256="0" * 64,
                                         document_id=document_id)

    def test_missing_archive_batch_keeps_every_report_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            result = observe_fixed_batch(Path(folder))
        self.assertEqual(len(result["reports"]), 6)
        self.assertEqual(result["verified_agency_receipt_count"], 0)
        self.assertTrue(all(row["status"] == "coverage_unknown" and
                            row["part7_filing_date_unverified"] and
                            row["filed_at"] is None
                            for row in result["reports"]))


if __name__ == "__main__":
    unittest.main()
