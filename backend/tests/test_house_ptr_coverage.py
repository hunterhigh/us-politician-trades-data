from __future__ import annotations

from copy import deepcopy
import unittest

from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_ptr_coverage import assess_report_coverage


SHA = "a" * 64


class HousePtrCoverageTests(unittest.TestCase):
    def qualification(self, qualified: int, quarantined: int) -> dict:
        return {
            "document_id": "example-report",
            "source_sha256": SHA,
            "transactions": [{"id": f"candidate-{n}"} for n in range(qualified)],
            "quarantined": [{"extraction_id": f"quarantine-{n}"} for n in range(quarantined)],
        }

    def test_known_omission_keeps_existing_candidate_identity(self):
        qualification = self.qualification(1, 3)
        before = deepcopy(qualification)
        audit = assess_report_coverage(qualification, source_sha256=SHA,
                                       observed_page_minimum_rows=[5, 6])
        self.assertEqual(audit["coverage_status"], "known_incomplete")
        self.assertEqual(audit["unhandled_row_lower_bound"], 7)
        self.assertEqual(audit["retained_candidate_ids"], ["candidate-0"])
        self.assertEqual(qualification, before)

    def test_equal_counts_are_unverified_without_row_mapping(self):
        audit = assess_report_coverage(self.qualification(1, 3), source_sha256=SHA,
                                       observed_page_minimum_rows=[2, 2])
        self.assertEqual(audit["coverage_status"], "unverified")
        self.assertEqual(audit["unhandled_row_lower_bound"], 0)

    def test_source_hash_mismatch_fails_closed(self):
        with self.assertRaisesRegex(HouseIndexError, "source hash"):
            assess_report_coverage(self.qualification(1, 3), source_sha256="b" * 64,
                                   observed_page_minimum_rows=[5, 6])

    def test_invalid_page_counts_fail_closed(self):
        with self.assertRaisesRegex(HouseIndexError, "per-page"):
            assess_report_coverage(self.qualification(0, 1), source_sha256=SHA,
                                   observed_page_minimum_rows=[True, 2])


if __name__ == "__main__":
    unittest.main()
