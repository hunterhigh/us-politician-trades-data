from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

from unison_snapshot.house import HouseIndexError

HAS_IMAGE_DEPS = (importlib.util.find_spec("PIL") is not None and
                  importlib.util.find_spec("pdfplumber") is not None)
if HAS_IMAGE_DEPS:
    from unison_snapshot.house_ptr_failed_grid_shadow import build_failed_grid_shadow


ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(HAS_IMAGE_DEPS, "optional House image audit dependencies unavailable")
class HousePtrFailedGridShadowTests(unittest.TestCase):
    def test_wrong_pdf_or_failure_identity_fails_before_render(self):
        spec = {"schema_version": "house-ptr-failed-grid-sample/v1",
                "document_id": "9115704", "source_sha256": "0" * 64}
        failure = {"schema_version": "house-ptr-parse-failure/v1",
                   "document_id": "9115704", "source_sha256": "0" * 64}
        with self.assertRaisesRegex(HouseIndexError, "source or failure identity mismatch"):
            build_failed_grid_shadow(b"wrong pdf", failure, spec)

    def test_checked_in_failed_samples_have_only_quarantined_locators(self):
        expected = {"9115704": ([5, 2], "legacy_checkbox_10_amount_columns"),
                    "9116260": ([4, 3], "legacy_checkbox_11_amount_columns"),
                    "9116326": ([4, 4], "legacy_checkbox_11_amount_columns")}
        for document_id, (pages, layout) in expected.items():
            with self.subTest(document_id=document_id):
                spec = json.loads((ROOT / "docs" /
                    f"house-ptr-failed-grid-{document_id}-spec.json").read_text(encoding="utf-8"))
                report = json.loads((ROOT / "docs" /
                    f"house-ptr-failed-grid-{document_id}-shadow.json").read_text(encoding="utf-8"))
                rows = report["physical_rows"]
                self.assertEqual(report["source_sha256"], spec["source_sha256"])
                self.assertEqual(report["layout_family"], layout)
                self.assertEqual(report["file_status"], "failed_open")
                self.assertEqual(report["counts"]["observed_page_minimum_rows"], pages)
                self.assertEqual(len(rows), sum(pages))
                self.assertEqual(len({row["locator"] for row in rows}), len(rows))
                self.assertEqual(report["counts"]["quarantined_shadow_rows"], len(rows))
                self.assertEqual(report["counts"]["qualified_rows"], 0)
                self.assertTrue(all(row["disposition"] == "quarantined_shadow_failed_report"
                                    and row["candidate_id"] is None for row in rows))
                for page_number, count in enumerate(pages, 1):
                    self.assertEqual([row["row"] for row in rows if row["page"] == page_number],
                                     list(range(1, count + 1)))


if __name__ == "__main__":
    unittest.main()
