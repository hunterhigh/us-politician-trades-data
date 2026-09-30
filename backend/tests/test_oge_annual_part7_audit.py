from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.oge_annual_part7_audit import audit_annual_part7


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class OgeAnnualPart7AuditTests(unittest.TestCase):
    def test_pinned_ten_rows_stay_quarantined_with_distinct_dates(self) -> None:
        extraction = _fixture("oge_annual_vance_2026_v2.json")
        evidence = _fixture("oge_annual_vance_2026_part7_evidence.json")
        result = audit_annual_part7(extraction, evidence)
        self.assertEqual(result, _fixture("oge_annual_vance_2026_part7_audit.json"))
        self.assertEqual(len(result["rows"]), 10)
        self.assertTrue(all(row["shadow_disposition"] == "quarantined" and
                            row["official_278t_duplicate_status"] == "unresolved" and
                            row["fixed_candidate_overlap_count"] == 0
                            for row in result["rows"]))
        dia = [row for row in result["rows"] if row["number"] in {"7", "8", "9"}]
        self.assertEqual([row["transaction_date"] for row in dia],
                         ["2025-07-14", "2025-10-14", "2025-12-15"])
        self.assertIsNone(result["annual_report_filing_date"])
        self.assertEqual(result["cover"]["filer_certified_on"], "2026-06-23")
        self.assertEqual(result["cover"]["oge_received_on"], "2026-06-29")

    def test_source_date_or_row_drift_fails_closed(self) -> None:
        extraction = _fixture("oge_annual_vance_2026_v2.json")
        evidence = _fixture("oge_annual_vance_2026_part7_evidence.json")
        for field, value in (("source_sha256", "0" * 64),
                             ("parser_version", "oge-278e-tables/v1"),
                             ("filing_date", "2026-06-23")):
            changed = deepcopy(extraction)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                audit_annual_part7(changed, evidence)
        changed = deepcopy(evidence)
        changed["printed_part7_rows"][0]["amount_low"] = 15001
        with self.assertRaises(ValueError):
            audit_annual_part7(extraction, changed)
        changed = deepcopy(evidence)
        changed["official_278t_coverage_complete"] = True
        with self.assertRaises(ValueError):
            audit_annual_part7(extraction, changed)


if __name__ == "__main__":
    unittest.main()
