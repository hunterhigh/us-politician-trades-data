from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.house_annual_section_batch import (
    FAILURES, OBSERVATIONS, run_section_batch, select_section_batch,
)
from unison_snapshot.house_annual_sections import SCHEMA


class HouseAnnualSectionBatchTests(unittest.TestCase):
    def test_metadata_path_components_must_be_source_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence, review = Path(folder) / "evidence", Path(folder) / "review"
            path = evidence / "house_clerk/financial-reports/2025/10000001/bad.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"filing_year": 2025,
                                        "document_id": "../../other",
                                        "sha256": "a" * 64}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source identity"):
                select_section_batch(evidence, review, [])

    def test_new_plus_two_old_and_failure_does_not_touch_holdings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            evidence, review = root / "evidence", root / "review"
            metadata_paths = []
            for number in range(1, 5):
                document_id = f"1000000{number}"
                sha = f"{number}" * 64
                relative = Path("2025") / document_id / f"{sha}.json"
                meta = {"filing_year": 2025, "document_id": document_id,
                        "sha256": sha,
                        "archive_path": (f"house_clerk/financial-reports/2025/"
                                         f"{document_id}/{sha}.pdf")}
                path = evidence / "house_clerk/financial-reports" / relative
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(meta), encoding="utf-8")
                metadata_paths.append(path)
                extraction = review / "house_clerk/holding_extractions" / relative
                qualification = review / "house_clerk/holding_qualifications" / relative
                extraction.parent.mkdir(parents=True)
                qualification.parent.mkdir(parents=True)
                extraction.write_text(json.dumps({"source_sha256": sha,
                                                  "source": {"document_id": document_id}}),
                                      encoding="utf-8")
                qualification.write_text('{"production_eligible":true}', encoding="utf-8")
            selected = select_section_batch(evidence, review, [metadata_paths[0]], old_limit=2)
            self.assertEqual([path.parent.name for path in selected],
                             ["10000001", "10000004", "10000003"])
            def observe(pdf, extraction):
                if "10000004" in str(pdf):
                    raise ValueError("unreadable section")
                return {"schema_version": SCHEMA, "source_sha256": extraction["source_sha256"],
                        "schedule_a_extraction_row_count": 2,
                        "schedule_b_date_anchored_row_count": 3}
            with patch("unison_snapshot.house_annual_section_batch.observe_archived_annual_pdf",
                       side_effect=observe):
                summary = run_section_batch(evidence, review, [metadata_paths[0]], old_limit=2)
            self.assertEqual(summary, {"new_selected": 1, "old_selected": 2,
                                       "observed": 2, "failed": 1,
                                       "schedule_a_rows": 4,
                                       "schedule_b_date_anchored_rows": 6})
            self.assertEqual(len(list((review / OBSERVATIONS).rglob("*.json"))), 2)
            self.assertEqual(len(list((review / FAILURES).rglob("*.json"))), 1)
            self.assertTrue(all(path.read_text(encoding="utf-8") ==
                                '{"production_eligible":true}' for path in
                                (review / "house_clerk/holding_qualifications").rglob("*.json")))
            self.assertEqual(select_section_batch(evidence, review, [], old_limit=2),
                             [metadata_paths[1]])
            self.assertEqual(select_section_batch(evidence, review,
                                                  [metadata_paths[0]], old_limit=0),
                             [metadata_paths[0]])


if __name__ == "__main__":
    unittest.main()
